"""Settings → Webhooks — the management UI for outbound notification
channels, event subscriptions and the delivery log.

Per `docs/notes/2026-05-20-hub-tier2-design.md` Feature 4b — the
Settings sub-page sitting on top of the Feature-6 notifications engine
(`app/services/notifications.py` + `webhook_delivery.py`).

Routes:
  GET  /app/settings/webhooks            — list channels + subscriptions
                                           + recent delivery attempts.
  POST /app/settings/webhooks/create     — create a channel.
  POST /app/settings/webhooks/<id>/test  — fire a synthetic event
                                           through the SSRF-guarded
                                           sender.
  POST /app/settings/webhooks/<id>/toggle  — enable/disable a channel.
  POST /app/settings/webhooks/<id>/delete  — delete a channel.
  POST /app/settings/webhooks/subscriptions/create — bind an event type.
  POST /app/settings/webhooks/subscriptions/<id>/delete — remove a sub.
"""

from __future__ import annotations

from flask import flash, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_ui
from app.models.notifications import CHANNEL_KINDS, EVENT_TYPES
from app.services import audit as audit_service
from app.services import notifications


def _webhooks_ctx(extra: dict | None = None) -> dict:
    """Standard template context for the Webhooks page — channel list,
    subscriptions, recent deliveries and the static enum lists the form
    selects need."""
    base = {
        "active": "settings",
        "settings_tab": "webhooks",
        "channels": notifications.list_channels(),
        "subscriptions": notifications.list_subscriptions(),
        "deliveries": _recent_deliveries(),
        "channel_kinds": CHANNEL_KINDS,
        "event_types": EVENT_TYPES,
    }
    base.update(extra or {})
    return _ctx(base)


def _recent_deliveries(limit: int = 25) -> list[dict]:
    """The last N delivery attempts, newest first — for the 'last
    delivery: 200 OK / failed' panel."""
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import WebhookDelivery

    def _iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(WebhookDelivery)
                .order_by(WebhookDelivery.created_at.desc())
                .limit(limit)
            )
        )
        return [
            {
                "id": d.id,
                "channel_id": d.channel_id,
                "event_type": d.event_type,
                "status": d.status,
                "attempts": d.attempts,
                "http_status": d.http_status,
                "response_snippet": (d.response_snippet or "")[:120],
                "created_at": _iso(d.created_at),
            }
            for d in rows
        ]


# ── Page ───────────────────────────────────────────────────────────────


@admin_ui_bp.get("/settings/webhooks")
@admin_required_ui
def settings_webhooks_page():
    return render_template("settings/webhooks.html", **_webhooks_ctx())


# ── Channel routes ─────────────────────────────────────────────────────


@admin_ui_bp.post("/settings/webhooks/create")
@admin_required_ui
def settings_webhooks_create_submit():
    name = (request.form.get("name") or "").strip()
    kind = (request.form.get("kind") or "").strip()
    # The config fields the template renders for every kind; the service
    # validates the subset relevant to `kind`.
    config = {
        "url": (request.form.get("url") or "").strip(),
        "method": (request.form.get("method") or "POST").strip(),
        "app_token": (request.form.get("app_token") or "").strip(),
        "user_key": (request.form.get("user_key") or "").strip(),
    }
    try:
        channel = notifications.create_channel(
            name=name,
            kind=kind,
            config=config,
            created_by_user_id=getattr(g.current_user, "id", None),
        )
    except notifications.NotificationError as e:
        flash(f"Could not create channel: {e.message}", "error")
        return redirect(url_for("admin_ui.settings_webhooks_page"))

    audit_service.record(
        "webhook_channel.created",
        actor_user_id=getattr(g.current_user, "id", None),
        target_type="webhook_channel",
        target_id=channel["id"],
        details={"name": channel["name"], "kind": channel["kind"]},
    )
    flash(f"Webhook channel '{channel['name']}' created.", "success")
    return redirect(url_for("admin_ui.settings_webhooks_page"))


@admin_ui_bp.post("/settings/webhooks/<channel_id>/toggle")
@admin_required_ui
def settings_webhooks_toggle_submit(channel_id: str):
    channel = notifications.toggle_channel(channel_id)
    if channel is None:
        flash("Channel not found.", "error")
        return redirect(url_for("admin_ui.settings_webhooks_page"))
    audit_service.record(
        "webhook_channel.toggled",
        actor_user_id=getattr(g.current_user, "id", None),
        target_type="webhook_channel",
        target_id=channel_id,
        details={"enabled": channel["enabled"]},
    )
    flash(
        f"Channel '{channel['name']}' "
        f"{'enabled' if channel['enabled'] else 'disabled'}.",
        "success",
    )
    return redirect(url_for("admin_ui.settings_webhooks_page"))


