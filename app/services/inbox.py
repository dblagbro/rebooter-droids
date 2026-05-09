"""Inbox / attention-feed for the v0.3.0+ Status page.

The Status page replaces the v0.2.x stat-grid dashboard. Its job is
to answer two questions on first glance:

  1. **Does anything need attention?**  (health verdict)
  2. **If yes, what and where?**         (attention items)

This module computes both. It is read-only. Best-effort: any
unexpected failure returns a safe `unknown` verdict + empty feed
rather than crashing the dashboard.

Health verdicts (R-DSH-2):
  - `all-clear`  — every non-fixture device online; no pending alerts
  - `attention`  — at least one device offline > N minutes OR a
                   recent enrollment without first heartbeat
  - `degraded`   — > 25% of non-fixture devices offline
  - `unknown`    — telemetry unreliable (DB query failed)

Attention item kinds (R-DSH-3):
  - `device_offline_short`   — heartbeated before, gone silent for >3 min
  - `device_offline_long`    — heartbeated before, gone silent > 24 h
  - `device_never`           — enrolled but never heartbeated > 30 min
  - `enrollment_pending`     — enrolled within last hour, no first heartbeat
  - `firmware_update_seen`   — device booted on new firmware version

Each item carries a stable `id` (composite of kind + target) so a
future P2.5 acknowledge-action can dedupe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Device

log = logging.getLogger(__name__)


# Tunable thresholds. Everything in seconds.
SHORT_OFFLINE_SECONDS = 3 * 60       # 3 min — beyond the heartbeat window
LONG_OFFLINE_SECONDS = 24 * 60 * 60  # 24 h
NEVER_HEARTBEATED_GRACE_SECONDS = 30 * 60   # ignore brand-new units < 30 min old
ENROLLMENT_PENDING_WINDOW_SECONDS = 60 * 60 # < 1 h since registration


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def health_and_attention(limit: int = 50) -> dict:
    """Single entry point — returns the verdict + the ranked feed in
    one DB hit so the Status page renders consistently."""
    try:
        return _compute(limit=limit)
    except Exception:
        log.exception("inbox computation failed")
        return {
            "verdict": "unknown",
            "verdict_label": "Unknown",
            "verdict_message": "Could not read fleet telemetry. Try again.",
            "attention": [],
            "totals": {
                "attention_total": 0,
                "devices_total": 0,
                "devices_online": 0,
                "devices_offline_short": 0,
                "devices_offline_long": 0,
                "devices_never": 0,
                "enrollments_pending": 0,
            },
        }


def _compute(limit: int = 50) -> dict:
    now = datetime.now(timezone.utc)
    short_cutoff = now - timedelta(seconds=SHORT_OFFLINE_SECONDS)
    long_cutoff = now - timedelta(seconds=LONG_OFFLINE_SECONDS)
    never_grace = now - timedelta(seconds=NEVER_HEARTBEATED_GRACE_SECONDS)
    enroll_recent = now - timedelta(seconds=ENROLLMENT_PENDING_WINDOW_SECONDS)

    attention: list[dict] = []
    devices_total = 0
    devices_online = 0
    devices_offline_short = 0
    devices_offline_long = 0
    devices_never = 0
    enrollments_pending = 0

    with session_scope() as session:
        # Real fleet only — QA fixtures are excluded from health math
        # by design (R-DEV-2 + R-UX-13: never lie about state).
        rows = list(
            session.scalars(
                select(Device).where(Device.is_qa_fixture.is_(False))
            )
        )
        for d in rows:
            devices_total += 1
            hb = d.last_heartbeat_at

            if hb is None:
                # Never heartbeated. Bucket by age:
                if d.created_at >= enroll_recent:
                    enrollments_pending += 1
                    attention.append(
                        {
                            "kind": "enrollment_pending",
                            "id": f"enrollment_pending:{d.id}",
                            "severity": "info",
                            "title": "Newly enrolled — waiting for first heartbeat",
                            "device_id": d.id,
                            "device_name": d.display_name or d.id,
                            "since": _iso(d.created_at),
                            "rank": 30,
                        }
                    )
                elif d.created_at < never_grace:
                    devices_never += 1
                    attention.append(
                        {
                            "kind": "device_never",
                            "id": f"device_never:{d.id}",
                            "severity": "warn",
                            "title": "Never heartbeated since enrolment",
                            "device_id": d.id,
                            "device_name": d.display_name or d.id,
                            "since": _iso(d.created_at),
                            "hint": (
                                "Check the firmware: device powered? "
                                "Wi-Fi joined? `central_base_url` correct?"
                            ),
                            "rank": 50,
                        }
                    )
                # else: < 30 min old, no heartbeat — too early to alarm
            elif hb < long_cutoff:
                devices_offline_long += 1
                attention.append(
                    {
                        "kind": "device_offline_long",
                        "id": f"device_offline_long:{d.id}",
                        "severity": "warn",
                        "title": f"Offline > 24 h",
                        "device_id": d.id,
                        "device_name": d.display_name or d.id,
                        "since": _iso(hb),
                        "rank": 60,
                    }
                )
            elif hb < short_cutoff:
                devices_offline_short += 1
                attention.append(
                    {
                        "kind": "device_offline_short",
                        "id": f"device_offline_short:{d.id}",
                        "severity": "warn",
                        "title": "Offline > 3 min",
                        "device_id": d.id,
                        "device_name": d.display_name or d.id,
                        "since": _iso(hb),
                        "rank": 40,
                    }
                )
            else:
                devices_online += 1

    attention.sort(key=lambda x: (-x["rank"], x.get("since") or ""), reverse=False)
    attention = attention[:limit]

    # Verdict logic (R-DSH-2 simplified for v0.3.1).
    attention_total = (
        devices_offline_short
        + devices_offline_long
        + devices_never
        + enrollments_pending
    )

    if devices_total == 0:
        verdict = "all-clear"
        verdict_label = "No devices yet"
        verdict_message = "Enrol your first device to start monitoring."
    elif devices_total > 0 and (
        devices_offline_short + devices_offline_long
    ) * 4 > devices_total:
        verdict = "degraded"
        verdict_label = "Degraded"
        verdict_message = (
            f"{devices_offline_short + devices_offline_long}"
            f" of {devices_total} devices offline"
        )
    elif attention_total > 0:
        verdict = "attention"
        verdict_label = "Attention"
        n = attention_total
        verdict_message = (
            f"{n} item{'s' if n != 1 else ''} need attention"
        )
    else:
        verdict = "all-clear"
        verdict_label = "All clear"
        verdict_message = (
            f"All {devices_total} device{'s' if devices_total != 1 else ''} online"
        )

    return {
        "verdict": verdict,
        "verdict_label": verdict_label,
        "verdict_message": verdict_message,
        # NOTE: this key MUST NOT be "items" — Jinja resolves
        # `obj.items` as the bound `dict.items` method, not a key.
        "attention": attention,
        "totals": {
            "attention_total": attention_total,
            "devices_total": devices_total,
            "devices_online": devices_online,
            "devices_offline_short": devices_offline_short,
            "devices_offline_long": devices_offline_long,
            "devices_never": devices_never,
            "enrollments_pending": enrollments_pending,
        },
    }
