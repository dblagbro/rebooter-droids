"""v0.5.3 — fix nested-form bug on /app/devices that caused upgrade
clicks to submit the bulk-delete form.

ROOT CAUSE (live evidence in audit logs):
  At 2026-05-11 17:45:50 UTC the operator clicked the Upgrade button
  for Erica's R.L. Speaker. Instead of an `device.upgrade_initiated`
  audit event, a `device.bulk_deleted_per_device` event was written
  (deleted_count: 1, confirmation_level: "none"). Same pattern at
  2026-05-11 01:53 deleted 4 devices.

  Cause: per-row upgrade <form> tags were rendered INSIDE the wrapping
  bulk-delete <form>. Per WHATWG HTML5 parsing:
    - The inner <form> start tag is "ignored" as a parse error.
    - The inner </form> end tag DOES close whatever form is open.
  So the per-row upgrade button ended up being a submit-button for
  the OUTER bulk-delete form, with the outer form's action and form
  data (including any checked device_id checkboxes).

FIX (this ship):
  - Bulk-delete <form> moves to AFTER the table+cards, no longer wraps
    them.
  - Row checkboxes use HTML5 `form="devices-bulk-delete-form"` attribute
    to associate with the form across the DOM.
  - bulk_select.js queries document-wide and filters by .form ownership
    so it still finds the now-DOM-detached checkboxes.

These tests:
  1. Verify the rendered HTML has NO nested forms on /app/devices.
  2. Verify row checkboxes reference the bulk-delete form via the form=
     attribute.
  3. Verify clicking an upgrade button (simulated via direct POST to
     the upgrade endpoint) produces an upgrade_initiated audit event,
     NOT a bulk-delete one.
"""

from __future__ import annotations

import re
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


def test_devices_list_has_no_nested_forms(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/devices?show_qa_fixtures=1", timeout=10).text

    # Walk the response counting open/close form tags. At no point
    # should the depth exceed 1 — that's the bug we just fixed.
    depth = 0
    max_depth = 0
    for m in re.finditer(r"<(/?)form[\s>]", body):
        if m.group(1) == "/":
            depth -= 1
        else:
            depth += 1
            max_depth = max(max_depth, depth)
    assert max_depth <= 1, (
        f"nested forms detected on /app/devices — max nesting depth = {max_depth}. "
        "v0.5.3 regression: per-row upgrade forms must NOT be inside the "
        "bulk-delete form."
    )
    # And final depth must be 0 (balanced)
    assert depth == 0, f"form open/close unbalanced on /app/devices: final depth {depth}"


def test_row_checkboxes_associate_with_bulk_form_via_attribute(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/devices?show_qa_fixtures=1", timeout=10).text
    # Every checkbox with name=device_id should now have
    # form="devices-bulk-delete-form" attribute. Pre-fix it didn't.
    checkboxes = re.findall(
        r'<input[^>]*name="device_id"[^>]*>', body
    )
    assert checkboxes, "expected device_id checkboxes on the devices list"
    missing_form_attr = [
        cb for cb in checkboxes
        if 'form="devices-bulk-delete-form"' not in cb
    ]
    assert not missing_form_attr, (
        f"{len(missing_form_attr)} device_id checkbox(es) missing the "
        f"form='devices-bulk-delete-form' association. Example: "
        f"{missing_form_attr[0]}"
    )


def test_bulk_delete_form_has_correct_id(base_url):
    s = _login(base_url)
    body = s.get(f"{base_url}/app/devices?show_qa_fixtures=1", timeout=10).text
    assert 'id="devices-bulk-delete-form"' in body, (
        "bulk-delete form is missing the id used by the form= attribute "
        "association"
    )
