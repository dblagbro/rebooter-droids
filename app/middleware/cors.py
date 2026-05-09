"""Strict CORS allowlist for `/api/v1/*` — v0.2.11 (R8-CORS).

Default allowlist is empty, so behaviour is unchanged for existing
self-hosted deployments. Operators opt in by setting
`REBOOTER_CORS_ALLOWED_ORIGINS` to a comma-separated list of exact
origins (including scheme + host + optional port). Only those origins
get echoed back in `Access-Control-Allow-Origin`.

Why hand-rolled instead of Flask-CORS:
- The policy we want is narrow (one URL prefix, exact-match allowlist,
  credentials-on, fixed method/header set).
- We do not want to inherit Flask-CORS's wildcard defaults or surprise
  configuration semantics.
- One file is easier to audit than a dependency.
"""

from __future__ import annotations

from flask import Flask, Response, make_response, request

_API_PREFIX = "/api/v1/"
_ALLOWED_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"
_ALLOWED_HEADERS = "Authorization, Content-Type, X-Requested-With"


def init_cors(app: Flask, allowed_origins: tuple[str, ...]) -> None:
    """Attach the after-request hook + an OPTIONS preflight handler.

    `allowed_origins` is the exact-match allowlist (e.g.
    `("https://app.example.com", "https://staging.app.example.com")`).
    Empty tuple = CORS effectively disabled.
    """

    allowed = frozenset(o.strip() for o in allowed_origins if o.strip())

    @app.before_request
    def _handle_preflight():  # pragma: no cover -- hit by OPTIONS only
        if request.method != "OPTIONS":
            return None
        if not request.path.startswith(_API_PREFIX):
            return None
        origin = request.headers.get("Origin")
        if origin not in allowed:
            return None
        resp = make_response("", 204)
        _attach_headers(resp, origin)
        return resp

    @app.after_request
    def _add_cors_headers(resp: Response) -> Response:
        if not request.path.startswith(_API_PREFIX):
            return resp
        origin = request.headers.get("Origin")
        if origin and origin in allowed:
            _attach_headers(resp, origin)
        return resp


def _attach_headers(resp: Response, origin: str) -> None:
    resp.headers["Access-Control-Allow-Origin"] = origin
    resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Methods"] = _ALLOWED_METHODS
    resp.headers["Access-Control-Allow-Headers"] = _ALLOWED_HEADERS
    resp.headers["Access-Control-Max-Age"] = "600"
    resp.headers.add("Vary", "Origin")
