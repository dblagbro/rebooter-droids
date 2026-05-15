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
import re
import urllib.parse
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
        # v0.5.23: per-kind extras, redacted for HA token before
        # being exposed via the admin API.
        "config": _redact_config(row.kind, row.config or {}),
        "latest_sample": latest_sample,
    }


_SECRET_CONFIG_KEYS = ("token", "api_key", "jwt")


def _redact_config(kind: str, config: dict) -> dict:
    """Strip secret fields (HA bearer tokens, SolarEdge API keys,
    Enphase JWTs, etc.) from the admin-facing source serialization."""
    if not config:
        return {}
    out = dict(config)
    for key in _SECRET_CONFIG_KEYS:
        if out.get(key):
            out[key] = "********"
    return out


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
    host: str = "",
    port: int | None = None,
    poll_interval_seconds: int = 30,
    config: dict | None = None,
) -> dict:
    """Register a new source. Per-kind validation lives in
    `_validate_kind_config()`. Raises ValueError for bad shape."""
    kind = (kind or "").strip().lower()
    if kind not in EXTERNAL_SOURCE_KINDS:
        raise ValueError(
            f"kind must be one of {EXTERNAL_SOURCE_KINDS}, got {kind!r}"
        )
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("display_name is required")
    host = (host or "").strip()
    # Default ports per kind. Kinds that don't need a host (weather,
    # ical, solaredge — cloud) skip host/port validation entirely.
    needs_host = kind in ("roku", "home_assistant", "enphase_envoy")
    if needs_host and not host:
        raise ValueError("host is required for this kind")
    if port is None:
        if kind == "roku":
            port = ROKU_DEFAULT_PORT
        elif kind == "home_assistant":
            port = 8123
        elif kind == "enphase_envoy":
            port = 80
        else:
            port = 0
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        raise ValueError("port must be an integer") from None
    if needs_host and (port_i < 1 or port_i > 65535):
        raise ValueError("port must be in 1..65535")
    try:
        interval = int(poll_interval_seconds or 30)
    except (TypeError, ValueError):
        raise ValueError("poll_interval_seconds must be an integer") from None
    # Default cadence per kind. Weather alerts + EPG don't need fast
    # polling; HA changes faster than Roku in practice.
    if interval < 5 or interval > 3600:
        raise ValueError("poll_interval_seconds must be in 5..3600")

    config = config or {}
    config = _validate_kind_config(kind, config)

    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = ExternalSensorSource(
            kind=kind,
            display_name=display_name,
            host=host,
            port=port_i,
            enabled=True,
            poll_interval_seconds=interval,
            config=config or None,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return _serialize(row)


def _validate_kind_config(kind: str, config: dict) -> dict:
    """Per-kind shape validation. Returns a normalized config dict
    (extra keys stripped). Raises ValueError on missing required keys."""
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    if kind == "roku":
        # No extra config required.
        return {}
    if kind == "home_assistant":
        token = str(config.get("token") or "").strip()
        if not token:
            raise ValueError("home_assistant config.token is required (HA long-lived access token)")
        out = {"token": token}
        if config.get("verify_ssl") is not None:
            out["verify_ssl"] = bool(config["verify_ssl"])
        return out
    if kind == "weather":
        try:
            lat = float(config.get("lat"))
            lng = float(config.get("lng"))
        except (TypeError, ValueError):
            raise ValueError(
                "weather config requires numeric lat + lng (decimal degrees)"
            ) from None
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("weather lat/lng out of range")
        return {"lat": lat, "lng": lng}
    if kind == "ical":
        url = str(config.get("url") or "").strip()
        if not url:
            raise ValueError("ical config.url is required (.ics feed URL)")
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("webcal://")):
            raise ValueError("ical url must use http://, https://, or webcal:// scheme")
        # Normalize webcal://… → https://… for the fetch path.
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://"):]
        return {"url": url}
    if kind == "solaredge":
        # v0.5.56 (P2.1): SolarEdge cloud monitoring API. Cloud — no
        # host; needs the site id + a monitoring API key.
        site_id = str(config.get("site_id") or "").strip()
        api_key = str(config.get("api_key") or "").strip()
        if not site_id:
            raise ValueError("solaredge config.site_id is required")
        if not api_key:
            raise ValueError("solaredge config.api_key is required (monitoring API key)")
        return {"site_id": site_id, "api_key": api_key}
    if kind == "enphase_envoy":
        # v0.5.56 (P2.1): local Envoy poll. `host` carries the Envoy IP.
        # `jwt` is optional — only firmware-7.0+ Envoys require a token;
        # legacy Envoys serve /production.json with no auth.
        out: dict = {}
        jwt = str(config.get("jwt") or "").strip()
        if jwt:
            out["jwt"] = jwt
        return out
    return {}


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
    if src.kind == "home_assistant":
        return _poll_home_assistant(src.host, src.port, src.config or {})
    if src.kind == "weather":
        return _poll_weather(src.config or {})
    if src.kind == "ical":
        return _poll_ical(src.config or {})
    if src.kind == "solaredge":
        return _poll_solaredge(src.config or {})
    if src.kind == "enphase_envoy":
        return _poll_enphase_envoy(src.host, src.port, src.config or {})
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


