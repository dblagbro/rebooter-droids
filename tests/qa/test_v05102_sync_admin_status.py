"""v0.5.102 — admin-Bearer-auth sync status endpoint.

The wire-side `/api/v1/sync/status` requires HMAC peer auth (same path
the replicator uses), so the dual-hub preflight script and any
external operator tooling can't query it directly with an admin token.
v0.5.102 adds `/api/v1/admin/sync/status` as an admin-Bearer-auth
wrapper that exposes the same outbox + cursor data plus the
`sync.enabled` flag, configured peer count, and the "HMAC key set"
boolean (never the key itself).

Verified against a live instance. Runs in the `-m ci` gate.
"""

from __future__ import annotations

import json

import pytest
import requests

from .conftest import unique_suffix

pytestmark = pytest.mark.ci


def _status(base_url, admin_headers):
    r = requests.get(
        f"{base_url}/api/v1/admin/sync/status",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True, body
    return body["data"]


def test_endpoint_returns_the_documented_shape(base_url, admin_headers):
    data = _status(base_url, admin_headers)
    # Every documented field must be present so the preflight script
    # and any future tooling can rely on the contract.
    for field in ("enabled", "hub_id", "hmac_key_set",
                  "peer_hubs", "outbox", "cursors"):
        assert field in data, f"missing field: {field} (got {sorted(data)})"
    assert isinstance(data["enabled"], bool)
    assert isinstance(data["hub_id"], str)
    assert isinstance(data["hmac_key_set"], bool)
    assert isinstance(data["peer_hubs"], list)
    assert isinstance(data["cursors"], list)
    assert set(data["outbox"]) >= {"max_seq", "total_events"}


def test_sync_enabled_is_false_by_default(base_url, admin_headers):
    """The B11 safety property — sync.enabled MUST default to false on
    a fresh instance. The preflight + the runbook depend on this."""
    data = _status(base_url, admin_headers)
    assert data["enabled"] is False, (
        "sync.enabled must be False by default — flipping it on is an "
        "operator-gated decision per docs/runbooks/sync-enable.md"
    )


def test_hmac_key_field_is_a_boolean_not_the_key(base_url, admin_headers):
    """The HMAC key is a credential — the endpoint must NEVER return
    the key itself, only a boolean for whether one is set."""
    data = _status(base_url, admin_headers)
    # Booleans serialize to true/false in JSON.
    assert data["hmac_key_set"] in (True, False)
    # Belt-and-braces: confirm no field on the response carries a
    # 64-char hex string (the HMAC key shape).
    blob = json.dumps(data)
    import re
    matches = re.findall(r"\b[0-9a-fA-F]{64}\b", blob)
    assert not matches, (
        f"the sync-status response leaked something that looks like an "
        f"HMAC key: {matches}"
    )


def test_endpoint_requires_admin_auth(base_url):
    r = requests.get(f"{base_url}/api/v1/admin/sync/status", timeout=10)
    # No token → 401 (or 403 if RBAC kicks in first).
    assert r.status_code in (401, 403), r.text


def test_peer_hub_entries_only_expose_id_and_url(base_url, admin_headers):
    """Each peer entry should only carry `id` + `url`. If a peer config
    grew (e.g. a per-peer secret), the admin status endpoint must keep
    the credential surface tight. Field name MUST be `url`, not
    `base_url` — the replicator's `_get_peer_hubs()` reads
    `peer.get("url")` and the production-truth peer config uses `url`."""
    data = _status(base_url, admin_headers)
    for peer in data["peer_hubs"]:
        assert isinstance(peer, dict)
        assert set(peer.keys()) <= {"id", "url"}, (
            f"peer entry leaked unexpected keys: {sorted(peer.keys())}"
        )


def test_seeded_peer_url_round_trips(base_url, admin_headers):
    """Seed a peer entry via the settings form, then confirm the admin
    status endpoint returns the URL under the right field name. This
    is the test that would catch a `base_url` vs `url` field-name
    drift — without seeded peer data the earlier round-trip test
    passes vacuously on an empty `peer_hubs` list."""
    import json as _json
    seed_url = f"https://qa-{unique_suffix()}.example.invalid/rebooter"
    seed_peer = {"id": "qa-preflight-peer", "url": seed_url}
    # Reuse the existing UI settings handler — it's the only writer.
    r = requests.post(
        f"{base_url}/app/settings/sync",
        data={
            "sync_enabled": "false",   # never touch the production flag
            "hub_id": "www",           # required field
            "hmac_key": "",            # leave existing value untouched
            "peer_hubs": _json.dumps([seed_peer]),
        },
        headers=admin_headers,
        allow_redirects=False,
        timeout=10,
    )
    assert r.status_code in (302, 303), r.text
    try:
        data = _status(base_url, admin_headers)
        assert any(
            p.get("id") == "qa-preflight-peer" and p.get("url") == seed_url
            for p in data["peer_hubs"]
        ), f"seeded peer did not round-trip: {data['peer_hubs']}"
    finally:
        # Restore an empty peer list so the test isn't sticky.
        requests.post(
            f"{base_url}/app/settings/sync",
            data={"sync_enabled": "false", "hub_id": "www",
                  "hmac_key": "", "peer_hubs": "[]"},
            headers=admin_headers,
            allow_redirects=False,
            timeout=10,
        )
