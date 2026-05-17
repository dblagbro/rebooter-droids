"""Unit tests — the Status-page inbox / attention-feed service.

`app/services/inbox.py::health_and_attention` computes the fleet
health verdict + the ranked attention feed. Its device-age bucketing
(`as_aware(d.created_at)` / `as_aware(hb)`) is a BUG-059(A) site: read
back naive from SQLite, those datetimes would `TypeError` against a
tz-aware cutoff — `_compute` is best-effort, so the symptom was a
silent `verdict="unknown"`. DB-backed → the `hub_db` fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import session_scope
from app.models import Device
from app.services.inbox import health_and_attention


def _device(session, device_id, *, last_heartbeat_at=None, created_at=None,
            is_qa_fixture=False):
    kw = {"id": device_id, "is_qa_fixture": is_qa_fixture,
          "last_heartbeat_at": last_heartbeat_at}
    if created_at is not None:
        kw["created_at"] = created_at
    session.add(Device(**kw))


def _kinds(result):
    return {a["kind"] for a in result["attention"]}


# ── empty fleet ────────────────────────────────────────────────────────

def test_empty_fleet_is_all_clear(hub_db):
    result = health_and_attention()
    assert result["verdict"] == "all-clear"
    assert result["totals"]["devices_total"] == 0


# ── the device-age buckets (the BUG-059 datetime sites) ────────────────

def test_recent_heartbeat_counts_online(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(s, "dev-1", last_heartbeat_at=now - timedelta(seconds=30))
    result = health_and_attention()
    assert result["verdict"] == "all-clear"
    assert result["totals"]["devices_online"] == 1


def test_offline_short_device_is_bucketed_and_flagged(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(s, "dev-1", last_heartbeat_at=now - timedelta(minutes=10))
    result = health_and_attention()
    assert result["verdict"] != "unknown"
    assert result["totals"]["devices_offline_short"] == 1
    assert "device_offline_short" in _kinds(result)


def test_offline_long_device_is_bucketed_and_flagged(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(s, "dev-1", last_heartbeat_at=now - timedelta(days=2))
    result = health_and_attention()
    assert result["verdict"] != "unknown"
    assert result["totals"]["devices_offline_long"] == 1
    assert "device_offline_long" in _kinds(result)


def test_recently_enrolled_device_is_enrollment_pending(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        # No heartbeat, created 10 min ago → within the 1 h window.
        _device(s, "dev-1", last_heartbeat_at=None,
                created_at=now - timedelta(minutes=10))
    result = health_and_attention()
    assert result["verdict"] != "unknown"
    assert result["totals"]["enrollments_pending"] == 1
    assert "enrollment_pending" in _kinds(result)


def test_never_heartbeated_old_device_is_device_never(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        # No heartbeat, created 3 h ago → past the grace window.
        _device(s, "dev-1", last_heartbeat_at=None,
                created_at=now - timedelta(hours=3))
    result = health_and_attention()
    assert result["verdict"] != "unknown"
    assert result["totals"]["devices_never"] == 1
    assert "device_never" in _kinds(result)


# ── QA-fixture exclusion ───────────────────────────────────────────────

def test_qa_fixture_devices_excluded_from_health_math(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(s, "dev-real", last_heartbeat_at=now - timedelta(seconds=30))
        _device(s, "dev-qa", last_heartbeat_at=now - timedelta(days=2),
                is_qa_fixture=True)
    result = health_and_attention()
    # The QA fixture must not show up as an offline device.
    assert result["totals"]["devices_total"] == 1
    assert result["totals"]["devices_offline_long"] == 0


# ── explicit BUG-059 regression guard ──────────────────────────────────

def test_verdict_never_unknown_on_a_real_fleet(hub_db):
    """The whole point of the BUG-059(A) fix: with devices carrying
    real (SQLite-naive) datetimes, `_compute` must not raise into the
    best-effort fallback that returns verdict='unknown'."""
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _device(s, "dev-on", last_heartbeat_at=now - timedelta(seconds=30))
        _device(s, "dev-off", last_heartbeat_at=now - timedelta(minutes=20))
        _device(s, "dev-new", last_heartbeat_at=None,
                created_at=now - timedelta(minutes=5))
    result = health_and_attention()
    assert result["verdict"] != "unknown"
    assert result["totals"]["devices_total"] == 3
