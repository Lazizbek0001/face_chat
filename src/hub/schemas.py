import os
import uuid
from urllib.parse import urlparse

from pydantic import BaseModel, Field, SecretStr, field_validator

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _check_url(value: str, *, require_secure: bool) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("must be an absolute http(s) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("must not contain a query string or fragment")
    if (
        require_secure
        and parsed.scheme != "https"
        and parsed.hostname not in _LOCAL_HOSTS
        and os.getenv("HUB_ALLOW_INSECURE_HTTP") != "1"
    ):
        # The verify URL receives users' passwords, so it must be encrypted in transit.
        raise ValueError("must use https (http is allowed only for localhost)")
    return value.rstrip("/")


class SiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(max_length=500)
    verify_url: str = Field(max_length=500)

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, v: str) -> str:
        return _check_url(v, require_secure=False)

    @field_validator("verify_url")
    @classmethod
    def _verify_url(cls, v: str) -> str:
        return _check_url(v, require_secure=True)


class SiteCreated(BaseModel):
    """Returned once, when the site is registered. The keys cannot be read again."""

    id: uuid.UUID
    name: str
    base_url: str
    verify_url: str
    site_key: str
    hub_key: str


class SiteRead(BaseModel):
    id: uuid.UUID
    name: str
    base_url: str
    linked: bool


class LinkRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: SecretStr = Field(min_length=1, max_length=256)


class LinkResponse(BaseModel):
    linked: bool
    site_id: uuid.UUID


class OpenResponse(BaseModel):
    redirect_url: str


class AuthorizeRequest(BaseModel):
    site_id: uuid.UUID
    # Made by the site and stored in a cookie there. The hub only passes it back.
    state: str = Field(min_length=16, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")


class RedeemRequest(BaseModel):
    code: str = Field(min_length=20, max_length=200)


class RedeemResponse(BaseModel):
    user_id: str
