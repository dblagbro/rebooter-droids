"""v0.5.2 — fix misleading "1 device · Pending adoption →" sub-line.

Pre-fix, the devices-list sub-header rendered:

    {{ devices|length }} device · Pending adoption →

which the eye reads as "1 device pending adoption" when there's
one device in the fleet view — but the "1" was the fleet-list
count and the link target was a separate page with its own (often
zero) count of pending announcements.

v0.5.2:
1. Adds `count_pending_announcements()` service helper.
2. Wires the count into the devices-list page context.
3. Restructures the sub-header so the count is BOUND to the link
   text: "Pending adoption: 0 →" (or N > 0). No more ambiguous
   middot juxtaposition.
"""

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


def test_pending_adoption_count_bound_to_link(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/devices", timeout=10).text
    # The new link text format is "Pending adoption: <N> →"
    import re
    m = re.search(r"Pending adoption:\s*(\d+)\s*→", body)
    assert m, "v0.5.2 sub-header format missing — count not bound to link"
    pending = int(m.group(1))

    # Cross-check against the actual /app/pending-adoption page
    body_padop = s.get(f"{base_url}/app/pending-adoption", timeout=10).text
    if pending == 0:
        # Page should render the "No pending devices" empty state
        assert "No pending devices" in body_padop
    else:
        # Page should NOT render the empty state if count > 0
        assert "No pending devices" not in body_padop, (
            f"sub-header claims {pending} pending but page shows none"
        )


def test_fleet_count_unambiguously_labeled(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/devices", timeout=10).text
    # The fleet count line now ends with "in fleet" so it can't be
    # misread as "pending"
    import re
    m = re.search(r"(\d+)\s+devices?\s+in fleet", body)
    assert m, "fleet-count line missing 'in fleet' qualifier"
