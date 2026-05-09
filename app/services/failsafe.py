"""Failsafe-event service — v0.3.8 (RFC-005 P1).

Records device-side failsafe reports and exposes query helpers for
the Status inbox and the per-device detail page.

Best-effort on the write path: the device-API endpoint is the one
caller and it must keep accepting a properly-shaped POST even if
our recording fails (we don't want a brief DB hiccup to make the
firmware retry forever). On unexpected failure, we log and return
a synthetic id so the device can move on.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db import session_scope
from app.models import Device, DeviceFailsafeEvent
from app.models.failsafe import KNOWN_FAILSAFE_REASONS

log = logging.getLogger(__name__)


def record(
    *,
    device_id: str,
    failed_version: str | None,
    fallback_to_version: str | None,
    reason: str,
    details: dict | None,
) -> dict:
    """Insert one failsafe row. Returns a small dict suitable for
    direct return to the device's POST. Never raises into the
    device-API path."""
    reason = (reason or "other").strip().lower()[:40]
    if reason not in KNOWN_FAILSAFE_REASONS:
        # Accept-and-record the unknown value verbatim — useful for
        # forensics if firmware extends the vocabulary mid-deploy.
        pass

    serialized_details: str | None = None
    if details:
        try:
            serialized_details = json.dumps(details, separators=(",", ":"))
        except Exception:
            serialized_details = None

    try:
        now = datetime.now(timezone.utc)
        row = DeviceFailsafeEvent(
            device_id=device_id,
            received_at=now,
            failed_version=(failed_version or None),
            fallback_to_version=(fallback_to_version or None),
            reason=reason,
            details=serialized_details,
        )
        with session_scope() as session:
            session.add(row)
            session.flush()
            return {
                "id": row.id,
                "received_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "reason": reason,
            }
    except Exception:
        log.exception(
            "failsafe.record failed for device_id=%s reason=%s",
            device_id, reason,
        )
        return {
            "id": None,
            "received_at": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "reason": reason,
        }


def list_for_device(device_id: str, limit: int = 25) -> list[dict]:
    limit = max(1, min(limit, 200))
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DeviceFailsafeEvent)
                .where(DeviceFailsafeEvent.device_id == device_id)
                .order_by(DeviceFailsafeEvent.received_at.desc())
                .limit(limit)
            )
        )
        return [_to_dict(r) for r in rows]


def list_recent(limit: int = 50, since_hours: int = 24) -> list[dict]:
    """Recent failsafe events across the fleet — used by the Status
    inbox to surface "this version failed on N devices in the last
    N hours" attention items."""
    limit = max(1, min(limit, 500))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(DeviceFailsafeEvent)
                .where(DeviceFailsafeEvent.received_at >= cutoff)
                .order_by(DeviceFailsafeEvent.received_at.desc())
                .limit(limit)
            )
        )
        return [_to_dict(r) for r in rows]


def count_recent(since_hours: int = 24) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    with session_scope() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(DeviceFailsafeEvent)
                .where(DeviceFailsafeEvent.received_at >= cutoff)
            )
            or 0
        )


def _to_dict(row: DeviceFailsafeEvent) -> dict:
    return {
        "id": row.id,
        "device_id": row.device_id,
        "received_at": row.received_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "failed_version": row.failed_version,
        "fallback_to_version": row.fallback_to_version,
        "reason": row.reason,
        "details": _try_parse_details(row.details),
    }


def _try_parse_details(s: str | None) -> dict | None:
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        # If firmware sent us non-JSON we still want to return SOMETHING
        # the UI can render rather than swallowing the diagnostic.
        return {"_raw": s}
