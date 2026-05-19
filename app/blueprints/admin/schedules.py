"""Schedules — v0.4.8 (B8)."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import flash, g, redirect, render_template, request, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import FormValidationError, _ctx, _int_field
from app.middleware.admin_auth import (
    ADMIN_AND_UP,
    admin_required_api,
    admin_required_ui,
    role_required_api,
    role_required_ui,
)
from app.middleware.response import err, ok
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import audit as audit_service
from app.services.devices import list_devices as svc_list_devices
from app.services.groups import list_groups as svc_list_groups
from app.services.schedules import (
    ScheduleValidationError,
    create as svc_create,
    delete as svc_delete,
    list_all as svc_list_all,
    set_enabled as svc_set_enabled,
)


# ── UI ─────────────────────────────────────────────────────────────────


@admin_ui_bp.get("/schedules")
@admin_required_ui
def schedules_page():
    schedules = svc_list_all()
    devices = svc_list_devices(include_qa_fixtures=False)
    groups = svc_list_groups()
    return render_template(
        "schedules/index.html",
        **_ctx({
            "active": "schedules",
            "schedules": schedules,
            "devices": devices,
            "groups": groups,
        }),
    )


@admin_ui_bp.post("/schedules")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def schedules_create_submit():
    name = (request.form.get("name") or "").strip()
    kind = (request.form.get("kind") or "").strip()
    recurrence = (request.form.get("recurrence") or "daily").strip()
    at_time_utc = (request.form.get("at_time_utc") or "").strip() or None
    weekdays_raw = request.form.getlist("weekdays")
    weekdays = [int(w) for w in weekdays_raw if w.isdigit()]
    try:
        duration_seconds = _int_field(
            request.form, "duration_seconds", default=0,
        )
        power_off_seconds = _int_field(
            request.form, "power_off_seconds", default=5,
        )
        post_reboot_holdoff_seconds = _int_field(
            request.form, "post_reboot_holdoff_seconds", default=180,
        )
    except FormValidationError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_ui.schedules_page"))

    target = {}
    target_kind = (request.form.get("target_kind") or "").strip()
    target_id = (request.form.get("target_id") or "").strip()
    if kind == "power_cycle":
        if target_kind in ("device", "group"):
            target = {"kind": target_kind, "id": target_id}
        elif target_kind == "tag":
            target = {"kind": "tag", "tag": target_id}

    start_at = None
    raw = (request.form.get("start_at") or "").strip()
    if raw:
        try:
            start_at = datetime.fromisoformat(raw + ":00+00:00" if len(raw) == 16 else raw)
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=timezone.utc)
        except ValueError:
            flash("Invalid start_at — use the date-time picker.", "error")
            return redirect(url_for("admin_ui.schedules_page"))

    try:
        s = svc_create(
            name=name,
            kind=kind,
            recurrence=recurrence,
            target=target,
            at_time_utc=at_time_utc,
            weekdays=weekdays,
            start_at=start_at,
            duration_seconds=duration_seconds,
            power_off_seconds=power_off_seconds,
            post_reboot_holdoff_seconds=post_reboot_holdoff_seconds,
            description=request.form.get("description"),
            created_by_user_id=g.current_user.id,
        )
    except ScheduleValidationError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_ui.schedules_page"))

    audit_service.record(
        "schedule.created",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="schedule",
        target_id=s["id"],
        details={"name": name, "kind": kind, "recurrence": recurrence},
    )
    flash(f"Schedule created: {s['sentence']}", "info")
    return redirect(url_for("admin_ui.schedules_page"))


@admin_ui_bp.post("/schedules/<schedule_id>/toggle")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def schedules_toggle_submit(schedule_id: str):
    enabled = (request.form.get("enabled") or "").lower() in ("1", "true", "on")
    svc_set_enabled(schedule_id, enabled)
    audit_service.record(
        "schedule.enabled_changed",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="schedule",
        target_id=schedule_id,
        details={"enabled": enabled},
    )
    return redirect(url_for("admin_ui.schedules_page"))


@admin_ui_bp.post("/schedules/<schedule_id>/delete")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def schedules_delete_submit(schedule_id: str):
    if svc_delete(schedule_id):
        audit_service.record(
            "schedule.deleted",
            actor_user_id=g.current_user.id,
            actor_email_snapshot=g.current_user.email,
            target_type="schedule",
            target_id=schedule_id,
            details={},
        )
    return redirect(url_for("admin_ui.schedules_page"))


# ── API ────────────────────────────────────────────────────────────────


@admin_api_bp.get("/schedules")
@admin_required_api
def list_schedules_api():
    return ok(svc_list_all())


@admin_api_bp.post("/schedules")
@role_required_api(*ADMIN_AND_UP)
def create_schedule_api():
    body = request.get_json(silent=True) or {}
    start_at = None
    if body.get("start_at"):
        try:
            start_at = datetime.fromisoformat(body["start_at"])
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=timezone.utc)
        except Exception:
            return err("validation_failed", "start_at must be ISO datetime", status=400)
    try:
        s = svc_create(
            name=body.get("name", ""),
            kind=body.get("kind", ""),
            recurrence=body.get("recurrence", "daily"),
            target=body.get("target") or {},
            at_time_utc=body.get("at_time_utc"),
            weekdays=body.get("weekdays") or [],
            start_at=start_at,
            duration_seconds=_int_field(body, "duration_seconds", default=0),
            power_off_seconds=_int_field(body, "power_off_seconds", default=5),
            post_reboot_holdoff_seconds=_int_field(
                body, "post_reboot_holdoff_seconds", default=180,
            ),
            description=body.get("description"),
            created_by_user_id=g.current_user.id,
        )
    except FormValidationError as e:
        return err("validation_failed", str(e), status=400)
    except ScheduleValidationError as e:
        return err("validation_failed", str(e), status=400)
    audit_service.record(
        "schedule.created",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="schedule",
        target_id=s["id"],
        details={"name": s["name"], "via": "api"},
    )
    return ok(s, status=201)


@admin_api_bp.delete("/schedules/<schedule_id>")
@role_required_api(*ADMIN_AND_UP)
def delete_schedule_api(schedule_id: str):
    if not svc_delete(schedule_id):
        return err("schedule_unknown", "Schedule not found.", status=404)
    audit_service.record(
        "schedule.deleted",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="schedule",
        target_id=schedule_id,
        details={"via": "api"},
    )
    return ok({"deleted": True})
