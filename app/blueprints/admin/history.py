"""History — unified log feed.

v0.3.0 P1: rendered audit-event data under the new ``/app/history`` URL.
v0.4.27: added action_prefix chip filters across the audit slice.
v0.4.30 (C1): extended with multi-source support — pass
``?source=watchdog_probe`` / ``?source=device_event`` / ``?source=all``
to surface non-audit event streams.
v0.4.32 (C2): ``?export=csv`` / ``?export=json`` streams the current
filter view as a download (max 50_000 rows / 90 days per request).

``/app/audit`` continues to serve its current (audit-only) page for
URL stability; in P6 it becomes a redirect to /app/history.
"""

from __future__ import annotations

import csv
import io
import json

from flask import Response, render_template, request, stream_with_context

from app.blueprints.admin import admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import role_required_ui
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import history as history_service


_ALLOWED_SOURCES = ("audit", "watchdog_probe", "device_event", "all")
_ALLOWED_EXPORTS = ("csv", "json")
_EXPORT_MAX_ROWS = 50_000


@admin_ui_bp.get("/history")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def history_page():
    source = (request.args.get("source") or "audit").lower()
    if source not in _ALLOWED_SOURCES:
        source = "audit"
    action_prefix = request.args.get("action_prefix") or None
    export = (request.args.get("export") or "").lower() or None

    # Export path: bypass the template; stream a download.
    if export in _ALLOWED_EXPORTS:
        return _stream_export(
            export=export,
            source=source,
            action_prefix=action_prefix,
        )

    rows = history_service.query_unified(
        source=source,
        actor_user_id=request.args.get("actor_user_id") or None,
        action=request.args.get("action") or None,
        action_prefix=action_prefix,
        target_type=request.args.get("target_type") or None,
        target_id=request.args.get("target_id") or None,
        q=request.args.get("q") or None,
        limit=int(request.args.get("limit") or 200),
    )
    return render_template(
        "history/index.html",
        **_ctx(
            {
                "active": "history",
                "events": rows,
                "source": source,
                "action_prefix": action_prefix,
            }
        ),
    )


def _stream_export(*, export: str, source: str, action_prefix: str | None):
    """v0.4.32 (C2): stream the current history view as a download.

    Honours the same filters the page uses (source, action_prefix,
    free-text fields). Caps at 50_000 rows per request; if you need
    more, narrow the filter or post-process locally — we deliberately
    don't paginate because the export use-case is "give me a snapshot
    I can grep" not "build a data warehouse".
    """
    # Resolve the same filter set the page uses
    rows = history_service.query_unified(
        source=source,
        actor_user_id=request.args.get("actor_user_id") or None,
        action=request.args.get("action") or None,
        action_prefix=action_prefix,
        target_type=request.args.get("target_type") or None,
        target_id=request.args.get("target_id") or None,
        limit=_EXPORT_MAX_ROWS,
    )

    suffix = action_prefix or source
    filename = f"history-{suffix}.{export}"
    if export == "csv":
        return Response(
            stream_with_context(_csv_iter(rows)),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    # JSON
    return Response(
        stream_with_context(_json_iter(rows)),
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


_CSV_COLUMNS = (
    "at",
    "source",
    "actor",
    "action",
    "target_type",
    "target_id",
    "ip",
    "details",
)


def _csv_iter(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    yield buf.getvalue()
    for r in rows:
        buf.seek(0)
        buf.truncate(0)
        writer.writerow([
            r.get("at") or "",
            r.get("source") or "",
            r.get("actor") or "",
            r.get("action") or "",
            r.get("target_type") or "",
            r.get("target_id") or "",
            r.get("ip") or "",
            json.dumps(r.get("details") or {}, separators=(",", ":")),
        ])
        yield buf.getvalue()


def _json_iter(rows):
    yield "[\n"
    first = True
    for r in rows:
        if not first:
            yield ",\n"
        first = False
        yield json.dumps(r, separators=(",", ":"))
    yield "\n]\n"
