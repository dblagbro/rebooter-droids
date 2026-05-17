"""Unit tests — the unregistered-auth-attempt tracker.

`app/services/unregistered.py` records device-auth 401s so unregistered
firmware is visible. It exercises two BUG-059 fixes: the
`UnregisteredAuthAttempt.id` `BigInteger`→SQLite-`Integer` variant (PK
autoincrement) and the dialect-branched `ON CONFLICT` upsert. DB-backed
→ the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import session_scope
from app.models import UnregisteredAuthAttempt
from app.services import unregistered


def test_record_creates_row_with_autoincrement_id(hub_db):
    # BUG-059(B): the BigInteger PK must autoincrement on SQLite.
    unregistered.record(
        claimed_device_id="dev-x", source_ip="10.0.0.1",
        endpoint="/heartbeat", user_agent="curl/8", auth_present=False,
    )
    with session_scope() as s:
        rows = list(s.scalars(select(UnregisteredAuthAttempt)))
        assert len(rows) == 1
        assert rows[0].id is not None
        assert rows[0].hit_count == 1


def test_record_upserts_repeat_of_same_tuple(hub_db):
    # BUG-059(C): the dialect-branched ON CONFLICT — a repeat of the
    # same (device_id, ip, endpoint) bumps hit_count, never duplicates.
    for _ in range(3):
        unregistered.record(
            claimed_device_id="dev-x", source_ip="10.0.0.1",
            endpoint="/heartbeat", user_agent="curl/8", auth_present=False,
        )
    with session_scope() as s:
        rows = list(s.scalars(select(UnregisteredAuthAttempt)))
        assert len(rows) == 1
        assert rows[0].hit_count == 3


def test_record_distinct_endpoint_is_a_separate_row(hub_db):
    unregistered.record(
        claimed_device_id="dev-x", source_ip="10.0.0.1",
        endpoint="/heartbeat", user_agent=None, auth_present=False,
    )
    unregistered.record(
        claimed_device_id="dev-x", source_ip="10.0.0.1",
        endpoint="/commands", user_agent=None, auth_present=False,
    )
    with session_scope() as s:
        assert s.scalar(
            select(func.count()).select_from(UnregisteredAuthAttempt)
        ) == 2


def test_record_null_device_id_inserts(hub_db):
    unregistered.record(
        claimed_device_id=None, source_ip="10.0.0.2",
        endpoint="/heartbeat", user_agent=None, auth_present=False,
    )
    rows = unregistered.list_recent()
    assert len(rows) == 1
    assert rows[0]["claimed_device_id"] is None


def test_record_never_raises_on_odd_input(hub_db):
    # Best-effort contract — record() must swallow + log, never raise
    # into the auth/middleware path. Blank/None inputs are sanitised.
    unregistered.record(
        claimed_device_id=None, source_ip=None,
        endpoint="", user_agent=None, auth_present=False,
    )
    # ip/endpoint fall back to "unknown" — one row, no exception.
    assert len(unregistered.list_recent()) == 1


def test_list_recent_returns_recorded_rows(hub_db):
    unregistered.record(
        claimed_device_id="a", source_ip="1.1.1.1", endpoint="/x",
        user_agent=None, auth_present=False,
    )
    unregistered.record(
        claimed_device_id="b", source_ip="2.2.2.2", endpoint="/y",
        user_agent=None, auth_present=True,
    )
    rows = unregistered.list_recent()
    assert {r["claimed_device_id"] for r in rows} == {"a", "b"}


def test_count_active_counts_recent_attempts(hub_db):
    unregistered.record(
        claimed_device_id="a", source_ip="1.1.1.1", endpoint="/x",
        user_agent=None, auth_present=False,
    )
    assert unregistered.count_active(since_minutes=60) == 1
