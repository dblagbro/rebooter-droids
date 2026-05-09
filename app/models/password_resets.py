"""Password-reset tokens — v0.4.1.

Single-use, short-TTL (default 1 h) tokens emailed to the user. The
token is stored hashed; the plaintext only exists in the email body
+ the URL the user follows.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "pwr")
    )
    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    email_snapshot: Mapped[str] = mapped_column(String(254), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    expires_at: Mapped[datetime] = ts_column(default_now=False, nullable=False)
    consumed_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)
    requested_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consumed_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = ts_column()


Index("ix_password_resets_user_id", PasswordReset.user_id)
Index("ix_password_resets_expires_at", PasswordReset.expires_at)
