import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import User, get_async_session, hash_secret
from src.hub.schemas import (
    AuthorizeRequest,
    LinkRequest,
    LinkResponse,
    OpenResponse,
    RedeemRequest,
    RedeemResponse,
    SiteCreate,
    SiteCreated,
    SiteRead,
)
from src.logging_config import logger
from src.models.hub import Site, SiteLink, SsoCode, utcnow
from src.users import current_active_user, current_superuser

router = APIRouter(prefix="/hub", tags=["Hub"])

CODE_TTL = timedelta(seconds=60)
VERIFY_TIMEOUT_SECONDS = 10.0


class RateLimiter:
    """Sliding-window limiter. In memory, so it is per process: use Redis if you run several workers."""

    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict = defaultdict(deque)

    def allow(self, key) -> bool:
        now = time.monotonic()
        if len(self._hits) > 10_000:
            self._prune(now)
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.max_calls:
            return False
        hits.append(now)
        return True

    def _prune(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] > self.window]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


# Linking is a password-guessing surface, so it is limited per hub user and per site account.
link_limiter = RateLimiter(max_calls=5, window_seconds=60)
username_limiter = RateLimiter(max_calls=10, window_seconds=60)


async def get_site_client():
    # No redirects: a site must not be able to bounce the hub (and the password) elsewhere.
    async with httpx.AsyncClient(timeout=VERIFY_TIMEOUT_SECONDS, follow_redirects=False) as client:
        yield client


async def _get_site(session: AsyncSession, site_id: UUID) -> Site:
    site = (await session.execute(select(Site).where(Site.id == site_id))).scalars().first()
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found.")
    return site


async def _get_link(session: AsyncSession, user_id, site_id) -> SiteLink | None:
    result = await session.execute(
        select(SiteLink).where(SiteLink.user_id == user_id, SiteLink.site_id == site_id)
    )
    return result.scalars().first()


# ---------------------------------------------------------------- admin: site registry


