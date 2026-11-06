import hashlib
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import CHAR, Boolean, Column, DateTime, ForeignKey, String, TypeDecorator
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./test.db")


def hash_secret(value: str) -> str:
    """API keys are stored only as this hash, so a database leak does not leak them."""
    return hashlib.sha256(value.encode()).hexdigest()


class GUID(TypeDecorator):
    """UUID stored as CHAR(36) text outside PostgreSQL.

    The same type fastapi-users used for user.id, so existing user rows keep matching.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID())
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(value))

    def process_result_value(self, value, dialect):
        if value is None or isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


class Base(DeclarativeBase):
    pass


class User(Base):
    """A person who has logged in through Authentik. Authentik holds their password."""

    __tablename__ = "user"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    email = Column(String(320), unique=True, index=True, nullable=False)
    # The "sub" claim from Authentik: the stable id of this person there.
    oidc_sub = Column(String(255), unique=True, nullable=True)
    # Left over from the old password login. Always empty for new users.
    hashed_password = Column(String(1024), nullable=False, default="")
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

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

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session
