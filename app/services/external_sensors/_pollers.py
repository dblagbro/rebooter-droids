"""External-sensor pollers — the pull side of the subpackage.

`poll_source` / `poll_all_due` are the entry points (the latter is the
APScheduler tick target). `_poll_kind` dispatches to a per-kind
`_poll_<kind>` function; adding a polled integration means adding one
branch + one function here. Webhook + subscriber kinds are *not* polled
— see `_inbound`.

Each `_poll_<kind>` raises on a transport / parse error so `poll_source`
records it in the source row's `last_error` and the tick marches on.
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import shutil
import subprocess
import urllib.parse
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import ExternalSensorSample, ExternalSensorSource
from app.models.external_sensors import SUBSCRIBER_KINDS, WEBHOOK_KINDS
from app.services.external_sensors._common import _iso

log = logging.getLogger(__name__)

ROKU_POLL_TIMEOUT_SECONDS = 3
HA_POLL_TIMEOUT_SECONDS = 5
WEATHER_POLL_TIMEOUT_SECONDS = 8
ICAL_POLL_TIMEOUT_SECONDS = 10
SOLAR_POLL_TIMEOUT_SECONDS = 8
SNMP_POLL_TIMEOUT_SECONDS = 12


# ── poll entry points ───────────────────────────────────────────────────


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
        if src.kind in WEBHOOK_KINDS:
            # v0.5.61 (B17 Ship 2): webhook sources receive inbound
            # events — there is nothing to poll.
            return {
                "error": "This is a webhook source — it receives inbound "
                         "events and is not polled."
            }
        if src.kind in SUBSCRIBER_KINDS:
            # v0.5.63 (B17 Ship 3): MQTT sources receive messages on a
            # long-lived subscription — nothing to poll.
            return {
                "error": "This is an MQTT source — a background subscriber "
                         "receives messages; it is not polled."
            }
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
                    ExternalSensorSource.enabled.is_(True),
                    # v0.5.61/.63: webhook + subscriber kinds receive
                    # inbound events; the poll tick skips them (no
                    # `_poll_<kind>` branch exists for them).
                    ExternalSensorSource.kind.not_in(
                        tuple(WEBHOOK_KINDS) + tuple(SUBSCRIBER_KINDS)
                    ),
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
    if src.kind == "snmp":
        return _poll_snmp(src.host, src.port, src.config or {})
    raise ValueError(f"unsupported source kind: {src.kind}")


def _insecure_ssl_context():
    """Lazy import — only used when a poller opts into verify_ssl=False
    (HA self-signed) or hits a self-signed Envoy."""
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _num(value) -> float | None:
    """Coerce a vendor JSON numeric to float, or None if missing/bad."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── Roku ECP ─────────────────────────────────────────────────────────────


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


# ── Home Assistant ──────────────────────────────────────────────────────


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


# ── Weather (NWS api.weather.gov) ───────────────────────────────────────


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


# ── SNMP (router / managed-switch IF-MIB poll) ──────────────────────────


# IF-MIB / ifXTable column OIDs. ifTable entries are
# 1.3.6.1.2.1.2.2.1.<col>.<ifIndex>; ifXTable entries are
# 1.3.6.1.2.1.31.1.1.1.<col>.<ifIndex>. See the design note
# docs/notes/2026-05-15-p2-router-switch-telemetry-design.md §2.1.
_SNMP_IFTABLE_OID = "1.3.6.1.2.1.2.2.1"
_SNMP_IFXTABLE_OID = "1.3.6.1.2.1.31.1.1.1"
_SNMP_IFTABLE_COLS = {
    "8": "oper_status",
    "13": "in_discards",
    "14": "in_errors",
    "19": "out_discards",
    "20": "out_errors",
}
_SNMP_IFXTABLE_COLS = {
    "1": "if_name",
    "6": "in_octets",   # ifHCInOctets — 64-bit
    "10": "out_octets",  # ifHCOutOctets — 64-bit
    "15": "speed_mbps",  # ifHighSpeed
}
_SNMP_OPER_STATUS = {
    "1": "up", "2": "down", "3": "testing", "4": "unknown",
    "5": "dormant", "6": "notPresent", "7": "lowerLayerDown",
}


def _snmp_auth_args(config: dict) -> list[str]:
    """Build the net-snmp version/auth argv fragment from a validated
    snmp config (see `_crud._validate_kind_config`)."""
    version = (config.get("version") or "2c").strip().lower()
    if version == "3":
        v3 = config.get("v3") or {}
        return [
            "-v", "3", "-l", "authPriv",
            "-u", str(v3.get("user") or ""),
            "-a", str(v3.get("auth_proto") or "SHA"),
            "-A", str(v3.get("auth_key") or ""),
            "-x", str(v3.get("priv_proto") or "AES"),
            "-X", str(v3.get("priv_key") or ""),
        ]
    return ["-v", "2c", "-c", str(config.get("community") or "")]


