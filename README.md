# face_chat

An AI chat API. Users log in through **Authentik**, which is the only place passwords
are kept. Every website you run logs in through the same Authentik, so one account works
everywhere.

```
            ┌──────────────┐   tokens   ┌──────────────────┐
 browser ──▶│  Authentik   │──────────▶ │ face_chat API    │  checks the token (JWKS)
            │  :9000       │            │ :8000            │
            │ login, 2FA,  │   login    ├──────────────────┤
            │ users        │──────────▶ │ website A, B, …  │  "Log in with Authentik"
            └──────────────┘            └──────────────────┘
```

## 1. Start Authentik (needs Docker)

```bash
cd authentik
cp .env.example .env        # fill in the secrets (see comments in the file)
docker compose up -d
```

Open http://localhost:9000/if/flow/initial-setup/ to set the admin password if you did not
set `AUTHENTIK_BOOTSTRAP_PASSWORD`. The admin panel is at http://localhost:9000/if/admin/.

`authentik/blueprints/face-chat.yaml` creates the **face_chat** and **Sample website**
applications automatically. Add users under *Directory → Users*.

## 2. Run the API

```bash
pip install -r requirements.txt
cp .env.example .env        # set OLLAMA_API_KEY
alembic upgrade head        # only for an existing database; a new one is created on start
python main.py
```

Open http://localhost:8000/docs, click **Authorize**, and log in with an Authentik user.

The API never sees passwords. Each request carries an Authentik access token
(`Authorization: Bearer …`), checked in `src/auth.py`. The first time someone calls the
API, a local user is created for them. API keys (`/apikeys/generate`) still work for
`/chat/*`.

## 3. Run the sample website

`examples/sample_site/` is a website that logs in through Authentik. Copy it into your sites.

```bash
cd examples/sample_site
pip install -r requirements.txt
cp .env.example .env        # OIDC_CLIENT_SECRET = SAMPLE_SITE_CLIENT_SECRET from authentik/.env
python app.py add-user alice    # optional: an "old" local account, to try linking
uvicorn app:app --port 8001
```

The first time someone logs in through Authentik, the site asks whether they already
have an account there. They enter its old password once to link it, or create a new
account. After that, Authentik is the only login.

## Adding another website

1. In `authentik/blueprints/face-chat.yaml`, copy the `sample-site` provider and
   application entries, then change the name, `client_id`, redirect URL, and secret.
2. In the website, use the code from `examples/sample_site/app.py` (`/auth/login`,
   `/auth/callback`, `/link`, `/logout`).
