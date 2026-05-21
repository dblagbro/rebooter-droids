"""Outbound notifications / webhooks data model — Tier-2 Feature 6.

Per `docs/notes/2026-05-20-hub-tier2-design.md` Feature 6.

Three tables, all org-scoped (the org boundary shipped on `main` in
Phases 1-2):

  * `webhook_channels`        — a configured outbound destination.
  * `notification_subscriptions` — which hub events feed which channels.
  * `webhook_deliveries`      — the delivery queue, processed by an
    APScheduler job with retry/backoff.

All three mix in `TenantScoped` so they are born with an
`organization_id` column and are auto-filtered by the `do_orm_execute`
tenant read filter / stamped by the `before_flush` write hook
(`app/services/tenant_scope.py`).

New tables: `Base.metadata.create_all()` at startup adds them on every
deployment; an Alembic revision under `migrations/` gives parity for
managed upgrades.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models._helpers import new_id, ts_column
from app.services.tenant_scope import TenantScoped


# ── Channel kinds ──────────────────────────────────────────────────────
#
# `webhook_generic` — operator-supplied URL + method + headers.
# `slack` / `discord` — an incoming-webhook URL; the sender formats a
#   kind-specific JSON body.
# `pushover` — an app token + a user key.
CHANNEL_KIND_WEBHOOK = "webhook_generic"
CHANNEL_KIND_SLACK = "slack"
CHANNEL_KIND_DISCORD = "discord"
CHANNEL_KIND_PUSHOVER = "pushover"
CHANNEL_KINDS = (
    CHANNEL_KIND_WEBHOOK,
    CHANNEL_KIND_SLACK,
    CHANNEL_KIND_DISCORD,
    CHANNEL_KIND_PUSHOVER,
)

# ── Event types ────────────────────────────────────────────────────────
#
# The v1 event catalogue (design Feature 6 "Concepts" + Q5).
EVENT_WATCHDOG_RULE_FIRED = "watchdog.rule_fired"
EVENT_WATCHDOG_RULE_ESCALATED = "watchdog.rule_escalated"
EVENT_DEVICE_WENT_OFFLINE = "device.went_offline"
EVENT_DEVICE_RECOVERED = "device.recovered"
EVENT_DEVICE_FAILSAFE = "device.failsafe"
EVENT_FIRMWARE_DEPLOYMENT_COMPLETED = "firmware.deployment_completed"
EVENT_TYPES = (
    EVENT_WATCHDOG_RULE_FIRED,
    EVENT_WATCHDOG_RULE_ESCALATED,
    EVENT_DEVICE_WENT_OFFLINE,
    EVENT_DEVICE_RECOVERED,
    EVENT_DEVICE_FAILSAFE,
    EVENT_FIRMWARE_DEPLOYMENT_COMPLETED,
)

# ── Delivery lifecycle ─────────────────────────────────────────────────
#
# pending → (worker) → sent | failed → (retry) → … → dead.
DELIVERY_PENDING = "pending"
DELIVERY_SENT = "sent"
DELIVERY_FAILED = "failed"   # transient — will be retried.
DELIVERY_DEAD = "dead"       # terminal — retries exhausted.
DELIVERY_STATUSES = (
    DELIVERY_PENDING,
    DELIVERY_SENT,
    DELIVERY_FAILED,
    DELIVERY_DEAD,
)


class WebhookChannel(TenantScoped, Base):
    """A configured outbound notification destination.

    `config` is a JSON blob whose shape depends on `kind` — for
    `webhook_generic`: `{url, method, headers}`; for `slack`/`discord`:
    `{url}`; for `pushover`: `{app_token, user_key}`. Secret values
    inside `config` (the Slack URL is itself a secret, Pushover tokens)
    are write-only at the UI layer and redacted by the Feature-3 backup
    export.

    `signing_secret` is the per-channel HMAC key — every outbound body
    is signed `X-Rebooter-Signature: sha256=<hex>` so receivers can
    verify authenticity.
    """

    # TODO(org-phase3): flip `organization_id` to NOT NULL alongside the
    # other Tier-A tables; swap `name`'s scope to UNIQUE(org, name).
    __tablename__ = "webhook_channels"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "whc")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Per-channel HMAC secret for X-Rebooter-Signature.
    signing_secret: Mapped[str | None] = mapped_column(String(80), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()


class NotificationSubscription(TenantScoped, Base):
    """Binds a hub event type to a channel.

    When `notifications.emit(event_type, payload, ...)` runs it resolves
    every enabled subscription whose `event_type` matches (and whose
    `site_id` filter, if set, matches the event's site) and queues one
    `webhook_deliveries` row per matched channel.
    """

    __tablename__ = "notification_subscriptions"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "nsub")
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("webhook_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional site filter — NULL = fire for events from any site.
    site_id: Mapped[str | None] = mapped_column(
        String(40),
        ForeignKey("sites.id", ondelete="SET NULL"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_at: Mapped[datetime] = ts_column()

    __table_args__ = (
        Index("ix_notification_subscriptions_event", "event_type"),
        Index("ix_notification_subscriptions_channel", "channel_id"),
    )


class WebhookDelivery(TenantScoped, Base):
    """One queued outbound send.

    Rows are inserted `pending` by `notifications.emit()`, picked up by
    the `webhook_delivery_tick` APScheduler job, moved to `sent` on a 2xx
    or `failed` on an error, retried with exponential backoff up to
    `MAX_ATTEMPTS`, then `dead`.

    `id` is a BigInteger autoincrement — this is the highest-volume of
    the three tables. The `(status, next_attempt_at)` index is what the
    worker's "claim due deliveries" query rides.
    """

    __tablename__ = "webhook_deliveries"

    # BigInteger on Postgres; Integer on SQLite so the PK autoincrements
    # (SQLite only autoincrements an INTEGER PRIMARY KEY). Same variant
    # pattern as `audit_events` / `device_power_samples`.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    channel_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("webhook_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DELIVERY_PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()

    __table_args__ = (
        # The worker's hot query: "deliveries due to send right now".
        Index(
            "ix_webhook_deliveries_status_next",
            "status",
            "next_attempt_at",
        ),
    )
