from __future__ import annotations

import logging

from argon2 import PasswordHasher
from sqlalchemy import inspect, select

from app.config import Settings
from app.db import get_engine, session_scope
from app.models import Base, User

log = logging.getLogger(__name__)
_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, candidate: str) -> bool:
    try:
        _hasher.verify(stored_hash, candidate)
        return True
    except Exception:
        return False


_SCHEMA_LOCK_KEY = 4242117309  # arbitrary stable bigint for pg_advisory_lock


def ensure_schema() -> bool:
    engine = get_engine()
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
        try:
            inspector = inspect(conn)
            if inspector.has_table("users"):
                return False
            log.info("Bootstrapping schema — running Base.metadata.create_all()")
            Base.metadata.create_all(bind=conn)
            return True
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEMA_LOCK_KEY})


def ensure_bootstrap_admin(settings: Settings) -> None:
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    with session_scope() as session:
        existing = session.scalar(
            select(User).where(User.email == settings.bootstrap_admin_email)
        )
        if existing is not None:
            return
        log.info(
            "Creating bootstrap admin %s from REBOOTER_BOOTSTRAP_ADMIN_* env vars",
            settings.bootstrap_admin_email,
        )
        admin = User(
            email=settings.bootstrap_admin_email,
            password_hash=hash_password(settings.bootstrap_admin_password),
            display_name=settings.bootstrap_admin_email.split("@", 1)[0],
            is_admin=True,
            is_active=True,
        )
        session.add(admin)


def run_startup_bootstrap(settings: Settings) -> None:
    ensure_schema()
    ensure_bootstrap_admin(settings)
