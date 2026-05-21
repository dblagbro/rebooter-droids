"""Unit tests — the scoped API-token service (Hub Tier-2 Feature 4a).

`app/services/api_tokens.py` — mint / list / revoke / verify scoped
bearer tokens. The plaintext is returned once at mint; only a SHA-256
hash is stored. DB-backed cases use the `hub_db` isolated-SQLite
fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db import session_scope
from app.models import ApiToken
from app.models.api_tokens import (
    DEFAULT_EXPIRY_DAYS,
    SCOPE_READ,
    SCOPE_WRITE,
    TOKEN_STRING_PREFIX,
)
from app.services import api_tokens as svc


# ── mint ───────────────────────────────────────────────────────────────


def test_mint_returns_serialized_row_and_plaintext(hub_db):
    row, plaintext = svc.mint(name="CI bot")
    assert row["id"].startswith("apt_")
    assert row["name"] == "CI bot"
    assert plaintext.startswith(TOKEN_STRING_PREFIX)
    # Prefix shown in the list matches the start of the plaintext.
    assert plaintext.startswith(row["token_prefix"])
    # Default scope is read-only.
    assert row["scopes"] == [SCOPE_READ]


def test_mint_never_stores_plaintext(hub_db):
    _, plaintext = svc.mint(name="bot")
    with session_scope() as s:
        stored = s.scalars(__import__("sqlalchemy").select(ApiToken)).all()
    assert len(stored) == 1
    # The stored hash is not the plaintext.
    assert stored[0].token_hash != plaintext
    assert len(stored[0].token_hash) == 64  # sha256 hex


def test_mint_write_scope_implies_read(hub_db):
    row, _ = svc.mint(name="w", scopes=["write"])
    assert set(row["scopes"]) == {SCOPE_READ, SCOPE_WRITE}


def test_mint_rejects_blank_name(hub_db):
    with pytest.raises(svc.ApiTokenError):
        svc.mint(name="   ")


def test_mint_rejects_unknown_scope(hub_db):
    with pytest.raises(svc.ApiTokenError):
        svc.mint(name="bot", scopes=["admin"])


def test_mint_default_expiry_is_set(hub_db):
    row, _ = svc.mint(name="bot")
    assert row["expires_at"] is not None
    expires = datetime.strptime(
        row["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    delta = expires - datetime.now(timezone.utc)
    # ~DEFAULT_EXPIRY_DAYS, allow a day of slack.
    assert timedelta(days=DEFAULT_EXPIRY_DAYS - 1) < delta < timedelta(
        days=DEFAULT_EXPIRY_DAYS + 1
    )


def test_mint_zero_days_means_never_expires(hub_db):
    row, _ = svc.mint(name="bot", expires_in_days=0)
    assert row["expires_at"] is None


def test_mint_rejects_expiry_over_cap(hub_db):
    with pytest.raises(svc.ApiTokenError):
        svc.mint(name="bot", expires_in_days=svc.MAX_EXPIRY_DAYS + 1)


# ── verify (issuance → verification round trip) ────────────────────────


def test_verify_accepts_a_freshly_minted_token(hub_db):
    row, plaintext = svc.mint(name="bot", scopes=["write"])
    principal = svc.verify(plaintext)
    assert principal is not None
    assert principal["token_id"] == row["id"]
    assert principal["name"] == "bot"
    assert set(principal["scopes"]) == {SCOPE_READ, SCOPE_WRITE}


def test_verify_rejects_unknown_token(hub_db):
    svc.mint(name="bot")
    assert svc.verify(TOKEN_STRING_PREFIX + "definitely-not-real") is None


def test_verify_rejects_non_rbt_string(hub_db):
    assert svc.verify("Bearer something") is None
    assert svc.verify("") is None
    assert svc.verify(None) is None


def test_verify_rejects_revoked_token(hub_db):
    row, plaintext = svc.mint(name="bot")
    assert svc.verify(plaintext) is not None
    assert svc.revoke(row["id"]) is True
    assert svc.verify(plaintext) is None


def test_verify_rejects_expired_token(hub_db):
    _, plaintext = svc.mint(name="bot")
    # Backdate the expiry directly.
    with session_scope() as s:
        tok = s.scalars(__import__("sqlalchemy").select(ApiToken)).one()
        tok.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert svc.verify(plaintext) is None


def test_verify_touches_last_used_at(hub_db):
    row, plaintext = svc.mint(name="bot")
    assert row["last_used_at"] is None
    svc.verify(plaintext)
    refreshed = svc.get(row["id"])
    assert refreshed["last_used_at"] is not None


# ── list / revoke ──────────────────────────────────────────────────────


def test_list_tokens_newest_first(hub_db):
    svc.mint(name="first")
    svc.mint(name="second")
    names = [t["name"] for t in svc.list_tokens()]
    assert names == ["second", "first"]


def test_list_can_exclude_revoked(hub_db):
    row, _ = svc.mint(name="gone")
    svc.mint(name="keep")
    svc.revoke(row["id"])
    visible = [t["name"] for t in svc.list_tokens(include_revoked=False)]
    assert visible == ["keep"]


def test_revoke_unknown_id_returns_false(hub_db):
    assert svc.revoke("apt_nope") is False


def test_revoke_is_idempotent(hub_db):
    row, _ = svc.mint(name="bot")
    assert svc.revoke(row["id"]) is True
    assert svc.revoke(row["id"]) is True


# ── has_scope ──────────────────────────────────────────────────────────


def test_has_scope():
    read_only = {"scopes": [SCOPE_READ]}
    writer = {"scopes": [SCOPE_READ, SCOPE_WRITE]}
    assert svc.has_scope(read_only, SCOPE_READ) is True
    assert svc.has_scope(read_only, SCOPE_WRITE) is False
    assert svc.has_scope(writer, SCOPE_WRITE) is True
    assert svc.has_scope(writer, SCOPE_READ) is True
    assert svc.has_scope(None, SCOPE_READ) is False
