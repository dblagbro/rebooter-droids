"""Unit tests — the outbound-notifications engine (Tier-2 Feature 6).

Covers `app/services/notifications.py` (channel + subscription CRUD,
`emit()`) and `app/services/webhook_delivery.py` (per-kind payload
formatters, the signing helper, retry/backoff, the worker `tick()`).

All DB-backed cases use the `hub_db` isolated-SQLite fixture and run
inside `tenant_scope.system()` — the new tables mix in `TenantScoped`,
so a write needs either an active org scope or an explicit system
bypass (the same context the real scheduler job runs under).

Network is never touched: `webhook_delivery.send_one` is exercised with
the SSRF guard's sender monkeypatched, so a "send" is deterministic.
"""

from __future__ import annotations

import json

import pytest

from app.db import session_scope
from app.models import WebhookChannel, WebhookDelivery
from app.models.notifications import (
    DELIVERY_DEAD,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_SENT,
)
from app.services import notifications, tenant_scope, webhook_delivery
from app.services.ssrf_guard import SSRFBlockedError


@pytest.fixture
def sysctx():
    """Run the test body inside an explicit system() tenant bypass —
    the three new tables are TenantScoped."""
    with tenant_scope.system():
        yield


# ── channel CRUD ───────────────────────────────────────────────────────


def test_create_channel_slack(hub_db, sysctx):
    ch = notifications.create_channel(
        name="Ops Slack", kind="slack",
        config={"url": "https://hooks.slack.com/services/T/B/X"},
    )
    assert ch["id"].startswith("whc_")
    assert ch["kind"] == "slack"
    # The URL is a secret — never echoed back through the serialiser.
    assert ch["config"]["url"] == "__redacted__"
    # A per-channel HMAC secret is auto-generated.
    assert ch["has_signing_secret"] is True


def test_create_channel_generic_normalises_config(hub_db, sysctx):
    ch = notifications.create_channel(
        name="Custom", kind="webhook_generic",
        config={"url": "https://api.example.com/hook", "method": "put",
                "headers": {"X-Token": "abc", "X-Blank": ""}},
    )
    raw = notifications.get_channel(ch["id"])
    assert raw is not None
    # method upper-cased, blank header dropped — checked via the DB row.
    with session_scope() as s:
        row = s.get(WebhookChannel, ch["id"])
        assert row.config["method"] == "PUT"
        assert row.config["headers"] == {"X-Token": "abc"}


def test_create_channel_pushover(hub_db, sysctx):
    ch = notifications.create_channel(
        name="Phone", kind="pushover",
        config={"app_token": "atok", "user_key": "ukey"},
    )
    assert ch["kind"] == "pushover"
    # Pushover token/key are secret — redacted.
    assert ch["config"]["app_token"] == "__redacted__"
    assert ch["config"]["user_key"] == "__redacted__"


def test_create_channel_rejects_blank_name(hub_db, sysctx):
    with pytest.raises(notifications.NotificationError):
        notifications.create_channel(
            name="  ", kind="slack", config={"url": "https://x.example.com"}
        )


def test_create_channel_rejects_unknown_kind(hub_db, sysctx):
    with pytest.raises(notifications.NotificationError):
        notifications.create_channel(
            name="X", kind="carrier_pigeon", config={}
        )


def test_create_channel_rejects_missing_url(hub_db, sysctx):
    with pytest.raises(notifications.NotificationError):
        notifications.create_channel(name="X", kind="slack", config={})


def test_create_channel_rejects_missing_pushover_keys(hub_db, sysctx):
    with pytest.raises(notifications.NotificationError):
        notifications.create_channel(
            name="X", kind="pushover", config={"app_token": "only"}
        )


def test_update_channel_name_only_keeps_config(hub_db, sysctx):
    ch = notifications.create_channel(
        name="Old", kind="slack", config={"url": "https://x.example.com/a"}
    )
    notifications.update_channel(ch["id"], name="New", config=None)
    with session_scope() as s:
        row = s.get(WebhookChannel, ch["id"])
        assert row.name == "New"
        # config untouched — "blank = unchanged".
        assert row.config["url"] == "https://x.example.com/a"


