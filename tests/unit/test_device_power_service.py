"""Unit tests — the device-power telemetry service.

`app/services/device_power.py` is the read-only query surface over
`device_power_samples` / `device_power_rollups`: latest-sample lookups,
windowed series, the source (real/synthetic) breakdown, fleet
aggregates and the nightly rollup compute. Pure helpers need no
fixture; everything DB-backed takes the `hub_db` isolated-SQLite
fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db import session_scope
from app.models import DevicePowerRollup, DevicePowerSample
from app.services import device_power, runtime_settings


def _add_sample(session, device_id, *, sampled_at, source="steady", p_w=None,
                channel_id=0, source_flags=0, energy_wh=None):
    session.add(DevicePowerSample(
        device_id=device_id,
        channel_id=channel_id,
        sampled_at=sampled_at,
        source=source,
        source_flags=source_flags,
        p_w=p_w,
        energy_wh=energy_wh,
    ))


# ── pure: decode_source_flags / source_kind ────────────────────────────

def test_decode_source_flags_none_and_zero():
    assert device_power.decode_source_flags(None) == {"raw": 0, "bits_set": [], "names": []}
    assert device_power.decode_source_flags(0) == {"raw": 0, "bits_set": [], "names": []}


def test_decode_source_flags_decodes_set_bits():
    assert device_power.decode_source_flags(5) == {
        "raw": 5,
        "bits_set": [0, 2],
        "names": ["SYNTHETIC", "VOLTAGE_VALID"],
    }
    assert device_power.decode_source_flags(8) == {
        "raw": 8,
        "bits_set": [3],
        "names": ["CURRENT_VALID"],
    }


def test_decode_source_flags_clamps_negative_to_zero():
    assert device_power.decode_source_flags(-1) == {"raw": 0, "bits_set": [], "names": []}


def test_decode_source_flags_real_field_value_86():
    # source_flags=86 (0x56) is the real-world value observed on .185
    # with central+power both on: REAL + VOLTAGE_VALID + POWER_VALID + ENERGY_VALID.
    assert device_power.decode_source_flags(86) == {
        "raw": 86,
        "bits_set": [1, 2, 4, 6],
        "names": ["REAL", "VOLTAGE_VALID", "POWER_VALID", "ENERGY_VALID"],
    }


def test_decode_source_flags_unmapped_bit_in_bits_set_but_not_names():
    # If firmware ever sets a bit beyond the named table (currently 8 bits),
    # bits_set shows it but names omits — surface visibility over silent drop.
    assert device_power.decode_source_flags(0x100) == {
        "raw": 0x100,
        "bits_set": [8],
        "names": [],
    }


def test_source_kind_real_vs_synthetic():
    assert device_power.source_kind("steady") == "real"
    assert device_power.source_kind("burst") == "real"
    assert device_power.source_kind("synthetic") == "synthetic"
    assert device_power.source_kind(None) == "synthetic"
    assert device_power.source_kind("anything-else") == "synthetic"


# ── cost_rate_per_kwh ──────────────────────────────────────────────────

def test_cost_rate_unset_returns_none_and_default_currency(hub_db):
    rate, currency = device_power.cost_rate_per_kwh()
    assert rate is None
    assert currency == "USD"


def test_cost_rate_set_is_returned(hub_db):
    runtime_settings.set_(device_power.RATE_PER_KWH_KEY, "0.18")
    runtime_settings.set_(device_power.CURRENCY_KEY, "EUR")
    rate, currency = device_power.cost_rate_per_kwh()
    assert rate == 0.18
    assert currency == "EUR"


def test_cost_rate_negative_is_rejected(hub_db):
    runtime_settings.set_(device_power.RATE_PER_KWH_KEY, "-1.0")
    rate, _ = device_power.cost_rate_per_kwh()
    assert rate is None


def test_cost_rate_non_numeric_is_rejected(hub_db):
    runtime_settings.set_(device_power.RATE_PER_KWH_KEY, "not-a-number")
    rate, _ = device_power.cost_rate_per_kwh()
    assert rate is None


# ── latest_sample ──────────────────────────────────────────────────────

def test_latest_sample_none_when_no_samples(hub_db):
    assert device_power.latest_sample("dev-unknown") is None
    assert device_power.latest_sample("") is None


def test_latest_sample_returns_the_newest(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=30), p_w=30)
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=1), p_w=11)
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=15), p_w=20)
    latest = device_power.latest_sample("dev-a")
    assert latest is not None
    assert latest["p_w"] == 11.0


def test_latest_sample_respects_channel_id(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=1),
                    channel_id=0, p_w=5)
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=1),
                    channel_id=1, p_w=77)
    assert device_power.latest_sample("dev-a", channel_id=1)["p_w"] == 77.0


def test_latest_sample_stale_flag(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-stale", sampled_at=now - timedelta(minutes=10), p_w=9)
        _add_sample(s, "dev-fresh", sampled_at=now - timedelta(minutes=1), p_w=9)
    assert device_power.latest_sample("dev-stale")["is_stale"] is True
    assert device_power.latest_sample("dev-fresh")["is_stale"] is False


# ── latest_samples_by_device ───────────────────────────────────────────

def test_latest_samples_by_device_batch(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=9), p_w=1)
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=1), p_w=2)
        _add_sample(s, "dev-b", sampled_at=now - timedelta(minutes=2), p_w=3)
    out = device_power.latest_samples_by_device(["dev-a", "dev-b", "dev-none"])
    assert set(out) == {"dev-a", "dev-b"}  # dev-none has no samples → absent
    assert out["dev-a"]["p_w"] == 2.0
    assert out["dev-b"]["p_w"] == 3.0


def test_latest_samples_by_device_empty_input(hub_db):
    assert device_power.latest_samples_by_device([]) == {}


# ── recent_samples ─────────────────────────────────────────────────────

def test_recent_samples_window_filters_and_orders_newest_first(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=10), p_w=10)
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=30), p_w=30)
        _add_sample(s, "dev-a", sampled_at=now - timedelta(hours=2), p_w=99)
    rows = device_power.recent_samples("dev-a", window_seconds=3600)
    assert [r["p_w"] for r in rows] == [10.0, 30.0]  # 2h sample excluded


def test_recent_samples_source_filter(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=2),
                    source="steady", p_w=50)
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=3),
                    source="synthetic", p_w=99)
    rows = device_power.recent_samples("dev-a", source="steady")
    assert [r["source"] for r in rows] == ["steady"]


def test_recent_samples_empty_device_returns_empty_list(hub_db):
    assert device_power.recent_samples("") == []


# ── power_source_breakdown ─────────────────────────────────────────────

def test_power_source_breakdown_splits_real_and_synthetic(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        for i in range(3):
            _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=i + 1),
                        source="steady", p_w=10)
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=4),
                    source="burst", p_w=10)
        for i in range(2):
            _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=i + 5),
                        source="synthetic", p_w=10)
    breakdown = device_power.power_source_breakdown("dev-a")
    assert breakdown["total"] == 6
    assert breakdown["real"] == 4   # 3 steady + 1 burst
    assert breakdown["synthetic"] == 2
    assert breakdown["by_source"] == {"steady": 3, "burst": 1, "synthetic": 2}


# ── intraday_power_series ──────────────────────────────────────────────

def test_intraday_power_series_fixed_width_with_gaps(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-a", sampled_at=now - timedelta(hours=2), p_w=40)
        _add_sample(s, "dev-a", sampled_at=now - timedelta(hours=2, minutes=2), p_w=60)
    series = device_power.intraday_power_series("dev-a", buckets=144)
    assert len(series["buckets"]) == 144            # fixed width regardless of density
    assert series["point_count"] >= 1               # the populated slice(s)
    assert any(b["avg_w"] is None for b in series["buckets"])  # empty slices = gaps
    assert any(b["avg_w"] is not None for b in series["buckets"])


def test_intraday_power_series_empty_device(hub_db):
    assert device_power.intraday_power_series("") == {"window_seconds": 0, "buckets": []}


# ── fleet_summary ──────────────────────────────────────────────────────

def test_fleet_summary_aggregates_and_sorts_biggest_first(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        # dev-hog averages higher than dev-quiet.
        _add_sample(s, "dev-hog", sampled_at=now - timedelta(minutes=5), p_w=100)
        _add_sample(s, "dev-hog", sampled_at=now - timedelta(minutes=10), p_w=80)
        _add_sample(s, "dev-quiet", sampled_at=now - timedelta(minutes=5), p_w=5)
    summary = device_power.fleet_summary()
    assert summary["device_count"] == 2
    assert summary["per_device"][0]["device_id"] == "dev-hog"  # sorted desc by avg
    assert summary["per_device"][0]["avg_w"] == 90.0
    # No Device rows seeded → display_name falls back to the id.
    assert summary["per_device"][0]["display_name"] == "dev-hog"


def test_fleet_summary_hides_cost_when_no_rate_set(hub_db):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-a", sampled_at=now - timedelta(minutes=5), p_w=50)
    summary = device_power.fleet_summary()
    assert summary["rate_per_kwh"] is None
    assert summary["fleet_cost"] is None
    assert summary["per_device"][0]["cost_window"] is None
    # kWh is still estimated from avg watts even without a rate.
    assert summary["per_device"][0]["kwh_window"] is not None


# ── daily rollups + compute_daily_rollups ──────────────────────────────

def test_compute_daily_rollups_aggregates_one_day(hub_db):
    day = datetime(2026, 5, 12, tzinfo=timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-a", sampled_at=day + timedelta(hours=3), p_w=10)
        _add_sample(s, "dev-a", sampled_at=day + timedelta(hours=8), p_w=20)
        _add_sample(s, "dev-b", sampled_at=day + timedelta(hours=4), p_w=5)
        # A sample on the next day must not bleed into this rollup.
        _add_sample(s, "dev-a", sampled_at=day + timedelta(days=1, hours=1), p_w=999)

    stats = device_power.compute_daily_rollups(day=day)
    assert stats["device_count"] == 2
    assert stats["rollups_written"] == 2

    with session_scope() as s:
        rows = {r.device_id: r for r in s.scalars(select(DevicePowerRollup))}
        assert float(rows["dev-a"].avg_w) == 15.0
        assert rows["dev-a"].sample_count == 2


def test_compute_daily_rollups_is_idempotent(hub_db):
    day = datetime(2026, 5, 12, tzinfo=timezone.utc)
    with session_scope() as s:
        _add_sample(s, "dev-a", sampled_at=day + timedelta(hours=3), p_w=10)

    device_power.compute_daily_rollups(day=day)
    device_power.compute_daily_rollups(day=day)  # re-run upserts, never duplicates

    with session_scope() as s:
        count = s.scalar(select(func.count()).select_from(DevicePowerRollup))
        assert count == 1


def test_daily_rollups_for_device_newest_first(hub_db):
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as s:
        for days_ago, avg in ((1, 11), (2, 22), (3, 33)):
            s.add(DevicePowerRollup(
                device_id="dev-a",
                day_bucket=midnight - timedelta(days=days_ago),
                sample_count=10,
                avg_w=avg,
            ))
    rollups = device_power.daily_rollups_for_device("dev-a", days=7)
    assert [r["avg_w"] for r in rollups] == [11.0, 22.0, 33.0]  # newest day first


def test_fleet_daily_rollups_pivots_by_day_and_device(hub_db):
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as s:
        for dev, avg in (("dev-a", 10), ("dev-b", 20)):
            s.add(DevicePowerRollup(
                device_id=dev,
                day_bucket=midnight - timedelta(days=1),
                sample_count=5,
                avg_w=avg,
            ))
    fleet = device_power.fleet_daily_rollups(days=30)
    assert fleet["device_ids"] == ["dev-a", "dev-b"]
    assert len(fleet["day_keys"]) == 1
    assert fleet["avg_w_by_day"][0]["values"] == [10.0, 20.0]
