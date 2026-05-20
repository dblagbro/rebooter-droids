"""Audit event archival and pruning — v0.5.36 (B1 RBAC P2).

Soft-prune audit_events older than system.audit_retention_days by moving
them to audit_events_archive. Nightly APScheduler job with date-rollover
guard so it only runs once per day.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db import session_scope
from app.models import AuditEvent, AuditEventArchive
from app.services import runtime_settings as rs

log = logging.getLogger(__name__)

_LAST_RUN_DATE_KEY = "system.audit_prune_last_run_date"


def prune_old_audit_events() -> dict:
    """Archive and prune audit events older than retention threshold.

    Returns:
        dict with keys: archived (int), pruned (int), errors (list)
    """
    retention_days = int(rs.get(
        "system.audit_retention_days",
        env_var="REBOOTER_AUDIT_RETENTION_DAYS",
        default=90
    ))

    # Date-rollover guard: only run once per UTC day
    today = datetime.now(timezone.utc).date().isoformat()
    last_run = rs.get(_LAST_RUN_DATE_KEY)
    if last_run == today:
        return {"archived": 0, "pruned": 0, "skipped": True}

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    archived = 0
    pruned = 0
    errors = []

    try:
        with session_scope() as session:
            # Find old audit events
            old_events = list(session.scalars(
                select(AuditEvent)
                .where(AuditEvent.at < cutoff)
                .order_by(AuditEvent.id)
            ))

            if not old_events:
                rs.set_(_LAST_RUN_DATE_KEY, today)
                return {"archived": 0, "pruned": 0}

            # Copy to archive
            now = datetime.now(timezone.utc)
            for event in old_events:
                archive_row = AuditEventArchive(
                    id=event.id,
                    at=event.at,
                    actor_user_id=event.actor_user_id,
                    actor_email_snapshot=event.actor_email_snapshot,
                    action=event.action,
                    target_type=event.target_type,
                    target_id=event.target_id,
                    details=event.details,
                    ip=event.ip,
                    organization_id=event.organization_id,
                    archived_at=now,
                )
                session.add(archive_row)
                archived += 1

            session.flush()

            # Delete from source. ORM delete() (dialect-portable IN)
            # rather than raw `id = ANY(:ids)` — the latter is
            # Postgres-only and blocked in-process SQLite testing.
            event_ids = [e.id for e in old_events]
            pruned = session.execute(
                delete(AuditEvent).where(AuditEvent.id.in_(event_ids))
            ).rowcount

            log.info(
                "Audit prune: archived %d event(s), pruned %d row(s) "
                "(retention=%d days, cutoff=%s)",
                archived, pruned, retention_days, cutoff.isoformat()
            )

        # Mark last run
        rs.set_(_LAST_RUN_DATE_KEY, today)

    except Exception as e:
        log.exception("Audit prune failed")
        errors.append(str(e))

    return {
        "archived": archived,
        "pruned": pruned,
        "errors": errors,
    }