# ── Home Assistant ──────────────────────────────────────────────────────


HA_POLL_TIMEOUT_SECONDS = 5


def _poll_home_assistant(host: str, port: int, config: dict) -> dict:
    """v0.5.23: GET <host>:<port>/api/states with bearer token.

    Returns a compact payload — `entities` is a dict keyed by
    `entity_id` carrying just `state`, `last_changed`, and (if small)
    `attributes`. Full HA states can be huge; we cap each entity's
    attribute payload at 1 KiB to keep DB rows small.

    Bearer token comes from `config.token` (long-lived access token
    minted via Profile → Long-Lived Access Tokens in the HA UI).
    """
    token = (config.get("token") or "").strip()
    if not token:
        raise RuntimeError("home_assistant config.token is required")
    verify_ssl = bool(config.get("verify_ssl") if "verify_ssl" in config else True)
    use_https = port == 443 or port == 8123 and bool(config.get("https"))

    if use_https:
        conn = http.client.HTTPSConnection(
            host, port, timeout=HA_POLL_TIMEOUT_SECONDS,
            context=(None if verify_ssl else _insecure_ssl_context()),
        )
    else:
        conn = http.client.HTTPConnection(host, port, timeout=HA_POLL_TIMEOUT_SECONDS)

    try:
        conn.request(
            "GET", "/api/states",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "rebooter-droids/B17",
            },
        )
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
    finally:
        conn.close()

    if status == 401:
        raise RuntimeError("home_assistant 401 — bad/expired token")
    if status != 200:
        raise RuntimeError(f"home_assistant HTTP {status}")

    try:
        states = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"home_assistant JSON parse failed: {e}") from None

    if not isinstance(states, list):
        raise RuntimeError("home_assistant /api/states returned non-list")

    entities: dict[str, dict] = {}
    for s in states[:1000]:  # safety cap
        if not isinstance(s, dict):
            continue
        eid = s.get("entity_id")
        if not eid:
            continue
        attrs = s.get("attributes") or {}
        # Trim oversized attribute payloads — operator-readable rendering
        # only needs the headline values, not e.g. base64 thumbnails.
        attrs_clipped = {
            k: (v if not isinstance(v, str) or len(v) < 200 else v[:200] + "…")
            for k, v in (attrs.items() if isinstance(attrs, dict) else [])
            if not k.startswith("device_") and not k.startswith("entity_picture")
        }
        entities[eid] = {
            "state": s.get("state"),
            "last_changed": s.get("last_changed"),
            "attributes": attrs_clipped,
        }
    return {
        "entity_count": len(entities),
        "entities": entities,
    }


def _insecure_ssl_context():
    """Lazy import — only used when operator opts into verify_ssl=False."""
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ── Weather (NWS api.weather.gov) ───────────────────────────────────────


WEATHER_POLL_TIMEOUT_SECONDS = 8


def _poll_weather(config: dict) -> dict:
    """v0.5.23: NWS api.weather.gov active alerts for a lat/lng point.

    Endpoint: GET https://api.weather.gov/alerts/active?point=lat,lng
    No auth. NWS asks for a User-Agent identifying the integration.
    Returns a compact alerts list (event, severity, headline, ends-at).
    """
    lat = float(config.get("lat") or 0)
    lng = float(config.get("lng") or 0)
    conn = http.client.HTTPSConnection(
        "api.weather.gov", 443, timeout=WEATHER_POLL_TIMEOUT_SECONDS,
    )
    try:
        conn.request(
            "GET", f"/alerts/active?point={lat:.4f},{lng:.4f}",
            headers={
                "User-Agent": "rebooter-droids/B17 (https://github.com/dblagbro/rebooter-droids)",
                "Accept": "application/geo+json",
            },
        )
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
    finally:
        conn.close()
    if status == 301 or status == 302:
        # NWS sometimes redirects on point lookups (e.g. canonical point→zone).
        loc = resp.getheader("Location") or ""
        raise RuntimeError(f"weather redirect to {loc!r} not followed")
    if status != 200:
        raise RuntimeError(f"weather HTTP {status}")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"weather JSON parse failed: {e}") from None
    features = data.get("features") or []
    alerts: list[dict] = []
    for f in features:
        props = (f.get("properties") or {}) if isinstance(f, dict) else {}
        alerts.append({
            "event": (props.get("event") or "").strip(),
            "severity": (props.get("severity") or "").strip(),
            "headline": (props.get("headline") or "")[:300],
            "effective": props.get("effective"),
            "ends": props.get("ends") or props.get("expires"),
        })
    return {
        "lat": lat,
        "lng": lng,
        "alerts": alerts,
        "alert_count": len(alerts),
    }


