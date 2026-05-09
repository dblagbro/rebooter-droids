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
    """Always run Base.metadata.create_all() under an advisory lock.

    create_all() is idempotent — it issues CREATE TABLE IF NOT EXISTS for each
    table and a no-op for indexes that already exist. v0.2.5: this used to
    short-circuit if `users` existed, which meant new tables added in a
    later release silently never got created on existing databases. Run
    every startup; it's cheap.

    create_all() does NOT issue ALTER TABLE for new columns on tables that
    already exist — _ensure_columns() handles those one-by-one with
    ADD COLUMN IF NOT EXISTS.
    """
    engine = get_engine()
    with engine.begin() as conn:
        from sqlalchemy import text

        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _SCHEMA_LOCK_KEY})
        try:
            inspector = inspect(conn)
            fresh = not inspector.has_table("users")
            if fresh:
                log.info("Bootstrapping schema — running Base.metadata.create_all()")
            Base.metadata.create_all(bind=conn)
            _ensure_columns(conn)
            return fresh
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _SCHEMA_LOCK_KEY})


# Idempotent ADD COLUMN steps for columns added after a table's first ship.
# Each entry is (table, column_name, column_ddl). Postgres only.
_PENDING_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("devices", "is_qa_fixture", "BOOLEAN NOT NULL DEFAULT FALSE"),  # v0.2.8
)


def _ensure_columns(conn) -> None:
    from sqlalchemy import text

    for table, column, ddl in _PENDING_COLUMNS:
        conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
        )


def ensure_bootstrap_admin(settings: Settings) -> None:
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    with session_scope() as session:
        existing = session.scalar(
            select(User).where(User.email == settings.bootstrap_admin_email)
        )
        if existing is not None:
            # Always reconcile the bootstrap admin's password and elevation
            # to whatever is currently in the env vars. This is what makes
            # `Super*120120` for dblagbro@gmail.com authoritative across
            # rebuilds without requiring a manual UPDATE.
            existing.password_hash = hash_password(settings.bootstrap_admin_password)
            existing.is_admin = True
            existing.is_active = True
            existing.is_super_admin = True
            session.add(existing)
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
            is_super_admin=True,
        )
        session.add(admin)


def run_startup_bootstrap(settings: Settings) -> None:
    ensure_schema()
    ensure_bootstrap_admin(settings)
