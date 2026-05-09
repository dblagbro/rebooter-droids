"""Server-side session bookkeeping — v0.2.10 *shadow mode*.

Writes only. The middleware does NOT yet consult this table to
authorise requests; flipping that switch lands in a future minor
behind `REBOOTER_SESSIONS_ENFORCE` (default false).

All public functions are best-effort: they MUST NOT raise back into
the auth path. A failed session record write should never block a
login.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from flask import has_request_context, request
from sqlalchemy import select, update

from app.db import session_scope
from app.models import Session
from app.models.sessions import (
    SESSION_KIND_ACCESS,
    SESSION_KIND_COOKIE,
    SESSION_KIND_REFRESH,
)

log = logging.getLogger(__name__)


def new_jti() -> str:
    return secrets.token_urlsafe(24)


def _request_meta() -> tuple[str | None, str | None]:
    if not has_request_context():
        return None, None
    ua = (request.headers.get("User-Agent") or "")[:255] or None
    return ua, request.remote_addr


def record(
    user_id: str,
    kind: str,
    jti: str,
    ttl_seconds: int,
) -> str | None:
    """Write a session row at login / token issuance. Returns the new
    row id on success, None on failure (best-effort)."""
    try:
        ua, ip = _request_meta()
        now = datetime.now(timezone.utc)
        row = Session(
            user_id=user_id,
            kind=kind,
            jti=jti,
            issued_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            user_agent=ua,
            ip=ip,
        )
        with session_scope() as s:
            s.add(row)
            s.flush()
            return row.id
    except Exception:
        log.exception(
            "session record failed for user=%s kind=%s", user_id, kind
        )
        return None


def revoke_one(jti: str) -> bool:
    try:
        now = datetime.now(timezone.utc)
        with session_scope() as s:
            res = s.execute(
                update(Session)
                .where(Session.jti == jti, Session.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            return (res.rowcount or 0) > 0
    except Exception:
        log.exception("revoke_one failed for jti=%s", jti)
        return False


def revoke_all_for_user(user_id: str) -> int:
    try:
        now = datetime.now(timezone.utc)
        with session_scope() as s:
            res = s.execute(
                update(Session)
                .where(
                    Session.user_id == user_id,
                    Session.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            return res.rowcount or 0
    except Exception:
        log.exception("revoke_all_for_user failed for user=%s", user_id)
        return 0


def list_active_for_user(user_id: str) -> list[dict]:
    """For the future "where am I signed in" surface. Read-only."""
    with session_scope() as s:
        rows = list(
            s.scalars(
                select(Session)
                .where(
                    Session.user_id == user_id,
                    Session.revoked_at.is_(None),
                )
                .order_by(Session.issued_at.desc())
            )
        )
        return [
            {
                "id": r.id,
                "kind": r.kind,
                "issued_at": r.issued_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expires_at": r.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "user_agent": r.user_agent,
                "ip": r.ip,
            }
            for r in rows
        ]


# Re-exported for callers (auth blueprint, login handlers)
KIND_COOKIE = SESSION_KIND_COOKIE
KIND_ACCESS = SESSION_KIND_ACCESS
KIND_REFRESH = SESSION_KIND_REFRESH
