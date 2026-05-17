"""v0.4.0 — Watchdog rules first slice (P4 of webui-redesign-plan).

v0.4.0 ships the data-model + CRUD + plain-English render. The
probe runtime that actually executes rules + writes events is
queued for v0.4.1+ — these tests cover ONLY:

- Create / list / delete rules via the admin API.
- Validation: bad probe.kind / target.kind / action.kind → 400.
- Plain-English sentence render covers all known probe + action
  + target kinds.
- Rule list page renders the form + sentence + per-rule actions.
- The page advertises rules-do-not-fire-yet so operators know.

Tests run against the live deployment.
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


def _create_rule(
    shell_session,
    base_url: str,
    *,
    name: str,
    probe: dict,
    target: dict,
    action: dict,
    **kwargs,
) -> dict:
    body = {"name": name, "probe": probe, "target": target, "action": action, **kwargs}
    r = shell_session.post(
        f"{base_url}/api/v1/admin/rules", json=body, timeout=10
    )
    return r


def _delete_rule(shell_session, base_url: str, rule_id: str) -> None:
    shell_session.delete(
        f"{base_url}/api/v1/admin/rules/{rule_id}", timeout=10
    )


# ── happy path ────────────────────────────────────────────────────────────


def test_create_then_list_then_delete(base_url, shell_session):
    name = f"qa040-{unique_suffix()}"
    r = _create_rule(
        shell_session,
        base_url,
        name=name,
        probe={"kind": "ping", "host": "192.168.1.1"},
        target={"kind": "tag", "tag": "qa040"},
        action={"kind": "cycle", "power_off_seconds": 5},
    )
    assert r.status_code == 201, r.text
    rule_id = r.json()["data"]["id"]
    sentence = r.json()["data"]["sentence"]
    try:
        # Plain-English shape covers the documented template.
        assert "If ping to" in sentence
        assert "192.168.1.1" in sentence
        assert "consecutive times" in sentence
        assert "power-cycle" in sentence
        assert "before re-arming" in sentence

        # Listed.
        rows = shell_session.get(
            f"{base_url}/api/v1/admin/rules", timeout=10
        ).json()["data"]
        assert any(row["id"] == rule_id for row in rows)
    finally:
        _delete_rule(shell_session, base_url, rule_id)

    # Gone after delete.
    rows = shell_session.get(
        f"{base_url}/api/v1/admin/rules", timeout=10
    ).json()["data"]
    assert not any(row["id"] == rule_id for row in rows)


# ── validation ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "probe_kind,target_kind,action_kind,expect_field",
    [
        ("bogus", "tag", "cycle", "probe.kind"),
        ("ping", "bogus", "cycle", "target.kind"),
        ("ping", "tag", "bogus", "action.kind"),
    ],
)
def test_validation_rejects_unknown_kinds(
    base_url, shell_session, probe_kind, target_kind, action_kind, expect_field
):
    r = _create_rule(
        shell_session,
        base_url,
        name=f"qa040v-{unique_suffix()}",
        probe={"kind": probe_kind, "host": "x"},
        target={"kind": target_kind, "tag": "x"},
        action={"kind": action_kind},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "validation_failed"
    # The user-facing message points at the offending field.
    assert expect_field in body["error"]["message"]


def test_validation_requires_name(base_url, shell_session):
    r = _create_rule(
        shell_session,
        base_url,
        name="",
        probe={"kind": "internet"},
        target={"kind": "tag", "tag": "x"},
        action={"kind": "notify_only"},
    )
    assert r.status_code == 400, r.text
    assert "name is required" in r.json()["error"]["message"]


# ── sentence render covers every probe + action shape ────────────────────


def test_sentence_renders_internet_probe(base_url, shell_session):
    r = _create_rule(
        shell_session,
        base_url,
        name=f"qa040si-{unique_suffix()}",
        probe={"kind": "internet"},
        target={"kind": "tag", "tag": "x"},
        action={"kind": "notify_only"},
    )
    assert r.status_code == 201
    rid = r.json()["data"]["id"]
    try:
        s = r.json()["data"]["sentence"]
        assert "outbound internet connectivity" in s
        assert "notify (no power action)" in s
    finally:
        _delete_rule(shell_session, base_url, rid)


def test_sentence_renders_http_probe_and_holdoff_action(base_url, shell_session):
    r = _create_rule(
        shell_session,
        base_url,
        name=f"qa040sh-{unique_suffix()}",
        probe={"kind": "http", "url": "https://example.com/ping"},
        target={"kind": "tag", "tag": "x"},
        action={"kind": "hold_off"},
    )
    assert r.status_code == 201
    rid = r.json()["data"]["id"]
    try:
        s = r.json()["data"]["sentence"]
        assert "HTTP GET to `https://example.com/ping`" in s
        assert "hold off" in s
    finally:
        _delete_rule(shell_session, base_url, rid)


# ── enable/disable toggle ─────────────────────────────────────────────────


def test_disable_then_re_enable(base_url, shell_session):
    r = _create_rule(
        shell_session,
        base_url,
        name=f"qa040en-{unique_suffix()}",
        probe={"kind": "internet"},
        target={"kind": "tag", "tag": "x"},
        action={"kind": "notify_only"},
    )
    rid = r.json()["data"]["id"]
    try:
        # The toggle is exposed via the form-style admin handler.
        # Use form-encoded post to /app/rules/<id>/toggle (UI surface)
        # which calls into set_enabled().
        rd = shell_session.post(
            f"{base_url}/app/rules/{rid}/toggle",
            data={"enabled": "0"},
            timeout=10,
        )
        # 302 redirect back to /app/rules.
        assert rd.status_code in (302, 200)

        rows = shell_session.get(
            f"{base_url}/api/v1/admin/rules", timeout=10
        ).json()["data"]
        match = next(r for r in rows if r["id"] == rid)
        assert match["enabled"] is False
        assert match["status"] == "disabled"

        rd = shell_session.post(
            f"{base_url}/app/rules/{rid}/toggle",
            data={"enabled": "1"},
            timeout=10,
        )
        assert rd.status_code in (302, 200)
        rows = shell_session.get(
            f"{base_url}/api/v1/admin/rules", timeout=10
        ).json()["data"]
        match = next(r for r in rows if r["id"] == rid)
        assert match["enabled"] is True
        assert match["status"] == "armed"
    finally:
        _delete_rule(shell_session, base_url, rid)


# ── UI surface ────────────────────────────────────────────────────────────


def test_rules_page_renders_form_and_advertises_runtime_pending(
    base_url, shell_session
):
    body = shell_session.get(f"{base_url}/app/rules", timeout=10).text
    # Page header
    assert "Rules" in body
    # Create form is rendered for admins
    assert "Create a watchdog rule" in body
    # Probe-kind options
    assert "internet" in body
    assert "ping" in body
    assert "http" in body
    # Action-kind options
    assert "power-cycle" in body or "cycle" in body
    # The runtime shipped in v0.4.2 — page advertises the cadence
    # explanation instead of the original "rules-do-not-fire" notice.
    assert (
        "10-second tick" in body
        or "DO NOT fire" in body  # back-compat with v0.4.0/.1 deploys
        or "do not fire yet" in body.lower()
    )
    # NB: the old "What's coming next" roadmap card was removed in
    # v0.5.77 (P-UI walkthrough #17) — it was internal release-log
    # content on an operator page, so there's nothing to assert here.


def test_rules_page_lists_created_rule_with_sentence(base_url, shell_session):
    """The list table renders the plain-English sentence for each rule."""
    name = f"qa040ui-{unique_suffix()}"
    r = _create_rule(
        shell_session,
        base_url,
        name=name,
        probe={"kind": "ping", "host": "10.0.0.1"},
        target={"kind": "tag", "tag": "x"},
        action={"kind": "cycle", "power_off_seconds": 7},
    )
    rid = r.json()["data"]["id"]
    try:
        body = shell_session.get(f"{base_url}/app/rules", timeout=10).text
        assert name in body
        assert "10.0.0.1" in body
        assert "power-cycle" in body
    finally:
        _delete_rule(shell_session, base_url, rid)
