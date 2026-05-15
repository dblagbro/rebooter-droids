"""Multi-hub sync API endpoints (RFC-004 Option C).

Provides /api/v1/sync/since for peer hubs to poll outbox events.
"""
from __future__ import annotations

import logging

from flask import Blueprint, request, jsonify
from pydantic import BaseModel, ValidationError

from app.db import session_scope
from app.middleware.admin_auth import require_authenticated
from app.services import sync as sync_svc

log = logging.getLogger(__name__)

bp = Blueprint("sync_api", __name__)


class SyncSinceRequest(BaseModel):
    """Query parameters for /api/v1/sync/since endpoint."""
    seq: int = 0  # Sequence number to sync from (default 0 = all events)
    limit: int = 100  # Max events to return (default 100)


@bp.get("/api/v1/sync/since")
@require_authenticated  # TODO: Change to HMAC bearer auth for peer hubs
def sync_since():
    """Fetch outbox events since a given sequence number.

    Query parameters:
    - seq: Last sequence number peer has applied (default 0)
    - limit: Max events to return (default 100, max 1000)

    Returns:
    {
        "events": [
            {
                "seq": 1,
                "at": "2026-05-15T12:34:56.789Z",
                "event_type": "device.created",
                "entity_type": "device",
                "entity_id": "dev_...",
                "payload": {...},
                "scope_claims": {...},
                "tombstone_for": null
            },
            ...
        ],
        "next_seq": 101,  # Peer should request from this seq next time
        "has_more": false  # Whether more events exist beyond this batch
    }
    """
    try:
        params = SyncSinceRequest(
            seq=int(request.args.get("seq", 0)),
            limit=min(int(request.args.get("limit", 100)), 1000),
        )
    except (ValueError, ValidationError) as e:
        return jsonify({"error": "invalid_params", "detail": str(e)}), 400

    with session_scope() as session:
        events = sync_svc.fetch_outbox_events_since(
            session,
            since_seq=params.seq,
            limit=params.limit,
        )

        # Serialize events
        events_data = []
        for event in events:
            events_data.append({
                "seq": event.seq,
                "at": event.at.isoformat(),
                "event_type": event.event_type,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "payload": event.payload,
                "scope_claims": event.scope_claims,
                "tombstone_for": event.tombstone_for,
            })

        # Determine if more events exist
        next_seq = events[-1].seq if events else params.seq
        # Simple heuristic: if we got a full batch, there might be more
        has_more = len(events) == params.limit

        return jsonify({
            "events": events_data,
            "next_seq": next_seq,
            "has_more": has_more,
        })


@bp.get("/api/v1/sync/status")
@require_authenticated
def sync_status():
    """Get sync status for this hub.

    Returns information about peer cursors and outbox state.
    """
    with session_scope() as session:
        from sqlalchemy import select, func
        from app.models.sync import SyncCursor, OutboxEvent

        # Get all peer cursors
        cursors = list(session.scalars(select(SyncCursor)))
        cursors_data = []
        for cursor in cursors:
            cursors_data.append({
                "peer_hub_id": cursor.peer_hub_id,
                "last_seq": cursor.last_seq,
                "updated_at": cursor.updated_at.isoformat(),
                "last_error": cursor.last_error,
                "last_error_at": cursor.last_error_at.isoformat() if cursor.last_error_at else None,
            })

        # Get outbox stats
        max_seq = session.scalar(select(func.max(OutboxEvent.seq))) or 0
        event_count = session.scalar(select(func.count()).select_from(OutboxEvent)) or 0

        return jsonify({
            "outbox": {
                "max_seq": max_seq,
                "total_events": event_count,
            },
            "peer_cursors": cursors_data,
        })
