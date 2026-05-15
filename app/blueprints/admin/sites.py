"""Admin UI + API for sites — list, create, delete."""

from __future__ import annotations

from flask import abort, redirect, render_template, request, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_api, admin_required_ui
from app.middleware.response import err, ok
from app.services.sites import (
    DuplicateNameError as DuplicateSiteName,
    create_site as svc_create_site,
    delete_site as svc_delete_site,
    list_sites as svc_list_sites,
)


# ── UI ─────────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/sites")
@admin_required_ui
def list_sites_page():
    sites = svc_list_sites()
    return render_template("sites_list.html", **_ctx({"sites": sites}))


@admin_ui_bp.get("/sites/<site_id>")
@admin_required_ui
def site_detail_page(site_id: str):
    """v0.5.43 (P4b): Site detail page with members tab showing users with bindings."""
    from app.services import role_bindings as rb
    from app.services.users import list_users
    from app.services.devices import list_devices

    sites = svc_list_sites()
    site = next((s for s in sites if s["id"] == site_id), None)
    if not site:
        abort(404)

    # Get all bindings for this site
    bindings = rb.list_for_scope(scope_type="site", scope_id=site_id)

    # Get user details for each binding
    all_users = {u["id"]: u for u in list_users()}
    members = []
    for b in bindings:
        user = all_users.get(b.user_id)
        if user:
            members.append({
                "user": user,
                "binding": b,
            })

    # Get devices at this site
    all_devices = list_devices()
    site_devices = [d for d in all_devices if d.get("site_id") == site_id]

    return render_template(
        "site_detail.html",
        **_ctx({
            "site": site,
            "members": members,
            "devices": site_devices,
        }),
    )


@admin_ui_bp.post("/sites")
@admin_required_ui
def create_site_submit():
    name = (request.form.get("name") or "").strip()
    if not name:
        abort(400)
    try:
        svc_create_site(
            name=name,
            description=(request.form.get("description") or "").strip() or None,
        )
    except DuplicateSiteName:
        sites = svc_list_sites()
        return (
            render_template(
                "sites_list.html",
                **_ctx({"sites": sites, "error": f"A site named '{name}' already exists."}),
            ),
            409,
        )
    return redirect(url_for("admin_ui.list_sites_page"))


@admin_ui_bp.post("/sites/<site_id>/delete")
@admin_required_ui
def delete_site_submit(site_id: str):
    svc_delete_site(site_id)
    return redirect(url_for("admin_ui.list_sites_page"))


# ── API ────────────────────────────────────────────────────────────────────

@admin_api_bp.get("/sites")
@admin_required_api
def list_sites_api():
    return ok({"sites": svc_list_sites()})


@admin_api_bp.post("/sites")
@admin_required_api
def create_site_api():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return err("validation_failed", "name is required", status=400)
    try:
        return ok(svc_create_site(name=name, description=body.get("description")), status=201)
    except DuplicateSiteName as e:
        return err("name_conflict", str(e), status=409)


@admin_api_bp.delete("/sites/<site_id>")
@admin_required_api
def delete_site_api(site_id: str):
    if not svc_delete_site(site_id):
        return err("site_unknown", "Site not found.", status=404)
    return ok({"deleted": True})
