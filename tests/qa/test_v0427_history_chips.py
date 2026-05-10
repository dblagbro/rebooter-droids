"""v0.4.27 — History page action_prefix chip filter."""

from __future__ import annotations

import requests


def _login(base_url: str) -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": "dblagbro@gmail.com", "password": "Super*120120"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return s


def test_history_page_renders_chip_nav(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/history", timeout=10).text
    # Chip nav present
    assert 'aria-label="History categories"' in body
    # A representative sample of chips is rendered
    for label in (
        "Devices",
        "Watchdog rules",
        "Schedules",
        "Firmware",
        "Maintenance toggle",
        "Attention ack",
    ):
        assert f">{label}<" in body or f"{label}<" in body, f"missing chip: {label}"
    # "All" chip starts active
    assert 'aria-pressed="true"' in body
    # action_prefix query knob is documented in chip hrefs
    assert "action_prefix=watchdog_rule" in body


def test_history_action_prefix_filter_narrows_results(base_url):
    s = _login(base_url)
    # Unfiltered count vs prefix-filtered count
    all_body = s.get(f"{base_url}/app/history?limit=1000", timeout=10).text
    wd_body = s.get(
        f"{base_url}/app/history?action_prefix=watchdog_rule&limit=1000",
        timeout=10,
    ).text
    # Filter chip is now marked active in the filtered view
    assert "v3-chip-active" in wd_body
    # Sanity: every action shown on the filtered page starts with the
    # prefix. Cheap check — pluck out <code>...</code> action cells.
    import re
    actions = re.findall(r"<code>([^<]+)</code>", wd_body)
    assert actions, "expected at least one event row on history page"
    bad = [a for a in actions if not a.startswith("watchdog_rule.")]
    assert not bad, f"non-watchdog_rule actions leaked through filter: {bad[:5]}"
    # Filtered view ≤ unfiltered view
    assert wd_body.count("<tr>") <= all_body.count("<tr>")
