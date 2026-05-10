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
    ttl_seconds: int | None = None,
) -> tuple[EnrollmentToken, str]:
    """v0.4.14 (BUG-043): caller-supplied `ttl_seconds` now honored.

    Pre-fix the env-var default
    (`REBOOTER_ENROLLMENT_TOKEN_TTL_SECONDS`, 24 h) was the only
    knob — operators wanting a 30-day token for a firmware-team
    handoff had to recreate the container with a bumped env var.
    Now: optional override, capped at 30 days so we don't end up
    with effectively-immortal tokens lying around.
    """
    if ttl_seconds is None or ttl_seconds <= 0:
        ttl = settings.enrollment_token_ttl_seconds
    else:
        # Cap at 30 days so the operator can't accidentally mint a
        # year-long token by typo.
        ttl = min(int(ttl_seconds), 60 * 60 * 24 * 30)
    secret = "et_" + secrets.token_urlsafe(24)
    record = EnrollmentToken(
        token_hash=_hash(secret),
        issued_by_user_id=issued_by_user_id,
        site_id=site_id,
        display_name_hint=display_name_hint,
        note=note,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
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


def revoke_enrollment_token(token_id: str) -> bool:
    """v0.3.4 (P3): operator-revoke a still-pending enrollment token.

    Hard-deletes only if the token has NOT been consumed — a consumed
    token is a record of a real device's bring-up and the audit chain
    needs it. Expired-but-unconsumed tokens delete cleanly.
    Returns True on delete, False on no-op (unknown / already consumed).
    """
    with session_scope() as session:
        et = session.get(EnrollmentToken, token_id)
        if et is None:
            return False
        if et.consumed_at is not None:
            return False
        session.delete(et)
        session.flush()
        return True


def revoke_enrollment_tokens_bulk(token_ids: list[str]) -> dict:
    """v0.3.4 (P3): bulk-revoke pending enrollment tokens.

    Returns {"revoked": [...], "skipped_unknown": [...],
    "skipped_consumed": [...]}.
    """
    revoked: list[str] = []
    skipped_unknown: list[str] = []
    skipped_consumed: list[str] = []
    with session_scope() as session:
        for tid in token_ids:
            et = session.get(EnrollmentToken, tid)
            if et is None:
                skipped_unknown.append(tid)
                continue
            if et.consumed_at is not None:
                skipped_consumed.append(tid)
                continue
            session.delete(et)
            revoked.append(tid)
        session.flush()
    return {
        "revoked": revoked,
        "skipped_unknown": skipped_unknown,
        "skipped_consumed": skipped_consumed,
    }


def consume_enrollment_token(token: str, registration_payload: dict) -> tuple[Device, str]:
    """
    Exchange an enrollment token for a new device + bearer credential.
    Returns (device, raw_device_token). The raw_device_token is shown
    once and never persisted in cleartext.
    """
    # v0.4.18 (BUG-050 + BUG-051): bound + sanity-check the
    # caller-supplied registration fields before they hit the
    # column-width-bound INSERT. Pre-fix:
    #  - display_name >120 chars → DataError → 500
    #  - mac_address `<script>alert(1)</script>` accepted as-is.
    import re as _re
    p = registration_payload or {}
    for field, max_len in (
        ("display_name", 120),
        ("hardware_model", 80),
        ("hardware_revision", 40),
        ("firmware_version", 40),
        ("mac_address", 40),
        ("serial_number", 80),
        ("local_ip", 64),
    ):
        v = p.get(field)
        if v is not None and not isinstance(v, str):
            raise EnrollmentError(
                "validation_failed",
                f"{field} must be a string",
            )
        if v and len(v) > max_len:
            raise EnrollmentError(
                "validation_failed",
                f"{field} must be {max_len} characters or fewer",
            )
    mac = p.get("mac_address")
    if mac:
        # Common MAC formats: AA:BB:CC:DD:EE:FF, AA-BB-..., AABB.CCDD.EEFF.
        # Reject anything that isn't hex + colon/dash/dot/space — keeps
        # garbage like '<script>...' out of the column without being
        # so strict that legitimate vendor formats break.
        if not _re.fullmatch(r"[0-9A-Fa-f:.\-\s]+", mac):
            raise EnrollmentError(
                "validation_failed",
                "mac_address must contain only hex digits, ':', '-', '.', or spaces",
            )

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
