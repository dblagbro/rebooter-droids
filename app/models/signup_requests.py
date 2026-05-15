"""v0.5.39: Signup request model for self-service access requests."""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column


class SignupRequest(Base):
    """Self-service signup requests submitted via public form.

    Admins review and can approve (sends invitation) or reject.
    """
    __tablename__ = "signup_requests"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "sreq")
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status: pending, approved, rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # Reviewer tracking
    reviewed_by_user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reviewed_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)

    # If approved, link to created invitation
    invitation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    created_at: Mapped[datetime] = ts_column()


Index("ix_signup_requests_status", SignupRequest.status)
Index("ix_signup_requests_email", SignupRequest.email)
Index("ix_signup_requests_created_at", SignupRequest.created_at)
