"""A sample website that logs users in through Authentik.

Copy the parts you need into each of your websites. What it shows:

  * "Log in with Authentik" (OpenID Connect, authorization code flow with PKCE).
    Authlib handles the state and nonce checks that stop login CSRF and replayed tokens.
  * Moving existing users over: the first time someone logs in through Authentik, they
    either link their old account on this site (by entering its password ONCE) or
    create a new one. After that, Authentik is the only login.
  * Logout from this site and from Authentik.

Run:
    pip install -r requirements.txt
    cp .env.example .env              # fill in the values
    python app.py add-user alice      # optional: an "old" account to try linking
    uvicorn app:app --port 8001
"""
import getpass
import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
import time
from html import escape
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth, OAuthError
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

SITE_URL = os.getenv("SITE_URL", "http://localhost:8001").rstrip("/")
OIDC_ISSUER = os.getenv("OIDC_ISSUER", "http://localhost:9000/application/o/sample-site/")
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "sample-site")
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
DB_PATH = os.getenv("SITE_DB_PATH", "site.db")

if not OIDC_CLIENT_SECRET or len(SESSION_SECRET) < 32:
    raise RuntimeError("Set OIDC_CLIENT_SECRET, and SESSION_SECRET (32+ characters), in .env.")

PENDING_SECONDS = 600
MAX_LINK_ATTEMPTS = 5

# ---------------------------------------------------------------- local users


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                email TEXT,
                password_hash TEXT,          -- old local password; NULL for new users
                oidc_sub TEXT UNIQUE         -- Authentik's id for this person, once linked
            )"""
        )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    _, salt_hex, digest_hex = stored.split("$")
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


def add_user(username: str, password: str, email: str | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (username, email, hash_password(password)),
        )


# ---------------------------------------------------------------- app

oauth = OAuth()
oauth.register(
    "authentik",
    client_id=OIDC_CLIENT_ID,
    client_secret=OIDC_CLIENT_SECRET,
    server_metadata_url=f"{OIDC_ISSUER.rstrip('/')}/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile", "code_challenge_method": "S256"},
)

app = FastAPI(title="Sample website")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="site_session",
    max_age=8 * 3600,
    same_site="lax",  # lax so the cookie comes back on the redirect from Authentik
    https_only=SITE_URL.startswith("https://"),
)
init_db()


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;max-width:460px;margin:48px auto;padding:0 16px;line-height:1.5}}
input,button{{font:inherit;padding:8px 10px;margin:4px 0;width:100%;box-sizing:border-box}}
button{{cursor:pointer}} .box{{border:1px solid #ccc;border-radius:8px;padding:16px;margin:16px 0}}
.err{{color:#b00020}}</style></head><body><h1>{escape(title)}</h1>{body}</body></html>""",
        status_code=status,
    )


def log_in(request: Request, user: sqlite3.Row, id_token: str | None) -> RedirectResponse:
    request.session.clear()  # new session on login
    request.session["user_id"] = user["id"]
    if id_token:
        request.session["id_token"] = id_token
    return RedirectResponse("/", status_code=303)


def get_pending(request: Request) -> dict | None:
    pending = request.session.get("pending")
    if not pending or pending["expires"] < time.time():
        request.session.pop("pending", None)
        return None
    return pending


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user_id = request.session.get("user_id")
    if user_id:
        with db() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user:
            return page(
                "Sample website",
                f"<p>Logged in as <b>{escape(user['username'])}</b>.</p>"
                '<form method="post" action="/logout"><button>Log out</button></form>',
            )
    return page(
        "Sample website",
        '<p>You are not logged in.</p><p><a href="/auth/login"><button>Log in with Authentik</button></a></p>',
    )


@app.get("/auth/login")
async def login(request: Request):
    return await oauth.authentik.authorize_redirect(request, f"{SITE_URL}/auth/callback")


