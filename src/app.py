import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.ai_ollama import MODELS, ask_ai_cloud
from src.db import ApiKey, User, create_db_and_tables, get_async_session, hash_secret
from src.hub.router import router as hub_router
from src.logging_config import logger
from src.models.chat import Chat, ChatMessage
from src.schemas import ChatRequest, UserCreate, UserRead, UserUpdate
from src.users import auth_backend, current_active_user, fastapi_users

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)

    logger.info(
        "%s %s %d",
        request.method,
        request.url.path,
        response.status_code,
    )

    return response


# Existing FastAPI-Users Routers
app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"])
app.include_router(fastapi_users.get_register_router(UserRead, UserCreate), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_reset_password_router(), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"])
app.include_router(hub_router)


@app.post("/apikeys/generate", tags=["API Keys"])
async def generate_api_key(
    user: User = Depends(current_active_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Generates a new API key for the logged-in user, valid for 30 days.

    The key is returned only here. Only its hash is stored, so it cannot be shown again.
    """
    raw_key = secrets.token_urlsafe(32)
    new_key = ApiKey(user_id=user.id, key_hash=hash_secret(raw_key))
    db.add(new_key)
    await db.commit()
    return {
        "id": str(new_key.id),
        "api_key": raw_key,
        "valid_until": new_key.valid_until.isoformat(),
    }


async def validate_api_key(x_api_key: str = Header(None), db: AsyncSession = Depends(get_async_session)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header missing.")

    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_secret(x_api_key)))
    api_key_record = result.scalars().first()

    if not api_key_record:
        raise HTTPException(status_code=401, detail="Unauthorized API Key.")
        
    # Check if the key has expired compared to the current UTC timestamp
    if api_key_record.valid_until.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="API Key has expired.")
        
    return api_key_record

@app.get("/apikeys/list", tags=["API Keys"])
async def get_apikeys(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session)
    ):
    """Lists the user's keys without the keys themselves: only their ids and dates."""
    result = await session.execute(
        select(ApiKey).filter_by(user_id=user.id).order_by(ApiKey.created_at.desc())
    )
    keys_data = [
        {
            "id": str(key.id),
            "user_id": str(key.user_id),
            "created_at": key.created_at,
            "valid_until": key.valid_until,
            "email": user.email,
        }
        for key in result.scalars().all()
    ]
    return {"api_keys": keys_data}


@app.post("/chat/new", tags=["AI Chat"])
async def create_chat(
    title: str,
    api_key_record: ApiKey = Depends(validate_api_key),
    session: AsyncSession = Depends(get_async_session)
):

    new_chat = Chat(
        user_id=api_key_record.user_id,
        title=title
    )


    session.add(new_chat)

    await session.commit()

    await session.refresh(new_chat)


    return {
        "chat_id": str(new_chat.id),
        "title": new_chat.title
    }

@app.get("/chat/list", tags=["AI Chat"])
async def get_chats(
    api_key_record: ApiKey = Depends(validate_api_key),
    session: AsyncSession = Depends(get_async_session)
    ):
    # selectinload fetches every chat's messages in one extra query, not one query per chat.
    result = await session.execute(
        select(Chat)
        .filter_by(user_id=api_key_record.user_id)
        .options(selectinload(Chat.messages))
    )
    chats = result.scalars().all()

    chats_data = []

    for chat in chats:
        messages = sorted(chat.messages, key=lambda m: m.created_at)
        chats_data.append(
            {
                "id": chat.id,
                "user_id": chat.user_id,
                "title": chat.title,
                "created_at": chat.created_at,
                "messages": [
                    {
                        "id": m.id,
                        "chat_id":m.chat_id,
                        "role":m.role,
                        "content": m.content,
                        "created_at":m.created_at,
                    }if m.role == 'user' else 
                    {
                        "id": m.id,
                        "chat_id":m.chat_id,
                        "role":m.role,
                        "duration": m.duration,
                        "content": m.content,
                        "created_at":m.created_at,
                    } for m in messages 
                ]

            }
        )
        
    return {"chat_list":chats_data}

# GET is the right method for reading a list. POST still works so existing clients keep working.
@app.api_route("/chat/ai/models", methods=["GET", "POST"], tags=["AI Chat"])
async def list_models(
    api_key_record: ApiKey = Depends(validate_api_key),
):
    return MODELS

@app.post("/chat/{chat_id}", tags=["AI Chat"])
async def send_message(
    chat_id: UUID,
    data: ChatRequest,
    api_key_record: ApiKey = Depends(validate_api_key),
    session: AsyncSession = Depends(get_async_session),
):
    result = await session.execute(
        select(Chat).where(
            Chat.id == chat_id,
            Chat.user_id == api_key_record.user_id,
        )
    )

    chat = result.scalars().first()

    if not chat:
        raise HTTPException(
            status_code=404,
            detail="Chat not found",
        )

    # Save user's message
    user_message = ChatMessage(
        chat_id=chat.id,
        role="user",
        content=data.message,
    )

    session.add(user_message)
    await session.flush()

    # Fetch conversation history
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat.id)
        .order_by(ChatMessage.created_at)
    )

    history = result.scalars().all()

    messages = [
        {
            "role": msg.role,
            "content": msg.content,
        }
        for msg in history
    ]

    # Generate AI response
    start = time.perf_counter()

    try:
        ai_response = await ask_ai_cloud(messages, model=data.model)
    except Exception:
        logger.exception("Failed to generate AI response.")
        raise HTTPException(
            status_code=503,
            detail="AI service is temporarily unavailable.",
        )

    duration = time.perf_counter() - start

    logger.info(
        "AI response generated in %.3f seconds for chat %s",
        duration,
        chat.id,
    )

    # Save assistant response
    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="assistant",
        content=ai_response,
        duration=round(duration, 3),
    )

    session.add(assistant_message)
    await session.commit()

    return {
        "chat_id": str(chat.id),
        "response": ai_response,
        "duration": round(duration, 3),
    }
