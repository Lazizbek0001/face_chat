import hashlib
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import Depends
from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./test.db")


def hash_secret(value: str) -> str:
    """API keys and hub keys are stored only as this hash, so a database leak does not leak them."""
    return hashlib.sha256(value.encode()).hexdigest()


class Base(DeclarativeBase):
    pass


class User(SQLAlchemyBaseUserTableUUID, Base):
    apikeys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "apikeys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user.id"), nullable=False)
    # SHA-256 of the key. The key itself is shown once, when it is generated.
    key_hash = Column(String(64), unique=True, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    valid_until = Column(DateTime, default=lambda: datetime.now(timezone.utc) + timedelta(days=30))

    user = relationship("User", back_populates="apikeys")


engine = create_async_engine(DATABASE_URL)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def create_db_and_tables():
    # Imported here so every model is registered on Base before the tables are created.
    import src.models.chat  # noqa: F401
    import src.models.hub  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)
