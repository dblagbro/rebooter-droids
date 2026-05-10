"""Schedule service — v0.4.8 (B8).

CRUD + plain-English render + next-run computation. The runtime
tick lives in `app/services/schedule_runtime.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Schedule
from app.models.schedules import (
    KIND_MAINTENANCE,
    KIND_POWER_CYCLE,
    KNOWN_KINDS,
    KNOWN_RECURRENCES,
    REC_DAILY,
    REC_ONCE,
    REC_WEEKLY,
)


class ScheduleValidationError(ValueError):
    pass


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def serialize(s: Schedule) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "enabled": bool(s.enabled),
        "kind": s.kind,
        "recurrence": s.recurrence,
        "at_time_utc": s.at_time_utc,
        "weekdays": s.weekdays or [],
        "start_at": _iso(s.start_at),
        "duration_seconds": s.duration_seconds,
        "target": s.target or {},
        "power_off_seconds": s.power_off_seconds,
        "post_reboot_holdoff_seconds": s.post_reboot_holdoff_seconds,
        "last_run_at": _iso(s.last_run_at),
        "next_run_at": _iso(s.next_run_at),
        "last_outcome": s.last_outcome,
        "created_at": _iso(s.created_at),
        "sentence": render_sentence(s),
    }


def list_all() -> list[dict]:
    with session_scope() as session:
        rows = list(session.scalars(
            select(Schedule).order_by(Schedule.created_at.desc())
        ))
        return [serialize(s) for s in rows]


def get(schedule_id: str) -> dict | None:
    with session_scope() as session:
        s = session.get(Schedule, schedule_id)
        return serialize(s) if s else None


def create(
    *,
    name: str,
    kind: str,
    recurrence: str,
    target: dict | None = None,
    at_time_utc: str | None = None,
    weekdays: list[int] | None = None,
    start_at: datetime | None = None,
    duration_seconds: int = 0,
    power_off_seconds: int = 5,
    post_reboot_holdoff_seconds: int = 180,
    description: str | None = None,
    created_by_user_id: str | None = None,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise ScheduleValidationError("name is required")
    # v0.4.11 (BUG-036 follow-up): bound to column width.
    if len(name) > 120:
        raise ScheduleValidationError("name must be 120 characters or fewer")
    if kind not in KNOWN_KINDS:
        raise ScheduleValidationError(f"kind must be one of {KNOWN_KINDS}")
    if recurrence not in KNOWN_RECURRENCES:
        raise ScheduleValidationError(f"recurrence must be one of {KNOWN_RECURRENCES}")
    if recurrence in (REC_DAILY, REC_WEEKLY):
        if not at_time_utc:
            raise ScheduleValidationError("at_time_utc is required for daily/weekly")
        # v0.4.10 (BUG-034): validate HH:MM shape before insert.
        # Column is VARCHAR(5); pre-fix, "not-a-time" raised
        # DataError → 500 instead of 400. Also reject non-numeric
        # parts.
        import re
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", at_time_utc.strip())
        if not m or not (0 <= int(m.group(1)) <= 23) or not (0 <= int(m.group(2)) <= 59):
            raise ScheduleValidationError(
                "at_time_utc must be HH:MM (00:00 to 23:59)"
            )
        # Normalize to zero-padded 5-char form.
        at_time_utc = f"{int(m.group(1)):02d}:{m.group(2)}"
    if recurrence == REC_WEEKLY:
        if not weekdays:
            raise ScheduleValidationError("weekdays is required for weekly")
        # v0.4.12 (BUG-040 + BUG-041): dedupe + range-check.
        cleaned = sorted({int(d) for d in weekdays})
        if any(d < 0 or d > 6 for d in cleaned):
            raise ScheduleValidationError(
                "weekdays must be integers 0 (Mon) to 6 (Sun)"
            )
        weekdays = cleaned
    if recurrence == REC_ONCE and not start_at:
        raise ScheduleValidationError("start_at is required for once")
    if kind == KIND_POWER_CYCLE:
        if not target or target.get("kind") not in ("device", "group", "tag"):
            raise ScheduleValidationError(
                "power_cycle requires target.kind in {device,group,tag}"
            )
    if kind == KIND_MAINTENANCE and duration_seconds <= 0:
        raise ScheduleValidationError(
            "maintenance schedule needs duration_seconds > 0"
        )

    s = Schedule(
        name=name,
        description=description,
        enabled=True,
        kind=kind,
        recurrence=recurrence,
        at_time_utc=at_time_utc,
        weekdays=weekdays or [],
        start_at=start_at,
        duration_seconds=int(duration_seconds),
        target=target or {},
        power_off_seconds=int(power_off_seconds),
        post_reboot_holdoff_seconds=int(post_reboot_holdoff_seconds),
        created_by_user_id=created_by_user_id,
    )
    with session_scope() as session:
        session.add(s)
        session.flush()
        s.next_run_at = compute_next_run_at(s, now=datetime.now(timezone.utc))
        session.flush()
        return serialize(s)


def delete(schedule_id: str) -> bool:
    with session_scope() as session:
        s = session.get(Schedule, schedule_id)
        if s is None:
            return False
        session.delete(s)
        session.flush()
        return True


def set_enabled(schedule_id: str, enabled: bool) -> bool:
    with session_scope() as session:
        s = session.get(Schedule, schedule_id)
        if s is None:
            return False
        s.enabled = bool(enabled)
        s.updated_at = datetime.now(timezone.utc)
        if enabled:
            s.next_run_at = compute_next_run_at(s, now=datetime.now(timezone.utc))
        session.flush()
        return True


# ── next-run computation ────────────────────────────────────────────────


def compute_next_run_at(s: Schedule, *, now: datetime) -> datetime | None:
    """Return the next time the schedule should fire, or None if
    never (one-shot already in the past)."""
    if s.recurrence == REC_ONCE:
        if s.start_at is None:
            return None
        if s.last_run_at is not None:
            return None  # one-shot already fired
        return s.start_at if s.start_at > now else None

    if s.recurrence in (REC_DAILY, REC_WEEKLY):
        if not s.at_time_utc:
            return None
        try:
            hh, mm = [int(x) for x in s.at_time_utc.split(":")]
        except Exception:
            return None
        candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)

        if s.recurrence == REC_DAILY:
            return candidate

        # weekly: walk forward until weekday matches
        weekdays = sorted(int(d) for d in (s.weekdays or []))
        if not weekdays:
            return None
        for _ in range(7):
            if candidate.weekday() in weekdays:
                return candidate
            candidate += timedelta(days=1)
        return None

    return None


# ── plain-English sentence ──────────────────────────────────────────────


_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def render_sentence(s: Schedule) -> str:
    if s.kind == KIND_POWER_CYCLE:
        target = s.target or {}
        target_str = (
            f"device {target.get('id')}"
            if target.get("kind") == "device"
            else f"group {target.get('id')}"
            if target.get("kind") == "group"
            else f"tag `{target.get('tag')}`"
            if target.get("kind") == "tag"
            else "?"
        )
        action_str = f"power-cycle ({s.power_off_seconds}s off) {target_str}"
    elif s.kind == KIND_MAINTENANCE:
        mins = max(s.duration_seconds // 60, 1)
        action_str = f"pause watchdog rules for {mins} min"
    else:
        action_str = f"unknown action `{s.kind}`"

    if s.recurrence == REC_ONCE:
        when = (
            "once at " + s.start_at.strftime("%Y-%m-%dT%H:%MZ")
            if s.start_at else "once (no start time set)"
        )
    elif s.recurrence == REC_DAILY:
        when = f"every day at {s.at_time_utc} UTC"
    elif s.recurrence == REC_WEEKLY:
        days = ", ".join(_DAY_NAMES[d] for d in (s.weekdays or []) if 0 <= d < 7)
        when = f"every week on {days} at {s.at_time_utc} UTC"
    else:
        when = "?"

    return f"{action_str.capitalize()} — {when}."
