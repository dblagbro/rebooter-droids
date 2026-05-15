"""Inbound integration webhook endpoint — v0.5.61 (B17 Ship 2).

`POST /api/v1/integrations/webhook/<source_id>` — the single inbound
endpoint shared by all webhook-kind external sensors (Plex, Jellyfin,
iOS Shortcuts). The external service POSTs an event here; the hub
authenticates a per-source secret and appends a sample.

Auth model (see `docs/notes/2026-05-15-b17-remaining-integrations-
design.md` §4): a per-source `webhook_secret` in `source.config`,
checked constant-time against the `X-Webhook-Secret` header. No CSRF
token — these are called by external systems with no browser context,
and `/api/v1/*` is already CSRF-exempt. No admin session.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from flask import Blueprint, request

from app.db import session_scope
from app.middleware.response import err, ok
from app.models import ExternalSensorSource
from app.models.external_sensors import WEBHOOK_KINDS
from app.services import external_sensors as ext_svc

log = logging.getLogger(__name__)

bp = Blueprint("integrations_webhook_api", __name__)

# Reject oversized bodies before reading them — a runaway (or a sender
# with a leaked secret) must not be able to pump huge rows into the DB.
_MAX_WEBHOOK_BODY_BYTES = 64 * 1024


@bp.post("/api/v1/integrations/webhook/<source_id>")
def webhook_inbound(source_id: str):
    # Size cap first — cheap, and rejects abuse before any parsing.
    if request.content_length and request.content_length > _MAX_WEBHOOK_BODY_BYTES:
        return err("payload_too_large", "Webhook body exceeds 64 KiB.", status=413)

    with session_scope() as session:
        src = session.get(ExternalSensorSource, source_id)
        if src is None or src.kind not in WEBHOOK_KINDS:
            # Same 404 for "no such source" and "not a webhook source" —
            # don't leak which source ids exist.
            return err("not_found", "Not found.", status=404)
        enabled = src.enabled
        expected_secret = (src.config or {}).get("webhook_secret") or ""

    if not enabled:
        return err("source_disabled", "This webhook source is disabled.", status=403)

    presented = request.headers.get("X-Webhook-Secret") or ""
    if not expected_secret or not secrets.compare_digest(presented, expected_secret):
        return err("auth_failed", "Bad or missing X-Webhook-Secret.", status=401)

    # Body: JSON for Jellyfin / iOS Shortcuts; Plex POSTs multipart with
    # a `payload` form field carrying the JSON.
    payload: dict = {}
    if request.is_json:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            payload = body
    elif request.form.get("payload"):
        try:
            parsed = json.loads(request.form["payload"])
            if isinstance(parsed, dict):
                payload = parsed
        except (ValueError, TypeError):
            return err("bad_payload", "Could not parse webhook payload JSON.", status=400)

    payload["_received_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    payload["_remote_addr"] = request.remote_addr

    result = ext_svc.record_webhook_event(source_id, payload)
    if "error" in result:
        return err("webhook_failed", result["error"], status=400)
    return ok({"recorded": True}, status=202)
