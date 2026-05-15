"""External-sensor source registry — CRUD, validation, serialization.

The operator-facing side of the subpackage: register / list / enable /
delete sources, validate per-kind config, and serialize a source row
(with secrets redacted) for the admin API. No polling or inbound-event
handling lives here — see `_pollers` / `_inbound`.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import session_scope
from app.models import ExternalSensorSample, ExternalSensorSource
from app.models.external_sensors import (
    EXTERNAL_SOURCE_KINDS,
    KIND_TO_MODALITY,
    WEBHOOK_KINDS,
)
from app.services.external_sensors._common import ROKU_DEFAULT_PORT, _iso

# Secret config fields masked in the admin-facing serialization.
_SECRET_CONFIG_KEYS = ("token", "api_key", "jwt", "community", "password")
# v0.5.58: SNMPv3 secrets live nested under config.v3.
_SECRET_V3_KEYS = ("auth_key", "priv_key")


def _redact_config(kind: str, config: dict) -> dict:
    """Strip secret fields (HA bearer tokens, SolarEdge API keys,
    Enphase JWTs, SNMP community strings + v3 keys) from the
    admin-facing source serialization."""
    if not config:
        return {}
    out = dict(config)
    for key in _SECRET_CONFIG_KEYS:
        if out.get(key):
            out[key] = "********"
    v3 = out.get("v3")
    if isinstance(v3, dict):
        v3 = dict(v3)
        for key in _SECRET_V3_KEYS:
            if v3.get(key):
                v3[key] = "********"
        out["v3"] = v3
    return out


def _serialize(row: ExternalSensorSource, *, latest_sample: dict | None = None) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        # v0.5.60 (P3a / RFC-006): cross-modal query key, derived from kind.
        "modality": KIND_TO_MODALITY.get(row.kind),
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
    # ical, solaredge — cloud; webhook kinds — inbound) skip host/port
    # validation entirely.
    needs_host = kind in ("roku", "home_assistant", "enphase_envoy", "snmp", "mqtt")
    if needs_host and not host:
        raise ValueError("host is required for this kind")
    if port is None:
        if kind == "roku":
            port = ROKU_DEFAULT_PORT
        elif kind == "home_assistant":
            port = 8123
        elif kind == "enphase_envoy":
            port = 80
        elif kind == "snmp":
            port = 161
        elif kind == "mqtt":
            port = 1883
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
    if kind == "mqtt":
        # v0.5.63 (B17 Ship 3): MQTT subscriber. `host`/`port` = broker;
        # `topics` is the subscribe list; username/password optional.
        topics_raw = config.get("topics")
        if isinstance(topics_raw, str):
            topics_raw = [t.strip() for t in topics_raw.splitlines() if t.strip()]
        if not isinstance(topics_raw, list) or not topics_raw:
            raise ValueError(
                "mqtt config.topics is required (a non-empty list of topic filters)"
            )
        topics = [str(t).strip() for t in topics_raw if str(t).strip()]
        if not topics:
            raise ValueError("mqtt config.topics must contain at least one topic")
        out: dict = {"topics": topics}
        username = str(config.get("username") or "").strip()
        password = str(config.get("password") or "")
        if username:
            out["username"] = username
        if password:
            out["password"] = password
        client_id = str(config.get("client_id") or "").strip()
        out["client_id"] = client_id or f"rebooter-droids-{secrets.token_hex(4)}"
        return out
    if kind in WEBHOOK_KINDS:
        # v0.5.61 (B17 Ship 2): inbound-webhook kinds. The hub never
        # reaches out — the external service POSTs to
        # /api/v1/integrations/webhook/<source_id>, authenticated by a
        # per-source secret. The secret is auto-minted here if absent.
        out: dict = {}
        secret = str(config.get("webhook_secret") or "").strip()
        out["webhook_secret"] = secret or secrets.token_hex(24)
        server_name = str(config.get("server_name") or "").strip()
        if server_name:
            out["server_name"] = server_name[:120]
        return out
    if kind == "snmp":
        # v0.5.58 (P2.2/P2.3): router/managed-switch SNMP poll. v2c uses
        # a community string; v3 uses a nested user/auth/priv block.
        version = str(config.get("version") or "2c").strip().lower()
        if version not in ("2c", "3"):
            raise ValueError("snmp config.version must be '2c' or '3'")
        out: dict = {"version": version}
        if version == "2c":
            community = str(config.get("community") or "").strip()
            if not community:
                raise ValueError("snmp config.community is required for SNMP v2c")
            out["community"] = community
        else:
            v3 = config.get("v3") or {}
            if not isinstance(v3, dict):
                raise ValueError("snmp config.v3 must be an object")
            user = str(v3.get("user") or "").strip()
            auth_key = str(v3.get("auth_key") or "").strip()
            priv_key = str(v3.get("priv_key") or "").strip()
            if not user or not auth_key or not priv_key:
                raise ValueError(
                    "snmp v3 requires v3.user, v3.auth_key, and v3.priv_key"
                )
            auth_proto = str(v3.get("auth_proto") or "SHA").strip().upper()
            priv_proto = str(v3.get("priv_proto") or "AES").strip().upper()
            if auth_proto not in ("SHA", "MD5"):
                raise ValueError("snmp v3.auth_proto must be SHA or MD5")
            if priv_proto not in ("AES", "DES"):
                raise ValueError("snmp v3.priv_proto must be AES or DES")
            out["v3"] = {
                "user": user, "auth_proto": auth_proto, "auth_key": auth_key,
                "priv_proto": priv_proto, "priv_key": priv_key,
            }
        # Optional interface allow-list — limits which interfaces are
        # stored in the sample (a 48-port switch otherwise stores 48).
        iface_filter = config.get("interface_filter")
        if iface_filter:
            if not isinstance(iface_filter, list):
                raise ValueError("snmp config.interface_filter must be a list")
            names = [str(x).strip() for x in iface_filter if str(x).strip()]
            if names:
                out["interface_filter"] = names
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
