"""v0.5.9 — multi-target internet probe (any-succeeds semantics).

Covers:
- legacy internet rule (no `targets`) still works and details payload
  reports the default target set was substituted
- explicit single-target good and bad cases via the REST API
- mixed-target case: 1 succeeds, 1 fails → outcome=success, details
  lists both succeeded and failed
- all-targets-fail case → outcome=failure
- validation rejects bad shapes (non-list, too many, bad port, missing
  host) at create time
- rules-list sentence mentions the chosen target count
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


def _create(shell_session, base_url, *, name, probe):
    body = {
        "name": name,
        "probe": probe,
        "target": {"kind": "tag", "tag": "qa0509"},
        "action": {"kind": "notify_only"},
        "failure_threshold": 3,
        "recovery_threshold": 2,
        "window_seconds": 60,
    }
    r = shell_session.post(
        f"{base_url}/api/v1/admin/rules", json=body, timeout=10
    )
    return r


def _probe_now(shell_session, base_url, rid):
    return shell_session.post(
        f"{base_url}/api/v1/admin/rules/{rid}/probe-now",
        timeout=15,
    )


def _delete(shell_session, base_url, rid):
    shell_session.delete(f"{base_url}/api/v1/admin/rules/{rid}", timeout=10)


def test_legacy_internet_rule_no_targets_uses_defaults(base_url, shell_session):
    r = _create(
        shell_session, base_url,
        name=f"qa0509-legacy-{unique_suffix()}",
        probe={"kind": "internet"},
    )
    assert r.status_code == 201, r.text
    rule = r.json()["data"]
    try:
        p = _probe_now(shell_session, base_url, rule["id"])
        assert p.status_code == 200, p.text
        details = p.json()["data"]["details"]
        # Defaults were substituted in
        assert details.get("used_default_targets") is True
        assert details.get("targets_total") == 3
        # At least one of cloudflare/google/level3 should reach :53
        assert len(details.get("targets_succeeded", [])) >= 1
        assert p.json()["data"]["outcome"] == "success"
    finally:
        _delete(shell_session, base_url, rule["id"])


def test_explicit_targets_all_fail_outcome_failure(base_url, shell_session):
    r = _create(
        shell_session, base_url,
        name=f"qa0509-allbad-{unique_suffix()}",
        probe={
            "kind": "internet",
            "targets": [
                # Two RFC-5737 documentation IPs that should never have
                # anything listening on :53.
                {"host": "192.0.2.1", "port": 53},
                {"host": "198.51.100.1", "port": 53},
            ],
        },
    )
    assert r.status_code == 201, r.text
    rule = r.json()["data"]
    try:
        p = _probe_now(shell_session, base_url, rule["id"])
        assert p.status_code == 200, p.text
        data = p.json()["data"]
        assert data["outcome"] == "failure"
        details = data["details"]
        assert details["targets_succeeded"] == []
        assert details["targets_total"] == 2
        assert len(details["targets_failed"]) == 2
        # used_default_targets must NOT be set when an explicit list was provided
        assert "used_default_targets" not in details
    finally:
        _delete(shell_session, base_url, rule["id"])


def test_mixed_targets_one_succeeds_outcome_success(base_url, shell_session):
    r = _create(
        shell_session, base_url,
        name=f"qa0509-mixed-{unique_suffix()}",
        probe={
            "kind": "internet",
            "targets": [
                {"host": "192.0.2.1", "port": 53},  # guaranteed black-hole
                {"host": "1.1.1.1", "port": 53},    # cloudflare
            ],
        },
    )
    assert r.status_code == 201, r.text
    rule = r.json()["data"]
    try:
        p = _probe_now(shell_session, base_url, rule["id"])
        assert p.status_code == 200, p.text
        data = p.json()["data"]
        assert data["outcome"] == "success"
        details = data["details"]
        # Failed entry tracked alongside the success — the operator
        # needs to see "192.0.2.1 down" so they can replace it later.
        succ_hosts = {t["host"] for t in details["targets_succeeded"]}
        fail_hosts = {t["host"] for t in details["targets_failed"]}
        assert "1.1.1.1" in succ_hosts
        assert "192.0.2.1" in fail_hosts
    finally:
        _delete(shell_session, base_url, rule["id"])


@pytest.mark.parametrize("bad_probe,expected_fragment", [
    ({"kind": "internet", "targets": "not-a-list"}, "must be a list"),
    ({"kind": "internet", "targets": [{} for _ in range(9)]}, "at most 8 entries"),
    ({"kind": "internet", "targets": [{"port": 53}]}, "host is required"),
    ({"kind": "internet", "targets": [{"host": "1.1.1.1", "port": 0}]}, "must be between 1 and 65535"),
    ({"kind": "internet", "targets": [{"host": "1.1.1.1", "port": 70000}]}, "must be between 1 and 65535"),
    ({"kind": "internet", "targets": [{"host": "1.1.1.1", "port": "wat"}]}, "must be an integer"),
])
def test_validation_rejects_bad_targets(base_url, shell_session, bad_probe, expected_fragment):
    r = _create(
        shell_session, base_url,
        name=f"qa0509-bad-{unique_suffix()}",
        probe=bad_probe,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "validation_failed"
    assert expected_fragment in r.json()["error"]["message"]


def test_rule_sentence_mentions_target_count(base_url, shell_session):
    r = _create(
        shell_session, base_url,
        name=f"qa0509-sentence-{unique_suffix()}",
        probe={
            "kind": "internet",
            "targets": [
                {"host": "1.1.1.1", "port": 53},
                {"host": "8.8.8.8", "port": 53},
            ],
        },
    )
    assert r.status_code == 201, r.text
    rule = r.json()["data"]
    try:
        assert "2 targets" in rule["sentence"]
    finally:
        _delete(shell_session, base_url, rule["id"])


def test_legacy_rule_sentence_mentions_defaults(base_url, shell_session):
    r = _create(
        shell_session, base_url,
        name=f"qa0509-default-sent-{unique_suffix()}",
        probe={"kind": "internet"},
    )
    assert r.status_code == 201, r.text
    rule = r.json()["data"]
    try:
        assert "3 default targets" in rule["sentence"]
    finally:
        _delete(shell_session, base_url, rule["id"])
