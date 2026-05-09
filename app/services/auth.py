from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select

from app.config import Settings
from app.db import session_scope
from app.models import User
from app.services.bootstrap import verify_password

JWT_ALG = "HS256"
JWT_AUDIENCE = "rebooter-droids"
ACCESS_TOKEN_TTL_SECONDS = 60 * 60 * 8
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 14


def authenticate(email: str, password: str) -> User | None:
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == email.lower().strip()))
        if user is None or not user.is_active:
            return None
        if not verify_password(user.password_hash, password):
            return None
        user.last_login_at = datetime.now(timezone.utc)
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def _issue_token(settings: Settings, user_id: str, kind: str, ttl_seconds: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "kind": kind,
        "aud": JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALG)


def issue_access_token(settings: Settings, user_id: str) -> str:
    return _issue_token(settings, user_id, "access", ACCESS_TOKEN_TTL_SECONDS)


def issue_refresh_token(settings: Settings, user_id: str) -> str:
    return _issue_token(settings, user_id, "refresh", REFRESH_TOKEN_TTL_SECONDS)


def decode_token(settings: Settings, token: str, expected_kind: str) -> dict:
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[JWT_ALG],
        audience=JWT_AUDIENCE,
    )
    if payload.get("kind") != expected_kind:
        raise jwt.InvalidTokenError(f"expected {expected_kind} token")
    return payload


def load_user(user_id: str) -> User | None:
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is not None:
            session.expunge(user)
        return user
