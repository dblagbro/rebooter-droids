from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import mapped_column
from ulid import ULID


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{ULID()}"


def ts_column(default_now: bool = True, nullable: bool = False):
    kwargs = dict(nullable=nullable)
    if default_now:
        kwargs["default"] = utcnow
    return mapped_column(DateTime(timezone=True), **kwargs)
