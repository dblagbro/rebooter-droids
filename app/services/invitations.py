from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import Settings
from app.db import session_scope
from app.models import Invitation
from app.models.users import ALL_ROLES
from app.services.users import UserError, create_user


class InvitationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_invitation(
    settings: Settings,
    email: str,
    role: str,
    issued_by_user_id: str | None,
    note: str | None = None,
) -> tuple[Invitation, str]:
    email = email.lower().strip()
    if not email or "@" not in email:
        raise InvitationError("validation_failed", "valid email is required")
    if role not in ALL_ROLES:
        raise InvitationError("validation_failed", f"role must be one of {ALL_ROLES}")

    secret = "inv_" + secrets.token_urlsafe(24)
    record = Invitation(
        email=email,
        role=role,
        token_hash=_hash(secret),
        issued_by_user_id=issued_by_user_id,
        note=note,
        expires_at=datetime.now(timezone.utc)
        + timedelta(seconds=settings.invitation_ttl_seconds),
    )
    with session_scope() as session:
        session.add(record)
        session.flush()
        session.expunge(record)
    return record, secret


def list_invitations() -> list[Invitation]:
    with session_scope() as session:
        rows = list(
            session.scalars(select(Invitation).order_by(Invitation.created_at.desc()))
        )
        for r in rows:
            session.expunge(r)
    return rows


def lookup_pending(token: str) -> Invitation | None:
    h = _hash(token)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        inv = session.scalar(select(Invitation).where(Invitation.token_hash == h))
        if inv is None or inv.consumed_at is not None or inv.expires_at <= now:
            return None
        session.expunge(inv)
        return inv


def redeem_invitation(
    token: str,
    password: str,
    display_name: str,
) -> dict:
    if len(password) < 8:
        raise InvitationError("validation_failed", "password must be at least 8 characters")

    h = _hash(token)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        # Row-level lock to prevent double-redeem races.
        inv = session.scalar(
            select(Invitation).where(Invitation.token_hash == h).with_for_update()
        )
        if inv is None:
            raise InvitationError("invitation_invalid", "invitation token is not recognized")
        if inv.consumed_at is not None:
            raise InvitationError("invitation_consumed", "invitation already used")
        if inv.expires_at <= now:
            raise InvitationError("invitation_expired", "invitation has expired")

        try:
            user_dict = create_user(
                email=inv.email,
                password=password,
                display_name=display_name,
                role=inv.role,
            )
        except UserError as e:
            raise InvitationError("validation_failed", str(e))

        inv.consumed_at = now
        inv.consumed_by_user_id = user_dict["id"]
        session.add(inv)
        session.flush()
        return user_dict
