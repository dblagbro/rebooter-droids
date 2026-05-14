"""External-sensor source registry + poller (B17 Layer 1).

Today supports `kind='roku'` against the documented Roku ECP HTTP API
(port 8060, no auth, LAN-local). Architecturally the same shape will
hold the future Home-Assistant / MQTT / Plex / weather / calendar
integrations — the dispatcher in `poll_source` is the extension point.

The APScheduler tick in `app/jobs/scheduler.py::_external_sensors_job`
calls `poll_all_due()` every 30 s; it skips sources whose
`last_polled_at + poll_interval_seconds` hasn't elapsed, so per-source
cadence is honored without per-source scheduling.

Tick is best-effort: any per-source exception is recorded in
`last_error` on the source row and the tick keeps marching.
"""

from __future__ import annotations

import http.client
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import ExternalSensorSample, ExternalSensorSource
from app.models.external_sensors import EXTERNAL_SOURCE_KINDS

log = logging.getLogger(__name__)

ROKU_DEFAULT_PORT = 8060
ROKU_POLL_TIMEOUT_SECONDS = 3


# ── source CRUD ─────────────────────────────────────────────────────────


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _serialize(row: ExternalSensorSource, *, latest_sample: dict | None = None) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "display_name": row.display_name,
        "host": row.host,
        "port": row.port,
        "enabled": row.enabled,
        "poll_interval_seconds": row.poll_interval_seconds,
        "last_polled_at": _iso(row.last_polled_at),
        "last_success_at": _iso(row.last_success_at),
        "last_error": row.last_error,
        "created_at": _iso(row.created_at),
        "latest_sample": latest_sample,
    }


def list_sources() -> list[dict]:
    """All registered sources + each one's latest sample (None if never polled)."""
    with session_scope() as session:
        sources = list(
            session.scalars(
                select(ExternalSensorSource).order_by(
                    ExternalSensorSource.created_at.desc()
                )
            )
        )
        out: list[dict] = []
        for src in sources:
            sample_row = session.scalar(
                select(ExternalSensorSample)
                .where(ExternalSensorSample.source_id == src.id)
                .order_by(ExternalSensorSample.sampled_at.desc())
                .limit(1)
            )
            sample = (
                {
                    "sampled_at": _iso(sample_row.sampled_at),
                    "payload": sample_row.payload or {},
                }
                if sample_row
                else None
            )
            out.append(_serialize(src, latest_sample=sample))
        return out


