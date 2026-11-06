"""Login through Authentik (OpenID Connect).

This API never sees passwords. The browser logs in at Authentik and gets an access token
(a JWT signed by Authentik). Each request sends it as "Authorization: Bearer <token>",
and this module checks it with Authentik's public keys (JWKS).

The first time someone calls the API, a local User row is created for them, keyed by the
token's "sub" claim, so chats and API keys can belong to them.
"""
import asyncio
import os

import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2AuthorizationCodeBearer
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import User, get_async_session

load_dotenv()

# Authentik's issuer for the face-chat application. Note the trailing slash.
OIDC_ISSUER = os.getenv("OIDC_ISSUER", "http://localhost:9000/application/o/face-chat/")
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "face-chat")

_authentik = OIDC_ISSUER.split("/application/o/")[0]
OIDC_AUTHORIZE_URL = os.getenv("OIDC_AUTHORIZE_URL", f"{_authentik}/application/o/authorize/")
OIDC_TOKEN_URL = os.getenv("OIDC_TOKEN_URL", f"{_authentik}/application/o/token/")
OIDC_JWKS_URL = os.getenv("OIDC_JWKS_URL", f"{OIDC_ISSUER.rstrip('/')}/jwks/")

# Only asymmetric algorithms: accepting HS256 here would let anyone who knows the
# public key forge tokens.
ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]

# Also gives the /docs page an "Authorize" button that logs in through Authentik.
oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=OIDC_AUTHORIZE_URL,
    tokenUrl=OIDC_TOKEN_URL,
    scopes={"openid": "Sign in", "email": "Email address", "profile": "Name"},
    auto_error=False,
)

# Caches Authentik's public keys, and fetches them again when a token uses an unknown key.
_jwks_client = jwt.PyJWKClient(OIDC_JWKS_URL, cache_keys=True, lifespan=3600, timeout=10)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def decode_token(token: str) -> dict:
    """Check the signature, issuer, audience and expiry. Returns the token's claims."""
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(status_code=503, detail="Login service is unreachable.")
    except (jwt.PyJWKClientError, jwt.DecodeError):
        raise _unauthorized("Invalid token.")
    try:
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=OIDC_CLIENT_ID,
            issuer=OIDC_ISSUER,
            leeway=30,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token has expired.")
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token.")


async def _get_or_create_user(session: AsyncSession, claims: dict) -> User:
    sub = str(claims["sub"])
    user = (await session.execute(select(User).where(User.oidc_sub == sub))).scalars().first()
    if user is not None:
        return user

    email = claims.get("email")
    if not email:
        raise _unauthorized("Token has no email. Request the 'email' scope.")
    email_verified = claims.get("email_verified") is True

    existing = (
        await session.execute(select(User).where(func.lower(User.email) == email.lower()))
    ).scalars().first()
    if existing is not None:
        # An account from before Authentik (or from another Authentik user) has this email.
        # Link it only if Authentik says the email is verified; otherwise anyone could
        # take over an old account by setting its email in Authentik.
        if existing.oidc_sub is None and email_verified:
            existing.oidc_sub = sub
            await session.commit()
            return existing
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Ask an administrator to link it.",
        )

    user = User(email=email, oidc_sub=sub, hashed_password="", is_verified=email_verified)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # Two first requests at the same time: the other one created the user.
        await session.rollback()
        user = (await session.execute(select(User).where(User.oidc_sub == sub))).scalars().first()
        if user is None:
            raise
    return user


async def current_active_user(
    token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_async_session),
) -> User:
    if not token:
        raise _unauthorized("Not logged in.")
    # PyJWKClient fetches keys with blocking I/O, so keep it off the event loop.
    claims = await asyncio.to_thread(decode_token, token)
    user = await _get_or_create_user(session, claims)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled.")
    return user
