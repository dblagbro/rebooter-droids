"""Unit tests — the external-sensor source registry + sample reads.

`app/services/external_sensors/` — `_crud` (source registry),
`_query` (sample reads consumed by the watchdog integration probes)
and `_pollers.poll_all_due` (the APScheduler poll tick). Two BUG-059
sites are exercised: `_query.last_two_samples` (the `as_aware`
freshness check) and `_pollers.poll_all_due` (the `as_aware` due-check).
DB-backed → the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import session_scope
from app.models import ExternalSensorSample, ExternalSensorSource
from app.services.external_sensors import (
    create_source,
    delete_source,
    last_two_samples,
    latest_sample,
    latest_sample_for_topic,
    list_sources,
    poll_all_due,
    set_enabled,
)


def _sample(session, source_id, *, sampled_at, payload=None):
    session.add(ExternalSensorSample(
        source_id=source_id, sampled_at=sampled_at, payload=payload or {},
    ))


def _roku(display_name="Roku", host="10.0.0.5") -> str:
    return create_source(kind="roku", display_name=display_name, host=host)["id"]


# ── create_source / list / delete / set_enabled ───────────────────────

def test_create_source_roku(hub_db):
    src = create_source(kind="roku", display_name="Living Room", host="10.0.0.5")
    assert src["id"]
    assert src["kind"] == "roku"
    assert src["port"] == 8060  # roku default


def test_create_source_rejects_unknown_kind(hub_db):
    with pytest.raises(ValueError):
        create_source(kind="teleporter", display_name="X", host="10.0.0.5")


def test_create_source_rejects_blank_display_name(hub_db):
    with pytest.raises(ValueError):
        create_source(kind="roku", display_name="  ", host="10.0.0.5")


def test_create_source_roku_requires_host(hub_db):
    with pytest.raises(ValueError):
        create_source(kind="roku", display_name="No Host")


def test_list_sources(hub_db):
    _roku("A", "10.0.0.1")
    _roku("B", "10.0.0.2")
    assert len(list_sources()) == 2


def test_delete_source(hub_db):
    sid = _roku()
    assert delete_source(sid) is True
    assert list_sources() == []
    assert delete_source("ext_nope") is False


def test_set_enabled(hub_db):
    sid = _roku()
    assert set_enabled(sid, False) is True
    assert set_enabled("ext_nope", False) is False


# ── latest_sample ──────────────────────────────────────────────────────

def test_latest_sample_returns_newest_fresh(hub_db):
    sid = _roku()
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _sample(s, sid, sampled_at=now - timedelta(seconds=60),
                payload={"active_app": "old"})
        _sample(s, sid, sampled_at=now - timedelta(seconds=5),
                payload={"active_app": "new"})
    out = latest_sample(sid)
    assert out is not None
    assert out["payload"]["active_app"] == "new"


def test_latest_sample_none_when_stale(hub_db):
    sid = _roku()
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _sample(s, sid, sampled_at=now - timedelta(seconds=600), payload={})
    # default max_age is 120 s — a 10-minute-old sample is stale.
    assert latest_sample(sid) is None


def test_latest_sample_none_when_absent(hub_db):
    assert latest_sample(_roku()) is None


# ── last_two_samples (BUG-059 as_aware site) ───────────────────────────

def test_last_two_samples_returns_newer_older_pair(hub_db):
    sid = _roku()
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _sample(s, sid, sampled_at=now - timedelta(seconds=60),
                payload={"n": "older"})
        _sample(s, sid, sampled_at=now - timedelta(seconds=10),
                payload={"n": "newer"})
    pair = last_two_samples(sid)
    assert pair is not None
    newer, older = pair
    assert newer["payload"]["n"] == "newer"
    assert older["payload"]["n"] == "older"


def test_last_two_samples_none_with_a_single_sample(hub_db):
    sid = _roku()
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _sample(s, sid, sampled_at=now - timedelta(seconds=10), payload={})
    assert last_two_samples(sid) is None


def test_last_two_samples_none_when_newer_is_stale(hub_db):
    # BUG-059(A): the freshness check coerces the SQLite-naive
    # `sampled_at` via as_aware — without the fix this raised TypeError.
    sid = _roku()
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _sample(s, sid, sampled_at=now - timedelta(seconds=1300), payload={})
        _sample(s, sid, sampled_at=now - timedelta(seconds=1200), payload={})
    # default max_age is 600 s — even the newer sample is too old.
    assert last_two_samples(sid) is None


# ── latest_sample_for_topic ────────────────────────────────────────────

def test_latest_sample_for_topic_matches_topic(hub_db):
    sid = _roku()
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        _sample(s, sid, sampled_at=now - timedelta(seconds=10),
                payload={"topic": "home/door", "value": "open"})
        _sample(s, sid, sampled_at=now - timedelta(seconds=5),
                payload={"topic": "home/light", "value": "on"})
    out = latest_sample_for_topic(sid, "home/door")
    assert out is not None
    assert out["payload"]["value"] == "open"
    assert latest_sample_for_topic(sid, "home/nonexistent") is None


# ── poll_all_due (BUG-059 as_aware site) ───────────────────────────────

def test_poll_all_due_due_check(hub_db, monkeypatch):
    """The poll tick: a never-polled source and one polled longer ago
    than its interval are due; one polled recently is skipped. The
    `elapsed = now - as_aware(last_polled_at)` line is the BUG-059(A)
    site — backdated `last_polled_at` is naive on SQLite."""
    polled: list[str] = []
    monkeypatch.setattr(
        "app.services.external_sensors._pollers.poll_source",
        lambda sid: polled.append(sid) or {},
    )
    a = _roku("never-polled", "10.0.0.1")
    b = _roku("recent", "10.0.0.2")
    c = _roku("stale", "10.0.0.3")
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        # default poll_interval_seconds is 30
        s.get(ExternalSensorSource, b).last_polled_at = now - timedelta(seconds=5)
        s.get(ExternalSensorSource, c).last_polled_at = now - timedelta(seconds=90)

    stats = poll_all_due()
    assert stats["considered"] == 3
    assert set(polled) == {a, c}      # never-polled + stale are due
    assert b not in polled            # recent is skipped
    assert stats["skipped"] == 1
    assert stats["polled"] == 2
