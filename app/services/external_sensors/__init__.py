"""External-sensor source registry + poller (B17).

Operator-registered external systems the hub watches — Roku ECP, Home
Assistant, NWS weather, iCal feeds, SolarEdge / Enphase solar,
router/switch SNMP, Plex/Jellyfin/iOS-Shortcut webhooks, and MQTT.
Three ingestion shapes share the `external_sensor_sources` /
`external_sensor_samples` table pair:

- **poll** — the hub fetches on a cadence (`_pollers`); the APScheduler
  tick calls `poll_all_due()` every 30 s.
- **webhook** — the external service POSTs to the hub (`_inbound`).
- **subscriber** — a long-lived MQTT connection pushes messages
  (`_inbound.record_mqtt_message`, driven by `services/mqtt_subscriber`).

v0.5.64 — split from a single 1369-LOC module into this subpackage per
`architecture.md` §"Service subpackages":

    _common.py    `_iso`, ROKU_DEFAULT_PORT — dependency-free shared leaf
    _crud.py      source registry: create / list / enable / delete,
                  per-kind config validation, redacted serialization
    _pollers.py   poll dispatch + every `_poll_<kind>` + SNMP helpers
    _inbound.py   webhook + MQTT sample writers (the push side)
    _query.py     sample reads consumed by the watchdog probes + UI

External callers import from this package root only — never from the
underscore-prefixed internal modules.
"""

from __future__ import annotations

from app.services.external_sensors._common import _iso
from app.services.external_sensors._crud import (
    create_source,
    delete_source,
    list_sources,
    set_enabled,
)
from app.services.external_sensors._inbound import (
    record_mqtt_message,
    record_webhook_event,
)
from app.services.external_sensors._pollers import (
    poll_all_due,
    poll_source,
)
from app.services.external_sensors._query import (
    ha_entities,
    last_two_samples,
    latest_active_app,
    latest_sample,
    latest_sample_for_topic,
)

__all__ = [
    "_iso",
    "create_source",
    "delete_source",
    "list_sources",
    "set_enabled",
    "poll_source",
    "poll_all_due",
    "record_webhook_event",
    "record_mqtt_message",
    "latest_sample",
    "latest_sample_for_topic",
    "last_two_samples",
    "latest_active_app",
    "ha_entities",
]
