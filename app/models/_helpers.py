from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import mapped_column
from ulid import ULID


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_aware(dt: datetime | None) -> datetime | None:
    """Coerce a possibly-naive datetime to UTC-aware (no-op if already
    aware, or None). Postgres returns the `TIMESTAMPTZ` columns aware;
    SQLite (the in-process test backend) returns them naive. Call this
    before comparing a DB-read datetime against a tz-aware `now` so the
    comparison can't raise `TypeError`. A no-op on a real deployment."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def ts_column(default_now: bool = True, nullable: bool = False):
    kwargs = dict(nullable=nullable)
    if default_now:
        kwargs["default"] = utcnow
    return mapped_column(DateTime(timezone=True), **kwargs)
