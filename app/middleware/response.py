from __future__ import annotations

from typing import Any

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException


def ok(
    data: Any | None = None,
    status: int = 200,
    headers: dict | None = None,
):
    """Standard envelope: ``{"ok": True, "data": …}``.

    v0.5.20: optional ``headers`` dict — useful for endpoints that need
    to return RFC-defined response headers (e.g. ``Preference-Applied``
    for the long-poll ``/device/commands``). Falsy/empty dict is
    treated as no headers, preserving the two-tuple shape that callers
    not passing ``headers`` keep emitting.
    """
    body = jsonify({"ok": True, "data": data if data is not None else {}})
    if headers:
        return body, status, headers
    return body, status


def err(
    code: str,
    message: str,
    status: int = 400,
    extra: dict | None = None,
    headers: dict | None = None,
):
    payload = {"code": code, "message": message}
    if extra:
        payload.update(extra)
    body = jsonify({"ok": False, "error": payload})
    if headers:
        return body, status, headers
    return body, status


def register_envelope_handlers(app: Flask) -> None:
    @app.errorhandler(HTTPException)
    def _handle_http_exc(exc: HTTPException):
        return err(
            code=exc.name.lower().replace(" ", "_"),
            message=exc.description or exc.name,
            status=exc.code or 500,
        )

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        app.logger.exception("Unhandled exception")
        return err("internal_error", "An internal error occurred.", status=500)
