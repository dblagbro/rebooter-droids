from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import get_engine, init_engine, session_scope
from app.models import Base, Device, DeviceHeartbeat, FirmwareRelease
from app.services.deployments import (
    assignment_for_device,
    create_deployment,
    list_deployments,
    mark_assignment_delivered,
)
from app.services.devices import get_device_detail, list_devices
from app.services.heartbeats import record_heartbeat


def _test_settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'rebooter-qa.sqlite'}",
        secret_key="qa-secret",
        firmware_dir=str(tmp_path / "firmware"),
        uploads_dir=str(tmp_path / "uploads"),
        public_base_url="https://qa.example.test/rebooter",
        firmware_public_base="https://qa.example.test/rebooter/firmware",
        bootstrap_admin_email=None,
        bootstrap_admin_password=None,
        bootstrap_admin_force_password_on_startup=False,
        log_level="INFO",
        heartbeat_interval_seconds=60,
        poll_interval_seconds=30,
        announce_pending_retry_after_seconds=5,
        enrollment_token_ttl_seconds=86400,
        invitation_ttl_seconds=86400,
        password_reset_ttl_seconds=3600,
        smtp_host="",
        smtp_port=25,
        smtp_user="",
        smtp_password="",
        smtp_from="",
        smtp_helo="",
        session_idle_timeout_seconds=3600,
        cors_allowed_origins=(),
        cookie_domain=None,
    )


@pytest.fixture
def isolated_hub_db(tmp_path):
    settings = _test_settings(tmp_path)
    init_engine(settings)
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return settings


def _seed_device_and_release(
    *,
    current_version: str = "0.1.16-dev-central",
    target_version: str = "0.1.17-dev-central",
) -> tuple[str, str, str]:
    with session_scope() as session:
        device = Device(
            id="dev_qa_status_truth",
            display_name="QA status truth",
            registration_state="active",
            central_management_enabled=True,
            firmware_version=current_version,
            local_ip="192.168.1.200",
        )
        release = FirmwareRelease(
            id="fwr_qa_status_truth",
            version=target_version,
            channel="stable",
            filename=f"rebooter-{target_version}.bin",
            download_url=f"https://qa.example.test/rebooter/firmware/{target_version}.bin",
            sha256="a" * 64,
            size_bytes=2048,
            release_notes="qa",
        )
        session.add(device)
        session.add(release)
        session.flush()
        return device.id, release.id, release.version


def _latest_deployment() -> dict:
    deployments = list_deployments()
    assert deployments, "expected at least one deployment"
    return deployments[0]


def test_heartbeat_completes_delivered_firmware_assignment(isolated_hub_db):
    device_id, release_id, target_version = _seed_device_and_release()

    create_deployment(
        release_id=release_id,
        target_type="device",
        target_id=device_id,
        channel="stable",
        force=False,
        issued_by_user_id=None,
    )
    assert assignment_for_device(device_id)["target_version"] == target_version

    mark_assignment_delivered(device_id)
    before = _latest_deployment()
    assert before["counts"]["delivered"] == 1
    assert before["counts"]["completed"] == 0

    record_heartbeat(
        device_id,
        {
            "firmware_version": target_version,
            "mode": "smart_plug",
            "relay_on": True,
            "wifi_connected": True,
            "health_state": "healthy",
            "uptime_seconds": 30,
        },
    )

    after = _latest_deployment()
    assert after["counts"]["delivered"] == 0
    assert after["counts"]["completed"] == 1
    assert assignment_for_device(device_id) is None


def test_online_device_with_old_version_surfaces_upgrade_pending(isolated_hub_db):
    device_id, release_id, target_version = _seed_device_and_release()
    create_deployment(
        release_id=release_id,
        target_type="device",
        target_id=device_id,
        channel="stable",
        force=False,
        issued_by_user_id=None,
    )
    mark_assignment_delivered(device_id)

    record_heartbeat(
        device_id,
        {
            "firmware_version": "0.1.16-dev-central",
            "mode": "smart_plug",
            "relay_on": True,
            "wifi_connected": True,
            "health_state": "healthy",
            "uptime_seconds": 45,
        },
    )

    row = next(d for d in list_devices() if d["id"] == device_id)
    assert row["heartbeat_state"] == "online"
    assert row["central_status"] == "upgrade_pending"
    assert target_version in row["central_status_reason"]

    detail = get_device_detail(device_id)
    assert detail is not None
    assert detail["central_status"] == "upgrade_pending"
    assert detail["active_firmware_assignment"]["state"] == "delivered"
    assert detail["active_firmware_assignment"]["target_version"] == target_version


def test_stale_device_with_active_assignment_surfaces_transport_stale(isolated_hub_db):
    device_id, release_id, target_version = _seed_device_and_release()
    create_deployment(
        release_id=release_id,
        target_type="device",
        target_id=device_id,
        channel="stable",
        force=False,
        issued_by_user_id=None,
    )
    mark_assignment_delivered(device_id)

    record_heartbeat(
        device_id,
        {
            "firmware_version": "0.1.16-dev-central",
            "mode": "smart_plug",
            "relay_on": True,
            "wifi_connected": True,
            "health_state": "healthy",
            "uptime_seconds": 90,
        },
    )

    stale_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    with session_scope() as session:
        device = session.get(Device, device_id)
        hb = session.scalar(
            select(DeviceHeartbeat)
            .where(DeviceHeartbeat.device_id == device_id)
            .order_by(DeviceHeartbeat.received_at.desc())
            .limit(1)
        )
        assert device is not None
        assert hb is not None
        device.last_heartbeat_at = stale_at
        hb.received_at = stale_at
        session.add(device)
        session.add(hb)

    row = next(d for d in list_devices() if d["id"] == device_id)
    assert row["heartbeat_state"] == "offline"
    assert row["central_status"] == "transport_stale"
    assert target_version in row["central_status_reason"]

    detail = get_device_detail(device_id)
    assert detail is not None
    assert detail["heartbeat_state"] == "offline"
    assert detail["central_status"] == "transport_stale"
    assert detail["active_firmware_assignment"]["state"] == "delivered"
    assert detail["active_firmware_assignment"]["target_version"] == target_version
