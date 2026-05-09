from __future__ import annotations

from typing import Any

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException


def ok(data: Any | None = None, status: int = 200):
    return jsonify({"ok": True, "data": data if data is not None else {}}), status


def err(code: str, message: str, status: int = 400, extra: dict | None = None):
    payload = {"code": code, "message": message}
    if extra:
        payload.update(extra)
    return jsonify({"ok": False, "error": payload}), status


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