def _run_snmp(binary: str, auth: list[str], target: str, oid: str) -> str:
    """Run one net-snmp CLI call, return stdout. `-Oqn` = quick output,
    numeric OIDs (no MIB files needed). Raises RuntimeError on a
    non-zero exit, surfacing the net-snmp error line."""
    bin_path = shutil.which(binary)
    if not bin_path:
        raise RuntimeError(f"net-snmp not installed in container ({binary} missing)")
    try:
        proc = subprocess.run(
            [bin_path, "-Oqn", "-t", "5", "-r", "1", *auth, target, oid],
            capture_output=True, text=True,
            timeout=SNMP_POLL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"snmp timeout polling {target}") from None
    if proc.returncode != 0:
        # net-snmp prints a one-time "Created directory: /var/lib/snmp/…"
        # notice to stderr on first run — filter that (and blank lines)
        # so the surfaced error is the real one (Timeout / Authentication
        # failure / Unknown host …), not the housekeeping noise.
        lines = [
            ln.strip()
            for ln in (proc.stderr or proc.stdout or "").splitlines()
            if ln.strip() and not ln.strip().startswith("Created directory:")
        ]
        raise RuntimeError(f"snmp error: {lines[0] if lines else 'unknown'}")
    return proc.stdout


def _parse_snmp_table(stdout: str, base_oid: str, cols: dict) -> dict:
    """Parse `-Oqn` walk output into {if_index: {field: value}}.

    Each line is `.<oid> <value>` — the OID's last component is the
    ifIndex and the one before it is the column number.
    """
    out: dict[str, dict] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        oid, value = parts[0].lstrip("."), parts[1].strip()
        segs = oid.split(".")
        if len(segs) < 2:
            continue
        col, if_index = segs[-2], segs[-1]
        field = cols.get(col)
        if field is None:
            continue
        out.setdefault(if_index, {})[field] = value
    return out


def _poll_snmp(host: str, port: int, config: dict) -> dict:
    """v0.5.58 (P2.2/P2.3): poll a router/managed-switch IF-MIB over SNMP.

    Shells out to net-snmp `snmpbulkwalk`/`snmpget` (the same
    subprocess pattern the watchdog ping probe uses) — two table walks
    + one scalar get. Raw monotonic counters are stored as-is; the
    watchdog rate probes compute deltas between consecutive samples.
    """
    target = f"{host}:{port or 161}"
    auth = _snmp_auth_args(config)

    iftable = _parse_snmp_table(
        _run_snmp("snmpbulkwalk", auth, target, _SNMP_IFTABLE_OID),
        _SNMP_IFTABLE_OID, _SNMP_IFTABLE_COLS,
    )
    ifxtable = _parse_snmp_table(
        _run_snmp("snmpbulkwalk", auth, target, _SNMP_IFXTABLE_OID),
        _SNMP_IFXTABLE_OID, _SNMP_IFXTABLE_COLS,
    )
    # Scalars — sysName + sysUptime in one get.
    sys_out = _run_snmp("snmpget", auth, target,
                        "1.3.6.1.2.1.1.5.0 1.3.6.1.2.1.1.3.0")
    sys_lines = [ln.strip() for ln in sys_out.splitlines() if ln.strip()]
    sys_name = sys_lines[0].split(None, 1)[-1].strip().strip('"') if sys_lines else None
    sys_uptime_ticks = None
    if len(sys_lines) > 1:
        try:
            sys_uptime_ticks = int(sys_lines[1].split(None, 1)[-1].strip())
        except (ValueError, IndexError):
            sys_uptime_ticks = None

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    iface_filter = config.get("interface_filter") or None
    interfaces: dict[str, dict] = {}
    for if_index in set(iftable) | set(ifxtable):
        ift = iftable.get(if_index, {})
        ifx = ifxtable.get(if_index, {})
        name = (ifx.get("if_name") or "").strip().strip('"') or f"if{if_index}"
        if iface_filter and name not in iface_filter:
            continue
        status_raw = ift.get("oper_status")
        interfaces[name] = {
            "if_index": _int(if_index),
            "oper_status": _SNMP_OPER_STATUS.get(str(status_raw), str(status_raw)),
            "speed_mbps": _int(ifx.get("speed_mbps")),
            "in_octets": _int(ifx.get("in_octets")),
            "out_octets": _int(ifx.get("out_octets")),
            "in_errors": _int(ift.get("in_errors")),
            "out_errors": _int(ift.get("out_errors")),
            "in_discards": _int(ift.get("in_discards")),
            "out_discards": _int(ift.get("out_discards")),
        }
    up_count = sum(1 for i in interfaces.values() if i["oper_status"] == "up")
    return {
        "modality": "network",
        "sys_name": sys_name,
        "sys_uptime_seconds": (
            sys_uptime_ticks // 100 if sys_uptime_ticks is not None else None
        ),
        "interface_count": len(interfaces),
        "interfaces_up": up_count,
        "interfaces": interfaces,
    }
