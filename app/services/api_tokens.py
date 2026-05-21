"""API-token service — mint / list / revoke / verify scoped bearer tokens.

Feature 4a of the Hub Tier-2 design
(`docs/notes/2026-05-20-hub-tier2-design.md` §4a).

The token plaintext (`rbt_<random>`) is generated and returned to the
caller exactly once, by `mint()`. Only its SHA-256 hash is persisted —
the same one-way discipline `app/services/enrollment.py` uses for device
and enrollment tokens. `verify()` hashes the presented bearer string and
looks the row up by hash, using a constant-time compare and rejecting
revoked / expired tokens.

The blueprint layer stays a thin HTTP translator; all validation,
hashing and expiry logic lives here.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import ApiToken
from app.models._helpers import as_aware
from app.models.api_tokens import (
    DEFAULT_EXPIRY_DAYS,
    KNOWN_SCOPES,
    SCOPE_READ,
    SCOPE_WRITE,
    TOKEN_STRING_PREFIX,
)

log = logging.getLogger(__name__)

# Length cap on the operator-facing label — matches the column width.
MAX_NAME_LEN = 120
# Hard ceiling on operator-chosen expiry so a public-SaaS bearer token
# can never be effectively immortal — design §4a "Risks".
MAX_EXPIRY_DAYS = 365


class ApiTokenError(ValueError):
    """A token operation failed validation. `code` is a stable
    machine-readable string; `message` is operator-facing."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _hash(token: str) -> str:
    """SHA-256 hex of a token plaintext — mirrors `enrollment._hash`."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_scopes(scopes) -> list[str]:
    """Normalise an operator-supplied scope list.

    Empty / None defaults to read-only. Unknown scopes are rejected.
    `write` implies `read` so a write token can also GET.
    """
    if not scopes:
        return [SCOPE_READ]
    out: set[str] = set()
    for s in scopes:
        s = str(s).strip().lower()
        if s not in KNOWN_SCOPES:
            raise ApiTokenError(
                "validation_failed",
                f"unknown scope {s!r}; allowed: {', '.join(KNOWN_SCOPES)}",
            )
        out.add(s)
    if SCOPE_WRITE in out:
        out.add(SCOPE_READ)
    # Deterministic order (read before write) for stable serialisation.
    return [s for s in (SCOPE_READ, SCOPE_WRITE) if s in out]


def _resolve_expiry(expires_in_days: int | None) -> datetime | None:
    """Map an operator-chosen lifetime to an absolute `expires_at`.

    `None` → the DEFAULT_EXPIRY_DAYS default. `0` is treated as "never
    expires" (an explicit operator choice). A positive value is capped
    at MAX_EXPIRY_DAYS.
    """
    if expires_in_days is None:
        days = DEFAULT_EXPIRY_DAYS
    else:
        days = int(expires_in_days)
        if days < 0:
            raise ApiTokenError(
                "validation_failed", "expiry days cannot be negative"
            )
        if days == 0:
            return None  # explicit "never expires"
        if days > MAX_EXPIRY_DAYS:
            raise ApiTokenError(
                "validation_failed",
                f"expiry cannot exceed {MAX_EXPIRY_DAYS} days",
            )
    return datetime.now(timezone.utc) + timedelta(days=days)


def serialize(token: ApiToken) -> dict:
    """List-/detail-shape dict. Never includes a plaintext or the hash."""
    return {
        "id": token.id,
        "name": token.name,
        "token_prefix": token.token_prefix,
        "scopes": list(token.scopes or []),
        "site_id": token.site_id,
        "created_by_user_id": token.created_by_user_id,
        "created_at": _iso(token.created_at),
        "expires_at": _iso(token.expires_at),
        "last_used_at": _iso(token.last_used_at),
        "revoked": bool(token.revoked),
        "expired": is_expired(token),
    }


def _iso(dt: datetime | None) -> str | None:
    dt = as_aware(dt)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else None


def is_expired(token: ApiToken, *, now: datetime | None = None) -> bool:
    """True when the token has a past expiry. NULL expiry → never."""
    if token.expires_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    return as_aware(token.expires_at) < now


def mint(
    *,
    name: str,
    scopes=None,
    site_id: str | None = None,
    expires_in_days: int | None = None,
    created_by_user_id: str | None = None,
) -> tuple[dict, str]:
    """Create a new API token.

    Returns `(serialized_row, plaintext)`. The plaintext is the ONLY
    time the secret is available — the caller must surface it to the
    operator once and never persist it.
    """
    name = (name or "").strip()
    if not name:
        raise ApiTokenError("validation_failed", "name is required")
    if len(name) > MAX_NAME_LEN:
        raise ApiTokenError(
            "validation_failed",
            f"name must be {MAX_NAME_LEN} characters or fewer",
        )
    cleaned_scopes = _clean_scopes(scopes)
    expires_at = _resolve_expiry(expires_in_days)

    # `rbt_` + 32 urlsafe random bytes (~43 chars). secrets is CSPRNG.
    plaintext = TOKEN_STRING_PREFIX + secrets.token_urlsafe(32)
    row = ApiToken(
        name=name,
        token_hash=_hash(plaintext),
        token_prefix=plaintext[:12],
        scopes=cleaned_scopes,
        site_id=site_id or None,
        created_by_user_id=created_by_user_id,
        expires_at=expires_at,
        revoked=False,
    )
    with session_scope() as session:
        session.add(row)
        session.flush()
        return serialize(row), plaintext


def list_tokens(*, include_revoked: bool = True) -> list[dict]:
    """All tokens, newest first. The org read-filter scopes this when an
    org context is bound."""
    with session_scope() as session:
        stmt = select(ApiToken).order_by(ApiToken.created_at.desc())
        if not include_revoked:
            stmt = stmt.where(ApiToken.revoked.is_(False))
        return [serialize(t) for t in session.scalars(stmt)]


def get(token_id: str) -> dict | None:
    with session_scope() as session:
        row = session.get(ApiToken, token_id)
        return serialize(row) if row is not None else None


def revoke(token_id: str) -> bool:
    """Mark a token revoked. Idempotent — revoking an already-revoked
    token still returns True. Returns False only for an unknown id."""
    with session_scope() as session:
        row = session.get(ApiToken, token_id)
        if row is None:
            return False
        row.revoked = True
        session.flush()
        return True


def verify(presented: str | None) -> dict | None:
    """Resolve a presented bearer string to a live token.

    Returns a principal dict `{token_id, name, scopes, site_id,
    organization_id, created_by_user_id}` for a valid token, or None
    when the string is malformed / unknown / revoked / expired.

    Touches `last_used_at` on a successful verify (best-effort — a
    write hiccup never fails the auth).
    """
    if not presented or not isinstance(presented, str):
        return None
    presented = presented.strip()
    if not presented.startswith(TOKEN_STRING_PREFIX):
        return None
    presented_hash = _hash(presented)
    with session_scope() as session:
        row = session.scalar(
            select(ApiToken).where(ApiToken.token_hash == presented_hash)
        )
        if row is None:
            return None
        # Constant-time compare even though the lookup was by hash — keeps
        # the verify path uniform with the device-credential discipline
        # and immune to a future lookup change.
        if not hmac.compare_digest(row.token_hash, presented_hash):
            return None
        if row.revoked:
            return None
        if is_expired(row):
            return None
        try:
            row.last_used_at = datetime.now(timezone.utc)
            session.flush()
        except Exception:  # pragma: no cover - best-effort
            log.debug("api-token last_used_at update failed", exc_info=True)
        return {
            "token_id": row.id,
            "name": row.name,
            "scopes": list(row.scopes or []),
            "site_id": row.site_id,
            "organization_id": getattr(row, "organization_id", None),
            "created_by_user_id": row.created_by_user_id,
        }


def has_scope(principal: dict | None, scope: str) -> bool:
    """Whether a verified principal carries `scope`. A `write` principal
    implicitly satisfies a `read` check (mint() always stores read
    alongside write, but check defensively here too)."""
    if not principal:
        return False
    scopes = set(principal.get("scopes") or [])
    if scope in scopes:
        return True
    if scope == SCOPE_READ and SCOPE_WRITE in scopes:
        return True
    return False
