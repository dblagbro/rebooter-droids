"""Public, no-auth firmware-channel-pointer redirects.

v0.3.9 (RFC-002 P1): a freshly-serial-flashed bootstrap firmware
(per RFC-005) does NOT have a bearer token yet — it's pre-
enrolment by definition. So the "fetch the latest stable main
firmware" URL has to be unauthenticated. This blueprint serves
that single shape.

Endpoint:
  GET /api/v1/firmware/<channel>/latest
    → 302 to <public-base>/<channel>/<latest-version-filename>.bin
    → 404 if no release exists in that channel

The redirect target is the per-channel layout written by
upload_release in v0.3.9. Devices that follow redirects (HTTP 302)
land on the actual binary; those that don't are pre-RFC-002 P3
firmware and aren't expected to use this surface anyway.

This bypasses nginx open_file_cache entirely: each request hits
Flask which queries the DB freshly. No static-file inode caching
to invalidate when a new release lands.
"""

from __future__ import annotations

from flask import Blueprint, current_app, redirect

from app.middleware.response import err
from app.services.firmware import ALLOWED_CHANNELS, latest_in_channel

bp = Blueprint("firmware_public", __name__)


@bp.get("/<channel>/latest")
def channel_latest_redirect(channel: str):
    if channel not in ALLOWED_CHANNELS:
        return err(
            "unknown_channel",
            f"channel must be one of {ALLOWED_CHANNELS}",
            status=400,
        )
    rel = latest_in_channel(channel)
    if rel is None:
        return err(
            "no_release",
            f"no firmware release exists in channel '{channel}' yet",
            status=404,
        )
    settings = current_app.config["SETTINGS"]
    base = settings.firmware_public_base.rstrip("/")
    target = f"{base}/{channel}/{rel.filename}"
    return redirect(target, code=302)
