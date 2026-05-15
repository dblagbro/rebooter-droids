"""MQTT broker subscriber — v0.5.63 (B17 Ship 3).

Long-lived background subscriber for `kind='mqtt'` external sensors.
Per the B17 design (`docs/notes/2026-05-15-b17-remaining-integrations-
design.md` §3.1 / §5) this is **Option A** — an in-process paho-mqtt
client per source, started once from the scheduler process (which the
APScheduler advisory lock already guarantees runs on exactly one
gunicorn worker).

Each message landing on a subscribed topic is written as one
`external_sensor_samples` row via `external_sensors.record_mqtt_message`
(`payload = {topic, msg, received_at}`).

First-ship scope (design §3.1): sources are read once at start. Adding
or editing an MQTT source needs a container restart to take effect —
acceptable for a rarely-changed integration; a live reconcile is a
documented follow-up.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from app.db import session_scope
from app.models import ExternalSensorSource

log = logging.getLogger(__name__)

# Module-level client registry — keeps paho Client objects alive (their
# network threads are daemon threads; losing the reference would not
# stop them, but we keep them for clean shutdown / introspection).
_clients: list = []
_started = False
_lock = threading.Lock()


def _on_connect(client, userdata, flags, reason_code, properties=None):
    """Re-subscribe on every (re)connect — MQTT subscriptions do not
    survive a broker reconnect."""
    src_id = userdata.get("source_id")
    topics = userdata.get("topics") or []
    if reason_code != 0:
        log.warning("mqtt source %s connect failed: rc=%s", src_id, reason_code)
        return
    for topic in topics:
        try:
            client.subscribe(topic)
        except Exception:
            log.exception("mqtt source %s failed to subscribe %s", src_id, topic)
    log.info("mqtt source %s connected — subscribed to %d topic(s)",
             src_id, len(topics))


def _on_message(client, userdata, message):
    """paho network-thread callback — write one sample per message.
    `record_mqtt_message` is best-effort and never raises."""
    from app.services.external_sensors import record_mqtt_message

    src_id = userdata.get("source_id")
    try:
        msg = message.payload.decode("utf-8", errors="replace")
    except Exception:
        msg = ""
    record_mqtt_message(src_id, message.topic, msg)


def _start_one(src) -> None:
    """Build + start one paho client for an MQTT source. Best-effort —
    a bad source logs and is skipped; it never blocks the others."""
    import paho.mqtt.client as mqtt

    cfg = src.config or {}
    topics = cfg.get("topics") or []
    if not topics:
        log.warning("mqtt source %s has no topics — skipped", src.id)
        return
    client_id = cfg.get("client_id") or f"rebooter-droids-{src.id[-8:]}"
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=True,
    )
    client.user_data_set({"source_id": src.id, "topics": topics})
    username = cfg.get("username")
    if username:
        client.username_pw_set(username, cfg.get("password") or None)
    client.on_connect = _on_connect
    client.on_message = _on_message
    # paho auto-reconnects from loop_start(); bound the backoff.
    client.reconnect_delay_set(min_delay=1, max_delay=120)

    host = src.host
    port = src.port or 1883
    try:
        # connect_async + loop_start — non-blocking; the loop thread
        # handles the initial connect and all reconnects.
        client.connect_async(host, port, keepalive=60)
        client.loop_start()
    except Exception:
        log.exception("mqtt source %s failed to start (%s:%s)", src.id, host, port)
        return
    _clients.append(client)
    log.info("mqtt source %s subscriber started → %s:%s", src.id, host, port)


def start() -> int:
    """Start subscribers for every enabled `kind='mqtt'` source.

    Called once from the scheduler bootstrap (single-worker, advisory-
    lock-guarded). Idempotent — a second call is a no-op. Returns the
    number of subscribers started.
    """
    global _started
    with _lock:
        if _started:
            return len(_clients)
        _started = True

    try:
        with session_scope() as session:
            sources = list(session.scalars(
                select(ExternalSensorSource).where(
                    ExternalSensorSource.kind == "mqtt",
                    ExternalSensorSource.enabled.is_(True),
                )
            ))
            # Detach the fields we need before the session closes.
            specs = [
                type("MqttSpec", (), {
                    "id": s.id, "host": s.host, "port": s.port,
                    "config": dict(s.config or {}),
                })()
                for s in sources
            ]
    except Exception:
        log.exception("mqtt_subscriber.start: failed to load MQTT sources")
        return 0

    for spec in specs:
        _start_one(spec)
    if specs:
        log.info("mqtt_subscriber: started %d/%d subscriber(s)",
                 len(_clients), len(specs))
    return len(_clients)
