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
    """
    Accept either a full email address or a bare username (the local-part
    of the email). Bare-username login is unambiguous as long as no two
    users share the same local-part — when there's a clash, the user must
    use the full email.
    """
    identifier = email.lower().strip()
    with session_scope() as session:
        user = session.scalar(select(User).where(User.email == identifier))
        if user is None and "@" not in identifier:
            matches = list(
                session.scalars(
                    select(User).where(User.email.like(f"{identifier}@%"))
                )
            )
            if len(matches) == 1:
                user = matches[0]
        if user is None or not user.is_active:
            return None
        if not verify_password(user.password_hash, password):
            return None
        user.last_login_at = datetime.now(timezone.utc)
        session.add(user)
        session.flush()
        session.expunge(user)
        return user


def _issue_token(
    settings: Settings,
    user_id: str,
    kind: str,
    ttl_seconds: int,
) -> str:
    """Issue a JWT and (v0.2.10, shadow-mode) record a server-side session
    row for it. Adding `jti` is the contract change that lets a future
    enforce path correlate the token back to its row."""
    from app.services import sessions as sessions_service

    now = datetime.now(timezone.utc)
    jti = sessions_service.new_jti()
    payload = {
        "sub": user_id,
        "kind": kind,
        "aud": JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "jti": jti,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=JWT_ALG)
    # Best-effort; never raise from the auth path.
    sessions_service.record(
        user_id=user_id,
        kind=(
            sessions_service.KIND_ACCESS
            if kind == "access"
            else sessions_service.KIND_REFRESH
        ),
        jti=jti,
        ttl_seconds=ttl_seconds,
    )
    return token


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
