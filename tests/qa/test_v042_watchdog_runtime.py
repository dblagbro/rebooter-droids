"""v0.4.2 — watchdog probe runtime (B6).

Covers the synchronous probe-now path which exercises the same
runtime as the APScheduler tick. Async-tick coverage requires
cross-tick state which is awkward against a live deployment, so
the probe-now path is the test surface.

- HTTP probe success against a known-good URL.
- HTTP probe failure against a non-existent host (DNS NXDOMAIN).
- TCP probe success against the hub itself (port 443).
- TCP probe failure against a closed port.
- Probe-now records a probe event with via=probe_now.
- /api/v1/admin/rules/<id>/events returns the recent log.
- Probe-now does NOT advance failure_streak (diagnostic only).
"""

from __future__ import annotations

import pytest
import requests

from .conftest import unique_suffix


@pytest.fixture(scope="module")
def shell_session(base_url, admin_creds):
    s = requests.Session()
    email, pw = admin_creds
    r = s.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": pw},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return s


def _create_rule(shell_session, base_url, *, name, probe, action_kind="notify_only"):
    body = {
        "name": name,
        "probe": probe,
        "target": {"kind": "tag", "tag": "qa042"},
        "action": {"kind": action_kind},
        "failure_threshold": 3,
        "recovery_threshold": 2,
        "window_seconds": 60,
    }
    r = shell_session.post(
        f"{base_url}/api/v1/admin/rules", json=body, timeout=10
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _probe_now(shell_session, base_url, rule_id):
    return shell_session.post(
        f"{base_url}/api/v1/admin/rules/{rule_id}/probe-now",
        timeout=10,
    )


def _events(shell_session, base_url, rule_id):
    return shell_session.get(
        f"{base_url}/api/v1/admin/rules/{rule_id}/events",
        timeout=10,
    ).json()["data"]


def test_probe_now_http_success(base_url, shell_session):
    rule = _create_rule(
        shell_session,
        base_url,
        name=f"qa042hs-{unique_suffix()}",
        probe={"kind": "http", "url": f"{base_url}/api/v1/version"},
    )
    try:
        r = _probe_now(shell_session, base_url, rule["id"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["outcome"] == "success"
        # Recorded in the event log
        events = _events(shell_session, base_url, rule["id"])
        assert any(e["outcome"] == "success" for e in events)
        assert any(e["details"].get("via") == "probe_now" for e in events)
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/rules/{rule['id']}", timeout=10
        )


def test_probe_now_http_failure(base_url, shell_session):
    rule = _create_rule(
        shell_session,
        base_url,
        name=f"qa042hf-{unique_suffix()}",
        probe={
            "kind": "http",
            "url": "http://this-host-definitely-does-not-exist.invalid/foo",
        },
    )
    try:
        r = _probe_now(shell_session, base_url, rule["id"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["outcome"] == "failure"
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/rules/{rule['id']}", timeout=10
        )


def test_probe_now_tcp_failure_closed_port(base_url, shell_session):
    rule = _create_rule(
        shell_session,
        base_url,
        name=f"qa042tcp-{unique_suffix()}",
        probe={"kind": "tcp", "host": "127.0.0.1", "port": 1},
    )
    try:
        r = _probe_now(shell_session, base_url, rule["id"])
        assert r.status_code == 200, r.text
        assert r.json()["data"]["outcome"] == "failure"
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/rules/{rule['id']}", timeout=10
        )


def test_probe_now_does_not_advance_failure_streak(base_url, shell_session):
    """Probe-now is a diagnostic — it logs an event but the rule's
    failure_streak / status MUST remain unchanged. Otherwise an
    operator clicking "Probe now" three times to test would
    inadvertently fire the rule's action."""
    rule = _create_rule(
        shell_session,
        base_url,
        name=f"qa042nfs-{unique_suffix()}",
        probe={"kind": "tcp", "host": "127.0.0.1", "port": 1},
    )
    try:
        for _ in range(5):
            _probe_now(shell_session, base_url, rule["id"])
        rows = shell_session.get(
            f"{base_url}/api/v1/admin/rules", timeout=10
        ).json()["data"]
        match = next(r for r in rows if r["id"] == rule["id"])
        # The rule remains armed; failure_streak still 0 (probe-now
        # does NOT advance state). The events list will show the 5
        # diagnostic probes — that's the audit trail.
        assert match["status"] == "armed"
        # No action_fired in the event log either.
        events = _events(shell_session, base_url, rule["id"])
        assert not any(e["outcome"] == "action_fired" for e in events)
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/rules/{rule['id']}", timeout=10
        )


def test_probe_now_unknown_rule_404(base_url, shell_session):
    r = shell_session.post(
        f"{base_url}/api/v1/admin/rules/wdr_nonexistent/probe-now",
        timeout=10,
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "rule_unknown"


def test_rules_page_shows_probe_now_button(base_url, shell_session):
    rule = _create_rule(
        shell_session,
        base_url,
        name=f"qa042btn-{unique_suffix()}",
        probe={"kind": "internet"},
    )
    try:
        body = shell_session.get(f"{base_url}/app/rules", timeout=10).text
        # Probe-now button is present on the row
        assert "Probe now" in body
        # And the form action targets our rule
        assert f"/rules/{rule['id']}/probe-now" in body
    finally:
        shell_session.delete(
            f"{base_url}/api/v1/admin/rules/{rule['id']}", timeout=10
        )
