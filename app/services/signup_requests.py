"""v0.5.39: Signup request service for self-service access requests."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import SignupRequest


class SignupRequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def create_signup_request(
    email: str,
    display_name: str,
    message: str | None = None,
) -> SignupRequest:
    """Create a new signup request from public form submission."""
    email = email.lower().strip()
    display_name = display_name.strip()

    if not email or "@" not in email:
        raise SignupRequestError("validation_failed", "Valid email is required")
    if not display_name:
        raise SignupRequestError("validation_failed", "Display name is required")

    # Check for duplicate pending request from same email
    with session_scope() as session:
        existing = session.scalar(
            select(SignupRequest)
            .where(
                SignupRequest.email == email,
                SignupRequest.status == "pending"
            )
        )
        if existing:
            raise SignupRequestError(
                "duplicate_request",
                "You already have a pending signup request. Please wait for admin review."
            )

    request = SignupRequest(
        email=email,
        display_name=display_name,
        message=message,
        status="pending",
    )

    with session_scope() as session:
        session.add(request)
        session.flush()
        session.expunge(request)

    return request


def list_signup_requests(status: str | None = None) -> list[SignupRequest]:
    """List signup requests, optionally filtered by status."""
    with session_scope() as session:
        stmt = select(SignupRequest).order_by(SignupRequest.created_at.desc())
        if status:
            stmt = stmt.where(SignupRequest.status == status)
        rows = list(session.scalars(stmt))
        for r in rows:
            session.expunge(r)
    return rows


def get_signup_request(request_id: str) -> SignupRequest | None:
    """Get a single signup request by ID."""
    with session_scope() as session:
        req = session.get(SignupRequest, request_id)
        if req:
            session.expunge(req)
        return req


def approve_signup_request(
    request_id: str,
    reviewer_user_id: str,
    invitation_id: str,
) -> SignupRequest | None:
    """Mark a signup request as approved and link to created invitation."""
    with session_scope() as session:
        req = session.get(SignupRequest, request_id)
        if not req:
            return None
        if req.status != "pending":
            raise SignupRequestError(
                "already_reviewed",
                f"Request already {req.status}"
            )

        req.status = "approved"
        req.reviewed_by_user_id = reviewer_user_id
        req.reviewed_at = datetime.now(timezone.utc)
        req.invitation_id = invitation_id
        session.add(req)
        session.flush()
        session.expunge(req)
        return req


def reject_signup_request(
    request_id: str,
    reviewer_user_id: str,
) -> SignupRequest | None:
    """Mark a signup request as rejected."""
    with session_scope() as session:
        req = session.get(SignupRequest, request_id)
        if not req:
            return None
        if req.status != "pending":
            raise SignupRequestError(
                "already_reviewed",
                f"Request already {req.status}"
            )

        req.status = "rejected"
        req.reviewed_by_user_id = reviewer_user_id
        req.reviewed_at = datetime.now(timezone.utc)
        session.add(req)
        session.flush()
        session.expunge(req)
        return req


def count_pending_requests() -> int:
    """Count pending signup requests for admin notification."""
    from sqlalchemy import func
    with session_scope() as session:
        count = session.scalar(
            select(func.count(SignupRequest.id))
            .where(SignupRequest.status == "pending")
        )
        return count or 0
