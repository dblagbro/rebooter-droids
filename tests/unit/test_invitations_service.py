"""Unit tests — the invitation service.

`app/services/invitations.py` — mint / look-up / redeem. The
`lookup_pending` and `redeem_invitation` expiry checks are the
BUG-059(A) sites: an `Invitation.expires_at` read back naive from
SQLite must be coerced (`as_aware`) before comparison. DB-backed →
the `hub_db` isolated-SQLite fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import session_scope
from app.models import Invitation
from app.services.invitations import (
    InvitationError,
    lookup_pending,
    mint_invitation,
    redeem_invitation,
)


def _expire(invitation_id: str) -> None:
    """Backdate an invitation's expiry into the past."""
    with session_scope() as s:
        s.get(Invitation, invitation_id).expires_at = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        )


# ── mint ───────────────────────────────────────────────────────────────

def test_mint_invitation_returns_record_and_inv_secret(hub_db):
    record, secret = mint_invitation(
        hub_db, "newuser@example.com", "admin", issued_by_user_id=None,
    )
    assert secret.startswith("inv_")
    assert record.email == "newuser@example.com"
    assert record.consumed_at is None


def test_mint_invitation_rejects_bad_email(hub_db):
    with pytest.raises(InvitationError):
        mint_invitation(hub_db, "not-an-email", "admin", issued_by_user_id=None)


def test_mint_invitation_rejects_unknown_role(hub_db):
    with pytest.raises(InvitationError):
        mint_invitation(hub_db, "x@example.com", "wizard", issued_by_user_id=None)


# ── lookup_pending ─────────────────────────────────────────────────────

def test_lookup_pending_valid_token(hub_db):
    _, secret = mint_invitation(hub_db, "x@example.com", "admin",
                                issued_by_user_id=None)
    inv = lookup_pending(secret)
    assert inv is not None
    assert inv.email == "x@example.com"


def test_lookup_pending_unknown_token(hub_db):
    assert lookup_pending("inv_does-not-exist") is None


def test_lookup_pending_expired_returns_none(hub_db):
    # BUG-059(A): the expiry comparison must coerce the SQLite-naive
    # expires_at — without the fix this raised TypeError.
    record, secret = mint_invitation(hub_db, "x@example.com", "admin",
                                     issued_by_user_id=None)
    _expire(record.id)
    assert lookup_pending(secret) is None


# ── redeem_invitation ──────────────────────────────────────────────────

def test_redeem_invitation_happy_path(hub_db):
    _, secret = mint_invitation(hub_db, "newbie@example.com", "operator",
                                issued_by_user_id=None)
    result = redeem_invitation(secret, "a-good-password", "Newbie")
    assert result  # the create_user dict
    # The token is single-use — now consumed.
    assert lookup_pending(secret) is None


def test_redeem_invitation_expired_raises(hub_db):
    record, secret = mint_invitation(hub_db, "x@example.com", "admin",
                                     issued_by_user_id=None)
    _expire(record.id)
    with pytest.raises(InvitationError) as exc:
        redeem_invitation(secret, "a-good-password", "X")
    assert exc.value.code == "invitation_expired"


def test_redeem_invitation_already_consumed_raises(hub_db):
    _, secret = mint_invitation(hub_db, "twice@example.com", "operator",
                                issued_by_user_id=None)
    redeem_invitation(secret, "a-good-password", "Twice")
    with pytest.raises(InvitationError) as exc:
        redeem_invitation(secret, "a-good-password", "Twice")
    assert exc.value.code == "invitation_consumed"


def test_redeem_invitation_short_password_raises(hub_db):
    _, secret = mint_invitation(hub_db, "x@example.com", "admin",
                                issued_by_user_id=None)
    with pytest.raises(InvitationError) as exc:
        redeem_invitation(secret, "short", "X")
    assert exc.value.code == "validation_failed"


def test_redeem_invitation_unknown_token_raises(hub_db):
    with pytest.raises(InvitationError) as exc:
        redeem_invitation("inv_nope", "a-good-password", "X")
    assert exc.value.code == "invitation_invalid"
