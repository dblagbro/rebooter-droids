from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import Settings
from app.db import session_scope
from app.models import Device, DeviceCredential, EnrollmentToken


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_enrollment_token(
    settings: Settings,
    issued_by_user_id: str | None,
    site_id: str | None = None,
    display_name_hint: str | None = None,
    note: str | None = None,
) -> tuple[EnrollmentToken, str]:
    secret = "et_" + secrets.token_urlsafe(24)
    record = EnrollmentToken(
        token_hash=_hash(secret),
        issued_by_user_id=issued_by_user_id,
        site_id=site_id,
        display_name_hint=display_name_hint,
        note=note,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.enrollment_token_ttl_seconds),
    )
    with session_scope() as session:
        session.add(record)
        session.flush()
        session.expunge(record)
    return record, secret


def list_enrollment_tokens() -> list[EnrollmentToken]:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc())
            )
        )
        for r in rows:
            session.expunge(r)
    return rows


def consume_enrollment_token(token: str, registration_payload: dict) -> tuple[Device, str]:
    """
    Exchange an enrollment token for a new device + bearer credential.
    Returns (device, raw_device_token). The raw_device_token is shown
    once and never persisted in cleartext.
    """
    token_hash = _hash(token)
    now = datetime.now(timezone.utc)

    with session_scope() as session:
        # Row-level lock to serialise two simultaneous redemption attempts.
        # Without this, two concurrent register calls both pass the
        # consumed_at-is-None check and produce two devices.
        et = session.scalar(
            select(EnrollmentToken)
            .where(EnrollmentToken.token_hash == token_hash)
            .with_for_update()
        )
        if et is None:
            raise EnrollmentError("enrollment_invalid", "Enrollment token is not recognized.")
        if et.consumed_at is not None:
            raise EnrollmentError("enrollment_consumed", "Enrollment token already used.")
        if et.expires_at <= now:
            raise EnrollmentError("enrollment_expired", "Enrollment token has expired.")

        resolved_name = (
            registration_payload.get("display_name")
            or et.display_name_hint
            or ""
        )
        # v0.2.8: tag QA-fixture devices so the admin view can hide them.
        # Trust order: explicit `qa_fixture: true` in the register payload
        # first; fall back to a display_name-prefix auto-detect for tests
        # that go through the public register API without setting it.
        is_qa = bool(registration_payload.get("qa_fixture")) or _looks_like_qa(
            resolved_name, et.display_name_hint, et.note
        )
        device = Device(
            display_name=resolved_name,
            hardware_model=registration_payload.get("hardware_model"),
            hardware_revision=registration_payload.get("hardware_revision"),
            firmware_version=registration_payload.get("firmware_version"),
            mac_address=registration_payload.get("mac_address"),
            serial_number=registration_payload.get("serial_number"),
            local_ip=registration_payload.get("local_ip"),
            site_id=et.site_id,
            registration_state="active",
            capabilities=registration_payload.get("capabilities") or {},
            is_qa_fixture=is_qa,
        )
        session.add(device)
        session.flush()

        raw_secret = "dt_" + secrets.token_urlsafe(32)
        credential = DeviceCredential(
            device_id=device.id,
            token_hash=_hash(raw_secret),
        )
        session.add(credential)

        et.consumed_at = now
        et.consumed_by_device_id = device.id
        session.add(et)

        session.flush()
        session.expunge(device)

    return device, raw_secret


class EnrollmentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_QA_PREFIXES = ("qa ", "qa-", "qa_", "test-", "playwright")


def _looks_like_qa(*candidates: str | None) -> bool:
    for c in candidates:
        if not c:
            continue
        s = c.strip().lower()
        if s.startswith(_QA_PREFIXES):
            return True
    return False
