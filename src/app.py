import time

from fastapi import FastAPI, HTTPException, File, Request, UploadFile, Depends, Header
from .ai_ollama import ask_ai_cloud, ask_ai_local
from .models.chat import Chat, ChatMessage
from src.schemas import UserCreate, UserRead, UserUpdate
from src.db import ApiKey, User, create_db_and_tables, get_async_session
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
from sqlalchemy import select
from datetime import datetime, timezone
import uuid
import tempfile
from deepface import DeepFace
from src.users import auth_backend, current_active_user, fastapi_users
from src.logging_config import logger
@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()

    response = await call_next(request)

    duration = time.perf_counter() - start

    logger.info(
        "%s %s %d %.3fs",
        request.method,
        request.url.path,
        response.status_code,
        duration,
    )

    return response


# Existing FastAPI-Users Routers
app.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth/jwt", tags=["auth"])
app.include_router(fastapi_users.get_register_router(UserRead, UserCreate), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_reset_password_router(), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_verify_router(UserRead), prefix="/auth", tags=["auth"])
app.include_router(fastapi_users.get_users_router(UserRead, UserUpdate), prefix="/users", tags=["users"])


@app.post("/apikeys/generate", tags=["API Keys"])
async def generate_api_key(
    user: User = Depends(current_active_user), 
    db: AsyncSession = Depends(get_async_session)
):
    """Generates a new API Key for the logged-in user valid for 30 days."""
    new_key = ApiKey(user_id=user.id)
    db.add(new_key)
    await db.commit()
    return {
        "api_key": str(new_key.id),
        "valid_until": new_key.valid_until.isoformat()
    }




async def validate_api_key(x_api_key: str = Header(None), db: AsyncSession = Depends(get_async_session)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header missing.")
    
    try:
        key_uuid = uuid.UUID(x_api_key)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid API Key format.")
    
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_uuid))
    api_key_record = result.scalars().first()
    
    if not api_key_record:
        raise HTTPException(status_code=401, detail="Unauthorized API Key.")
        
    # Check if the key has expired compared to the current UTC timestamp
    if api_key_record.valid_until.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="API Key has expired.")
        
    return api_key_record

@app.post("/api/v1/verify-faces", tags=["Face Recognition"])
async def verify_two_faces(
    img1: UploadFile = File(...),
    img2: UploadFile = File(...),
    api_key_record: ApiKey = Depends(validate_api_key)
):
    try:
        start = time.perf_counter()



    
        file_bytes1 = await img1.read()
        file_bytes2 = await img2.read()
        
        with tempfile.NamedTemporaryFile(delete=True, suffix=".jpg") as tmp1, \
             tempfile.NamedTemporaryFile(delete=True, suffix=".jpg") as tmp2:
             
            tmp1.write(file_bytes1)
            tmp2.write(file_bytes2)
            tmp1.flush()
            tmp2.flush()
            
            result = DeepFace.verify(
                img1_path=tmp1.name,
                img2_path=tmp2.name,
                model_name="Facenet512",
                enforce_detection=True
            )
            distance = result["distance"]
            max_distance = 1.5
            similarity_percentage = max(0, (1 - distance / max_distance) * 100)
            duration = time.perf_counter() - start
            logger.info(
                "Deepface response generated in %.3f seconds",
                duration,
            )
            
        return {
            "verified": result["verified"],
            "similarity": round(similarity_percentage, 2),  # 0-100%
            "similarity_confidence": "high" if similarity_percentage > 70 else "medium" if similarity_percentage > 40 else "low",
            "distance": round(distance, 4),  # Raw metric for reference
            "threshold": result["threshold"],
            "modelUsed": result["model"]
        }
        
    except ValueError as e:
        # DeepFace raises ValueError when no face is detected
        raise HTTPException(status_code=400, detail=f"Could not detect face: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Facial analysis failed: {str(e)}")
    
@app.get("/apikeys/list", tags=["API Keys"])
async def get_apikeys(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session)
    ):
    result = await session.execute(select(ApiKey).filter_by(user_id=user.id).order_by(ApiKey.created_at.desc()))
    apikeys = [row[0] for row in result.all()]

    result = await session.execute(select(User).filter_by(id=user.id))
    users = [row[0] for row in result.all()]
    user_dict = {u.id: u.email for u in users}
    keys_data = []

    for key in apikeys:
        keys_data.append(
            {
                "key": str(key.id),
                "user_id": str(key.user_id),
                "created_at": key.created_at,
                "is_owner": key.user_id == user.id,
                "email": user_dict[key.user_id]

            }
        )
    return {"api_keys":keys_data}


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
    result = await session.execute(select(Chat).filter_by(user_id=api_key_record.user_id))
    chats = [row[0] for row in result.all()]
    
    chats_data = []

    for chat in chats:
        result = await session.execute(select(ChatMessage).filter_by(chat_id=chat.id))
        messages = [m[0] for m in result.all()]
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
        
    return {"api_keys":chats_data}

from uuid import UUID


@app.post("/chat/{chat_id}", tags=["AI Chat"])
async def send_message(
    chat_id: UUID,
    message: str,
    api_key_record: ApiKey = Depends(validate_api_key),
    session: AsyncSession = Depends(get_async_session)
):

 
    result = await session.execute(
        select(Chat)
        .where(
            Chat.id == chat_id,
            Chat.user_id == api_key_record.user_id
        )
    )


    chat = result.scalars().first()


    if not chat:
        raise HTTPException(
            status_code=404,
            detail="Chat not found"
        )


    user_message = ChatMessage(
        chat_id=chat.id,
        role="user",
        content=message
    )


    session.add(user_message)

    await session.flush()



    result = await session.execute(
        select(ChatMessage)
        .where(
            ChatMessage.chat_id == chat.id
        )
        .order_by(
            ChatMessage.created_at
        )
    )


    history = result.scalars().all()


    messages = [
        {
            "role": msg.role,
            "content": msg.content
        }
        for msg in history
    ]

    start = time.perf_counter()
    ai_response = await ask_ai_cloud(messages)


    duration = time.perf_counter() - start
    assistant_message = ChatMessage(
        chat_id=chat.id,
        role="system",
        content=ai_response,
        duration=round(duration, 3)
    )
    logger.info(
        "AI response generated in %.3f seconds for chat %s",
        duration,
        chat.id,
    )


    session.add(assistant_message)


    await session.commit()


    return {
        "chat_id": str(chat.id),
        "response": ai_response
    }