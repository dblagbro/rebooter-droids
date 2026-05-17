"""Password-reset flow — v0.4.1.

Single-use, short-TTL tokens (default 1 h). Token plaintext exists
only in the email body + the URL the user clicks; DB stores SHA-256.

API:
    request_reset(email, *, ip)              → (token_or_none, masked_email)
    consume_reset(token, new_password, *, ip) → User | None

Both functions are intentionally non-disclosing: `request_reset`
always succeeds-shaped (email-or-not), and `consume_reset` does NOT
distinguish "unknown token" from "expired token" in its return shape
beyond None — the route handler maps any None to a generic
"link-expired" page.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import load_settings
from app.db import session_scope
from app.models import PasswordReset, User
from app.models._helpers import as_aware


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mask_email(email: str) -> str:
    """Return e.g. `dbl****@earthlink.net` for the post-submit
    confirmation page so the operator can sanity-check they typed
    the right address without leaking it back fully."""
    if "@" not in email:
        return email
    user, _, domain = email.partition("@")
    if len(user) <= 3:
        return f"{user[:1]}***@{domain}"
    return f"{user[:3]}***@{domain}"


def request_reset(email: str, *, ip: str | None = None) -> tuple[str | None, str]:
    """Generate a reset token if a user with this email exists.

    Always returns the masked-email form; the second tuple element is
    None only when no user exists (caller decides whether to leak that
    fact — UI doesn't).
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None, _mask_email(email)

    settings = load_settings()
    with session_scope() as session:
        u = session.scalar(select(User).where(User.email == email))
        if u is None or not u.is_active:
            return None, _mask_email(email)

        # v0.4.26: TTL prefers runtime_settings → env-var →
        # config dataclass default.
        ttl = settings.password_reset_ttl_seconds
        try:
            from app.services import runtime_settings as _rs
            v = _rs.get(
                "system.password_reset_ttl_seconds",
                env_var="REBOOTER_PASSWORD_RESET_TTL_SECONDS",
                default=None,
            )
            if v is not None:
                ttl = int(v)
        except Exception:
            pass

        secret = "pwr_" + secrets.token_urlsafe(24)
        record = PasswordReset(
            user_id=u.id,
            email_snapshot=email,
            token_hash=_hash(secret),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl),
            requested_ip=ip,
        )
        session.add(record)
        session.flush()
    return secret, _mask_email(email)


def consume_reset(token: str, new_password: str, *, ip: str | None = None) -> User | None:
    if not token or not new_password or len(new_password) < 8:
        return None
    from app.services.bootstrap import hash_password

    th = _hash(token)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        rec = session.scalar(
            select(PasswordReset).where(PasswordReset.token_hash == th)
        )
        if (
            rec is None
            or rec.consumed_at is not None
            or as_aware(rec.expires_at) <= now
        ):
            return None
        u = session.get(User, rec.user_id)
        if u is None or not u.is_active:
            return None
        u.password_hash = hash_password(new_password)
        u.updated_at = now
        # Invalidate every existing session/JWT issued for this user
        # — the security-sensitive "log everyone out" behaviour every
        # password reset implies.
        u.tokens_valid_after = now
        rec.consumed_at = now
        rec.consumed_ip = ip
        session.flush()
        session.expunge(u)
        return u


def expire_old() -> int:
    """Optional periodic cleanup. Not wired up to APScheduler in
    v0.4.1; left here so the future probe-runtime can call it."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)  # keep recent ones for audit
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(PasswordReset).where(PasswordReset.expires_at < cutoff)
            )
        )
        count = 0
        for r in rows:
            if r.consumed_at is None:  # only delete unused
                session.delete(r)
                count += 1
        return count
