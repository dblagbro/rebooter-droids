"""Unit tests — the device-event ingest/query service.

`app/services/events.py::ingest_events` / `query_events` over the
`device_events` table. Exercises the BUG-059(B) fix —
`DeviceEvent.id` `BigInteger`→SQLite-`Integer` variant so the PK
autoincrements on the in-process backend. DB-backed → `hub_db`.
"""

from __future__ import annotations

import pytest

from app.services import events


def test_ingest_events_inserts_and_returns_count(hub_db):
    n = events.ingest_events("dev-1", [
        {"type": "boot", "message": "started"},
        {"type": "relay_on"},
    ])
    assert n == 2
    rows = events.query_events(device_id="dev-1")
    assert len(rows) == 2
    # BUG-059(B): every row got an autoincremented id.
    assert all(r["id"] is not None for r in rows)


def test_ingest_events_empty_list_is_noop(hub_db):
    assert events.ingest_events("dev-1", []) == 0


def test_ingest_events_rejects_oversize_batch(hub_db):
    with pytest.raises(ValueError):
        events.ingest_events("dev-1", [{"type": "x"}] * (events.MAX_BATCH + 1))


def test_ingest_events_parses_iso_timestamp(hub_db):
    events.ingest_events("dev-1", [{"type": "boot",
                                    "timestamp": "2026-05-17T08:00:00Z"}])
    rows = events.query_events(device_id="dev-1")
    assert rows[0]["timestamp"] == "2026-05-17T08:00:00Z"


def test_ingest_events_bad_timestamp_falls_back_to_now(hub_db):
    # A malformed timestamp must not crash ingest — it falls back to now.
    events.ingest_events("dev-1", [{"type": "boot", "timestamp": "garbage"}])
    rows = events.query_events(device_id="dev-1")
    assert rows[0]["timestamp"] is not None


def test_query_events_filters_by_type(hub_db):
    events.ingest_events("dev-1", [{"type": "boot"}, {"type": "relay_on"}])
    assert len(events.query_events(device_id="dev-1", type_="boot")) == 1


def test_query_events_filters_by_device(hub_db):
    events.ingest_events("dev-1", [{"type": "boot"}])
    events.ingest_events("dev-2", [{"type": "boot"}])
    assert len(events.query_events(device_id="dev-2")) == 1


def test_query_events_honours_limit(hub_db):
    events.ingest_events("dev-1", [{"type": f"e{i}"} for i in range(10)])
    assert len(events.query_events(device_id="dev-1", limit=3)) == 3
