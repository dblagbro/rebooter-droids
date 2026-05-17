"""Unit tests — schedule recurrence math (`compute_next_run_at`).

`app/services/schedules.py::compute_next_run_at` decides when a
schedule next fires. Pure date arithmetic over an in-memory `Schedule`
instance — no DB needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import Schedule
from app.models.schedules import REC_DAILY, REC_ONCE, REC_WEEKLY
from app.services.schedules import compute_next_run_at

NOON = datetime(2026, 1, 5, 12, 0, 0, tzinfo=timezone.utc)  # a Monday


def _schedule(**kwargs) -> Schedule:
    """An un-persisted Schedule with sane recurrence defaults."""
    defaults = dict(
        recurrence=REC_ONCE,
        start_at=None,
        last_run_at=None,
        at_time_utc=None,
        weekdays=[],
    )
    defaults.update(kwargs)
    return Schedule(**defaults)


# ── once ───────────────────────────────────────────────────────────────

def test_once_future_start_returns_start():
    start = NOON + timedelta(hours=2)
    s = _schedule(recurrence=REC_ONCE, start_at=start)
    assert compute_next_run_at(s, now=NOON) == start


def test_once_past_start_returns_none():
    s = _schedule(recurrence=REC_ONCE, start_at=NOON - timedelta(hours=2))
    assert compute_next_run_at(s, now=NOON) is None


def test_once_already_fired_returns_none():
    s = _schedule(
        recurrence=REC_ONCE,
        start_at=NOON + timedelta(hours=2),
        last_run_at=NOON,
    )
    assert compute_next_run_at(s, now=NOON) is None


# ── daily ──────────────────────────────────────────────────────────────

def test_daily_returns_today_when_time_not_yet_passed():
    s = _schedule(recurrence=REC_DAILY, at_time_utc="23:59")
    nxt = compute_next_run_at(s, now=NOON)
    assert nxt == NOON.replace(hour=23, minute=59, second=0, microsecond=0)


def test_daily_returns_tomorrow_when_time_already_passed():
    s = _schedule(recurrence=REC_DAILY, at_time_utc="06:00")
    nxt = compute_next_run_at(s, now=NOON)
    assert nxt == (NOON + timedelta(days=1)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )


# ── weekly ─────────────────────────────────────────────────────────────

def test_weekly_walks_forward_to_next_matching_weekday():
    # NOON is Monday (weekday 0); ask for Wednesday (weekday 2).
    s = _schedule(recurrence=REC_WEEKLY, at_time_utc="03:00", weekdays=[2])
    nxt = compute_next_run_at(s, now=NOON)
    # Mon 03:00 has passed → Tue (no match) → Wed 2026-01-07 03:00.
    assert nxt == datetime(2026, 1, 7, 3, 0, 0, tzinfo=timezone.utc)


def test_weekly_with_no_weekdays_returns_none():
    s = _schedule(recurrence=REC_WEEKLY, at_time_utc="03:00", weekdays=[])
    assert compute_next_run_at(s, now=NOON) is None