def create_source(
    *,
    kind: str,
    display_name: str,
    host: str,
    port: int | None = None,
    poll_interval_seconds: int = 30,
) -> dict:
    """Register a new source. Raises ValueError for bad shape."""
    kind = (kind or "").strip().lower()
    if kind not in EXTERNAL_SOURCE_KINDS:
        raise ValueError(
            f"kind must be one of {EXTERNAL_SOURCE_KINDS}, got {kind!r}"
        )
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("display_name is required")
    host = (host or "").strip()
    if not host:
        raise ValueError("host is required")
    if port is None:
        port = ROKU_DEFAULT_PORT if kind == "roku" else 0
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        raise ValueError("port must be an integer") from None
    if port_i < 1 or port_i > 65535:
        raise ValueError("port must be in 1..65535")
    try:
        interval = int(poll_interval_seconds or 30)
    except (TypeError, ValueError):
        raise ValueError("poll_interval_seconds must be an integer") from None
    if interval < 5 or interval > 3600:
        raise ValueError("poll_interval_seconds must be in 5..3600")

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = ExternalSensorSource(
            kind=kind,
            display_name=display_name,
            host=host,
            port=port_i,
            enabled=True,
            poll_interval_seconds=interval,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return _serialize(row)


def delete_source(source_id: str) -> bool:
    with session_scope() as session:
        row = session.get(ExternalSensorSource, source_id)
        if row is None:
            return False
        session.delete(row)
        session.flush()
        return True


def set_enabled(source_id: str, enabled: bool) -> bool:
    with session_scope() as session:
        row = session.get(ExternalSensorSource, source_id)
        if row is None:
            return False
        row.enabled = bool(enabled)
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.flush()
        return True


# ── poller ──────────────────────────────────────────────────────────────


def poll_source(source_id: str) -> dict:
    """Poll one source by id. Used by the manual-test admin button and
    by `poll_all_due` from the APScheduler tick. Records sample +
    updates last_polled_at / last_success_at / last_error.

    Returns the freshly recorded sample dict, or {"error": ...} if the
    poll failed.
    """
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        src = session.get(ExternalSensorSource, source_id)
        if src is None:
            return {"error": "source not found"}
        src.last_polled_at = now
        try:
            payload = _poll_kind(src)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            src.last_error = err[:500]
            session.add(src)
            session.flush()
            log.warning("external poll %s (%s) failed: %s", src.id, src.kind, err)
            return {"error": err}
        # Success path.
        src.last_success_at = now
        src.last_error = None
        session.add(src)
        sample = ExternalSensorSample(
            source_id=src.id,
            sampled_at=now,
            payload=payload,
        )
        session.add(sample)
        session.flush()
        return {
            "sampled_at": _iso(now),
            "payload": payload,
        }


def poll_all_due() -> dict:
    """APScheduler entry point. Returns counts for the log line."""
    now = datetime.now(timezone.utc)
    stats = {"considered": 0, "polled": 0, "errors": 0, "skipped": 0}
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(ExternalSensorSource).where(
                    ExternalSensorSource.enabled.is_(True)
                )
            )
        )
        due_ids: list[str] = []
        for src in rows:
            stats["considered"] += 1
            if src.last_polled_at is None:
                due_ids.append(src.id)
                continue
            elapsed = (now - src.last_polled_at).total_seconds()
            if elapsed >= src.poll_interval_seconds:
                due_ids.append(src.id)
            else:
                stats["skipped"] += 1
    for sid in due_ids:
        out = poll_source(sid)
        if "error" in out:
            stats["errors"] += 1
        else:
            stats["polled"] += 1
    return stats


def _poll_kind(src: ExternalSensorSource) -> dict:
    """Per-kind poll dispatch. New kinds add a branch here + a name in
    EXTERNAL_SOURCE_KINDS. Raises on transport / parse error so caller
    records last_error.
    """
    if src.kind == "roku":
        return _poll_roku(src.host, src.port)
    raise ValueError(f"unsupported source kind: {src.kind}")


def _poll_roku(host: str, port: int) -> dict:
    """Roku ECP — GET http://<host>:<port>/query/active-app.

    Response is XML; we parse minimally without pulling lxml. Shape:
        <active-app>
          <app id="31" type="appl" version="...">Spectrum TV</app>
          <screensaver id="..." type="ssvr" version="...">My Screensaver</screensaver>
        </active-app>

    The `<screensaver>` element only appears when the screensaver is
    active; absence means "user is interacting with the named app".
    """
    conn = http.client.HTTPConnection(host, port, timeout=ROKU_POLL_TIMEOUT_SECONDS)
    try:
        conn.request("GET", "/query/active-app",
                     headers={"User-Agent": "rebooter-droids/B17"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        status = resp.status
    finally:
        conn.close()
    if status != 200:
        raise RuntimeError(f"Roku ECP HTTP {status}")
    return _parse_roku_active_app(body)


def _parse_roku_active_app(xml_body: str) -> dict:
    """Tiny hand-parser — avoids lxml dep for one well-known shape.

    Extracts:
      - active_app (str | None)
      - active_app_id (str | None)
      - screensaver_active (bool)
      - raw_xml (kept short — first 500 chars)
    """
    import re

    app_match = re.search(
        r"<app(?:\s+([^>]*))?>([^<]*)</app>", xml_body, re.IGNORECASE
    )
    ss_match = re.search(
        r"<screensaver\b[^>]*>", xml_body, re.IGNORECASE
    )

    active_app = None
    active_app_id = None
    if app_match:
        attrs_str, name = app_match.group(1) or "", app_match.group(2) or ""
        active_app = (name or "").strip() or None
        id_match = re.search(r'id="([^"]*)"', attrs_str)
        if id_match:
            active_app_id = id_match.group(1)

    return {
        "active_app": active_app,
        "active_app_id": active_app_id,
        "screensaver_active": ss_match is not None,
        "raw_xml": xml_body[:500],
    }


# ── consumed by the watchdog probe ──────────────────────────────────────


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
