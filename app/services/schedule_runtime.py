"""Schedule runtime — v0.4.8 (B8).

APScheduler tick fires schedules whose `next_run_at` has elapsed.
Two kinds:

- `power_cycle` — enqueue a `relay_cycle` command for each device
  in the target. Records `last_run_at` + `next_run_at` (recurrence-
  aware) and `last_outcome`.
- `maintenance` — flip portal-wide maintenance ON, schedule an
  internal "expire-maintenance" follow-up after `duration_seconds`
  via a one-shot APScheduler job (or by simply re-checking on the
  next tick if `next_run_at + duration_seconds` has passed).

For v0.4.8 we keep the maintenance-end logic dead-simple: each
tick computes `should_be_in_maintenance_now()` from the schedules
and reconciles the flag accordingly. No separate timers; the
schedule_tick runs every 30 s so worst-case granularity is ~30 s
on the OFF transition. Acceptable.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import Schedule
from app.models.schedules import KIND_MAINTENANCE, KIND_POWER_CYCLE
from app.services.schedules import compute_next_run_at

log = logging.getLogger(__name__)


def tick() -> dict:
    if os.environ.get("REBOOTER_SCHEDULER_DISABLED") == "1":
        return {"disabled": True}

    now = datetime.now(timezone.utc)
    stats = {"fired": 0, "errors": 0, "considered": 0}

    with session_scope() as session:
        rows = list(session.scalars(
            select(Schedule).where(Schedule.enabled.is_(True))
        ))
        for s in rows:
            stats["considered"] += 1
            if s.next_run_at is None:
                # First-time scheduling
                s.next_run_at = compute_next_run_at(s, now=now)
                continue

            if s.next_run_at > now:
                continue  # not yet due

            try:
                outcome = _fire_schedule(s, now)
                stats["fired"] += 1
                s.last_outcome = outcome
            except Exception as e:
                log.exception("schedule fire failed for %s", s.id)
                s.last_outcome = f"error: {type(e).__name__}"
                stats["errors"] += 1

            s.last_run_at = now
            s.next_run_at = compute_next_run_at(s, now=now)

        # Maintenance reconciliation: if any active maintenance
        # schedule's window covers `now`, ensure portal flag is ON.
        # If none cover now AND a maintenance schedule was the last
        # to flip it, flip OFF.
        _reconcile_maintenance_flag(session, rows, now)
        session.flush()

    return stats


def _fire_schedule(s: Schedule, now: datetime) -> str:
    if s.kind == KIND_POWER_CYCLE:
        return _fire_power_cycle(s)
    if s.kind == KIND_MAINTENANCE:
        # No-op at fire-time; the reconciler does the flip.
        return "maintenance_window_open"
    return f"unsupported_kind:{s.kind}"


def _fire_power_cycle(s: Schedule) -> str:
    from app.services.commands import enqueue_for_device
    from app.services.watchdog_runtime import _resolve_target_devices

    device_ids = _resolve_target_devices(s.target or {})
    if not device_ids:
        return "no_target_devices"

    payload = {
        "power_off_seconds": s.power_off_seconds,
        "post_reboot_holdoff_seconds": s.post_reboot_holdoff_seconds,
    }
    enqueued = 0
    for did in device_ids:
        try:
            enqueue_for_device(
                device_id=did,
                cmd_type="relay_cycle",
                payload=payload,
                issued_by_user_id=None,
                override_lockout=False,
            )
            enqueued += 1
        except Exception:
            pass
    return f"enqueued:{enqueued}"


def _reconcile_maintenance_flag(session, schedules, now: datetime) -> None:
    """v0.4.7 / v0.4.10 — reconcile portal-wide maintenance flag
    against scheduled-maintenance windows.

    Rules:
      - In a schedule window AND flag OFF: flip ON (reason=schedule),
        UNLESS the operator explicitly toggled it OFF *during this
        same window* (BUG-032 — respect operator override).
      - Outside all schedule windows AND flag is ON with reason=
        schedule: flip OFF (reason=schedule_window_ended).
      - Operator-set ON or OFF carries `operator_override_at`. The
        reconciler honours that override for as long as we're in
        the window the override happened during.
    """
    from app.services import runtime_flags

    active_window_start: datetime | None = None
    for s in schedules:
        if s.kind != KIND_MAINTENANCE or not s.enabled:
            continue
        if s.last_run_at is None:
            continue
        window_end = s.last_run_at + timedelta(seconds=s.duration_seconds)
        if s.last_run_at <= now <= window_end:
            active_window_start = s.last_run_at
            break

    current = runtime_flags.is_maintenance_mode_active()
    details = runtime_flags.maintenance_mode_details()
    operator_override_at_iso = details.get("operator_override_at")

    in_window = active_window_start is not None
    operator_overrode_in_this_window = False
    if in_window and operator_override_at_iso:
        try:
            override_at = datetime.fromisoformat(operator_override_at_iso)
            if override_at.tzinfo is None:
                override_at = override_at.replace(tzinfo=timezone.utc)
            if override_at >= active_window_start:
                operator_overrode_in_this_window = True
        except ValueError:
            pass

    if in_window and not current and not operator_overrode_in_this_window:
        runtime_flags.set_maintenance_mode(
            True, user_id=None, reason="schedule"
        )
    elif (not in_window) and current:
        if details.get("reason") == "schedule":
            runtime_flags.set_maintenance_mode(
                False, user_id=None, reason="schedule_window_ended"
            )
