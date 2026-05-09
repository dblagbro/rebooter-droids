"""Admin UI + API for enrollment tokens (mint + list)."""

from __future__ import annotations

from flask import current_app, g, redirect, render_template, request, session, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_api, admin_required_ui
from app.middleware.response import ok
from app.services.enrollment import list_enrollment_tokens, mint_enrollment_token


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/enrollment-tokens")
@admin_required_ui
def enrollment_tokens_page():
    tokens = list_enrollment_tokens()
    return render_template(
        "enrollment_tokens.html",
        **_ctx({"tokens": tokens, "new_token": session.pop("_new_enrollment_token", None)}),
    )


@admin_ui_bp.post("/enrollment-tokens")
@admin_required_ui
def enrollment_tokens_create():
    settings = current_app.config["SETTINGS"]
    record, raw_secret = mint_enrollment_token(
        settings,
        issued_by_user_id=g.current_user.id,
        display_name_hint=(request.form.get("display_name_hint") or "").strip() or None,
        note=(request.form.get("note") or "").strip() or None,
    )
    session["_new_enrollment_token"] = {
        "id": record.id,
        "enrollment_token": raw_secret,
        "expires_at": record.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return redirect(url_for("admin_ui.enrollment_tokens_page"))


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.post("/enrollment-tokens")
@admin_required_api
def create_enrollment_token():
    body = request.get_json(silent=True) or {}
    settings = current_app.config["SETTINGS"]
    record, raw_secret = mint_enrollment_token(
        settings,
        issued_by_user_id=g.current_user.id,
        site_id=body.get("site_id"),
        display_name_hint=body.get("display_name_hint"),
        note=body.get("note"),
    )
    return ok(
        {
            "id": record.id,
            "enrollment_token": raw_secret,
            "expires_at": record.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "display_name_hint": record.display_name_hint,
            "site_id": record.site_id,
            "note": record.note,
        },
        status=201,
    )


@admin_api_bp.get("/enrollment-tokens")
@admin_required_api
def list_enrollment_tokens_api():
    rows = list_enrollment_tokens()
    return ok(
        {
            "tokens": [
                {
                    "id": r.id,
                    "consumed_at": r.consumed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if r.consumed_at
                    else None,
                    "consumed_by_device_id": r.consumed_by_device_id,
                    "expires_at": r.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "display_name_hint": r.display_name_hint,
                    "site_id": r.site_id,
                    "note": r.note,
                    "created_at": r.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                for r in rows
            ]
        }
    )
