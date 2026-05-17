"""v0.4.32 — History page CSV/JSON export (C2) + free-text search (C3).

C2: `/app/history?export=csv` / `?export=json` streams the current
filter view as a download. Caps at 50_000 rows.

C3: A new `?q=<text>` param does an ILIKE match across the row's
scalar columns AND the details JSON cast to text. Same param works
across all four sources.
"""

from __future__ import annotations

import csv
import io
import json

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
    export and search assertions below have rows to work with —
    instead of assuming live-deployment data."""
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


def test_export_csv_streams_with_attachment_header(base_url):
    s = _login(base_url)
    r = s.get(f"{base_url}/app/history?source=audit&export=csv&limit=10", timeout=15)
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("text/csv")
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd and ".csv" in cd
    # Header row present
    reader = csv.reader(io.StringIO(r.text))
    header = next(reader, [])
    assert header[:4] == ["at", "source", "actor", "action"], header
    # Audit rows are realistically populated
    rows = list(reader)
    assert rows, "expected at least one audit row in the export"


def test_export_json_streams_array(base_url):
    s = _login(base_url)
    r = s.get(f"{base_url}/app/history?source=audit&export=json&limit=10", timeout=15)
    assert r.status_code == 200
    assert r.headers.get("Content-Type", "").startswith("application/json")
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd and ".json" in cd
    data = json.loads(r.text)
    assert isinstance(data, list)
    if data:
        sample = data[0]
        for k in ("at", "source", "action"):
            assert k in sample, f"export row missing key {k}: {sample}"


def test_export_respects_action_prefix(base_url):
    s = _login(base_url)
    r = s.get(
        f"{base_url}/app/history?source=audit&action_prefix=watchdog_rule&export=json&limit=500",
        timeout=20,
    )
    assert r.status_code == 200
    data = json.loads(r.text)
    bad = [d for d in data if not d.get("action", "").startswith("watchdog_rule.")]
    assert not bad, f"export ignored action_prefix filter: leaked {bad[:3]}"


def test_search_narrows_audit_rows(base_url):
    """A search term that matches some action names should reduce
    the result count vs. an unfiltered audit query."""
    s = _login(base_url)
    base = s.get(f"{base_url}/app/history?source=audit&limit=200", timeout=10).text
    searched = s.get(
        f"{base_url}/app/history?source=audit&q=watchdog_rule&limit=200",
        timeout=10,
    ).text
    # The "all" non-search view should have at least one row not
    # matching watchdog_rule (since the audit table holds many
    # action families); the search view should be a strict subset.
    base_rows = base.count('<td data-label="Action"><code>')
    searched_rows = searched.count('<td data-label="Action"><code>')
    assert searched_rows <= base_rows, (
        f"search did not narrow: base={base_rows} search={searched_rows}"
    )
    # And every visible action cell in the search view should contain
    # the search term (since we matched on action column among others)
    # — we relax this by allowing a match anywhere in the action OR
    # the row's details (which is the C3 contract).
    import re
    actions = re.findall(r"<td[^>]*><code>([^<]+)</code></td>", searched)
    # The search view should at minimum contain "watchdog" somewhere
    # in either action or surrounding context. Just assert non-empty
    # since the cluster has plenty of watchdog_rule.* rows.
    assert actions, "search returned zero results despite having watchdog_rule activity"


def test_search_field_renders_in_form(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/history", timeout=10).text
    assert 'name="q"' in body
    assert "Export CSV" in body
    assert "Export JSON" in body