@admin_ui_bp.post("/settings/webhooks/<channel_id>/delete")
@admin_required_ui
def settings_webhooks_delete_submit(channel_id: str):
    if not notifications.delete_channel(channel_id):
        flash("Channel not found.", "error")
        return redirect(url_for("admin_ui.settings_webhooks_page"))
    audit_service.record(
        "webhook_channel.deleted",
        actor_user_id=getattr(g.current_user, "id", None),
        target_type="webhook_channel",
        target_id=channel_id,
    )
    flash("Webhook channel deleted.", "success")
    return redirect(url_for("admin_ui.settings_webhooks_page"))


@admin_ui_bp.post("/settings/webhooks/<channel_id>/test")
@admin_required_ui
def settings_webhooks_test_submit(channel_id: str):
    """Fire a synthetic event through the SSRF-guarded sender — sends
    *now*, inline, so the operator gets immediate feedback on whether
    the receiver is reachable and the URL passes the SSRF policy."""
    channel = notifications.get_channel(channel_id)
    if channel is None:
        flash("Channel not found.", "error")
        return redirect(url_for("admin_ui.settings_webhooks_page"))

    # Queue a synthetic delivery, then drain it inline so the result is
    # visible immediately rather than waiting for the 15s worker tick.
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import WebhookChannel, WebhookDelivery
    from app.models.notifications import DELIVERY_PENDING
    from app.services.webhook_delivery import send_one

    from datetime import datetime, timezone

    with session_scope() as session:
        ch = session.get(WebhookChannel, channel_id)
        delivery = WebhookDelivery(
            channel_id=channel_id,
            event_type="watchdog.rule_fired",
            payload={"name": "Test notification",
                     "note": "Synthetic test from Settings → Webhooks"},
            status=DELIVERY_PENDING,
            attempts=0,
            next_attempt_at=datetime.now(timezone.utc),
            organization_id=getattr(ch, "organization_id", None),
        )
        session.add(delivery)
        session.flush()
        delivery_id = delivery.id

    result = send_one(delivery_id)
    audit_service.record(
        "webhook_channel.tested",
        actor_user_id=getattr(g.current_user, "id", None),
        target_type="webhook_channel",
        target_id=channel_id,
        details=result,
    )
    if result.get("status") == "sent":
        flash(
            f"Test delivered — HTTP {result.get('http_status')}.", "success"
        )
    else:
        flash(
            f"Test delivery did not succeed: "
            f"{result.get('error') or result.get('status')}.",
            "error",
        )
    return redirect(url_for("admin_ui.settings_webhooks_page"))


# ── Subscription routes ────────────────────────────────────────────────


@admin_ui_bp.post("/settings/webhooks/subscriptions/create")
@admin_required_ui
def settings_webhooks_subscription_create_submit():
    event_type = (request.form.get("event_type") or "").strip()
    channel_id = (request.form.get("channel_id") or "").strip()
    site_id = (request.form.get("site_id") or "").strip() or None
    try:
        sub = notifications.create_subscription(
            event_type=event_type, channel_id=channel_id, site_id=site_id
        )
    except notifications.NotificationError as e:
        flash(f"Could not create subscription: {e.message}", "error")
        return redirect(url_for("admin_ui.settings_webhooks_page"))
    audit_service.record(
        "notification_subscription.created",
        actor_user_id=getattr(g.current_user, "id", None),
        target_type="notification_subscription",
        target_id=sub["id"],
        details={"event_type": event_type, "channel_id": channel_id},
    )
    flash("Event subscription created.", "success")
    return redirect(url_for("admin_ui.settings_webhooks_page"))


@admin_ui_bp.post("/settings/webhooks/subscriptions/<subscription_id>/delete")
@admin_required_ui
def settings_webhooks_subscription_delete_submit(subscription_id: str):
    if not notifications.delete_subscription(subscription_id):
        flash("Subscription not found.", "error")
        return redirect(url_for("admin_ui.settings_webhooks_page"))
    audit_service.record(
        "notification_subscription.deleted",
        actor_user_id=getattr(g.current_user, "id", None),
        target_type="notification_subscription",
        target_id=subscription_id,
    )
    flash("Event subscription removed.", "success")
    return redirect(url_for("admin_ui.settings_webhooks_page"))
