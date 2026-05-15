"""External-sensor inbound writers — the push side of the subpackage.

Webhook + MQTT-subscriber kinds are not polled (`poll_all_due` skips
them). Instead the event arrives via the
`/api/v1/integrations/webhook/<id>` endpoint or the background MQTT
subscriber, and these functions write the sample row — mirroring
`_pollers.poll_source`'s success path without the `_poll_kind` switch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db import session_scope
from app.models import ExternalSensorSample, ExternalSensorSource
from app.models.external_sensors import WEBHOOK_KINDS
from app.services.external_sensors._common import _iso

log = logging.getLogger(__name__)


def record_webhook_event(source_id: str, payload: dict) -> dict:
    """v0.5.61 (B17 Ship 2): inbound-webhook sample writer.

    Called by the `/api/v1/integrations/webhook/<source_id>` endpoint
    after it has authenticated the per-source secret. Mirrors
    `poll_source`'s success path — append an `ExternalSensorSample`,
    stamp `last_polled_at`/`last_success_at` — but skips the
    `_poll_kind` switch entirely (the hub did not reach out).

    Returns `{"recorded": True, "sampled_at": ...}` or `{"error": ...}`.
    Best-effort: callers should treat any exception as a 500.
    """
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        src = session.get(ExternalSensorSource, source_id)
        if src is None:
            return {"error": "source not found"}
        if src.kind not in WEBHOOK_KINDS:
            return {"error": "source is not a webhook kind"}
        if not src.enabled:
            return {"error": "source disabled"}
        src.last_polled_at = now
        src.last_success_at = now
        src.last_error = None
        session.add(src)
        session.add(ExternalSensorSample(
            source_id=src.id,
            sampled_at=now,
            payload=payload if isinstance(payload, dict) else {},
        ))
        session.flush()
    return {"recorded": True, "sampled_at": _iso(now)}


def record_mqtt_message(source_id: str, topic: str, msg: str) -> dict:
    """v0.5.63 (B17 Ship 3): MQTT message sample writer.

    Called by the background MQTT subscriber's on-message callback (in
    paho's network thread). Appends one `ExternalSensorSample` per
    message — `payload = {"topic", "msg", "received_at"}` — and stamps
    `last_polled_at`/`last_success_at`.

    Best-effort: callers (the network-thread callback) must not let an
    exception escape, so this swallows + logs failures.
    """
    now = datetime.now(timezone.utc)
    try:
        with session_scope() as session:
            src = session.get(ExternalSensorSource, source_id)
            if src is None or src.kind != "mqtt" or not src.enabled:
                return {"error": "source missing/disabled/not mqtt"}
            src.last_polled_at = now
            src.last_success_at = now
            src.last_error = None
            session.add(src)
            session.add(ExternalSensorSample(
                source_id=src.id,
                sampled_at=now,
                payload={
                    "topic": str(topic)[:400],
                    "msg": str(msg)[:2000],
                    "received_at": _iso(now),
                },
            ))
            session.flush()
        return {"recorded": True}
    except Exception:
        log.exception("record_mqtt_message failed for %s topic=%s", source_id, topic)
        return {"error": "internal error"}
