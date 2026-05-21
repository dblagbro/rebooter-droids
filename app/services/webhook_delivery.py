"""Webhook delivery — the SSRF-guarded sender + retry/backoff worker.

Per `docs/notes/2026-05-20-hub-tier2-design.md` Feature 6.

This module is the *send* side of the notifications engine:

  * per-kind payload formatters (generic / slack / discord / pushover).
  * `send_one(delivery_id)` — formats, signs and SSRF-guard-sends a
    single `webhook_deliveries` row; records status / http_status /
    response snippet.
  * `tick()` — the APScheduler worker entrypoint. Claims `pending`
    deliveries whose `next_attempt_at` is due, sends each, and on
    failure schedules an exponential-backoff retry up to `MAX_ATTEMPTS`,
    after which the row goes `dead`.

Every outbound call goes through `app/services/ssrf_guard.py` — there is
no raw `requests` call anywhere in here. The Slack / Discord / Pushover
URLs are validated by the *same* SSRF guard as a generic webhook: a
vendor hostname can still be CNAME-spoofed, so the IP-range check is the
real gate (design Feature 6 step 8).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import WebhookChannel, WebhookDelivery
from app.models.notifications import (
    CHANNEL_KIND_DISCORD,
    CHANNEL_KIND_PUSHOVER,
    CHANNEL_KIND_SLACK,
    CHANNEL_KIND_WEBHOOK,
    DELIVERY_DEAD,
    DELIVERY_FAILED,
    DELIVERY_PENDING,
    DELIVERY_SENT,
)
from app.services.ssrf_guard import SSRFBlockedError, safe_request

log = logging.getLogger(__name__)

# Retry policy. attempt 1 fails → +1 min, attempt 2 → +4 min,
# attempt 3 → +9 min … (attempt^2 minutes). After MAX_ATTEMPTS the row
# is `dead`.
MAX_ATTEMPTS = 5
# How many due deliveries one tick drains — bounds the per-tick HTTP
# fan-out so a backlog cannot stall the scheduler thread.
BATCH_SIZE = 20
PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


# ── HMAC signing ───────────────────────────────────────────────────────


def sign_body(secret: str, body: bytes) -> str:
    """`X-Rebooter-Signature` value — `sha256=<hex>` over the raw body,
    same scheme as the inbound integration webhooks and sync HMAC."""
    digest = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


# ── Per-kind payload formatters ────────────────────────────────────────
#
# Each returns `(url, headers, body_bytes)` ready to hand to the SSRF
# guard. `event_type` + `payload` are the emit()-time event data.


def _human_summary(event_type: str, payload: dict) -> str:
    """A short one-line summary used as the human-readable text in the
    Slack / Discord / Pushover bodies."""
    payload = payload or {}
    name = payload.get("name") or payload.get("device_name") or payload.get("rule_name")
    base = event_type.replace(".", " ").replace("_", " ")
    if name:
        return f"Rebooter: {base} — {name}"
    return f"Rebooter: {base}"


def _format_generic(channel: WebhookChannel, event_type: str, payload: dict):
    config = channel.config or {}
    url = config.get("url")
    method = (config.get("method") or "POST").upper()
    body = json.dumps(
        {
            "event": event_type,
            "payload": payload or {},
            "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    headers.update(config.get("headers") or {})
    return url, method, headers, body


def _format_slack(channel: WebhookChannel, event_type: str, payload: dict):
    config = channel.config or {}
    body = json.dumps(
        {"text": _human_summary(event_type, payload)},
        separators=(",", ":"),
    ).encode("utf-8")
    return config.get("url"), "POST", {"Content-Type": "application/json"}, body


def _format_discord(channel: WebhookChannel, event_type: str, payload: dict):
    config = channel.config or {}
    # Discord incoming-webhook expects `content`.
    body = json.dumps(
        {"content": _human_summary(event_type, payload)},
        separators=(",", ":"),
    ).encode("utf-8")
    return config.get("url"), "POST", {"Content-Type": "application/json"}, body


def _format_pushover(channel: WebhookChannel, event_type: str, payload: dict):
    config = channel.config or {}
    # Pushover takes form-encoded params at a fixed endpoint.
    from urllib.parse import urlencode

    body = urlencode(
        {
            "token": config.get("app_token", ""),
            "user": config.get("user_key", ""),
            "title": "Rebooter Hub",
            "message": _human_summary(event_type, payload),
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    return PUSHOVER_API_URL, "POST", headers, body


_FORMATTERS = {
    CHANNEL_KIND_WEBHOOK: _format_generic,
    CHANNEL_KIND_SLACK: _format_slack,
    CHANNEL_KIND_DISCORD: _format_discord,
    CHANNEL_KIND_PUSHOVER: _format_pushover,
}


def format_delivery(channel: WebhookChannel, event_type: str, payload: dict):
    """Dispatch to the per-kind formatter. Returns
    `(url, method, headers, body_bytes)`."""
    formatter = _FORMATTERS.get(channel.kind)
    if formatter is None:
        raise ValueError(f"no formatter for channel kind {channel.kind!r}")
    return formatter(channel, event_type, payload)


# ── Sending one delivery ───────────────────────────────────────────────


def _backoff_delay(attempts: int) -> timedelta:
    """Exponential backoff: attempt^2 minutes (1, 4, 9, 16 …)."""
    return timedelta(minutes=attempts * attempts)


def send_one(delivery_id: int) -> dict:
    """Send a single delivery row through the SSRF guard.

    Records `sent` on a 2xx; on any error records `failed` (and
    schedules a retry) or `dead` once `MAX_ATTEMPTS` is reached. Returns
    a small result dict for tests / the worker log line.

    Never raises — a send failure is data on the row, not an exception.
    """
    with session_scope() as session:
        delivery = session.get(WebhookDelivery, delivery_id)
        if delivery is None:
            return {"delivery_id": delivery_id, "error": "delivery not found"}
        channel = session.get(WebhookChannel, delivery.channel_id)
        if channel is None:
            delivery.status = DELIVERY_DEAD
            delivery.response_snippet = "channel deleted"
            delivery.updated_at = datetime.now(timezone.utc)
            return {"delivery_id": delivery_id, "status": DELIVERY_DEAD,
                    "error": "channel deleted"}

        delivery.attempts += 1
        delivery.updated_at = datetime.now(timezone.utc)

        try:
            url, method, headers, body = format_delivery(
                channel, delivery.event_type, delivery.payload or {}
            )
            if channel.signing_secret:
                headers = dict(headers)
                headers["X-Rebooter-Signature"] = sign_body(
                    channel.signing_secret, body
                )
            # The load-bearing call: SSRF-guarded, IP-pinned, no
            # redirects, response-size capped.
            resp = safe_request(
                method, url, headers=headers, data=body,
                allow_redirects=False,
            )
            delivery.http_status = resp.status_code
            delivery.response_snippet = (resp.text or "")[:500]
            if 200 <= resp.status_code < 300:
                delivery.status = DELIVERY_SENT
                delivery.next_attempt_at = None
                result = {"delivery_id": delivery_id, "status": DELIVERY_SENT,
                          "http_status": resp.status_code}
            else:
                result = _schedule_retry_or_dead(
                    delivery, f"HTTP {resp.status_code}"
                )
        except SSRFBlockedError as e:
            # An SSRF-blocked URL is a *permanent* failure — retrying
            # cannot fix a private/loopback target. Go straight to dead.
            delivery.status = DELIVERY_DEAD
            delivery.next_attempt_at = None
            delivery.response_snippet = f"blocked by SSRF guard: {e.reason}"[:500]
            log.warning(
                "webhook delivery %s blocked by SSRF guard: %s",
                delivery_id, e.reason,
            )
            result = {"delivery_id": delivery_id, "status": DELIVERY_DEAD,
                      "error": f"ssrf_blocked: {e.reason}"}
        except Exception as e:
            log.warning("webhook delivery %s failed: %s", delivery_id, e)
            result = _schedule_retry_or_dead(delivery, str(e))

        delivery.updated_at = datetime.now(timezone.utc)
        return result


def _schedule_retry_or_dead(delivery: WebhookDelivery, error: str) -> dict:
    """A transient failure: schedule the next backoff attempt, or mark
    the row `dead` if retries are exhausted."""
    delivery.response_snippet = (error or "")[:500]
    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = DELIVERY_DEAD
        delivery.next_attempt_at = None
        return {"delivery_id": delivery.id, "status": DELIVERY_DEAD,
                "error": error}
    delivery.status = DELIVERY_FAILED
    delivery.next_attempt_at = datetime.now(timezone.utc) + _backoff_delay(
        delivery.attempts
    )
    return {"delivery_id": delivery.id, "status": DELIVERY_FAILED,
            "error": error, "next_attempt_at": delivery.next_attempt_at}


# ── The worker tick ────────────────────────────────────────────────────


def tick(now: datetime | None = None) -> dict:
    """Drain due deliveries — the APScheduler `webhook_delivery_tick`
    job body.

    Claims up to `BATCH_SIZE` rows in `pending`/`failed` status whose
    `next_attempt_at` is due, sends each via `send_one`, and returns a
    small stats dict for the job log line and tests.

    `now` is injectable for deterministic tests; the scheduler calls it
    with no argument (wall-clock).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    stats = {"considered": 0, "sent": 0, "failed": 0, "dead": 0}

    with session_scope() as session:
        due_ids = list(
            session.scalars(
                select(WebhookDelivery.id)
                .where(
                    WebhookDelivery.status.in_(
                        (DELIVERY_PENDING, DELIVERY_FAILED)
                    ),
                    WebhookDelivery.next_attempt_at.isnot(None),
                    WebhookDelivery.next_attempt_at <= now,
                )
                .order_by(WebhookDelivery.next_attempt_at)
                .limit(BATCH_SIZE)
            )
        )

    for delivery_id in due_ids:
        stats["considered"] += 1
        result = send_one(delivery_id)
        status = result.get("status")
        if status == DELIVERY_SENT:
            stats["sent"] += 1
        elif status == DELIVERY_DEAD:
            stats["dead"] += 1
        elif status == DELIVERY_FAILED:
            stats["failed"] += 1

    return stats


