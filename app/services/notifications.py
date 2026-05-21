"""Outbound notifications — channel + subscription CRUD and `emit()`.

Per `docs/notes/2026-05-20-hub-tier2-design.md` Feature 6.

This module owns the *configuration* and the *enqueue* side of the
notifications engine:

  * channel CRUD  — create / list / get / update / toggle / delete
    `webhook_channels` rows.
  * subscription CRUD — bind hub event types to channels.
  * `emit(event_type, payload, site_id=...)` — the single hook every
    other part of the hub calls when something happens. It resolves the
    matching enabled subscriptions and inserts one `webhook_deliveries`
    row per matched channel. It does **not** send inline — the
    APScheduler `webhook_delivery_tick` job drains the queue.

`emit()` is strictly best-effort: it never raises into its caller, so a
slow DB or a config error can never degrade heartbeat ingestion or a
watchdog tick (same discipline as `device_config.record_reported_config`).

The SSRF-guarded sender + retry/backoff worker live in
`app/services/webhook_delivery.py`.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import NotificationSubscription, WebhookChannel, WebhookDelivery
from app.models.notifications import (
    CHANNEL_KINDS,
    CHANNEL_KIND_PUSHOVER,
    CHANNEL_KIND_WEBHOOK,
    DELIVERY_PENDING,
    EVENT_TYPES,
)

log = logging.getLogger(__name__)

# Secret config keys — never echoed back to the UI, redacted in the
# Feature-3 backup export. Keep greppable.
SECRET_CONFIG_KEYS = ("url", "app_token", "user_key", "webhook_auth_token")


class NotificationError(ValueError):
    """Operator-facing validation failure for channel / subscription
    config. `code` lets a JSON route pick the right HTTP status."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _iso(dt) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


# ── Channel config validation ──────────────────────────────────────────


def _validate_channel_config(kind: str, config: dict) -> dict:
    """Validate + normalise a channel's `config` blob for its `kind`.

    Returns the cleaned config; raises `NotificationError`. The deep
    SSRF check on the URL happens at *send* time (a channel may be saved
    before its receiver's DNS is live) — here we only check shape.
    """
    if not isinstance(config, dict):
        raise NotificationError("validation_failed", "config must be an object")

    if kind in (CHANNEL_KIND_WEBHOOK, "slack", "discord"):
        url = str(config.get("url") or "").strip()
        if not url:
            raise NotificationError(
                "validation_failed", f"{kind} channel requires a 'url'"
            )
        if not (url.startswith("http://") or url.startswith("https://")):
            raise NotificationError(
                "validation_failed", "url must start with http:// or https://"
            )
        cleaned: dict = {"url": url}
        if kind == CHANNEL_KIND_WEBHOOK:
            method = str(config.get("method") or "POST").strip().upper()
            if method not in ("POST", "PUT"):
                raise NotificationError(
                    "validation_failed", "method must be POST or PUT"
                )
            cleaned["method"] = method
            headers = config.get("headers") or {}
            if not isinstance(headers, dict):
                raise NotificationError(
                    "validation_failed", "headers must be an object"
                )
            # Stringify header values; drop blanks.
            cleaned["headers"] = {
                str(k): str(v) for k, v in headers.items() if str(v).strip()
            }
        return cleaned

    if kind == CHANNEL_KIND_PUSHOVER:
        app_token = str(config.get("app_token") or "").strip()
        user_key = str(config.get("user_key") or "").strip()
        if not app_token or not user_key:
            raise NotificationError(
                "validation_failed",
                "pushover channel requires 'app_token' and 'user_key'",
            )
        return {"app_token": app_token, "user_key": user_key}

    raise NotificationError(
        "validation_failed", f"unknown channel kind: {kind!r}"
    )


# ── Serialisation ──────────────────────────────────────────────────────


def _redact_config(kind: str, config: dict) -> dict:
    """Config with secret values replaced by a sentinel — for the list
    UI and the backup export. The raw `config` never leaves the service
    layer through a serialiser."""
    out: dict = {}
    for k, v in (config or {}).items():
        if k in SECRET_CONFIG_KEYS and v:
            out[k] = "__redacted__"
        else:
            out[k] = v
    return out


def serialize_channel(c: WebhookChannel, *, redacted: bool = True) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,
        "config": _redact_config(c.kind, c.config) if redacted else dict(c.config or {}),
        "enabled": c.enabled,
        "has_signing_secret": bool(c.signing_secret),
        "created_at": _iso(c.created_at),
        "updated_at": _iso(c.updated_at),
    }


def serialize_subscription(s: NotificationSubscription) -> dict:
    return {
        "id": s.id,
        "event_type": s.event_type,
        "channel_id": s.channel_id,
        "site_id": s.site_id,
        "enabled": s.enabled,
        "created_at": _iso(s.created_at),
    }


# ── Channel CRUD ───────────────────────────────────────────────────────


