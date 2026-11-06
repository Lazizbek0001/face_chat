import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.db import Base


def utcnow() -> datetime:
    """Naive UTC, matching the DateTime columns used across the project."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Site(Base):
    """A website registered in the hub by an admin."""

    __tablename__ = "hub_sites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False)
    base_url = Column(String(500), nullable=False)
    verify_url = Column(String(500), nullable=False)

    site_key_hash = Column(String(64), unique=True, nullable=False)
    hub_key = Column(String(128), nullable=False)

    created_at = Column(DateTime, default=utcnow)

    links = relationship("SiteLink", back_populates="site", cascade="all, delete-orphan")
    codes = relationship("SsoCode", back_populates="site", cascade="all, delete-orphan")


class SiteLink(Base):
    """Connects a hub user to their account on a site. Never holds a password."""

    __tablename__ = "hub_links"
    __table_args__ = (
        UniqueConstraint("user_id", "site_id", name="uq_hub_link_user_site"),
        # One account on a site can belong to only one hub user.
        UniqueConstraint("site_id", "remote_user_id", name="uq_hub_link_site_remote_user"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    site_id = Column(UUID(as_uuid=True), ForeignKey("hub_sites.id", ondelete="CASCADE"), nullable=False)
    remote_user_id = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    site = relationship("Site", back_populates="links")


class SsoCode(Base):
    """One-time code that lets a site log a linked user in. Only the hash is stored."""

    __tablename__ = "hub_sso_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code_hash = Column(String(64), unique=True, nullable=False)
    site_id = Column(UUID(as_uuid=True), ForeignKey("hub_sites.id", ondelete="CASCADE"), nullable=False)
    remote_user_id = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)

    site = relationship("Site", back_populates="codes")