def test_toggle_channel(hub_db, sysctx):
    ch = notifications.create_channel(
        name="X", kind="slack", config={"url": "https://x.example.com"}
    )
    assert ch["enabled"] is True
    toggled = notifications.toggle_channel(ch["id"])
    assert toggled["enabled"] is False


def test_delete_channel(hub_db, sysctx):
    ch = notifications.create_channel(
        name="X", kind="slack", config={"url": "https://x.example.com"}
    )
    assert notifications.delete_channel(ch["id"]) is True
    assert notifications.get_channel(ch["id"]) is None
    assert notifications.delete_channel("whc_nope") is False


def test_new_tables_are_org_scoped():
    """The three new models must be born org-scoped (TenantScoped)."""
    for model in (WebhookChannel, WebhookDelivery):
        assert issubclass(model, tenant_scope.TenantScoped)
        assert hasattr(model, "organization_id")


# ── subscription CRUD ──────────────────────────────────────────────────


def test_create_subscription(hub_db, sysctx):
    ch = notifications.create_channel(
        name="X", kind="slack", config={"url": "https://x.example.com"}
    )
    sub = notifications.create_subscription(
        event_type="watchdog.rule_fired", channel_id=ch["id"]
    )
    assert sub["id"].startswith("nsub_")
    assert sub["event_type"] == "watchdog.rule_fired"


def test_create_subscription_rejects_unknown_event(hub_db, sysctx):
    ch = notifications.create_channel(
        name="X", kind="slack", config={"url": "https://x.example.com"}
    )
    with pytest.raises(notifications.NotificationError):
        notifications.create_subscription(
            event_type="aliens.landed", channel_id=ch["id"]
        )


def test_create_subscription_rejects_unknown_channel(hub_db, sysctx):
    with pytest.raises(notifications.NotificationError) as exc:
        notifications.create_subscription(
            event_type="watchdog.rule_fired", channel_id="whc_nope"
        )
    assert exc.value.code == "channel_unknown"


def test_delete_subscription(hub_db, sysctx):
    ch = notifications.create_channel(
        name="X", kind="slack", config={"url": "https://x.example.com"}
    )
    sub = notifications.create_subscription(
        event_type="watchdog.rule_fired", channel_id=ch["id"]
    )
    assert notifications.delete_subscription(sub["id"]) is True
    assert notifications.delete_subscription("nsub_nope") is False


# ── emit() — queue, not send ───────────────────────────────────────────


def test_emit_queues_one_delivery_per_subscribed_channel(hub_db, sysctx):
    ch1 = notifications.create_channel(
        name="A", kind="slack", config={"url": "https://a.example.com"}
    )
    ch2 = notifications.create_channel(
        name="B", kind="discord", config={"url": "https://b.example.com"}
    )
    notifications.create_subscription(
        event_type="device.went_offline", channel_id=ch1["id"]
    )
    notifications.create_subscription(
        event_type="device.went_offline", channel_id=ch2["id"]
    )
    queued = notifications.emit("device.went_offline", {"device_name": "TV"})
    assert queued == 2
    with session_scope() as s:
        rows = list(s.scalars(__import__("sqlalchemy").select(WebhookDelivery)))
        assert len(rows) == 2
        assert all(r.status == DELIVERY_PENDING for r in rows)


def test_emit_ignores_disabled_channels(hub_db, sysctx):
    ch = notifications.create_channel(
        name="A", kind="slack", config={"url": "https://a.example.com"}
    )
    notifications.create_subscription(
        event_type="device.recovered", channel_id=ch["id"]
    )
    notifications.toggle_channel(ch["id"])  # now disabled
    assert notifications.emit("device.recovered", {}) == 0


def test_emit_site_filter(hub_db, sysctx):
    """A subscription with a site filter only fires for that site."""
    ch = notifications.create_channel(
        name="A", kind="slack", config={"url": "https://a.example.com"}
    )
    notifications.create_subscription(
        event_type="device.failsafe", channel_id=ch["id"],
        site_id="site_alpha",
    )
    # Event from a different site — filtered out.
    assert notifications.emit("device.failsafe", {}, site_id="site_beta") == 0
    # Event from the matching site — queued.
    assert notifications.emit("device.failsafe", {}, site_id="site_alpha") == 1


def test_emit_unknown_event_type_is_noop(hub_db, sysctx):
    assert notifications.emit("not.a.real.event", {}) == 0


