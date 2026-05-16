"""Multi-hub sync models (RFC-004 Option C).

Option C architecture:
- Each hub keeps an append-only `outbox_events` table mirroring mutations
- Replicator daemon polls `/api/v1/sync/since?seq=<n>` on peer hubs
- Idempotent apply with UUID-keyed rows
- Last-writer-wins on `event.at` with audit row preserving both versions
- Tombstone pattern for deletes
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import ts_column


class OutboxEvent(Base):
    """Append-only event log for multi-hub sync.

    Every mutation (create, update, delete) emits one outbox event.
    Replicator daemons poll peer hubs' outbox and apply events locally.
    """
    __tablename__ = "outbox_events"

    # Sequential ID used for sync cursors. BigInteger on Postgres;
    # plain Integer on SQLite so the PK still ROWID-aliases and
    # autoincrements (lets the applier/emission be unit-tested without
    # Postgres — same precedent as DeviceHeartbeat).
    seq: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )

    # When the event occurred (used for last-writer-wins conflict resolution)
    at: Mapped[datetime] = ts_column()

    # Event type (e.g., "device.created", "device.updated", "site.deleted")
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)

    # Entity type (e.g., "device", "site", "group", "user")
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)

    # Entity ULID (the primary key of the affected resource)
    entity_id: Mapped[str] = mapped_column(String(40), nullable=False)

    # Full entity payload as JSON (for creates/updates) or tombstone marker (for deletes)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Optional: scope claims for RBAC enforcement on peer applier
    # e.g., {"scope_type": "site", "scope_id": "sit_..."}
    scope_claims: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # For delete events: UUID of the entity being deleted (enables tombstone tracking)
    tombstone_for: Mapped[str | None] = mapped_column(String(40), nullable=True)


Index("ix_outbox_seq", OutboxEvent.seq)
Index("ix_outbox_at", OutboxEvent.at.desc())
Index("ix_outbox_entity", OutboxEvent.entity_type, OutboxEvent.entity_id)
Index("ix_outbox_tombstone", OutboxEvent.tombstone_for)


class SyncCursor(Base):
    """Tracks last-applied sequence number per peer hub.

    Each hub stores one cursor per peer to track sync progress.
    """
    __tablename__ = "sync_cursors"

    # Peer hub identifier (e.g., "www", "www2")
    peer_hub_id: Mapped[str] = mapped_column(String(40), primary_key=True)

    # Last successfully applied sequence number from this peer
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # When the cursor was last updated
    updated_at: Mapped[datetime] = ts_column()

    # Last error encountered while syncing from this peer (for diagnostics)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # When the last error occurred
    last_error_at: Mapped[datetime | None] = ts_column(default_now=False, nullable=True)


Index("ix_sync_cursor_updated", SyncCursor.updated_at.desc())


class Tombstone(Base):
    """Tracks deleted entities to prevent resurrection during sync.

    When a delete event replicates from a peer, we write a tombstone.
    Future create/update events for the same UUID are rejected.
    """
    __tablename__ = "tombstones"

    # Entity ULID that was deleted
    entity_id: Mapped[str] = mapped_column(String(40), primary_key=True)

    # Entity type for filtering
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)

    # When the tombstone was created
    created_at: Mapped[datetime] = ts_column()

    # Which outbox event seq triggered this tombstone
    from_outbox_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)


Index("ix_tombstone_entity_type", Tombstone.entity_type)
Index("ix_tombstone_created", Tombstone.created_at.desc())