@router.post("/sites", response_model=SiteCreated, status_code=201)
async def create_site(
    data: SiteCreate,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    """Register a website. The two keys are returned once: give them to that website's config."""
    site_key = secrets.token_urlsafe(32)
    hub_key = secrets.token_urlsafe(32)
    site = Site(
        name=data.name,
        base_url=data.base_url,
        verify_url=data.verify_url,
        site_key_hash=hash_secret(site_key),
        hub_key=hub_key,
    )
    session.add(site)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A site with this name already exists.")
    return SiteCreated(
        id=site.id,
        name=site.name,
        base_url=site.base_url,
        verify_url=site.verify_url,
        site_key=site_key,
        hub_key=hub_key,
    )


@router.delete("/sites/{site_id}", status_code=204)
async def delete_site(
    site_id: UUID,
    admin: User = Depends(current_superuser),
    session: AsyncSession = Depends(get_async_session),
):
    site = await _get_site(session, site_id)
    await session.delete(site)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- user: catalog and links


@router.get("/sites", response_model=list[SiteRead])
async def list_sites(
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Every registered site, with whether this user has linked it."""
    sites = (await session.execute(select(Site).order_by(Site.name))).scalars().all()
    linked = set(
        (await session.execute(select(SiteLink.site_id).where(SiteLink.user_id == user.id)))
        .scalars()
        .all()
    )
    return [
        SiteRead(id=s.id, name=s.name, base_url=s.base_url, linked=s.id in linked) for s in sites
    ]


@router.post("/sites/{site_id}/link", response_model=LinkResponse)
async def link_site(
    site_id: UUID,
    data: LinkRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
    client: httpx.AsyncClient = Depends(get_site_client),
):
    """Check the user's credentials with the site. If correct, save the link (never the password)."""
    site = await _get_site(session, site_id)

    if not link_limiter.allow((user.id, site.id)) or not username_limiter.allow(
        (site.id, data.username.lower())
    ):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a minute.")

    try:
        resp = await client.post(
            site.verify_url,
            json={"username": data.username, "password": data.password.get_secret_value()},
            headers={"X-Hub-Key": site.hub_key},
        )
    except httpx.HTTPError:
        logger.warning("Hub: verify request to site '%s' failed", site.name)
        raise HTTPException(status_code=502, detail="Site is unreachable.")

    if resp.status_code in (401, 403):
        logger.error("Hub: site '%s' rejected the hub key", site.name)
        raise HTTPException(status_code=502, detail="Site rejected the hub. Contact an administrator.")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Unexpected response from site.")

    try:
        body = resp.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or "ok" not in body:
        raise HTTPException(status_code=502, detail="Unexpected response from site.")
    if body["ok"] is not True:
        raise HTTPException(status_code=400, detail="Invalid credentials.")
    remote_user_id = body.get("user_id")
    if remote_user_id is None or str(remote_user_id) == "":
        raise HTTPException(status_code=502, detail="Unexpected response from site.")

    remote_user_id = str(remote_user_id)
    taken = await session.execute(
        select(SiteLink.id).where(
            SiteLink.site_id == site.id,
            SiteLink.remote_user_id == remote_user_id,
            SiteLink.user_id != user.id,
        )
    )
    if taken.first() is not None:
        raise HTTPException(
            status_code=409, detail="This account is already linked to another hub user."
        )

    link = await _get_link(session, user.id, site.id)
    if link is None:
        session.add(SiteLink(user_id=user.id, site_id=site.id, remote_user_id=remote_user_id))
    else:
        link.remote_user_id = remote_user_id
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Link already exists.")
    return LinkResponse(linked=True, site_id=site.id)


@router.delete("/sites/{site_id}/link", status_code=204)
async def unlink_site(
    site_id: UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    link = await _get_link(session, user.id, site_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Site is not linked.")
    await session.delete(link)
    await session.commit()
    return Response(status_code=204)


@router.post("/sites/{site_id}/open", response_model=OpenResponse)
async def open_site(
    site_id: UUID,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Return the URL that starts a login on the site. The frontend sets window.location to it.

    The site's /hub/start saves a random state in a cookie and sends the browser to the hub
    frontend's /authorize page, which calls POST /hub/authorize. Opening from the hub and
    clicking "Log in with Hub" on the site therefore run the same flow, and the state check
    on the site stops an attacker from logging a victim in to the attacker's account.
    """
    site = await _get_site(session, site_id)
    if await _get_link(session, user.id, site.id) is None:
        raise HTTPException(status_code=404, detail="Link this site first.")
    return OpenResponse(redirect_url=f"{site.base_url}/hub/start")


@router.post("/authorize", response_model=OpenResponse)
async def authorize(
    data: AuthorizeRequest,
    user: User = Depends(current_active_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Called by the hub frontend's /authorize page for the logged-in user.

    Returns the site's /hub/sso URL with a one-time code. The URL always points to the
    site's registered base_url, never to an address from the request, so it cannot be
    used to send codes elsewhere. 404 means the user must link the site first.
    """
    site = await _get_site(session, data.site_id)
    link = await _get_link(session, user.id, site.id)
    if link is None:
        raise HTTPException(status_code=404, detail="Link this site first.")

    code = secrets.token_urlsafe(32)
    now = utcnow()
    await session.execute(delete(SsoCode).where(SsoCode.expires_at < now - timedelta(hours=1)))
    session.add(
        SsoCode(
            code_hash=hash_secret(code),
            site_id=site.id,
            remote_user_id=link.remote_user_id,
            expires_at=now + CODE_TTL,
        )
    )
    await session.commit()
    query = urlencode({"code": code, "state": data.state})
    return OpenResponse(redirect_url=f"{site.base_url}/hub/sso?{query}")


# ---------------------------------------------------------------- site -> hub


async def get_calling_site(
    x_site_key: str = Header(None),
    session: AsyncSession = Depends(get_async_session),
) -> Site:
    if not x_site_key:
        raise HTTPException(status_code=401, detail="X-Site-Key header missing.")
    result = await session.execute(select(Site).where(Site.site_key_hash == hash_secret(x_site_key)))
    site = result.scalars().first()
    if site is None:
        raise HTTPException(status_code=401, detail="Unauthorized site.")
    return site


@router.post("/sso/redeem", response_model=RedeemResponse)
async def redeem_code(
    data: RedeemRequest,
    site: Site = Depends(get_calling_site),
    session: AsyncSession = Depends(get_async_session),
):
    """Called by a site (server to server). A code works once, for the site it was issued to, within 60 s."""
    now = utcnow()
    # One atomic statement: two concurrent redeems cannot both succeed.
    result = await session.execute(
        update(SsoCode)
        .where(
            SsoCode.code_hash == hash_secret(data.code),
            SsoCode.site_id == site.id,
            SsoCode.used_at.is_(None),
            SsoCode.expires_at > now,
        )
        .values(used_at=now)
        .returning(SsoCode.remote_user_id)
        .execution_options(synchronize_session=False)
    )
    row = result.first()
    await session.commit()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code.")
    return RedeemResponse(user_id=row[0])
