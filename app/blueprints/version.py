from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint

from app.middleware.response import ok
from app.version import __version__

bp = Blueprint("version", __name__)


@bp.get("/version")
def get_version():
    return ok(
        {
            "service": "rebooter-droids",
            "version": __version__,
            "server_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
