"""Unit tests — the password-reset service.

`app/services/password_resets.py` — request / consume / expire. The
`consume_reset` expiry check is the BUG-059(A) site: a
`PasswordReset.expires_at` read back naive from SQLite must be coerced
(`as_aware`) before comparison. DB-backed → the `hub_db` fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import PasswordReset, User
from app.services import password_resets


def _user(session, email="user@example.com"):
    session.add(User(
        email=email, password_hash="placeholder",
        display_name="Test User", is_active=True,
    ))


def _backdate_reset(delta: timedelta) -> None:
    with session_scope() as s:
        rec = s.scalars(select(PasswordReset)).first()
        rec.expires_at = datetime.now(timezone.utc) + delta


# ── request_reset ──────────────────────────────────────────────────────

def test_request_reset_unknown_email_yields_no_token(hub_db):
    token, masked = password_resets.request_reset("nobody@example.com")
    assert token is None
    assert "@" in masked  # masked form still returned (no user-enumeration)


def test_request_reset_known_user_returns_pwr_token(hub_db):
    with session_scope() as s:
        _user(s, "user@example.com")
    token, _ = password_resets.request_reset("user@example.com")
    assert token is not None
    assert token.startswith("pwr_")


def test_request_reset_inactive_user_yields_no_token(hub_db):
    with session_scope() as s:
        s.add(User(email="off@example.com", password_hash="x",
                   display_name="Off", is_active=False))
    token, _ = password_resets.request_reset("off@example.com")
    assert token is None


# ── consume_reset ──────────────────────────────────────────────────────

def test_consume_reset_valid(hub_db):
    with session_scope() as s:
        _user(s, "user@example.com")
    token, _ = password_resets.request_reset("user@example.com")
    user = password_resets.consume_reset(token, "a-new-password")
    assert user is not None


def test_consume_reset_expired_returns_none(hub_db):
    # BUG-059(A): the expiry comparison must coerce the SQLite-naive
    # expires_at — without the fix this raised TypeError.
    with session_scope() as s:
        _user(s, "user@example.com")
    token, _ = password_resets.request_reset("user@example.com")
    _backdate_reset(timedelta(hours=-1))
    assert password_resets.consume_reset(token, "a-new-password") is None


def test_consume_reset_unknown_token_returns_none(hub_db):
    assert password_resets.consume_reset("pwr_does-not-exist", "a-new-password") is None


def test_consume_reset_short_password_returns_none(hub_db):
    with session_scope() as s:
        _user(s, "user@example.com")
    token, _ = password_resets.request_reset("user@example.com")
    assert password_resets.consume_reset(token, "short") is None


def test_consume_reset_already_consumed_returns_none(hub_db):
    with session_scope() as s:
        _user(s, "user@example.com")
    token, _ = password_resets.request_reset("user@example.com")
    password_resets.consume_reset(token, "a-new-password")
    assert password_resets.consume_reset(token, "another-password") is None


# ── expire_old ─────────────────────────────────────────────────────────

def test_expire_old_deletes_stale_unconsumed_resets(hub_db):
    with session_scope() as s:
        _user(s, "user@example.com")
    password_resets.request_reset("user@example.com")
    # expire_old() deletes unconsumed resets older than 7 days.
    _backdate_reset(timedelta(days=-10))
    assert password_resets.expire_old() >= 1
