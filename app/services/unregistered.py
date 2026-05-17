"""Track failed device-auth attempts so unregistered firmware is visible.

v0.2.5: surfaces the "firmware reuses a device_id without ever calling
/api/v1/device/register" failure mode. See docs/bug-log.md BUG-013.

Best-effort. NEVER raise from `record()` — auth/middleware paths must keep
serving the real 401 even if our tracking insert fails.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db import session_scope
from app.models import UnregisteredAuthAttempt

log = logging.getLogger(__name__)

# Hard caps to bound storage and avoid letting attackers spray-fill the table.
_MAX_DEVICE_ID_LEN = 80
_MAX_USER_AGENT_LEN = 200
_MAX_ROWS_KEPT = 5000  # rolling cap; oldest pruned on insert when exceeded


def _sanitize(value, max_len: int) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s[:max_len]


def record(
    *,
    claimed_device_id: str | None,
    source_ip: str | None,
    endpoint: str,
    user_agent: str | None,
    auth_present: bool,
) -> None:
    """Upsert one row in unregistered_auth_attempts. Never raises."""
    try:
        cdi = _sanitize(claimed_device_id, _MAX_DEVICE_ID_LEN)
        ip = _sanitize(source_ip, 64) or "unknown"
        ep = _sanitize(endpoint, 80) or "unknown"
        ua = _sanitize(user_agent, _MAX_USER_AGENT_LEN)
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            # BUG-059: branch the upsert by dialect — `pg_insert`
            # compiled against a SQLite engine raises. Both the
            # postgresql and sqlite `insert` variants expose the same
            # `on_conflict_do_update` API. Postgres in production;
            # SQLite under the in-process unit tests.
            dialect = session.bind.dialect.name if session.bind else "postgresql"
            insert_fn = sqlite_insert if dialect == "sqlite" else pg_insert
            stmt = insert_fn(UnregisteredAuthAttempt).values(
                claimed_device_id=cdi,
                source_ip=ip,
                endpoint=ep,
                user_agent=ua,
                auth_present=auth_present,
                first_seen_at=now,
                last_seen_at=now,
                hit_count=1,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    UnregisteredAuthAttempt.claimed_device_id,
                    UnregisteredAuthAttempt.source_ip,
                    UnregisteredAuthAttempt.endpoint,
                ],
                set_={
                    "last_seen_at": now,
                    "hit_count": UnregisteredAuthAttempt.hit_count + 1,
                    "user_agent": ua,
                    "auth_present": auth_present,
                },
            )
            session.execute(stmt)

            # Cheap rolling cap — if we exceed _MAX_ROWS_KEPT, prune the oldest
            # 10%. Only checks once per insert which is rare relative to real
            # traffic.
            total = session.scalar(
                select(func.count()).select_from(UnregisteredAuthAttempt)
            ) or 0
            if total > _MAX_ROWS_KEPT:
                cutoff_count = max(1, _MAX_ROWS_KEPT // 10)
                # delete oldest cutoff_count rows
                oldest_ids_subq = (
                    select(UnregisteredAuthAttempt.id)
                    .order_by(UnregisteredAuthAttempt.last_seen_at.asc())
                    .limit(cutoff_count)
                ).scalar_subquery()
                session.execute(
                    delete(UnregisteredAuthAttempt).where(
                        UnregisteredAuthAttempt.id.in_(oldest_ids_subq)
                    )
                )
    except Exception:
        log.exception(
            "unregistered.record failed for claimed_device_id=%s ip=%s endpoint=%s",
            claimed_device_id,
            source_ip,
            endpoint,
        )


def list_recent(
    *,
    limit: int = 200,
    since_minutes: int | None = None,
) -> list[dict]:
    limit = max(1, min(limit, 1000))
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        if since_minutes
        else None
    )
    with session_scope() as session:
        stmt = select(UnregisteredAuthAttempt).order_by(
            UnregisteredAuthAttempt.last_seen_at.desc()
        )
        if cutoff is not None:
            stmt = stmt.where(UnregisteredAuthAttempt.last_seen_at >= cutoff)
        stmt = stmt.limit(limit)
        rows = list(session.scalars(stmt))
        return [_to_dict(r) for r in rows]


def count_active(since_minutes: int = 60) -> int:
    """How many distinct (device_id, ip, endpoint) tuples have hit a 401 in
    the last `since_minutes` minutes. Used for the dashboard badge."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    with session_scope() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(UnregisteredAuthAttempt)
                .where(UnregisteredAuthAttempt.last_seen_at >= cutoff)
            )
            or 0
        )


def _to_dict(row: UnregisteredAuthAttempt) -> dict:
    return {
        "id": row.id,
        "claimed_device_id": row.claimed_device_id,
        "source_ip": row.source_ip,
        "endpoint": row.endpoint,
        "user_agent": row.user_agent,
        "auth_present": bool(row.auth_present),
        "hit_count": row.hit_count,
        "first_seen_at": row.first_seen_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_seen_at": row.last_seen_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