def create_channel(
    *,
    name: str,
    kind: str,
    config: dict,
    created_by_user_id: str | None = None,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise NotificationError("validation_failed", "name is required")
    if len(name) > 120:
        raise NotificationError(
            "validation_failed", "name must be 120 characters or fewer"
        )
    if kind not in CHANNEL_KINDS:
        raise NotificationError(
            "validation_failed",
            f"kind must be one of {CHANNEL_KINDS}",
        )
    cleaned = _validate_channel_config(kind, config or {})
    channel = WebhookChannel(
        name=name,
        kind=kind,
        config=cleaned,
        # Per-channel HMAC secret for X-Rebooter-Signature. URL-safe,
        # ~43 chars — well within the column width.
        signing_secret=secrets.token_urlsafe(32),
        enabled=True,
        created_by_user_id=created_by_user_id,
    )
    with session_scope() as session:
        session.add(channel)
        session.flush()
        return serialize_channel(channel)


def list_channels() -> list[dict]:
    with session_scope() as session:
        return [
            serialize_channel(c)
            for c in session.scalars(
                select(WebhookChannel).order_by(WebhookChannel.name)
            )
        ]


def get_channel(channel_id: str) -> dict | None:
    with session_scope() as session:
        c = session.get(WebhookChannel, channel_id)
        return serialize_channel(c) if c is not None else None


def update_channel(
    channel_id: str, *, name: str, config: dict | None = None
) -> dict | None:
    """Update a channel's name and (optionally) its config.

    A `config` of None means "name only" — the existing config and
    secrets are untouched. This is the same "blank = unchanged" trick
    the SMTP-password field uses: the UI sends config only when the
    operator actually re-enters a URL/token.
    """
    name = (name or "").strip()
    if not name:
        raise NotificationError("validation_failed", "name is required")
    with session_scope() as session:
        c = session.get(WebhookChannel, channel_id)
        if c is None:
            return None
        c.name = name
        if config is not None:
            c.config = _validate_channel_config(c.kind, config)
        c.updated_at = datetime.now(timezone.utc)
        session.flush()
        return serialize_channel(c)


def toggle_channel(channel_id: str) -> dict | None:
    with session_scope() as session:
        c = session.get(WebhookChannel, channel_id)
        if c is None:
            return None
        c.enabled = not c.enabled
        c.updated_at = datetime.now(timezone.utc)
        session.flush()
        return serialize_channel(c)


def delete_channel(channel_id: str) -> bool:
    with session_scope() as session:
        c = session.get(WebhookChannel, channel_id)
        if c is None:
            return False
        session.delete(c)
        session.flush()
        return True


# ── Subscription CRUD ──────────────────────────────────────────────────


def create_subscription(
    *, event_type: str, channel_id: str, site_id: str | None = None
) -> dict:
    if event_type not in EVENT_TYPES:
        raise NotificationError(
            "validation_failed",
            f"event_type must be one of {EVENT_TYPES}",
        )
    with session_scope() as session:
        if session.get(WebhookChannel, channel_id) is None:
            raise NotificationError(
                "channel_unknown", "channel not found"
            )
        sub = NotificationSubscription(
            event_type=event_type,
            channel_id=channel_id,
            site_id=(site_id or None),
            enabled=True,
        )
        session.add(sub)
        session.flush()
        return serialize_subscription(sub)


def list_subscriptions() -> list[dict]:
    with session_scope() as session:
        return [
            serialize_subscription(s)
            for s in session.scalars(
                select(NotificationSubscription).order_by(
                    NotificationSubscription.event_type
                )
            )
        ]


def delete_subscription(subscription_id: str) -> bool:
    with session_scope() as session:
        s = session.get(NotificationSubscription, subscription_id)
        if s is None:
            return False
        session.delete(s)
        session.flush()
        return True


# ── emit() — the single enqueue hook ───────────────────────────────────


def _queue_for_channel(
    session: Session,
    channel: WebhookChannel,
    event_type: str,
    payload: dict,
) -> None:
    """Insert one `pending` delivery row, due immediately."""
    session.add(
        WebhookDelivery(
            channel_id=channel.id,
            event_type=event_type,
            payload=payload or {},
            status=DELIVERY_PENDING,
            attempts=0,
            next_attempt_at=datetime.now(timezone.utc),
            # Inherit the channel's org so the delivery row is correctly
            # tenant-scoped even when emit() runs in a system context.
            organization_id=channel.organization_id,
        )
    )


def emit(event_type: str, payload: dict, *, site_id: str | None = None) -> int:
    """Queue outbound deliveries for a hub event.

    Resolves every enabled `NotificationSubscription` matching
    `event_type` (and, when the subscription carries a `site_id` filter,
    matching `site_id`) and inserts one `pending` `webhook_deliveries`
    row per enabled target channel. Returns the number of rows queued.

    Best-effort by contract: any exception is swallowed and logged, so
    `emit()` is safe to call from a watchdog tick, the heartbeat path, a
    failsafe handler or a deployment finaliser without a try/except at
    every call site. It never sends inline — the
    `webhook_delivery_tick` job drains the queue.
    """
    if event_type not in EVENT_TYPES:
        log.warning("notifications.emit: unknown event_type %r — ignored", event_type)
        return 0
    try:
        queued = 0
        with session_scope() as session:
            subs = list(
                session.scalars(
                    select(NotificationSubscription).where(
                        NotificationSubscription.event_type == event_type,
                        NotificationSubscription.enabled.is_(True),
                    )
                )
            )
            for sub in subs:
                # A subscription with a site filter only fires for that
                # site; a NULL filter fires for every site.
                if sub.site_id and site_id and sub.site_id != site_id:
                    continue
                channel = session.get(WebhookChannel, sub.channel_id)
                if channel is None or not channel.enabled:
                    continue
                _queue_for_channel(session, channel, event_type, payload)
                queued += 1
            session.flush()
        if queued:
            log.info(
                "notifications.emit: %s queued %d delivery(ies)",
                event_type, queued,
            )
        return queued
    except Exception:
        # NEVER raise into the caller — emit() is best-effort.
        log.exception("notifications.emit failed for event_type=%s", event_type)
        return 0
