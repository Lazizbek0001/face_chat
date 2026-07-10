import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
from fastapi import FastAPI, HTTPException, File, Request, UploadFile, Depends, Header, WebSocket, WebSocketDisconnect
from src.ai_face import compare_embeddings, generate_embedding
from src.video import create_video_writer, decode_frame, generate_frames, write_frame
from .ai_ollama import ask_ai_cloud
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
from fastapi.responses import StreamingResponse

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)

    if request.url.path not in {"/video_feed", "/ws/feed"}:
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
            
            img1, model_name1 = generate_embedding(tmp1.name)
            img2, model_name2 = generate_embedding(tmp2.name)
            result = compare_embeddings(img1, img2)
            duration = time.perf_counter() - start
            logger.info(
                f"Deepface response generated in {round(duration, 2)} seconds and similarity percentage is {result['similarity']}, model: {model_name1}",
            )
        return {
            "verified": bool(result['verified']),
            "similarity": result['similarity'],
            "cosine_similarity": result["cosine_similarity"],
            "threshold": result["threshold"],
            "duration": duration
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
        content=message,
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
        ai_response = await ask_ai_cloud(messages)
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




@app.get("/video_feed")
async def video_feed():
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.websocket("/ws/feed")
async def receive_stream(
    websocket: WebSocket,
    api_key_record: ApiKey = Depends(validate_api_key),
    session: AsyncSession = Depends(get_async_session),
):
    await websocket.accept()

    writer = None

    try:
        while True:
            data = await websocket.receive_bytes()

            frame = decode_frame(data)
            if frame is None:
                continue
            
            name = (
                f"{api_key_record.user_id}_"
                f"{datetime.now():%Y%m%d_%H%M%S}.mp4"
            )

            if writer is None:
                writer = create_video_writer(name, frame)

            write_frame(writer, frame)

    except WebSocketDisconnect:
        print("Client disconnected")

    finally:
        if writer is not None:
            writer.release()