"""Admin UI + API for enrollment tokens (mint + list)."""

from __future__ import annotations

from flask import current_app, g, redirect, render_template, request, session, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_api, admin_required_ui
from app.middleware.response import ok
from app.services import audit as audit_service
from app.services import mass_action
from app.services.enrollment import (
    list_enrollment_tokens,
    mint_enrollment_token,
    revoke_enrollment_token,
    revoke_enrollment_tokens_bulk,
)


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


# v0.3.4 (P3): single + bulk revoke pending enrollment tokens.
@admin_ui_bp.post("/enrollment-tokens/<token_id>/revoke")
@admin_required_ui
def enrollment_token_revoke_submit(token_id: str):
    from flask import flash

    if revoke_enrollment_token(token_id):
        audit_service.record(
            "enrollment_token.revoked",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="enrollment_token",
            target_id=token_id,
            details={"reason": "operator"},
        )
        flash("Enrollment token revoked.", "info")
    else:
        flash(
            "Could not revoke that token — it may already be consumed.",
            "warning",
        )
    return redirect(url_for("admin_ui.enrollment_tokens_page"))


@admin_ui_bp.post("/enrollment-tokens/bulk-revoke")
@admin_required_ui
def enrollment_tokens_bulk_revoke_submit():
    from flask import flash

    ids = [i for i in request.form.getlist("token_id") if i]
    if not ids:
        flash("Select at least one token first.", "warning")
        return redirect(url_for("admin_ui.enrollment_tokens_page"))
    try:
        mass_action.validate(
            target_count=len(ids),
            expected_typed_value="revoke",
            confirmation_level=request.form.get("confirmation_level"),
            confirmation_typed_value=request.form.get("confirmation_typed_value"),
        )
    except mass_action.ConfirmationRequired as e:
        flash(
            f"Bulk revoke affects {len(ids)} tokens and requires "
            f"confirmation ({e.required_level}).",
            "error",
        )
        return redirect(url_for("admin_ui.enrollment_tokens_page"))
    result = revoke_enrollment_tokens_bulk(ids)
    audit_service.record(
        "enrollment_token.bulk_revoked",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="enrollment_token",
        target_id=None,
        details={
            "revoked_count": len(result["revoked"]),
            "skipped_unknown": len(result["skipped_unknown"]),
            "skipped_consumed": len(result["skipped_consumed"]),
            "revoked_ids": result["revoked"],
            "confirmation_level": mass_action.required_level(len(ids)),
            "reason": "operator",
        },
    )
    flash(
        f"Revoked {len(result['revoked'])} token(s)."
        + (f" {len(result['skipped_consumed'])} already consumed." if result["skipped_consumed"] else "")
        + (f" {len(result['skipped_unknown'])} unknown." if result["skipped_unknown"] else ""),
        "info",
    )
    return redirect(url_for("admin_ui.enrollment_tokens_page"))


# v0.3.1 (P2): friendlier "+ Enrol a device" wizard at /app/devices/new.
@admin_ui_bp.get("/devices/new")
@admin_required_ui
def enroll_device_wizard():
    new_token = session.pop("_new_enrollment_token", None)
    return render_template(
        "devices/new.html",
        **_ctx({"new_token": new_token}),
    )


@admin_ui_bp.post("/devices/new")
@admin_required_ui
def enroll_device_wizard_submit():
    settings = current_app.config["SETTINGS"]
    record, raw_secret = mint_enrollment_token(
        settings,
        issued_by_user_id=g.current_user.id,
        display_name_hint=(request.form.get("display_name_hint") or "").strip() or None,
        note=(request.form.get("note") or "qa-friendly enrolment").strip() or None,
    )
    session["_new_enrollment_token"] = {
        "id": record.id,
        "enrollment_token": raw_secret,
        "expires_at": record.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "display_name_hint": record.display_name_hint,
    }
    return redirect(url_for("admin_ui.enroll_device_wizard"))


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
