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
            hub_url=os.environ["HUB_URL"],                    # hub API, e.g. https://api.hub.example.uz
            hub_frontend_url=os.environ["HUB_FRONTEND_URL"],  # hub web app, e.g. https://hub.example.uz
            site_id=os.environ["HUB_SITE_ID"],                # returned by POST /hub/sites
            hub_key=os.environ["HUB_KEY"],                    # returned by POST /hub/sites
            site_key=os.environ["HUB_SITE_KEY"],              # returned by POST /hub/sites
            authenticate=authenticate,
            start_session=start_session,
        )
    )

Put a "Log in with Hub" link on the site's login page that points to /hub/start.

Login flow (the same whether it starts on the hub or on the site):
  1. Browser opens  SITE/hub/start  -> the site saves a random state in a cookie and
     redirects to  HUB_FRONTEND/authorize?site_id=...&state=...
  2. The hub frontend logs the user in if needed, links the site if needed, then calls
     POST HUB/hub/authorize {site_id, state} and sets window.location to redirect_url.
  3. Browser opens  SITE/hub/sso?code=...&state=...  -> the site checks the state against
     its cookie, redeems the code with the hub, and starts the session.

The state check is what stops login CSRF: without it, an attacker could send a victim a
link with the attacker's own code and the victim would be logged in as the attacker.

Also restrict POST /hub/verify to the hub's IP at the reverse proxy: it checks passwords,
so it must not be reachable by everyone.
"""
import hmac
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

STATE_COOKIE = "hub_state"
STATE_MAX_AGE_SECONDS = 600


def build_hub_router(
    *,
    hub_url: str,
    hub_frontend_url: str,
    site_id: str,
    hub_key: str,
    site_key: str,
    authenticate: Callable[[str, str], Awaitable[str | None]],
    start_session: Callable[[Request, str], Awaitable[Response]],
    secure_cookies: bool = True,
    http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> APIRouter:
    hub_url = hub_url.rstrip("/")
    hub_frontend_url = hub_frontend_url.rstrip("/")
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

    @router.get("/start")
    async def start():
        """Start a hub login: remember a random state in this browser, then go to the hub."""
        state = secrets.token_urlsafe(32)
        query = urlencode({"site_id": site_id, "state": state})
        response = RedirectResponse(f"{hub_frontend_url}/authorize?{query}", status_code=303)
        response.set_cookie(
            STATE_COOKIE,
            state,
            max_age=STATE_MAX_AGE_SECONDS,
            path="/hub",
            httponly=True,
            secure=secure_cookies,
            # Lax: the cookie is sent when the hub redirects the browser back here.
            samesite="lax",
        )
        return response

    @router.get("/sso")
    async def sso(
        code: str,
        state: str,
        request: Request,
        hub_state: str | None = Cookie(None),
    ):
        """The browser lands here from the hub. Check the state, redeem the code, log the user in."""
        if not hub_state or not hmac.compare_digest(hub_state.encode(), state.encode()):
            raise HTTPException(
                status_code=400, detail="Login was not started in this browser. Try again."
            )
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

        response = await start_session(request, resp.json()["user_id"])
        response.delete_cookie(STATE_COOKIE, path="/hub")
        return response

    return router
