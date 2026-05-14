"""v0.5.25 Phase 2A: integration probe kinds (roku_app_active /
ha_state_is / weather_alert_active / ical_event_active) are canonical
in `KNOWN_PROBE_KINDS` and accepted by `create_rule` / `update_rule`.

Pre-v0.5.25 the four kinds were runtime-supported in
`watchdog_runtime/_probes.py::run_probe` but the model validation gate
rejected them, which made it impossible to create such a rule via the
API or the JSON editor. This test guards the contract.

The form-builder (`<select name="probe_kind">` on /app/rules) still
doesn't expose these kinds — that's Phase 2B (v0.5.28). Operators
use the JSON editor for now.
"""

from __future__ import annotations

import pytest
import requests


def _create_external_source(base_url, admin_headers, kind, payload):
    r = requests.post(
        f"{base_url}/api/v1/admin/external-sensors/sources",
        headers={**admin_headers, "Content-Type": "application/json"},
        json={"kind": kind, **payload},
        timeout=10,
    )
    return r


@pytest.mark.parametrize(
    "probe_kind, probe_extra",
    [
        ("roku_app_active", {"source_id": "ext_qa_smoke", "app_name": "Spectrum TV"}),
        ("ha_state_is", {
            "source_id": "ext_qa_smoke",
            "entity_id": "sensor.qa_test",
            "expected_state": "on",
        }),
        ("weather_alert_active", {"source_id": "ext_qa_smoke", "event_contains": "Storm"}),
        ("ical_event_active", {"source_id": "ext_qa_smoke", "summary_contains": "Jeopardy"}),
    ],
)
def test_create_rule_accepts_integration_probe_kind(
    base_url, admin_headers, probe_kind, probe_extra
):
    """JSON-editor escape hatch: POST /api/v1/admin/rules with one of the
    four integration probe kinds should succeed (201) — pre-v0.5.25 the
    validation gate rejected these with validation_failed."""
    body = {
        "name": f"qa-v0525-{probe_kind}",
        "probe": {"kind": probe_kind, **probe_extra},
        "target": {"kind": "tag", "tag": "qa-noop"},
        "action": {"kind": "notify_only"},
        "failure_threshold": 3,
        "recovery_threshold": 2,
        "window_seconds": 60,
        "cooldown_seconds": 300,
    }
    r = requests.post(
        f"{base_url}/api/v1/admin/rules",
        headers={**admin_headers, "Content-Type": "application/json"},
        json=body,
        timeout=10,
    )
    rid = None
    try:
        assert r.status_code == 201, f"{probe_kind}: {r.status_code} {r.text}"
        data = r.json()["data"]
        rid = data["id"]
        # Plain-English sentence should mention the probe kind, not fall
        # through to the "unknown probe" string the pre-v0.5.25 phrase
        # renderer used.
        assert "unknown probe" not in (data.get("sentence") or ""), (
            f"sentence rendered as 'unknown probe' for {probe_kind}: {data.get('sentence')}"
        )
        # probe.kind round-trips intact
        assert data["probe"]["kind"] == probe_kind
    finally:
        if rid:
            requests.delete(
                f"{base_url}/api/v1/admin/rules/{rid}",
                headers=admin_headers,
                timeout=10,
            )
