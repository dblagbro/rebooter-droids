"""Multi-hub sync replicator daemon (RFC-004 Option C / B11 Phase 5).

Polls peer hubs' /api/v1/sync/since endpoints, fetches outbox events,
applies them locally, and updates sync cursors. Runs as an APScheduler
background job (target: every 3s for steady-state ~1-3s latency).
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from app.db import session_scope
from app.services import sync as sync_svc
from app.services import runtime_settings as rs

log = logging.getLogger(__name__)


def _get_peer_hubs() -> list[dict[str, Any]]:
    """Fetch configured peer hubs from runtime settings.

    Returns list of peer hub configs:
    [{"id": "www2", "url": "https://www2.voipguru.org/rebooter", "token": "..."}]

    Config stored in runtime_settings as JSON at key "sync.peer_hubs".
    Returns empty list if not configured.
    """
    peers_json = rs.get("sync.peer_hubs", default="[]")
    if isinstance(peers_json, str):
        import json
        try:
            peers = json.loads(peers_json)
        except (json.JSONDecodeError, TypeError):
            log.warning("Invalid sync.peer_hubs config: %r", peers_json)
            return []
    else:
        peers = peers_json

    if not isinstance(peers, list):
        log.warning("sync.peer_hubs must be a list, got %s", type(peers))
        return []

    return peers


def _fetch_events_from_peer(
    peer_url: str,
    peer_token: str,
    since_seq: int,
    limit: int = 100,
) -> tuple[list[dict], int, bool]:
    """Fetch outbox events from a peer hub.

    Returns (events, next_seq, has_more).

    On error, returns ([], since_seq, False) and logs the exception.
    """
    try:
        resp = requests.get(
            f"{peer_url}/api/v1/sync/since",
            params={"seq": since_seq, "limit": limit},
            headers={"Authorization": f"Bearer {peer_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return (
            data.get("events", []),
            data.get("next_seq", since_seq),
            data.get("has_more", False),
        )
    except Exception as e:
        log.exception(
            "Failed to fetch events from peer %s since seq=%d: %s",
            peer_url,
            since_seq,
            e,
        )
        return ([], since_seq, False)


def _apply_event_batch(peer_id: str, events: list[dict]) -> dict[str, int]:
    """Apply a batch of events from a peer hub.

    Returns stats: {"applied": N, "skipped": M, "errors": K}
    """
    stats = {"applied": 0, "skipped": 0, "errors": 0}

    with session_scope() as session:
        for event_data in events:
            try:
                # Reconstruct OutboxEvent from JSON
                from app.models.sync import OutboxEvent

                event = OutboxEvent(
                    seq=event_data["seq"],
                    at=event_data["at"],  # ISO string, SQLAlchemy will parse
                    event_type=event_data["event_type"],
                    entity_type=event_data["entity_type"],
                    entity_id=event_data["entity_id"],
                    payload=event_data["payload"],
                    scope_claims=event_data.get("scope_claims"),
                    tombstone_for=event_data.get("tombstone_for"),
                )

                # Apply the event
                applied = sync_svc.apply_outbox_event(session, event)
                if applied:
                    stats["applied"] += 1
                else:
                    stats["skipped"] += 1

            except Exception:
                log.exception("Failed to apply event seq=%d from peer %s", event_data.get("seq"), peer_id)
                stats["errors"] += 1

        # Update cursor after successful batch
        if events and stats["errors"] == 0:
            last_seq = events[-1]["seq"]
            sync_svc.update_sync_cursor(session, peer_id, last_seq)

    return stats


def tick() -> dict[str, Any]:
    """Main replicator tick: poll all peers and apply events.

    Returns stats: {"peers_polled": N, "events_applied": M, "errors": K}
    """
    # Check if sync is enabled
    sync_enabled = rs.get("sync.enabled", default=False)
    if not sync_enabled:
        return {"peers_polled": 0, "events_applied": 0, "errors": 0, "skipped": "sync disabled"}

    peers = _get_peer_hubs()
    if not peers:
        return {"peers_polled": 0, "events_applied": 0, "errors": 0, "skipped": "no peers configured"}

    total_stats = {"peers_polled": 0, "events_applied": 0, "errors": 0}

    for peer in peers:
        peer_id = peer.get("id")
        peer_url = peer.get("url")
        peer_token = peer.get("token")

        if not (peer_id and peer_url and peer_token):
            log.warning("Peer missing required fields: %r", peer)
            total_stats["errors"] += 1
            continue

        try:
            # Get last sync cursor for this peer
            with session_scope() as session:
                last_seq = sync_svc.get_sync_cursor(session, peer_id)

            # Fetch events from peer
            events, next_seq, has_more = _fetch_events_from_peer(
                peer_url, peer_token, last_seq, limit=100
            )

            if events:
                # Apply event batch
                batch_stats = _apply_event_batch(peer_id, events)
                total_stats["events_applied"] += batch_stats["applied"]
                total_stats["errors"] += batch_stats["errors"]

                log.debug(
                    "Synced from peer %s: %d events, %d applied, %d skipped, %d errors",
                    peer_id,
                    len(events),
                    batch_stats["applied"],
                    batch_stats["skipped"],
                    batch_stats["errors"],
                )

            total_stats["peers_polled"] += 1

        except Exception:
            log.exception("Sync tick failed for peer %s", peer_id)
            total_stats["errors"] += 1

    return total_stats
