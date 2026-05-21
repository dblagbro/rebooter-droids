from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


# ── Connection-pool sizing ─────────────────────────────────────────────
#
# load-degradation fix (2026-05-21): the engine used SQLAlchemy's default
# QueuePool (pool_size=5 + max_overflow=10 = 15 connections). The hub's
# concurrency profile is well above that: gunicorn runs `gthread` with 8
# threads (gunicorn.conf.py), and the in-process APScheduler runs 9 jobs
# — several of which (sync_replicator every 3s, webhook_delivery every
# 15s, the watchdog/schedule/rollup ticks) open their own DB sessions.
# An 8-thread request burst that overlaps a couple of scheduler ticks can
# exhaust the default pool; callers then block for `pool_timeout` and a
# slow query cascades into a hub-wide stall.
#
# These are sized explicitly so a slow path degrades gracefully instead
# of deadlocking the pool: `pool_size` covers every gunicorn thread plus
# the scheduler with headroom; `max_overflow` absorbs short bursts;
# `pool_timeout` fails a checkout in bounded time (a fast 5xx beats an
# unbounded hang). All three are env-overridable for ops tuning.
# `pool_pre_ping=True` is kept — it recycles a connection the database
# dropped underneath the pool. The values are Postgres-oriented;
# SQLite's default in-memory/file connection ignores pool sizing
# harmlessly (the unit-test backend is single-connection).
_DEFAULT_POOL_SIZE = 12       # 8 gunicorn threads + scheduler headroom
_DEFAULT_MAX_OVERFLOW = 8     # short-burst absorption above pool_size
_DEFAULT_POOL_TIMEOUT = 30    # seconds to wait for a free connection


def _pool_kwargs(database_url: str) -> dict:
    """QueuePool sizing kwargs for `create_engine`.

    Returns an empty dict for SQLite, whose default connection pool does
    not take these arguments meaningfully (and `:memory:` uses a
    single-connection pool) — keeps the unit-test backend unaffected.
    """
    if database_url.startswith("sqlite"):
        return {}
    return {
        "pool_size": int(
            os.environ.get("REBOOTER_DB_POOL_SIZE", _DEFAULT_POOL_SIZE)
        ),
        "max_overflow": int(
            os.environ.get("REBOOTER_DB_MAX_OVERFLOW", _DEFAULT_MAX_OVERFLOW)
        ),
        "pool_timeout": int(
            os.environ.get("REBOOTER_DB_POOL_TIMEOUT", _DEFAULT_POOL_TIMEOUT)
        ),
    }


def init_engine(settings: Settings) -> None:
    global _engine, _SessionLocal
    _engine = create_engine(
        settings.database_url,
        future=True,
        pool_pre_ping=True,
        **_pool_kwargs(settings.database_url),
    )
    _SessionLocal = sessionmaker(
        bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Engine not initialized. Call init_engine() first.")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        raise RuntimeError("Session factory not initialized.")
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
