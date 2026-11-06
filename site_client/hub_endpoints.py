"""Drop-in hub endpoints for a FastAPI website registered in the hub.

Copy this file into the website and mount the router:

    from hub_endpoints import build_hub_router

    async def authenticate(username: str, password: str) -> str | None:
        # Use the site's EXISTING login check, including its own rate limiting.
        # Return the local user's id, or None if the credentials are wrong.
        ...

    async def start_session(request: Request, user_id: str) -> Response:
        # Do what the site's normal login does after success (set the session cookie
        # or issue its JWT) and return the response, usually a redirect to "/".
        ...

    app.include_router(
        build_hub_router(
            hub_url=os.environ["HUB_URL"],        # e.g. https://hub.example.uz
            hub_key=os.environ["HUB_KEY"],        # returned by POST /hub/sites
            site_key=os.environ["HUB_SITE_KEY"],  # returned by POST /hub/sites
            authenticate=authenticate,
            start_session=start_session,
        )
    )

Also restrict POST /hub/verify to the hub's IP at the reverse proxy: it checks passwords,
so it must not be reachable by everyone.
"""
import hmac
from collections.abc import Awaitable, Callable

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field


def build_hub_router(
    *,
    hub_url: str,
    hub_key: str,
    site_key: str,
    authenticate: Callable[[str, str], Awaitable[str | None]],
    start_session: Callable[[Request, str], Awaitable[Response]],
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> APIRouter:
    hub_url = hub_url.rstrip("/")
    make_client = http_client_factory or (
        lambda: httpx.AsyncClient(timeout=10, follow_redirects=False)
    )
    router = APIRouter(prefix="/hub", tags=["Hub"])

    class VerifyRequest(BaseModel):
        username: str = Field(min_length=1, max_length=255)
        password: str = Field(min_length=1, max_length=256)

    @router.post("/verify")
    async def verify(data: VerifyRequest, x_hub_key: str = Header(None)):
        """Called by the hub when a user links this site. Wrong password is a 200 with ok=false."""
        if not x_hub_key or not hmac.compare_digest(x_hub_key.encode(), hub_key.encode()):
            raise HTTPException(status_code=401, detail="Invalid hub key.")
        user_id = await authenticate(data.username, data.password)
        if user_id is None:
            return {"ok": False}
        return {"ok": True, "user_id": str(user_id)}

    @router.get("/sso")
    async def sso(code: str, request: Request):
        """The browser lands here from the hub. Redeem the one-time code, then log the user in."""
        try:
            async with make_client() as client:
                resp = await client.post(
                    f"{hub_url}/hub/sso/redeem",
                    json={"code": code},
                    headers={"X-Site-Key": site_key},
                )
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="Hub is unreachable.")
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid or expired code.")
        return await start_session(request, resp.json()["user_id"])

    return router
