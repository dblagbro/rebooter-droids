"""Admin UI + API for scoped API tokens — Hub Tier-2 Feature 4a.

The API-tokens Settings sub-page: list existing tokens (prefix, name,
scopes, last-used, expiry), mint a new one (the plaintext is shown
exactly once), and revoke. All routes are `admin`-and-up and audited.

The blueprint is a thin HTTP translator over `app/services/api_tokens.py`
— validation, hashing and expiry logic live in the service.
"""

from __future__ import annotations

from flask import flash, g, redirect, render_template, request, session, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    admin_required_api,
    role_required_api,
    role_required_ui,
)
from app.middleware.response import err, ok
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import api_tokens as svc
from app.services import audit as audit_service

# Session key for the one-time plaintext flash-through. The plaintext is
# stashed for exactly one render after a mint and then popped — never
# stored anywhere persistent.
_NEW_TOKEN_SESSION_KEY = "api_token_new_plaintext"


def _tokens_ctx(extra: dict | None = None) -> dict:
    base = {
        "active": "settings",
        "settings_tab": "api-tokens",
        "tokens": svc.list_tokens(),
        "new_token": None,
        "error": None,
        "form_name": "",
        "form_scopes": ["read"],
        "form_expires_in_days": svc.DEFAULT_EXPIRY_DAYS,
        "known_scopes": list(svc.KNOWN_SCOPES),
        "default_expiry_days": svc.DEFAULT_EXPIRY_DAYS,
    }
    base.update(extra or {})
    return _ctx(base)


# ── UI ─────────────────────────────────────────────────────────────────


@admin_ui_bp.get("/settings/api-tokens")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def settings_api_tokens_page():
    # Pop a just-minted plaintext (shown once) out of the session.
    new_token = session.pop(_NEW_TOKEN_SESSION_KEY, None)
    return render_template(
        "settings/api_tokens.html",
        **_tokens_ctx({"new_token": new_token}),
    )


@admin_ui_bp.post("/settings/api-tokens/create")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def settings_api_tokens_create_submit():
    name = (request.form.get("name") or "").strip()
    scopes = request.form.getlist("scopes") or ["read"]
    raw_expiry = (request.form.get("expires_in_days") or "").strip()

    def _re(msg: str):
        return (
            render_template(
                "settings/api_tokens.html",
                **_tokens_ctx({
                    "error": msg,
                    "form_name": name,
                    "form_scopes": scopes,
                    "form_expires_in_days": raw_expiry or svc.DEFAULT_EXPIRY_DAYS,
                }),
            ),
            400,
        )

    expires_in_days: int | None = None
    if raw_expiry:
        try:
            expires_in_days = int(raw_expiry)
        except ValueError:
            return _re(f"Expiry days must be a whole number: {raw_expiry!r}")

    try:
        token, plaintext = svc.mint(
            name=name,
            scopes=scopes,
            expires_in_days=expires_in_days,
            created_by_user_id=g.current_user.id,
        )
    except svc.ApiTokenError as e:
        return _re(e.message)

    audit_service.record(
        "api_token.created",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="api_token",
        target_id=token["id"],
        details={"name": token["name"], "scopes": token["scopes"]},
    )
    # Stash the plaintext for a single render — never persisted.
    session[_NEW_TOKEN_SESSION_KEY] = {
        "id": token["id"],
        "name": token["name"],
        "plaintext": plaintext,
    }
    return redirect(url_for("admin_ui.settings_api_tokens_page"))


@admin_ui_bp.post("/settings/api-tokens/<token_id>/revoke")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def settings_api_tokens_revoke_submit(token_id: str):
    if svc.revoke(token_id):
        audit_service.record(
            "api_token.revoked",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="api_token",
            target_id=token_id,
        )
        flash("API token revoked.", "info")
    else:
        flash("API token not found.", "error")
    return redirect(url_for("admin_ui.settings_api_tokens_page"))


# ── API ────────────────────────────────────────────────────────────────


@admin_api_bp.get("/api-tokens")
@admin_required_api
def list_api_tokens_api():
    return ok({"api_tokens": svc.list_tokens()})


@admin_api_bp.post("/api-tokens")
@role_required_api(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def create_api_token_api():
    body = request.get_json(silent=True) or {}
    try:
        token, plaintext = svc.mint(
            name=body.get("name", ""),
            scopes=body.get("scopes"),
            expires_in_days=body.get("expires_in_days"),
            created_by_user_id=g.current_user.id,
        )
    except svc.ApiTokenError as e:
        return err(e.code, e.message, status=400)
    audit_service.record(
        "api_token.created",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="api_token",
        target_id=token["id"],
        details={"name": token["name"], "scopes": token["scopes"]},
    )
    # The plaintext is returned exactly once — here.
    return ok({"api_token": token, "token": plaintext}, status=201)


@admin_api_bp.post("/api-tokens/<token_id>/revoke")
@role_required_api(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def revoke_api_token_api(token_id: str):
    if not svc.revoke(token_id):
        return err("token_unknown", "API token not found.", status=404)
    audit_service.record(
        "api_token.revoked",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="api_token",
        target_id=token_id,
    )
    return ok({"revoked": True})
