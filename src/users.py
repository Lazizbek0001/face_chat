import os
import uuid

from dotenv import load_dotenv
from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy

from src.db import SQLAlchemyUserDatabase, User, get_user_db

load_dotenv()

SECRET = os.getenv("SECRET")
if not SECRET or len(SECRET) < 32:
    # Every login token is signed with this. A missing or short secret lets anyone forge tokens.
    raise RuntimeError("Set SECRET in .env to a random string of at least 32 characters.")


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user, request=None):
        print(f"User {user.id} has registered")

    async def on_after_forgot_password(self, user, token, request=None):
        print(f"User {user.id} has forgot password. Reset token: {token}")

    async def on_after_request_verify(self, user, token, request=None):
        print(f"Verfication requested for user {user.id}, Verification token: {token}")


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy():
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, auth_backends=[auth_backend])

current_active_user = fastapi_users.current_user(active=True)
current_superuser = fastapi_users.current_user(active=True, superuser=True)
