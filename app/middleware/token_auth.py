"""API-token bearer authentication — Hub Tier-2 Feature 4a.

The `admin_auth` middleware resolves a human principal from a session
cookie or a user JWT. This module adds the *machine* principal path:
an `Authorization: Bearer rbt_<token>` header resolved against the
`api_tokens` table to a scoped, non-human principal.

`api_token_required(scope=...)` is a route decorator for JSON API
endpoints that should be reachable by an API token. It:
  * resolves the `rbt_`-prefixed bearer token via
    `app/services/api_tokens.py::verify`,
  * rejects unknown / revoked / expired tokens with 401,
  * enforces the required scope server-side (a `read` route needs the
    `read` scope; a `write` route needs `write`) — a scope miss is 403,
  * binds the token's org onto the tenant-scope ContextVar so Tier-A
    queries in the view are filtered to the token's organization,
  * exposes the principal as `g.api_token` for the view + audit.

A token principal is deliberately NOT a `User` — it has no role and is
not `g.current_user`. Routes that must stay human-only keep using the
`role_required_*` decorators and are simply unreachable with a token.
"""

from __future__ import annotations

import logging
from functools import wraps

from flask import g, request

from app.middleware.response import err
from app.services import api_tokens as token_svc

log = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer "


def _presented_token() -> str | None:
    """Extract an `rbt_`-prefixed bearer string from the request, or
    None when the Authorization header is absent / not an API token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith(_BEARER_PREFIX):
        return None
    candidate = auth[len(_BEARER_PREFIX):].strip()
    if not candidate.startswith(token_svc.TOKEN_STRING_PREFIX):
        return None
    return candidate


def resolve_api_token_principal() -> dict | None:
    """Resolve the request's API token to a principal dict, or None.

    Used both by the decorator below and by `admin_auth` so a route that
    accepts either a human or a token principal can branch on it.
    """
    candidate = _presented_token()
    if candidate is None:
        return None
    return token_svc.verify(candidate)


def api_token_required(scope: str = token_svc.SCOPE_READ):
    """Require a valid API token carrying `scope` for a JSON API route.

    Apply to endpoints that machine clients should reach. The decorated
    view runs with `g.api_token` set and the tenant scope bound to the
    token's org.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            principal = resolve_api_token_principal()
            if principal is None:
                return err(
                    "auth_required",
                    "A valid API token is required (Authorization: Bearer rbt_...).",
                    status=401,
                )
            if not token_svc.has_scope(principal, scope):
                return err(
                    "insufficient_scope",
                    f"this API token lacks the '{scope}' scope.",
                    status=403,
                )
            g.api_token = principal
            # Bind the tenant scope to the token's org so Tier-A queries
            # in the view are filtered (design cross-cutting §scope).
            _bind_token_tenant_scope(principal)
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def _bind_token_tenant_scope(principal: dict) -> None:
    """Push the token's org onto the tenant-scope ContextVar.

    Best-effort — a binding hiccup must not 500 an otherwise-valid
    request; the `do_orm_execute` filter then flags any unscoped Tier-A
    access as a `tenant.unscoped_access` audit row, the intended
    fail-loud behaviour. The ContextVar is cleared by the existing
    `register_tenant_teardown` hook at request end.
    """
    try:
        from app.services import tenant_scope

        org_id = principal.get("organization_id")
        if org_id is not None:
            tenant_scope.set_org(org_id)
    except Exception:  # pragma: no cover - defensive
        log.exception("API-token tenant-scope binding failed")
