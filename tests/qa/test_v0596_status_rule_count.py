"""v0.5.96 — status page "active rules" tile shows a live count.

The status page totals row hardcoded `0` active rules with a stale
"watchdogs ship in P4" sub-label since v0.3.1 — watchdogs shipped long
ago. The tile now reads `dashboard.stats()`'s live `rules_active` /
`rules_total` counts.

Verified against a live instance. Auth: Bearer headers (the `-m ci`
gate runs over `http://localhost`, no Secure cookie). Runs in `-m ci`.
"""

from __future__ import annotations

import re

import pytest
import requests

from .conftest import unique_suffix

pytestmark = pytest.mark.ci

# the rules tile: `<div class="num">N</div>` immediately followed by the
# `active rules` label — pins the count to the right tile.
_RULES_TILE = re.compile(
    r'<div class="num">(\d+)</div>\s*<div class="label">active rules</div>'
)


def _rules_tile_count(body: str) -> int:
    m = _RULES_TILE.search(body)
    assert m is not None, "status page has no 'active rules' tile"
    return int(m.group(1))


def test_status_page_has_no_stale_p4_copy(base_url, admin_headers):
    r = requests.get(f"{base_url}/app/", headers=admin_headers, timeout=10)
    assert r.status_code == 200, r.text
    body = r.text
    # the stale placeholder sub-label is gone
    assert "watchdogs ship in P4" not in body
    # the tile is still there, still links to the rules page
    assert "active rules" in body
    assert "/app/rules" in body


def test_status_tile_reflects_a_created_rule(base_url, admin_headers):
    """Creating a rule moves the live count — the tile is wired to the
    backend, not hardcoded."""
    before = requests.get(f"{base_url}/app/", headers=admin_headers, timeout=10)
    assert before.status_code == 200, before.text
    count_before = _rules_tile_count(before.text)

    name = f"qa0596-{unique_suffix()}"
    created = requests.post(
        f"{base_url}/api/v1/admin/rules",
        json={
            "name": name,
            "probe": {"kind": "internet"},
            "target": {"kind": "tag", "tag": "qa0596"},
            "action": {"kind": "notify_only"},
        },
        headers=admin_headers,
        timeout=10,
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["data"]["id"]
    try:
        after = requests.get(f"{base_url}/app/", headers=admin_headers, timeout=10)
        assert after.status_code == 200, after.text
        assert _rules_tile_count(after.text) == count_before + 1
    finally:
        requests.delete(f"{base_url}/api/v1/admin/rules/{rule_id}",
                        headers=admin_headers, timeout=10)
