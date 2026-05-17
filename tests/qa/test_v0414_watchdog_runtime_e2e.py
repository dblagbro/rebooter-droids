"""v0.4.14 — watchdog runtime state machine (was a wall-clock e2e test).

Rewritten v0.5.82 (P-QA gate-3): the original waited ~25 s for the
real APScheduler tick, which flakes when the test's sleeps don't line
up with a tick boundary. `watchdog_runtime.tick()` now accepts an
injectable `now` (the seam), so this drives the rule state machine
in-process against an isolated SQLite DB — deterministic, no sleeps.

`now` is injected tz-aware (UTC); the watchdog runtime coerces the
naive datetimes SQLite hands back so the comparisons stay consistent.
"""

from __future__ import annotations

import socket
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import flask
import pytest

from app.config import load_settings
from app.db import get_engine, init_engine
from app.models import Base
from app.services import watchdog_runtime
from app.services.watchdog import create_rule, get_rule, list_recent_events

# v0.5.82: in the `-m ci` gate (P-QA gate-3 — timing e2e, now in-process).
pytestmark = pytest.mark.ci

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def hub_db(tmp_path):
    """Isolated SQLite hub DB + a bare Flask app context (the action
    dispatch may reach for Flask `g`). Mirrors test_v0514."""
    settings = replace(
        load_settings(),
        database_url=f"sqlite:///{tmp_path / 'rebooter-qa.sqlite'}",
    )
    init_engine(settings)
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with flask.Flask(__name__).app_context():
        yield settings


def test_failing_probe_fires_action_then_cooldown_skips(hub_db):
    """failure_threshold=1 → first failing tick fires the action;
    subsequent failing ticks within cooldown record cooldown_skip."""
    rule = create_rule(
        name="qa-e2e-fail",
        probe={"kind": "tcp", "host": "127.0.0.1", "port": 1},  # always refused
        target={"kind": "tag", "tag": "qa-e2e"},
        action={"kind": "notify_only"},
        failure_threshold=1,
        recovery_threshold=1,
        window_seconds=10,
        cooldown_seconds=30,
    )
    rid = rule["id"]

    # tick 1 — probe fails, threshold=1 crossed → action fires.
    watchdog_runtime.tick(now=T0)
    # ticks 2 + 3 — rule is due again (past window_seconds), still
    # failing, but inside cooldown_seconds=30 → cooldown_skip.
    watchdog_runtime.tick(now=T0 + timedelta(seconds=11))
    watchdog_runtime.tick(now=T0 + timedelta(seconds=22))

    outcomes = [e["outcome"] for e in list_recent_events(rid)]
    assert "failure" in outcomes, outcomes
    assert "action_fired" in outcomes, outcomes
    assert "cooldown_skip" in outcomes, outcomes

    r = get_rule(rid)
    assert r["status"] == "firing"
    assert r["failure_streak"] >= 1
    assert r["last_outcome"] is not None
    assert r["last_probed_at"] is not None
    assert r["last_action_at"] is not None


def test_succeeding_probe_keeps_rule_armed(hub_db):
    """A probe that succeeds never fires the action; the rule stays
    armed with failure_streak 0."""
    # A listening socket the tcp probe can connect to. listen()'s
    # backlog completes the handshake without an explicit accept().
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        rule = create_rule(
            name="qa-e2e-ok",
            probe={"kind": "tcp", "host": "127.0.0.1", "port": port},
            target={"kind": "tag", "tag": "qa-e2e"},
            action={"kind": "notify_only"},
            failure_threshold=1,
            recovery_threshold=1,
            window_seconds=10,
            cooldown_seconds=30,
        )
        rid = rule["id"]
        watchdog_runtime.tick(now=T0)
        watchdog_runtime.tick(now=T0 + timedelta(seconds=11))

        outcomes = [e["outcome"] for e in list_recent_events(rid)]
        assert "success" in outcomes, outcomes
        assert "action_fired" not in outcomes, outcomes
        assert "failure" not in outcomes, outcomes

        r = get_rule(rid)
        assert r["status"] == "armed"
        assert r["failure_streak"] == 0
    finally:
        srv.close()


def test_maintenance_window_suppresses_runtime_action(hub_db):
    """A failing probe inside a maintenance window records
    maintenance_skip — never failure / action_fired."""
    rule = create_rule(
        name="qa-e2e-maint",
        probe={"kind": "tcp", "host": "127.0.0.1", "port": 1},
        target={"kind": "tag", "tag": "qa-e2e"},
        action={"kind": "notify_only"},
        failure_threshold=1,
        recovery_threshold=1,
        window_seconds=10,
        cooldown_seconds=30,
        maintenance_windows=[{
            "start": (T0 - timedelta(minutes=5)).isoformat(),
            "end": (T0 + timedelta(minutes=5)).isoformat(),
        }],
    )
    rid = rule["id"]
    watchdog_runtime.tick(now=T0)
    watchdog_runtime.tick(now=T0 + timedelta(seconds=11))

    outcomes = [e["outcome"] for e in list_recent_events(rid)]
    assert "maintenance_skip" in outcomes, outcomes
    assert "action_fired" not in outcomes, outcomes
    assert "failure" not in outcomes, outcomes
