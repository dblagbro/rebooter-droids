"""Attention-ack service — v0.4.22 (Tier-2 E).

Stores per-attention-item acks/snoozes. Inbox queries this to
filter acked items out of the operator-facing list.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import AttentionAck


def acked_ids() -> set[str]:
    """Returns the set of attention_ids that are currently acked
    (and not expired). Cheap, called per inbox render. The
    attention_acks table is small — even a fleet of 10k devices
    will have <100 active items."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        rows = list(session.scalars(select(AttentionAck)))
        out: set[str] = set()
        for r in rows:
            if r.snooze_until is None:
                out.add(r.attention_id)
            elif r.snooze_until > now:
                out.add(r.attention_id)
        return out


def is_acked(attention_id: str) -> bool:
    return attention_id in acked_ids()


def ack(
    attention_id: str,
    *,
    by_user_id: str | None,
    snooze_seconds: int | None = None,
    reason: str | None = None,
) -> dict:
    """Idempotent — re-acking an already-acked id updates its
    snooze_until / reason fields. Returns the row id + final
    snooze_until."""
    now = datetime.now(timezone.utc)
    snooze_until = (
        (now + timedelta(seconds=int(snooze_seconds)))
        if snooze_seconds and snooze_seconds > 0
        else None
    )
    with session_scope() as session:
        row = session.scalar(
            select(AttentionAck).where(
                AttentionAck.attention_id == attention_id
            )
        )
        if row is None:
            row = AttentionAck(
                attention_id=attention_id,
                acked_by_user_id=by_user_id,
                acked_at=now,
                snooze_until=snooze_until,
                reason=reason,
            )
            session.add(row)
        else:
            row.acked_by_user_id = by_user_id
            row.acked_at = now
            row.snooze_until = snooze_until
            if reason:
                row.reason = reason
        session.flush()
        return {
            "id": row.id,
            "attention_id": row.attention_id,
            "acked_at": row.acked_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "snooze_until": (
                row.snooze_until.strftime("%Y-%m-%dT%H:%M:%SZ")
                if row.snooze_until else None
            ),
        }


def unack(attention_id: str) -> bool:
    """Operator manually clears an ack — re-surfaces the item."""
    with session_scope() as session:
        row = session.scalar(
            select(AttentionAck).where(
                AttentionAck.attention_id == attention_id
            )
        )
        if row is None:
            return False
        session.delete(row)
        session.flush()
        return True


def expire_old_acks() -> int:
    """Cleanup: delete ack rows whose snooze_until is in the past
    by more than 24 h. Optional housekeeping; the `acked_ids()`
    query already filters expired entries out at read time."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(AttentionAck).where(
                    AttentionAck.snooze_until.is_not(None),
                    AttentionAck.snooze_until < cutoff,
                )
            )
        )
        for r in rows:
            session.delete(r)
        session.flush()
        return len(rows)
