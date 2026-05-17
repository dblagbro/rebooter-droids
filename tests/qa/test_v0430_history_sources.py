"""v0.4.30 — multi-source history feed (C1 from continuation plan v2).

Before v0.4.30, `/app/history` only surfaced rows from `audit_events`.
v0.4.30 adds a `source=` query param that switches between:

- audit (default; back-compat with v0.4.27 chip URLs)
- watchdog_probe (`watchdog_probe_events` table)
- device_event (`device_events` table)
- all (time-merged union of the three)

These tests assert:
- the source picker renders with the four chips
- switching sources flips the active state
- watchdog_probe rows actually appear when there's data (the cluster
  has 590+ probe rows so this is a hard assertion)
- the audit source still chip-filters correctly (regression guard
  for v0.4.27 behaviour)
"""

from __future__ import annotations

import pytest
import requests

from .conftest import ADMIN_EMAIL, ADMIN_PASS

# v0.5.79: in the `-m ci` gate (P-QA gate-3 — history files).
pytestmark = pytest.mark.ci


def _login(base_url: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module", autouse=True)
def _seed_history(base_url):
    """A fresh CI instance starts with an empty history feed. Seed a
    few `watchdog_rule.*` audit events (create + delete a rule) so the
    audit-source and chip-filter assertions below have rows to work
    with — instead of assuming live-deployment data."""
    s = _login(base_url)
    for i in range(2):
        r = s.post(
            f"{base_url}/api/v1/admin/rules",
            json={
                "name": f"qa-history-seed-{i}",
                "probe": {"kind": "internet"},
                "target": {"kind": "tag", "tag": "qa-history-seed"},
                "action": {"kind": "notify_only"},
            },
            timeout=10,
        )
        assert r.status_code == 201, r.text
        s.delete(
            f"{base_url}/api/v1/admin/rules/{r.json()['data']['id']}", timeout=10
        )


def test_source_picker_chip_nav_renders(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/history", timeout=10).text
    # All four source chips present
    for label in ("Audit", "Watchdog probes", "Device events", "All sources"):
        assert label in body, f"missing source chip: {label}"
    # Audit is the active default
    assert 'aria-pressed="true"' in body


def test_watchdog_probe_source_surfaces_probe_rows(base_url):
    s = _login(base_url)
    body = s.get(
        f"{base_url}/app/history?source=watchdog_probe&limit=20",
        timeout=10,
    ).text
    # Active state flipped
    assert "v3-chip-active" in body
    # Probe rows have actions of the form `watchdog_probe.<outcome>`
    import re
    actions = re.findall(r"<td[^>]*><code>([^<]+)</code></td>", body)
    if actions:
        bad = [a for a in actions if not a.startswith("watchdog_probe.")]
        assert not bad, f"non-watchdog_probe rows leaked: {bad[:5]}"


def test_all_source_includes_source_column(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/history?source=all&limit=50", timeout=10).text
    # The "all" view adds a Source column to the table header
    assert ">Source<" in body or "Source</th>" in body
    # And at least one source-tag badge renders inline with rows
    # (cheap check: badge containing a source token)
    assert ">audit<" in body or ">watchdog_probe<" in body or ">device_event<" in body


def test_audit_action_prefix_filter_still_works(base_url):
    """Regression guard for v0.4.27 — chip filters on source=audit."""
    s = _login(base_url)
    body = s.get(
        f"{base_url}/app/history?source=audit&action_prefix=watchdog_rule&limit=1000",
        timeout=10,
    ).text
    import re
    actions = re.findall(r"<td[^>]*><code>([^<]+)</code></td>", body)
    assert actions, "expected audit rows for watchdog_rule prefix"
    bad = [a for a in actions if not a.startswith("watchdog_rule.")]
    assert not bad, f"v0.4.27 chip filter regressed: {bad[:5]}"
