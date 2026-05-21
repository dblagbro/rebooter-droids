"""Unit tests — the enrollment-token service.

`app/services/enrollment.py` is the registration core: an operator
mints an enrollment token, a device exchanges it (via `/register`) for
a Device row + a bearer credential, and the token can be revoked while
still pending. DB-backed → the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import DeviceCredential, EnrollmentToken
from app.services.enrollment import (
    EnrollmentError,
    consume_enrollment_token,
    list_enrollment_tokens,
    mint_enrollment_token,
    revoke_enrollment_token,
    revoke_enrollment_tokens_bulk,
)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ── mint ───────────────────────────────────────────────────────────────

def test_mint_returns_et_prefixed_secret_stored_only_as_hash(hub_db):
    record, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    assert secret.startswith("et_")
    # Only the hash is persisted — never the plaintext.
    assert record.token_hash == _sha256(secret)


def test_mint_records_issuer_and_hint(hub_db):
    record, _ = mint_enrollment_token(
        hub_db,
        issued_by_user_id="usr_qa",
        display_name_hint="Garage plug",
        note="qa note",
    )
    assert record.issued_by_user_id == "usr_qa"
    assert record.display_name_hint == "Garage plug"
    assert record.consumed_at is None


def test_mint_default_ttl_is_the_configured_window(hub_db):
    # hub_db.enrollment_token_ttl_seconds defaults to 86400 (24 h).
    record, _ = mint_enrollment_token(hub_db, issued_by_user_id=None, ttl_seconds=None)
    delta = record.expires_at - datetime.now(timezone.utc)
    assert timedelta(hours=23) < delta <= timedelta(hours=24, minutes=1)


def test_mint_caps_caller_ttl_at_30_days(hub_db):
    record, _ = mint_enrollment_token(
        hub_db, issued_by_user_id=None, ttl_seconds=60 * 60 * 24 * 90,  # 90 days
    )
    delta = record.expires_at - datetime.now(timezone.utc)
    assert timedelta(days=29) < delta <= timedelta(days=30, minutes=1)


# ── consume — happy path ───────────────────────────────────────────────

def test_consume_creates_device_and_bearer_credential(hub_db):
    _, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    device, device_token = consume_enrollment_token(
        secret,
        {
            "display_name": "Bench plug",
            "hardware_model": "sonoff_s31",
            "mac_address": "AA:BB:CC:DD:EE:01",
        },
    )
    assert device_token.startswith("dt_")
    assert device.display_name == "Bench plug"
    assert device.registration_state == "active"
    assert device.site_id, "fresh adoption resolves to the Default site"

    with session_scope() as session:
        cred = session.scalar(
            select(DeviceCredential).where(DeviceCredential.device_id == device.id)
        )
        assert cred is not None
        # The device token is stored only as a hash, like the enrol token.
        assert cred.token_hash == _sha256(device_token)


def test_consume_stamps_the_token_consumed(hub_db):
    _, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    device, _ = consume_enrollment_token(secret, {"display_name": "Plug"})
    tokens = list_enrollment_tokens()
    assert len(tokens) == 1
    assert tokens[0].consumed_at is not None
    assert tokens[0].consumed_by_device_id == device.id


def test_consume_display_name_falls_back_to_token_hint(hub_db):
    _, secret = mint_enrollment_token(
        hub_db, issued_by_user_id=None, display_name_hint="Hinted name",
    )
    device, _ = consume_enrollment_token(secret, {})  # no display_name in payload
    assert device.display_name == "Hinted name"


def test_consume_qa_display_name_flags_the_device_as_fixture(hub_db):
    _, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    device, _ = consume_enrollment_token(secret, {"display_name": "QA bench plug"})
    assert device.is_qa_fixture is True


# ── resolve_default_site_id — the org-stamping fix ─────────────────────
#
# load-degradation fix (2026-05-21): `sites.organization_id` is NOT NULL
# (org-boundary phase 3). The device-register path
# (`consume_enrollment_token` -> `resolve_default_site_id`) runs with NO
# bound org scope — and the `before_flush` org-stamping is a no-op both
# when no org is bound and under `tenant_scope.system()`. So creating the
# "Default" site with no `organization_id` raised a `NotNullViolation`
# and `/register` 500'd. `resolve_default_site_id` must now resolve and
# stamp the default org explicitly.


def test_resolve_default_site_creates_default_site_with_org_when_unscoped(hub_db):
    """`resolve_default_site_id`, run UNDER `tenant_scope.system()` (the
    state the device-register path runs in — no bound org, before_flush
    stamping is a no-op), must still create the `Default` site with a
    valid non-NULL `organization_id`.

    Fails on `main`: the old code created `Site(name="Default")` with no
    org -> NotNullViolation. Passes with the fix: the org is resolved via
    `resolve_default_org_id` and stamped explicitly.
    """
    from app.models import Site
    from app.services import tenant_scope
    from app.services.sites import resolve_default_site_id

    # No site exists yet; drive the create branch the way `/register`
    # does — inside a system() bypass, where before_flush will NOT stamp.
    with tenant_scope.system():
        with session_scope() as session:
            site_id = resolve_default_site_id(session)

    assert site_id, "a Default site id is returned"
    with tenant_scope.system():
        with session_scope() as session:
            site = session.get(Site, site_id)
            assert site is not None
            assert site.name == "Default"
            assert site.organization_id, (
                "the Default site must be stamped with a valid "
                "organization_id — sites.organization_id is NOT NULL"
            )


def test_resolve_default_site_is_idempotent_under_system(hub_db):
    """Calling `resolve_default_site_id` twice under `system()` reuses the
    existing `Default` site rather than creating a second one."""
    from app.models import Site
    from app.services import tenant_scope
    from app.services.sites import resolve_default_site_id
    from sqlalchemy import func

    with tenant_scope.system():
        with session_scope() as session:
            first = resolve_default_site_id(session)
        with session_scope() as session:
            second = resolve_default_site_id(session)
        with session_scope() as session:
            count = session.scalar(
                select(func.count()).select_from(Site).where(Site.name == "Default")
            )
    assert first == second
    assert count == 1


def test_register_creates_default_site_with_org_under_system(hub_db):
    """End-to-end: a `/register` whose enrollment token carries no
    `site_id`, exercised under `tenant_scope.system()` (the production
    device-register scope), must succeed and land the device on a
    `Default` site that carries a valid `organization_id`.

    Fails on `main` with a NotNullViolation surfacing as an error out of
    `consume_enrollment_token`; passes with the fix.
    """
    from app.models import Site
    from app.services import tenant_scope

    _, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    with tenant_scope.system():
        device, device_token = consume_enrollment_token(
            secret,
            {"display_name": "Adopted plug", "mac_address": "AA:BB:CC:DD:EE:99"},
        )
    assert device_token.startswith("dt_")
    assert device.site_id, "the device is sited on the resolved Default site"

    with session_scope() as session:
        site = session.get(Site, device.site_id)
        assert site is not None
        assert site.name == "Default"
        assert site.organization_id, (
            "the Default site created during register must have a valid "
            "organization_id"
        )


# ── consume — rejections ───────────────────────────────────────────────

def test_consume_unknown_token_raises(hub_db):
    with pytest.raises(EnrollmentError) as exc:
        consume_enrollment_token("et_does-not-exist", {})
    assert exc.value.code == "enrollment_invalid"


def test_consume_already_consumed_token_raises(hub_db):
    _, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    consume_enrollment_token(secret, {"display_name": "First"})
    with pytest.raises(EnrollmentError) as exc:
        consume_enrollment_token(secret, {"display_name": "Second"})
    assert exc.value.code == "enrollment_consumed"


def test_consume_expired_token_raises(hub_db):
    record, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    # Backdate the expiry into the past.
    with session_scope() as session:
        session.get(EnrollmentToken, record.id).expires_at = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        )
    with pytest.raises(EnrollmentError) as exc:
        consume_enrollment_token(secret, {})
    assert exc.value.code == "enrollment_expired"


def test_consume_rejects_overlong_display_name(hub_db):
    _, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    with pytest.raises(EnrollmentError) as exc:
        consume_enrollment_token(secret, {"display_name": "x" * 121})
    assert exc.value.code == "validation_failed"


def test_consume_rejects_malformed_mac(hub_db):
    _, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    with pytest.raises(EnrollmentError) as exc:
        consume_enrollment_token(secret, {"mac_address": "<script>alert(1)</script>"})
    assert exc.value.code == "validation_failed"


# ── revoke ─────────────────────────────────────────────────────────────

def test_revoke_pending_token_deletes_it(hub_db):
    record, _ = mint_enrollment_token(hub_db, issued_by_user_id=None)
    assert revoke_enrollment_token(record.id) is True
    assert list_enrollment_tokens() == []


def test_revoke_consumed_token_is_a_noop(hub_db):
    record, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    consume_enrollment_token(secret, {"display_name": "Plug"})
    # A consumed token is the record of a real bring-up — kept for the
    # audit chain, never hard-deleted.
    assert revoke_enrollment_token(record.id) is False
    assert len(list_enrollment_tokens()) == 1


def test_revoke_unknown_token_returns_false(hub_db):
    assert revoke_enrollment_token("et_no-such-token") is False


def test_revoke_bulk_partitions_revoked_unknown_consumed(hub_db):
    pending, _ = mint_enrollment_token(hub_db, issued_by_user_id=None)
    used, secret = mint_enrollment_token(hub_db, issued_by_user_id=None)
    consume_enrollment_token(secret, {"display_name": "Plug"})

    result = revoke_enrollment_tokens_bulk([pending.id, used.id, "et_unknown"])
    assert result["revoked"] == [pending.id]
    assert result["skipped_consumed"] == [used.id]
    assert result["skipped_unknown"] == ["et_unknown"]
