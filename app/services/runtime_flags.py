"""Runtime flag service — v0.4.7.

Tiny key/value store with two well-known keys today:

    maintenance_mode_active   bool — portal-wide watchdog pause (B7)

`get(name, default)` returns the stored JSON value (or the default
if no row exists). `set(name, value, user_id)` upserts.

Reads run inside the watchdog tick, so they need to be cheap. The
table is small (one row per flag) so a per-tick SELECT is fine.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.db import session_scope
from app.models import RuntimeFlag


MAINTENANCE_MODE = "maintenance_mode_active"


def get(name: str, default=None):
    with session_scope() as session:
        row = session.get(RuntimeFlag, name)
        if row is None:
            return default
        return row.value


def set_(name: str, value, *, user_id: str | None = None) -> None:
    """`set` is a builtin, so the function is named `set_`. Imports
    typically alias to `set_flag` for readability."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.get(RuntimeFlag, name)
        if row is None:
            row = RuntimeFlag(name=name, value=value, updated_at=now)
        else:
            row.value = value
            row.updated_at = now
        row.updated_by_user_id = user_id
        session.add(row)
        session.flush()


# ── convenience wrappers ───────────────────────────────────────────────

def is_maintenance_mode_active() -> bool:
    val = get(MAINTENANCE_MODE, default={"on": False})
    return bool(val.get("on")) if isinstance(val, dict) else bool(val)


def set_maintenance_mode(on: bool, *, user_id: str | None, reason: str | None = None) -> None:
    """v0.4.10 (BUG-032): when an operator toggles, also stamp the
    `operator_override_at` so the schedule_tick reconciler doesn't
    fight them. The override lapses when the next scheduled window
    starts (the reconciler recomputes against window boundaries).
    """
    payload = {
        "on": bool(on),
        "reason": reason,
        "set_at": datetime.now(timezone.utc).isoformat(),
    }
    # Anything the operator changes carries an override stamp;
    # `reason='schedule'` and `reason='schedule_window_ended'` are
    # the schedule-runtime's own writes and do NOT mark override.
    if reason not in ("schedule", "schedule_window_ended"):
        payload["operator_override_at"] = payload["set_at"]
    set_(MAINTENANCE_MODE, payload, user_id=user_id)


def maintenance_mode_details() -> dict:
    val = get(MAINTENANCE_MODE, default={"on": False})
    if not isinstance(val, dict):
        val = {"on": bool(val)}
    return val
