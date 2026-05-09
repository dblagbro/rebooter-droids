"""Server-side session table — v0.2.10 ships in *shadow* mode.

Every UI login + JWT issuance writes a row. v0.2.10 does NOT consult
this table at request authorisation time — that flip is gated behind
the `REBOOTER_SESSIONS_ENFORCE` setting (default false) and lands in
a future minor. Today the table is pure observability + the storage
foundation for the future enforce path.

This closes BUG-005 (cookie-revocation gap) once enforce flips: with
this table populated, "revoke everywhere" can mark every active row
revoked and a future authoriser can reject any cookie or JWT whose
`session_id` is revoked, regardless of cookie expiry or JWT TTL.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


SESSION_KIND_COOKIE = "cookie"
SESSION_KIND_ACCESS = "access"
SESSION_KIND_REFRESH = "refresh"


class Session(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "sess")
    )
    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    issued_at: Mapped[datetime] = ts_column()
    last_seen_at: Mapped[datetime] = ts_column()
    expires_at: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    revoked_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)

    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)


Index("ix_user_sessions_user_active", Session.user_id, Session.revoked_at)
Index("ix_user_sessions_jti", Session.jti)
