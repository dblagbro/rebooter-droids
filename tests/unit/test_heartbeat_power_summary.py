"""Unit tests — Tier-2 power-in-heartbeat consumer.

The firmware retires the dedicated `/device/power-samples` endpoint and
folds a compact `power` summary object (min/avg/max W, latest V/A/PF,
energy Wh, frame counts) into the `/device/heartbeat` payload. The hub
parses it in `record_heartbeat` and stores a `source="heartbeat"`
`DevicePowerSample` row via `events.ingest_power_summary`.

All DB-backed → the `hub_db` isolated-SQLite fixture. The autouse
`recovery_push_spy` keeps the deferred device_config/commands/audit
chain out of the heartbeat path (same trick as test_heartbeats_service).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db import session_scope
from app.models import Device, DevicePowerSample
from app.services.events import ingest_power_summary
from app.services.heartbeats import record_heartbeat


@pytest.fixture(autouse=True)
def recovery_push_spy(monkeypatch):
    monkeypatch.setattr(
        "app.services.device_config.maybe_push_after_recovery",
        lambda device_id, *, trigger: {},
    )


def _device(session, device_id="dev-1", **kw):
    session.add(Device(id=device_id, **kw))


def _samples(device_id="dev-1"):
    with session_scope() as s:
        return list(
            s.query(DevicePowerSample)
            .filter(DevicePowerSample.device_id == device_id)
            .all()
        )


# ── ingest_power_summary — direct ──────────────────────────────────────

_FULL = {
    "min_w": 1.2,
    "avg_w": 12.4,
    "max_w": 90.1,
    "v_v": 122.7,
    "i_a": 0.10,
    "pf": 0.62,
    "energy_wh": 14821,
    "valid_frame_count": 58,
    "invalid_frame_count": 2,
    "sampled_uptime_seconds": 87421,
}


def test_ingest_power_summary_writes_one_heartbeat_row(hub_db):
    with session_scope() as s:
        _device(s)
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        wrote = ingest_power_summary(s, "dev-1", _FULL, now)
    assert wrote is True
    rows = _samples()
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "heartbeat"


def test_ingest_power_summary_maps_avg_to_p_w_and_extremes(hub_db):
    with session_scope() as s:
        _device(s)
    with session_scope() as s:
        ingest_power_summary(s, "dev-1", _FULL, datetime.now(timezone.utc))
    row = _samples()[0]
    # avg → the canonical p_w column so existing queries keep working.
    assert float(row.p_w) == pytest.approx(12.4)
    assert float(row.min_w) == pytest.approx(1.2)
    assert float(row.max_w) == pytest.approx(90.1)


def test_ingest_power_summary_converts_amps_to_milliamps(hub_db):
    with session_scope() as s:
        _device(s)
    with session_scope() as s:
        ingest_power_summary(s, "dev-1", _FULL, datetime.now(timezone.utc))
    row = _samples()[0]
    assert row.i_ma == 100  # 0.10 A → 100 mA
    assert float(row.v_v) == pytest.approx(122.7)
    assert float(row.pf) == pytest.approx(0.62)
    assert row.energy_wh == 14821


def test_ingest_power_summary_maps_invalid_frames_to_crc_fail(hub_db):
    with session_scope() as s:
        _device(s)
    with session_scope() as s:
        ingest_power_summary(s, "dev-1", _FULL, datetime.now(timezone.utc))
    assert _samples()[0].crc_fail_count == 2


def test_ingest_power_summary_requires_avg_w(hub_db):
    with session_scope() as s:
        _device(s)
    with session_scope() as s:
        # A summary with no average → nothing to store.
        wrote = ingest_power_summary(
            s, "dev-1", {"v_v": 120.0}, datetime.now(timezone.utc)
        )
    assert wrote is False
    assert _samples() == []


def test_ingest_power_summary_accepts_p_w_alias_for_avg(hub_db):
    with session_scope() as s:
        _device(s)
    with session_scope() as s:
        wrote = ingest_power_summary(
            s, "dev-1", {"p_w": 5.5}, datetime.now(timezone.utc)
        )
    assert wrote is True
    assert float(_samples()[0].p_w) == pytest.approx(5.5)


def test_ingest_power_summary_minimal_shape_nullable_fields(hub_db):
    with session_scope() as s:
        _device(s)
    with session_scope() as s:
        ingest_power_summary(s, "dev-1", {"avg_w": 7.0}, datetime.now(timezone.utc))
    row = _samples()[0]
    assert float(row.p_w) == pytest.approx(7.0)
    assert row.min_w is None
    assert row.max_w is None
    assert row.v_v is None
    assert row.i_ma is None
    assert row.energy_wh is None


def test_ingest_power_summary_ignores_non_dict(hub_db):
    with session_scope() as s:
        _device(s)
    with session_scope() as s:
        assert ingest_power_summary(s, "dev-1", "nope", datetime.now(timezone.utc)) is False
        assert ingest_power_summary(s, "dev-1", None, datetime.now(timezone.utc)) is False
    assert _samples() == []


def test_ingest_power_summary_carries_low_load_current_semantics(hub_db):
    with session_scope() as s:
        _device(s)
    summary = {"avg_w": 2.0, "i_a": 0.0, "i_ma_estimated": True, "i_ma_estimate": 38}
    with session_scope() as s:
        ingest_power_summary(s, "dev-1", summary, datetime.now(timezone.utc))
    row = _samples()[0]
    assert row.i_ma_estimated is True
    assert row.i_ma_estimate == 38


def test_ingest_power_summary_explicit_i_ma_wins_over_amps(hub_db):
    with session_scope() as s:
        _device(s)
    with session_scope() as s:
        ingest_power_summary(
            s, "dev-1", {"avg_w": 5.0, "i_ma": 777, "i_a": 0.10},
            datetime.now(timezone.utc),
        )
    assert _samples()[0].i_ma == 777


# ── record_heartbeat integration ───────────────────────────────────────

def test_record_heartbeat_stores_power_summary(hub_db):
    with session_scope() as s:
        _device(s)
    record_heartbeat("dev-1", {"mode": "smart_plug", "power": _FULL})
    rows = _samples()
    assert len(rows) == 1
    assert rows[0].source == "heartbeat"
    assert float(rows[0].p_w) == pytest.approx(12.4)


def test_record_heartbeat_without_power_writes_no_sample(hub_db):
    with session_scope() as s:
        _device(s)
    record_heartbeat("dev-1", {"mode": "smart_plug"})
    assert _samples() == []


def test_record_heartbeat_one_sample_per_heartbeat(hub_db):
    with session_scope() as s:
        _device(s)
    record_heartbeat("dev-1", {"power": {"avg_w": 10.0}})
    record_heartbeat("dev-1", {"power": {"avg_w": 11.0}})
    assert len(_samples()) == 2


def test_record_heartbeat_malformed_power_does_not_block_heartbeat(hub_db):
    with session_scope() as s:
        _device(s)
    # A non-dict `power` value must not raise out of the heartbeat path.
    out = record_heartbeat("dev-1", {"mode": "smart_plug", "power": "garbage"})
    assert "recorded_at" in out
    assert _samples() == []


def test_record_heartbeat_power_summary_sampled_at_is_received_at(hub_db):
    with session_scope() as s:
        _device(s)
    record_heartbeat("dev-1", {"power": {"avg_w": 3.0}})
    row = _samples()[0]
    assert row.sampled_at is not None
    assert row.received_at is not None
