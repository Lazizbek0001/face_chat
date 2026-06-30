import uuid

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Float,
    Text,
    DateTime,
    ForeignKey,
    String
)

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.db import Base


class Chat(Base):
    __tablename__ = "chats"


    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )


    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("user.id"),
        nullable=False
    )


    title = Column(
        String(255),
        default="New Chat"
    )


    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc)
    )


    messages = relationship(
        "ChatMessage",
        back_populates="chat",
        cascade="all, delete-orphan"
    )



class ChatMessage(Base):

    __tablename__ = "chat_messages"


    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )


    chat_id = Column(
        UUID(as_uuid=True),
        ForeignKey("chats.id"),
        nullable=False
    )


    role = Column(
        String(20)
    )


    content = Column(
        Text
    )
    
    duration = Column(
        Float,
        nullable=True,
        default=None
    )


    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc)
    )


    chat = relationship(
        "Chat",
        back_populates="messages"
    )