# ── iCal / WebCal feeds ─────────────────────────────────────────────────


ICAL_POLL_TIMEOUT_SECONDS = 10
_ICAL_EVENT_RE = re.compile(
    r"BEGIN:VEVENT(.*?)END:VEVENT", re.DOTALL | re.IGNORECASE
)
_ICAL_KV_RE = re.compile(r"^([A-Z\-]+)(?:;[^:]*)?:(.*)$")


def _poll_ical(config: dict) -> dict:
    """v0.5.23: fetch + parse an iCal/WebCal .ics feed.

    Minimal VEVENT parser — no external lib. Stores ONLY events that
    are currently airing or starting within the next 24 h so the
    payload stays small.

    Robust enough for Google Calendar's `basic.ics` feed and stock
    macOS calendar exports; not a full RFC 5545 implementation.
    """
    url = (config.get("url") or "").strip()
    if not url:
        raise RuntimeError("ical config.url is required")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("ical url must use http:// or https://")
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    if parsed.scheme == "https":
        conn = http.client.HTTPSConnection(host, port, timeout=ICAL_POLL_TIMEOUT_SECONDS)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=ICAL_POLL_TIMEOUT_SECONDS)
    try:
        conn.request("GET", path, headers={
            "User-Agent": "rebooter-droids/B17",
            "Accept": "text/calendar, text/plain;q=0.9, */*;q=0.5",
        })
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
    finally:
        conn.close()
    if status != 200:
        raise RuntimeError(f"ical HTTP {status}")
    text = body.decode("utf-8", errors="replace")
    events = _parse_ical_events(text)
    return {
        "event_count": len(events),
        "events": events[:50],  # cap for storage size
    }


def _parse_ical_events(text: str) -> list[dict]:
    """Tiny VEVENT extractor. Returns events whose [start, end) window
    overlaps NOW or is within the next 24 h."""
    from datetime import timedelta as _td

    now = datetime.now(timezone.utc)
    horizon = now + _td(hours=24)
    out: list[dict] = []
    for m in _ICAL_EVENT_RE.finditer(text):
        block = m.group(1)
        ev: dict[str, str] = {}
        # iCal lines can be folded — RFC 5545 §3.1: a CRLF + leading
        # SPACE/TAB is a soft-fold. Unfold first.
        unfolded = re.sub(r"\r?\n[ \t]", "", block)
        for line in unfolded.splitlines():
            line = line.strip()
            if not line:
                continue
            km = _ICAL_KV_RE.match(line)
            if not km:
                continue
            key = km.group(1).upper()
            value = km.group(2)
            ev[key] = value
        start = _parse_ical_dt(ev.get("DTSTART"))
        end = _parse_ical_dt(ev.get("DTEND")) or start
        if start is None:
            continue
        # Keep events that are currently airing OR start within the
        # next 24 h. Skip events fully in the past.
        if end and end < now:
            continue
        if start > horizon:
            continue
        out.append({
            "summary": ev.get("SUMMARY", "")[:300],
            "start": start.isoformat(),
            "end": end.isoformat() if end else None,
            "uid": ev.get("UID", "")[:120],
        })
    # Sort by start time for stable rendering.
    out.sort(key=lambda e: e["start"])
    return out


