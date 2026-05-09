"""Admin API surface — happy paths + negative tests."""

import os
import tempfile
import hashlib

import requests

from .conftest import unique_suffix


def test_devices_list_filters(base_url, admin_headers):
    r = requests.get(
        f"{base_url}/api/v1/admin/devices?status=active",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()["data"]
    for d in body["devices"]:
        assert d["registration_state"] == "active"


def test_get_unknown_device_404(base_url, admin_headers):
    r = requests.get(
        f"{base_url}/api/v1/admin/devices/dev_nope",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "device_unknown"


def test_patch_unknown_device_404(base_url, admin_headers):
    r = requests.patch(
        f"{base_url}/api/v1/admin/devices/dev_nope",
        headers=admin_headers,
        json={"display_name": "x"},
        timeout=10,
    )
    assert r.status_code == 404


def test_send_command_validates_set_mode(base_url, admin_headers):
    """Locked v0.1 schema — set_mode requires mode in {smart_plug, internet_watchdog, device_watchdog}."""
    devs = (
        requests.get(f"{base_url}/api/v1/admin/devices", headers=admin_headers)
        .json()["data"]["devices"]
    )
    if not devs:
        return  # nothing to send to
    dev_id = devs[0]["id"]
    bad = requests.post(
        f"{base_url}/api/v1/admin/devices/{dev_id}/commands",
        headers=admin_headers,
        json={"type": "set_mode", "payload": {"mode": "hyperdrive"}},
        timeout=10,
    )
    assert bad.status_code == 400
    assert "smart_plug" in bad.json()["error"]["message"]

    good = requests.post(
        f"{base_url}/api/v1/admin/devices/{dev_id}/commands",
        headers=admin_headers,
        json={"type": "set_mode", "payload": {"mode": "smart_plug"}},
        timeout=10,
    )
    assert good.status_code == 201


def test_send_command_validates_apply_config(base_url, admin_headers):
    devs = (
        requests.get(f"{base_url}/api/v1/admin/devices", headers=admin_headers)
        .json()["data"]["devices"]
    )
    if not devs:
        return
    dev_id = devs[0]["id"]
    r = requests.post(
        f"{base_url}/api/v1/admin/devices/{dev_id}/commands",
        headers=admin_headers,
        json={
            "type": "apply_config",
            "payload": {"admin_password": "pwn", "device_name": "xx"},
        },
        timeout=10,
    )
    assert r.status_code == 400
    assert "admin_password" in r.json()["error"]["message"]


def test_send_unsupported_command_type_rejected(base_url, admin_headers):
    devs = (
        requests.get(f"{base_url}/api/v1/admin/devices", headers=admin_headers)
        .json()["data"]["devices"]
    )
    if not devs:
        return
    dev_id = devs[0]["id"]
    r = requests.post(
        f"{base_url}/api/v1/admin/devices/{dev_id}/commands",
        headers=admin_headers,
        json={"type": "self_destruct"},
        timeout=10,
    )
    assert r.status_code == 400
    assert "unsupported command type" in r.json()["error"]["message"]


def test_groups_crud(base_url, admin_headers):
    name = f"qa-grp-{unique_suffix()}"
    r = requests.post(
        f"{base_url}/api/v1/admin/groups",
        headers=admin_headers,
        json={"name": name, "description": "qa"},
        timeout=10,
    )
    assert r.status_code == 201
    gid = r.json()["data"]["id"]

    listing = requests.get(
        f"{base_url}/api/v1/admin/groups", headers=admin_headers, timeout=10
    ).json()["data"]
    assert any(g["id"] == gid for g in listing["groups"])

    detail = requests.get(
        f"{base_url}/api/v1/admin/groups/{gid}", headers=admin_headers, timeout=10
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["name"] == name


def test_group_empty_name_rejected(base_url, admin_headers):
    r = requests.post(
        f"{base_url}/api/v1/admin/groups",
        headers=admin_headers,
        json={"name": "  "},
        timeout=10,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "validation_failed"


def test_group_add_unknown_device_silently_skipped(base_url, admin_headers):
    """Documented behaviour: missing device IDs are skipped, count returned."""
    name = f"qa-grp-skip-{unique_suffix()}"
    gid = requests.post(
        f"{base_url}/api/v1/admin/groups",
        headers=admin_headers,
        json={"name": name},
        timeout=10,
    ).json()["data"]["id"]
    r = requests.post(
        f"{base_url}/api/v1/admin/groups/{gid}/members",
        headers=admin_headers,
        json={"device_ids": ["dev_does_not_exist", "dev_also_no"]},
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["data"]["added"] == 0


def test_remove_nonmember_returns_404(base_url, admin_headers):
    name = f"qa-grp-rm-{unique_suffix()}"
    gid = requests.post(
        f"{base_url}/api/v1/admin/groups",
        headers=admin_headers,
        json={"name": name},
        timeout=10,
    ).json()["data"]["id"]
    r = requests.delete(
        f"{base_url}/api/v1/admin/groups/{gid}/members/dev_x",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 404


def test_sites_crud(base_url, admin_headers):
    name = f"qa-site-{unique_suffix()}"
    r = requests.post(
        f"{base_url}/api/v1/admin/sites",
        headers=admin_headers,
        json={"name": name, "description": "qa"},
        timeout=10,
    )
    assert r.status_code == 201
    sid = r.json()["data"]["id"]

    listing = requests.get(
        f"{base_url}/api/v1/admin/sites", headers=admin_headers, timeout=10
    ).json()["data"]
    assert any(s["id"] == sid for s in listing["sites"])

    delete = requests.delete(
        f"{base_url}/api/v1/admin/sites/{sid}", headers=admin_headers, timeout=10
    )
    assert delete.status_code == 200


def test_delete_unknown_site_404(base_url, admin_headers):
    r = requests.delete(
        f"{base_url}/api/v1/admin/sites/site_nope",
        headers=admin_headers,
        timeout=10,
    )
    assert r.status_code == 404


def test_firmware_upload_sha_mismatch_rejected(base_url, admin_headers):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        f.write(os.urandom(1024))
        path = f.name
    try:
        r = requests.post(
            f"{base_url}/api/v1/admin/firmware/releases",
            headers=admin_headers,
            data={
                "version": f"qa-{unique_suffix()}",
                "channel": "dev",
                "sha256": "0" * 64,
            },
            files={"file": ("fw.bin", open(path, "rb"))},
            timeout=15,
        )
    finally:
        os.unlink(path)
    assert r.status_code == 400
    assert "sha256 mismatch" in r.json()["error"]["message"]


def test_firmware_upload_then_download_via_nginx(base_url, admin_headers):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        body = os.urandom(2048)
        f.write(body)
        path = f.name
    sha = hashlib.sha256(body).hexdigest()
    try:
        r = requests.post(
            f"{base_url}/api/v1/admin/firmware/releases",
            headers=admin_headers,
            data={
                "version": f"qa-{unique_suffix()}",
                "channel": "dev",
                "sha256": sha,
            },
            files={"file": ("fw.bin", open(path, "rb"))},
            timeout=15,
        )
        assert r.status_code == 201, r.text
        rel = r.json()["data"]
        download = requests.get(rel["download_url"], timeout=15)
        assert download.status_code == 200
        assert hashlib.sha256(download.content).hexdigest() == sha
        # cleanup
        requests.delete(
            f"{base_url}/api/v1/admin/firmware/releases/{rel['id']}",
            headers=admin_headers,
            timeout=10,
        )
    finally:
        os.unlink(path)


def test_firmware_duplicate_version_rejected(base_url, admin_headers):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        body = os.urandom(512)
        f.write(body)
        path = f.name
    sha = hashlib.sha256(body).hexdigest()
    suffix = unique_suffix()
    try:
        r1 = requests.post(
            f"{base_url}/api/v1/admin/firmware/releases",
            headers=admin_headers,
            data={"version": f"qa-{suffix}", "channel": "dev", "sha256": sha},
            files={"file": ("fw.bin", open(path, "rb"))},
            timeout=15,
        )
        assert r1.status_code == 201
        rel_id = r1.json()["data"]["id"]
        r2 = requests.post(
            f"{base_url}/api/v1/admin/firmware/releases",
            headers=admin_headers,
            data={"version": f"qa-{suffix}", "channel": "dev", "sha256": sha},
            files={"file": ("fw.bin", open(path, "rb"))},
            timeout=15,
        )
        assert r2.status_code == 400
        assert "already exists" in r2.json()["error"]["message"]
        requests.delete(
            f"{base_url}/api/v1/admin/firmware/releases/{rel_id}",
            headers=admin_headers,
            timeout=10,
        )
    finally:
        os.unlink(path)


def test_firmware_missing_file_rejected(base_url, admin_headers):
    r = requests.post(
        f"{base_url}/api/v1/admin/firmware/releases",
        headers=admin_headers,
        data={"version": "qa-nofile", "channel": "dev"},
        timeout=10,
    )
    assert r.status_code == 400


def test_events_query_unauth(base_url):
    r = requests.get(f"{base_url}/api/v1/admin/events", timeout=10)
    assert r.status_code == 401


def test_admin_endpoint_requires_admin_token_not_device_token(
    base_url, admin_headers
):
    """Mint an enrollment, register, then try device-token at /admin/devices."""
    et = requests.post(
        f"{base_url}/api/v1/admin/enrollment-tokens",
        headers=admin_headers,
        json={"display_name_hint": f"QA {unique_suffix()}"},
        timeout=10,
    ).json()["data"]["enrollment_token"]
    reg = requests.post(
        f"{base_url}/api/v1/device/register",
        json={"enrollment_token": et, "hardware_model": "sonoff_s31"},
        timeout=10,
    ).json()["data"]
    r = requests.get(
        f"{base_url}/api/v1/admin/devices",
        headers={"Authorization": f"Bearer {reg['device_token']}"},
        timeout=10,
    )
    assert r.status_code == 401, (
        "device token must not authenticate against admin API"
    )