# ── The watchdog escalation webhook — folded-in security fix ────────────


def send_escalation_webhook(rule_id: str, url: str, payload: dict) -> dict:
    """Send a watchdog `escalation` `{kind:"webhook", url:...}` notice.

    Per design Feature 6 ("Call sites that emit events" + step 7): the
    watchdog rule schema already documents an `escalation` action of
    `{kind:"webhook", url:...}`, but that URL was a raw field with no
    SSRF protection. This routes it through the *same* SSRF guard as the
    notification channels — a folded-in security fix.

    Best-effort: returns a result dict, never raises into the watchdog
    tick.
    """
    try:
        body = json.dumps(
            {
                "event": "watchdog.rule_escalated",
                "rule_id": rule_id,
                "payload": payload or {},
                "sent_at": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        resp = safe_request(
            "POST", url,
            headers={"Content-Type": "application/json"},
            data=body,
            allow_redirects=False,
        )
        return {
            "rule_id": rule_id,
            "escalation": "webhook",
            "http_status": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
        }
    except SSRFBlockedError as e:
        log.warning(
            "watchdog escalation webhook for rule %s blocked by SSRF "
            "guard: %s", rule_id, e.reason,
        )
        return {"rule_id": rule_id, "escalation": "webhook",
                "error": f"ssrf_blocked: {e.reason}"}
    except Exception as e:
        log.warning(
            "watchdog escalation webhook for rule %s failed: %s", rule_id, e
        )
        return {"rule_id": rule_id, "escalation": "webhook", "error": str(e)}