def _parse_ical_dt(raw: str | None) -> datetime | None:
    """Parse `20260514T193000Z` or `20260514T193000` or `20260514`."""
    if not raw:
        return None
    raw = raw.strip()
    # DATE-only form.
    if len(raw) == 8 and raw.isdigit():
        try:
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    # DATETIME forms.
    fmts = ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S")
    for f in fmts:
        try:
            dt = datetime.strptime(raw, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# ── Solar (SolarEdge cloud / Enphase Envoy local) ───────────────────────


SOLAR_POLL_TIMEOUT_SECONDS = 8


def _num(value) -> float | None:
    """Coerce a vendor JSON numeric to float, or None if missing/bad."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _poll_solaredge(config: dict) -> dict:
    """v0.5.56 (P2.1): SolarEdge cloud monitoring API — site overview.

    GET https://monitoringapi.solaredge.com/site/{id}/overview?api_key=…
    Static API key (no OAuth). Rate limit is 300 requests/day, so the
    operator should keep `poll_interval_seconds` >= 300 (the integrations
    UI defaults solar sources to 300s).

    Returns the current production wattage + lifetime/day energy.
    """
    site_id = (config.get("site_id") or "").strip()
    api_key = (config.get("api_key") or "").strip()
    if not site_id or not api_key:
        raise RuntimeError("solaredge config requires site_id + api_key")
    path = (
        f"/site/{urllib.parse.quote(site_id)}/overview"
        f"?api_key={urllib.parse.quote(api_key)}"
    )
    conn = http.client.HTTPSConnection(
        "monitoringapi.solaredge.com", 443, timeout=SOLAR_POLL_TIMEOUT_SECONDS
    )
    try:
        conn.request("GET", path, headers={
            "User-Agent": "rebooter-droids/B17",
            "Accept": "application/json",
        })
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
    finally:
        conn.close()
    if status == 403:
        raise RuntimeError("solaredge 403 — bad api_key or site not authorized")
    if status == 429:
        raise RuntimeError("solaredge 429 — rate limited (300 requests/day cap)")
    if status != 200:
        raise RuntimeError(f"solaredge HTTP {status}")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"solaredge JSON parse failed: {e}") from None
    overview = (data.get("overview") or {}) if isinstance(data, dict) else {}
    current = overview.get("currentPower") or {}
    return {
        "vendor": "solaredge",
        "production_w": _num(current.get("power")),
        "lifetime_energy_wh": _num((overview.get("lifeTimeData") or {}).get("energy")),
        "day_energy_wh": _num((overview.get("lastDayData") or {}).get("energy")),
        "last_update_time": overview.get("lastUpdateTime"),
    }


def _poll_enphase_envoy(host: str, port: int, config: dict) -> dict:
    """v0.5.56 (P2.1): Enphase Envoy local poll — GET /production.json.

    Legacy Envoys serve this with no auth over HTTP. Firmware-7.0+
    Envoys require a JWT bearer and serve HTTPS with a self-signed cert
    (so we use an insecure SSL context when a JWT is configured).

    `/production.json` returns a `production` array with `inverters` and
    (on metered gateways) `eim` entries. The `eim` (metered) reading is
    the accurate one — preferred when present, per the design's
    "firmware 7.0+ metered gateways" first-pass scope.
    """
    use_https = bool((config.get("jwt") or "").strip()) or port == 443
    jwt = (config.get("jwt") or "").strip()
    headers = {
        "User-Agent": "rebooter-droids/B17",
        "Accept": "application/json",
    }
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    if use_https:
        conn_port = port if port and port != 80 else 443
        conn = http.client.HTTPSConnection(
            host, conn_port, timeout=SOLAR_POLL_TIMEOUT_SECONDS,
            context=_insecure_ssl_context(),
        )
    else:
        conn = http.client.HTTPConnection(
            host, port or 80, timeout=SOLAR_POLL_TIMEOUT_SECONDS
        )
    try:
        conn.request("GET", "/production.json", headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
    finally:
        conn.close()
    if status == 401:
        raise RuntimeError(
            "enphase_envoy 401 — a JWT is required (firmware-7.0+ Envoy); "
            "set config.jwt"
        )
    if status != 200:
        raise RuntimeError(f"enphase_envoy HTTP {status}")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"enphase_envoy JSON parse failed: {e}") from None
    production = data.get("production") if isinstance(data, dict) else None
    if not isinstance(production, list):
        raise RuntimeError("enphase_envoy /production.json missing production array")
    eim = next(
        (p for p in production if isinstance(p, dict) and p.get("type") == "eim"),
        None,
    )
    inverters = next(
        (p for p in production if isinstance(p, dict) and p.get("type") == "inverters"),
        None,
    )
    chosen = eim or inverters or {}
    return {
        "vendor": "enphase_envoy",
        "production_w": _num(chosen.get("wNow")),
        "lifetime_energy_wh": _num(chosen.get("whLifetime")),
        "active_count": chosen.get("activeCount"),
        "reading_type": chosen.get("type"),  # "eim" (metered) or "inverters"
    }


# ── consumed by the watchdog probes ─────────────────────────────────────


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