def test_emit_never_raises(hub_db, sysctx, monkeypatch):
    """emit() is best-effort — a DB error inside it must not propagate."""
    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(notifications, "session_scope", _boom)
    # Must return 0, not raise.
    assert notifications.emit("watchdog.rule_fired", {}) == 0


# ── webhook_delivery — formatters + signing ────────────────────────────


def test_sign_body_is_deterministic_sha256():
    sig = webhook_delivery.sign_body("secret", b"payload")
    assert sig.startswith("sha256=")
    # Same input → same signature.
    assert sig == webhook_delivery.sign_body("secret", b"payload")
    # Different secret → different signature.
    assert sig != webhook_delivery.sign_body("other", b"payload")


def test_format_generic_payload(hub_db, sysctx):
    ch_id = notifications.create_channel(
        name="C", kind="webhook_generic",
        config={"url": "https://api.example.com/h", "method": "POST",
                "headers": {"X-Env": "prod"}},
    )["id"]
    with session_scope() as s:
        ch = s.get(WebhookChannel, ch_id)
        url, method, headers, body = webhook_delivery.format_delivery(
            ch, "watchdog.rule_fired", {"rule_name": "R1"}
        )
    assert url == "https://api.example.com/h"
    assert method == "POST"
    assert headers["X-Env"] == "prod"
    doc = json.loads(body)
    assert doc["event"] == "watchdog.rule_fired"
    assert doc["payload"]["rule_name"] == "R1"


def test_format_slack_payload(hub_db, sysctx):
    ch_id = notifications.create_channel(
        name="S", kind="slack", config={"url": "https://hooks.slack.com/x"}
    )["id"]
    with session_scope() as s:
        ch = s.get(WebhookChannel, ch_id)
        url, method, headers, body = webhook_delivery.format_delivery(
            ch, "device.went_offline", {"device_name": "Garage TV"}
        )
    doc = json.loads(body)
    assert "text" in doc
    assert "Garage TV" in doc["text"]


def test_format_discord_payload(hub_db, sysctx):
    ch_id = notifications.create_channel(
        name="D", kind="discord", config={"url": "https://discord.com/api/webhooks/x"}
    )["id"]
    with session_scope() as s:
        ch = s.get(WebhookChannel, ch_id)
        _, _, _, body = webhook_delivery.format_delivery(
            ch, "device.recovered", {"device_name": "Router"}
        )
    doc = json.loads(body)
    assert "content" in doc  # Discord uses `content`, not `text`.


def test_format_pushover_payload(hub_db, sysctx):
    ch_id = notifications.create_channel(
        name="P", kind="pushover",
        config={"app_token": "atok", "user_key": "ukey"},
    )["id"]
    with session_scope() as s:
        ch = s.get(WebhookChannel, ch_id)
        url, _, headers, body = webhook_delivery.format_delivery(
            ch, "device.failsafe", {}
        )
    assert url == webhook_delivery.PUSHOVER_API_URL
    assert "application/x-www-form-urlencoded" in headers["Content-Type"]
    assert b"token=atok" in body and b"user=ukey" in body


# ── webhook_delivery — send_one + retry/backoff ────────────────────────


class _FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def _queue_one(channel_id) -> int:
    """Helper: queue a single pending delivery, return its id."""
    from datetime import datetime, timezone

    with session_scope() as s:
        ch = s.get(WebhookChannel, channel_id)
        d = WebhookDelivery(
            channel_id=channel_id, event_type="watchdog.rule_fired",
            payload={"rule_name": "R"}, status=DELIVERY_PENDING, attempts=0,
            next_attempt_at=datetime.now(timezone.utc),
            organization_id=getattr(ch, "organization_id", None),
        )
        s.add(d)
        s.flush()
        return d.id


def test_send_one_success_marks_sent(hub_db, sysctx, monkeypatch):
    ch_id = notifications.create_channel(
        name="S", kind="slack", config={"url": "https://hooks.slack.com/x"}
    )["id"]
    did = _queue_one(ch_id)

    monkeypatch.setattr(
        webhook_delivery, "safe_request",
        lambda *a, **k: _FakeResponse(200, "ok"),
    )
    result = webhook_delivery.send_one(did)
    assert result["status"] == DELIVERY_SENT
    with session_scope() as s:
        d = s.get(WebhookDelivery, did)
        assert d.status == DELIVERY_SENT
        assert d.http_status == 200
        assert d.attempts == 1
        assert d.next_attempt_at is None


