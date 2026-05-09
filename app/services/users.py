from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import User
from app.models.users import ALL_ROLES, ROLE_SUPER_ADMIN
from app.services.bootstrap import hash_password


class UserError(ValueError):
    pass


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def serialize_user(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": u.is_active,
        "is_super_admin": u.is_super_admin,
        "created_at": _iso(u.created_at),
        "last_login_at": _iso(u.last_login_at),
    }


def list_users() -> list[dict]:
    with session_scope() as session:
        rows = list(session.scalars(select(User).order_by(User.created_at.desc())))
        return [serialize_user(u) for u in rows]


def get_user(user_id: str) -> dict | None:
    with session_scope() as session:
        u = session.get(User, user_id)
        return serialize_user(u) if u else None


def create_user(
    email: str,
    password: str,
    display_name: str,
    role: str,
) -> dict:
    if role not in ALL_ROLES:
        raise UserError(f"role must be one of {ALL_ROLES}")
    email = email.lower().strip()
    with session_scope() as session:
        existing = session.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise UserError(f"a user with email '{email}' already exists")
        u = User(
            email=email,
            password_hash=hash_password(password),
            display_name=display_name or email.split("@", 1)[0],
            role=role,
            is_admin=role != "viewer",
            is_super_admin=role == ROLE_SUPER_ADMIN,
            is_active=True,
        )
        session.add(u)
        session.flush()
        return serialize_user(u)


def update_user_role(user_id: str, role: str) -> dict | None:
    if role not in ALL_ROLES:
        raise UserError(f"role must be one of {ALL_ROLES}")
    with session_scope() as session:
        u = session.get(User, user_id)
        if u is None:
            return None
        u.role = role
        u.is_super_admin = role == ROLE_SUPER_ADMIN
        u.is_admin = role != "viewer"
        u.updated_at = datetime.now(timezone.utc)
        return serialize_user(u)


def deactivate_user(user_id: str) -> bool:
    with session_scope() as session:
        u = session.get(User, user_id)
        if u is None:
            return False
        u.is_active = False
        u.tokens_valid_after = datetime.now(timezone.utc)
        u.updated_at = datetime.now(timezone.utc)
        return True


def revoke_all_tokens(user_id: str) -> bool:
    with session_scope() as session:
        u = session.get(User, user_id)
        if u is None:
            return False
        u.tokens_valid_after = datetime.now(timezone.utc)
        u.updated_at = datetime.now(timezone.utc)
        return True


def update_user_display_name(user_id: str, display_name: str) -> dict | None:
    display_name = (display_name or "").strip()
    if not display_name:
        raise UserError("display_name is required")
    with session_scope() as session:
        u = session.get(User, user_id)
        if u is None:
            return None
        u.display_name = display_name
        u.updated_at = datetime.now(timezone.utc)
        return serialize_user(u)
