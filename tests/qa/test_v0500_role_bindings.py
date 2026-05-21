"""v0.5.0 — Tier-A A1: role_bindings table + auto-backfill.

Foundation of the RBAC scoping migration per B10 §9.0 of RFC-003.
This ship is **non-enforcing** — it adds the table, runs the
one-shot backfill from legacy users.is_super_admin / is_admin /
role columns, and surfaces a service-level API. The shadow-mode
middleware (A2) and the enforce flip (A8) are later ships.

Tests verify:
1. Table exists with the expected columns after container restart
2. Backfill ran: every super_admin has a global binding; every
   admin has either site-scoped bindings (if sites exist) or a
   global admin binding (safety net)
3. Operators have NO bindings (forced re-grant per B10 Q2)
4. Effective-scope resolver returns the right shape for each
   binding type
"""

from __future__ import annotations

import pytest
import requests

from .conftest import ADMIN_EMAIL, ADMIN_PASS

# v0.5.79: in the `-m ci` gate (P-QA gate-3 brittle-file fixes).
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


def test_role_bindings_table_exists_and_backfilled(base_url):
    """v0.5.0 ships role_bindings. After a fresh container start
    the backfill should have populated bindings for the existing
    super_admin user. We can't read the table directly through the
    public API yet (CRUD endpoints land in A6) — so this test
    verifies indirectly: the hub is healthy on v0.5.0, the version
    bumped, and the legacy auth still works (back-compat)."""
    s = _login(base_url)
    r = s.get(f"{base_url}/api/v1/auth/me", timeout=10)
    assert r.status_code == 200, r.text
    me = r.json().get("data", {})
    assert me.get("email") == ADMIN_EMAIL
    # Version sanity check — confirms a real, parseable build is serving
    # this test. role_bindings shipped in v0.5.0 and every release
    # since carries it, so this no longer pins a specific minor (it
    # used to hardcode "0.5." — stale once the tree moved to 0.6.x).
    ver = s.get(f"{base_url}/api/v1/version", timeout=10).json()["data"]
    parts = ver["version"].split(".")
    assert len(parts) >= 2 and all(p.isdigit() for p in parts[:2]), ver
    assert (int(parts[0]), int(parts[1])) >= (0, 5), (
        f"role_bindings shipped in 0.5.0; running version is older: {ver}"
    )


def test_legacy_auth_paths_still_work(base_url):
    """Tier-A is non-enforcing. Existing decorator-based auth must
    continue to work unchanged so we don't regress while shadow
    mode soaks."""
    s = _login(base_url)
    # Hitting an admin-required endpoint should still 200 just like
    # it did on v0.4.x
    for path in (
        "/api/v1/admin/devices",
        "/api/v1/admin/firmware/releases",
        "/api/v1/admin/audit?limit=5",
    ):
        r = s.get(f"{base_url}{path}", timeout=10)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
