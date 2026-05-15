"""External-sensor sample reads — consumed by the watchdog probes + UI.

Pure read helpers over `external_sensor_samples`: latest sample, the
two-most-recent pair (for SNMP counter-delta probes), topic-filtered
latest (MQTT), and the HA entity browser. The watchdog probes in
`watchdog_runtime/_probes_integrations.py` are the main callers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import ExternalSensorSample, ExternalSensorSource
from app.services.external_sensors._common import _iso


def last_two_samples(
    source_id: str, *, max_age_seconds: int = 600
) -> tuple[dict, dict] | None:
    """v0.5.58 (P2.2/P2.3): return the (newer, older) two most-recent
    samples for a source, used by the SNMP rate probes to compute a
    counter delta. Returns None if there are fewer than two samples or
    the *newer* one is already older than `max_age_seconds`.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(ExternalSensorSample)
                .where(ExternalSensorSample.source_id == source_id)
                .order_by(ExternalSensorSample.sampled_at.desc())
                .limit(2)
            )
        )
        if len(rows) < 2:
            return None
        newer, older = rows[0], rows[1]
        if newer.sampled_at < cutoff:
            return None
        return (
            {"sampled_at": newer.sampled_at, "payload": newer.payload or {}},
            {"sampled_at": older.sampled_at, "payload": older.payload or {}},
        )


def latest_sample(source_id: str, *, max_age_seconds: int = 120) -> dict | None:
    """v0.5.23: generic latest-sample lookup, used by the HA / weather /
    iCal probe kinds. Returns None if sample is stale or absent.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    with session_scope() as session:
        row = session.scalar(
            select(ExternalSensorSample)
            .where(
                ExternalSensorSample.source_id == source_id,
                ExternalSensorSample.sampled_at >= cutoff,
            )
            .order_by(ExternalSensorSample.sampled_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        return {
            "sampled_at": _iso(row.sampled_at),
            "payload": row.payload or {},
        }


def latest_sample_for_topic(
    source_id: str, topic: str, *, max_age_seconds: int = 300
) -> dict | None:
    """v0.5.63 (B17 Ship 3): most-recent MQTT sample for a specific
    topic under a source.

    MQTT messages span many topics under one source, so the generic
    `latest_sample()` (newest overall) is not enough. Per the design's
    first-ship option (a), this filters in Python: scan recent samples
    within the window for one whose `payload.topic` matches.

    Returns None if no matching message inside `max_age_seconds`.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    with session_scope() as session:
        rows = session.scalars(
            select(ExternalSensorSample)
            .where(
                ExternalSensorSample.source_id == source_id,
                ExternalSensorSample.sampled_at >= cutoff,
            )
            .order_by(ExternalSensorSample.sampled_at.desc())
            .limit(500)
        )
        for row in rows:
            payload = row.payload or {}
            if isinstance(payload, dict) and payload.get("topic") == topic:
                return {"sampled_at": _iso(row.sampled_at), "payload": payload}
    return None


def latest_active_app(source_id: str, *, max_age_seconds: int = 120) -> dict | None:
    """Return the most-recent sample's payload if it's younger than
    `max_age_seconds`. Returns None if no sample, or the sample is
    stale (poller may have hit an error after a while).

    Stale samples MUST NOT trigger watchdog rules — the operator would
    have a 30-min-old "Spectrum TV active" sample firing a power-cycle
    they didn't expect.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    with session_scope() as session:
        row = session.scalar(
            select(ExternalSensorSample)
            .where(
                ExternalSensorSample.source_id == source_id,
                ExternalSensorSample.sampled_at >= cutoff,
            )
            .order_by(ExternalSensorSample.sampled_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        return {
            "sampled_at": _iso(row.sampled_at),
            "payload": row.payload or {},
        }


def ha_entities(source_id: str) -> dict | None:
    """v0.5.57 (P2.4): Home Assistant entity browser.

    The HA poll already caches every entity in the sample payload; this
    flattens the most-recent sample into a sorted, browsable list so the
    operator can discover `entity_id`s (and their current state / unit)
    for `ha_state_is` / `ha_numeric_*` rules without leaving the hub.

    Returns None if the source does not exist or is not a
    `home_assistant` kind. An HA source that has never polled returns an
    empty `entities` list with `sampled_at=None`.
    """
    with session_scope() as session:
        src = session.get(ExternalSensorSource, source_id)
        if src is None or src.kind != "home_assistant":
            return None
        display_name = src.display_name
        sample_row = session.scalar(
            select(ExternalSensorSample)
            .where(ExternalSensorSample.source_id == source_id)
            .order_by(ExternalSensorSample.sampled_at.desc())
            .limit(1)
        )
        if sample_row is None:
            return {
                "source_id": source_id,
                "display_name": display_name,
                "sampled_at": None,
                "entities": [],
            }
        payload = sample_row.payload or {}
        raw = payload.get("entities") if isinstance(payload, dict) else None
        entities: list[dict] = []
        for eid, entry in (raw.items() if isinstance(raw, dict) else []):
            if not isinstance(entry, dict):
                continue
            attrs = entry.get("attributes") if isinstance(entry.get("attributes"), dict) else {}
            entities.append({
                "entity_id": eid,
                "friendly_name": attrs.get("friendly_name"),
                "state": entry.get("state"),
                "unit": attrs.get("unit_of_measurement"),
                "last_changed": entry.get("last_changed"),
            })
        entities.sort(key=lambda e: e["entity_id"])
        return {
            "source_id": source_id,
            "display_name": display_name,
            "sampled_at": _iso(sample_row.sampled_at),
            "entities": entities,
        }
