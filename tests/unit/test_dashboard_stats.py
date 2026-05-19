"""Unit tests — `dashboard.stats()` watchdog-rule counts (v0.5.96).

The status page's "active rules" tile hardcoded `0` with a stale
"watchdogs ship in P4" sub-label since v0.3.1. `dashboard.stats()` now
returns live `rules_total` / `rules_active` counts. DB-backed — takes
the `hub_db` fixture.
"""

from __future__ import annotations

from app.services.dashboard import stats
from app.services.watchdog import create_rule, set_enabled

_VALID = dict(
    probe={"kind": "internet"},
    target={"kind": "tag", "tag": "edge"},
    action={"kind": "notify_only"},
)


def test_rule_counts_are_zero_on_an_empty_fleet(hub_db):
    s = stats()
    assert s["rules_total"] == 0
    assert s["rules_active"] == 0


def test_rule_counts_reflect_created_rules(hub_db):
    create_rule(name="rule a", **_VALID)
    create_rule(name="rule b", **_VALID)
    disabled = create_rule(name="rule c", **_VALID)
    set_enabled(disabled["id"], False)

    s = stats()
    assert s["rules_total"] == 3
    # rules are created enabled; one was disabled → 2 active.
    assert s["rules_active"] == 2
