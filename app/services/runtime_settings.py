"""Runtime settings service — v0.4.25.

Tiny key/value abstraction with env-var fallback. Used for any
config the operator should be able to change without recreating
the container — currently SMTP creds (v0.4.25); v0.4.26 extends to
network + system settings.

Pattern:
    runtime_settings.get('smtp.host', env_var='REBOOTER_SMTP_HOST')

Reads DB row first; falls back to the env-var if not set; falls
back to a hard default if neither. So a fresh deployment keeps
picking up env-var defaults until the operator edits via the UI.

Writes always audit-log via the caller; this service just stores.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models import RuntimeSetting


_SENTINEL = object()


def get(name: str, *, env_var: str | None = None, default: Any = None) -> Any:
    """Look up a setting. DB → env-var → default."""
    with session_scope() as session:
        row = session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == name)
        )
        if row is not None:
            v = (row.value or {}).get("v", _SENTINEL)
            if v is not _SENTINEL:
                return v
    if env_var:
        env = os.environ.get(env_var)
        if env is not None:
            return env
    return default


def has_db_value(name: str) -> bool:
    """Whether this setting has a DB-side override (regardless of
    env-var fallback)."""
    with session_scope() as session:
        row = session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == name)
        )
        return row is not None


def set_(name: str, value: Any, *, user_id: str | None = None) -> None:
    """Upsert. Use `delete()` to remove the override and revert
    to env-var fallback. NOTE: empty string is *not* a delete —
    operators can explicitly want a setting cleared (e.g.
    blank `helo`). Use `delete()` to fall back to env."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == name)
        )
        if row is None:
            row = RuntimeSetting(name=name, value={"v": value}, updated_at=now)
        else:
            row.value = {"v": value}
            row.updated_at = now
        row.updated_by_user_id = user_id
        session.add(row)
        session.flush()


def delete(name: str) -> bool:
    """Drop the DB row → reverts to env-var fallback."""
    with session_scope() as session:
        row = session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == name)
        )
        if row is None:
            return False
        session.delete(row)
        session.flush()
        return True


def list_keys() -> list[dict]:
    """Operator-side audit view: enumerate all DB rows + their
    metadata. Never returns secret values verbatim — see the
    blueprint for masked rendering."""
    with session_scope() as session:
        rows = list(session.scalars(select(RuntimeSetting)))
        return [
            {
                "name": r.name,
                "updated_at": r.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "updated_by_user_id": r.updated_by_user_id,
            }
            for r in rows
        ]


# ── known SMTP keys + a typed helper for the email service ────────


SMTP_KEYS = (
    ("smtp.host", "REBOOTER_SMTP_HOST"),
    ("smtp.port", "REBOOTER_SMTP_PORT"),
    ("smtp.user", "REBOOTER_SMTP_USER"),
    ("smtp.password", "REBOOTER_SMTP_PASSWORD"),
    ("smtp.from", "REBOOTER_SMTP_FROM"),
    ("smtp.helo", "REBOOTER_SMTP_HELO"),
)


def smtp_config() -> dict:
    """Returns the live SMTP config the email service should use.
    Each value comes from `runtime_settings` if a DB override
    exists, else from the env var, else from a sensible default
    (port → 587, others → empty string)."""
    out = {}
    for name, env in SMTP_KEYS:
        out[name] = get(name, env_var=env, default="")
    # Coerce port to int
    try:
        out["smtp.port"] = int(out["smtp.port"]) if out["smtp.port"] else 587
    except (TypeError, ValueError):
        out["smtp.port"] = 587
    return out