@app.get("/auth/callback")
async def callback(request: Request):
    try:
        # Checks state (login CSRF), exchanges the code, and verifies the ID token
        # (signature, issuer, audience, expiry, nonce).
        token = await oauth.authentik.authorize_access_token(request)
    except OAuthError as e:
        return page("Login failed", f'<p class="err">{escape(e.description or e.error)}</p><a href="/">Back</a>', 400)

    info = token["userinfo"]
    sub = str(info["sub"])
    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE oidc_sub = ?", (sub,)).fetchone()
    if user:
        return log_in(request, user, token.get("id_token"))

    # First login through Authentik: ask whether they already have an account here.
    request.session["pending"] = {
        "sub": sub,
        "email": info.get("email"),
        "username": info.get("preferred_username") or (info.get("email") or "user").split("@")[0],
        "id_token": token.get("id_token"),
        "expires": time.time() + PENDING_SECONDS,
        "attempts": 0,
    }
    return RedirectResponse("/link", status_code=303)


def link_page(pending: dict, error: str = "", status: int = 200) -> HTMLResponse:
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    return page(
        "One more step",
        f"<p>You logged in as <b>{escape(pending.get('email') or pending['username'])}</b>. "
        "This is your first time on this website with that login.</p>"
        f'<div class="box"><b>I already have an account on this website</b>{err}'
        '<form method="post" action="/link">'
        '<input name="username" placeholder="Username on this website" required autocomplete="username">'
        '<input name="password" type="password" placeholder="Password on this website" required autocomplete="current-password">'
        "<button>Link my account</button></form>"
        "<small>You only do this once. After that, you log in with Authentik only.</small></div>"
        '<div class="box"><b>I am new here</b>'
        '<form method="post" action="/link/new"><button>Create a new account</button></form></div>',
        status,
    )


@app.get("/link", response_class=HTMLResponse)
async def link_form(request: Request):
    pending = get_pending(request)
    if not pending:
        return RedirectResponse("/", status_code=303)
    return link_page(pending)


@app.post("/link", response_class=HTMLResponse)
async def link_existing(request: Request, username: str = Form(...), password: str = Form(...)):
    pending = get_pending(request)
    if not pending:
        return RedirectResponse("/", status_code=303)
    if pending["attempts"] >= MAX_LINK_ATTEMPTS:
        request.session.pop("pending", None)
        return page("Too many attempts", '<p class="err">Too many wrong passwords. Log in again.</p><a href="/">Back</a>', 429)

    with db() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not user or not check_password(password, user["password_hash"]):
            pending["attempts"] += 1
            request.session["pending"] = pending
            return link_page(pending, "Wrong username or password.", 400)
        if user["oidc_sub"] and user["oidc_sub"] != pending["sub"]:
            return link_page(pending, "That account is already linked to a different Authentik login.", 409)
        conn.execute("UPDATE users SET oidc_sub = ? WHERE id = ?", (pending["sub"], user["id"]))
    return log_in(request, user, pending["id_token"])


@app.post("/link/new", response_class=HTMLResponse)
async def link_new(request: Request):
    pending = get_pending(request)
    if not pending:
        return RedirectResponse("/", status_code=303)
    with db() as conn:
        base = pending["username"]
        username, n = base, 1
        while conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            n += 1
            username = f"{base}{n}"
        conn.execute(
            "INSERT INTO users (username, email, oidc_sub) VALUES (?, ?, ?)",
            (username, pending["email"], pending["sub"]),
        )
        user = conn.execute("SELECT * FROM users WHERE oidc_sub = ?", (pending["sub"],)).fetchone()
    return log_in(request, user, pending["id_token"])


@app.post("/logout")
async def logout(request: Request):
    id_token = request.session.get("id_token")
    request.session.clear()
    # Also end the Authentik session, so the next "Log in" asks for the password again.
    metadata = await oauth.authentik.load_server_metadata()
    end_session = metadata.get("end_session_endpoint")
    if not end_session:
        return RedirectResponse("/", status_code=303)
    query = {"post_logout_redirect_uri": f"{SITE_URL}/", "client_id": OIDC_CLIENT_ID}
    if id_token:
        query["id_token_hint"] = id_token
    return RedirectResponse(f"{end_session}?{urlencode(query)}", status_code=303)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "add-user":
        init_db()
        add_user(sys.argv[2], getpass.getpass(f"Password for {sys.argv[2]}: "))
        print(f"Added {sys.argv[2]}.")
    else:
        print("Usage: python app.py add-user <username>")
        print("Run the site with: uvicorn app:app --port 8001")
