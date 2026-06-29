from fastapi import FastAPI, HTTPException, File, UploadFile, Depends, Header
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield

app = FastAPI(lifespan=lifespan)

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
        file_bytes1 = await img1.read()
        file_bytes2 = await img2.read()
        
        with tempfile.NamedTemporaryFile(delete=True, suffix=".jpg") as tmp1, \
             tempfile.NamedTemporaryFile(delete=True, suffix=".jpg") as tmp2:
             
            tmp1.write(file_bytes1)
            tmp2.write(file_bytes2)
            tmp1.flush()
            tmp2.flush()
            
            # Perform direct facial verification
            result = DeepFace.verify(
                img1_path=tmp1.name,
                img2_path=tmp2.name,
                model_name="Facenet512",
                enforce_detection=True
            )
            
            # Convert distance to similarity percentage (0-100)
            # Smaller distance = higher similarity
            # Typical max distance for VGG-Face: ~1.5
            distance = result["distance"]
            max_distance = 1.5
            similarity_percentage = max(0, (1 - distance / max_distance) * 100)
            
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