def test_send_one_http_error_schedules_retry(hub_db, sysctx, monkeypatch):
    ch_id = notifications.create_channel(
        name="S", kind="slack", config={"url": "https://hooks.slack.com/x"}
    )["id"]
    did = _queue_one(ch_id)

    monkeypatch.setattr(
        webhook_delivery, "safe_request",
        lambda *a, **k: _FakeResponse(500, "server error"),
    )
    result = webhook_delivery.send_one(did)
    assert result["status"] == DELIVERY_FAILED
    with session_scope() as s:
        d = s.get(WebhookDelivery, did)
        assert d.status == DELIVERY_FAILED
        # A backoff retry time is set in the future.
        assert d.next_attempt_at is not None
        assert d.attempts == 1


def test_send_one_ssrf_blocked_goes_straight_to_dead(hub_db, sysctx, monkeypatch):
    """An SSRF-blocked URL is a permanent failure — retrying cannot fix
    a private/loopback target, so the delivery goes straight to dead."""
    ch_id = notifications.create_channel(
        name="Evil", kind="slack", config={"url": "https://evil.example.com/x"}
    )["id"]
    did = _queue_one(ch_id)

    def _blocked(*a, **k):
        raise SSRFBlockedError("private address (10.0.0.1) is blocked")

    monkeypatch.setattr(webhook_delivery, "safe_request", _blocked)
    result = webhook_delivery.send_one(did)
    assert result["status"] == DELIVERY_DEAD
    with session_scope() as s:
        d = s.get(WebhookDelivery, did)
        assert d.status == DELIVERY_DEAD
        assert "ssrf" in (d.response_snippet or "").lower()


def test_send_one_exhausts_retries_to_dead(hub_db, sysctx, monkeypatch):
    """After MAX_ATTEMPTS transient failures the row is marked dead."""
    ch_id = notifications.create_channel(
        name="S", kind="slack", config={"url": "https://hooks.slack.com/x"}
    )["id"]
    did = _queue_one(ch_id)

    monkeypatch.setattr(
        webhook_delivery, "safe_request",
        lambda *a, **k: _FakeResponse(503, "down"),
    )
    last = None
    for _ in range(webhook_delivery.MAX_ATTEMPTS):
        last = webhook_delivery.send_one(did)
    assert last["status"] == DELIVERY_DEAD
    with session_scope() as s:
        d = s.get(WebhookDelivery, did)
        assert d.status == DELIVERY_DEAD
        assert d.attempts == webhook_delivery.MAX_ATTEMPTS


def test_send_one_signs_body_when_channel_has_secret(hub_db, sysctx, monkeypatch):
    ch_id = notifications.create_channel(
        name="S", kind="slack", config={"url": "https://hooks.slack.com/x"}
    )["id"]
    did = _queue_one(ch_id)

    captured = {}

    def _capture(method, url, *, headers=None, data=None, **k):
        captured["headers"] = headers or {}
        return _FakeResponse(200, "ok")

    monkeypatch.setattr(webhook_delivery, "safe_request", _capture)
    webhook_delivery.send_one(did)
    assert "X-Rebooter-Signature" in captured["headers"]
    assert captured["headers"]["X-Rebooter-Signature"].startswith("sha256=")


def test_tick_drains_due_deliveries(hub_db, sysctx, monkeypatch):
    ch_id = notifications.create_channel(
        name="S", kind="slack", config={"url": "https://hooks.slack.com/x"}
    )["id"]
    notifications.create_subscription(
        event_type="watchdog.rule_fired", channel_id=ch_id
    )
    notifications.emit("watchdog.rule_fired", {"rule_name": "R"})
    notifications.emit("watchdog.rule_fired", {"rule_name": "R2"})

    monkeypatch.setattr(
        webhook_delivery, "safe_request",
        lambda *a, **k: _FakeResponse(204, ""),
    )
    stats = webhook_delivery.tick()
    assert stats["considered"] == 2
    assert stats["sent"] == 2
