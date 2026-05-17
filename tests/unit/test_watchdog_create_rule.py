"""Unit tests — watchdog `create_rule` validation.

`app/services/watchdog.py::create_rule` validates the probe / target /
action shapes before insert and raises `WatchdogValidationError` on bad
input. DB-backed (the happy path inserts a row) so these take the
`hub_db` fixture.
"""

from __future__ import annotations

import pytest

from app.services.watchdog import WatchdogValidationError, create_rule

_VALID = dict(
    probe={"kind": "internet"},
    target={"kind": "tag", "tag": "edge"},
    action={"kind": "notify_only"},
)


def test_rejects_unknown_probe_kind(hub_db):
    with pytest.raises(WatchdogValidationError):
        create_rule(name="qa", **{**_VALID, "probe": {"kind": "bogus"}})


def test_rejects_unknown_target_kind(hub_db):
    with pytest.raises(WatchdogValidationError):
        create_rule(name="qa", **{**_VALID, "target": {"kind": "bogus"}})


def test_rejects_unknown_action_kind(hub_db):
    with pytest.raises(WatchdogValidationError):
        create_rule(name="qa", **{**_VALID, "action": {"kind": "bogus"}})


def test_rejects_empty_name(hub_db):
    with pytest.raises(WatchdogValidationError):
        create_rule(name="   ", **_VALID)


def test_rejects_overlong_name(hub_db):
    with pytest.raises(WatchdogValidationError):
        create_rule(name="x" * 121, **_VALID)


def test_creates_a_valid_rule(hub_db):
    rule = create_rule(name="qa valid rule", **_VALID)
    assert rule["id"].startswith("wdr_")
    assert rule["name"] == "qa valid rule"
    assert rule["probe"]["kind"] == "internet"
    assert rule["enabled"] is True
    # A freshly created rule has not been probed yet.
    assert rule["failure_streak"] == 0
    assert rule["last_probed_at"] is None
