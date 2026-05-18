"""Admin UI + API for device scenes — v0.5.92 (Stage C).

A scene is a named bundle of per-device target states; a watchdog
`apply_scene` action references one by id. Scene items are authored as
a JSON array (one object per device) — the same escape-hatch shape as
the rules JSON editor.
"""

from __future__ import annotations

import json

from flask import redirect, render_template, request, url_for

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import admin_required_api, admin_required_ui
from app.middleware.response import err, ok
from app.services.scenes import (
    SceneError,
    create_scene as svc_create_scene,
    delete_scene as svc_delete_scene,
    get_scene as svc_get_scene,
    list_scenes as svc_list_scenes,
    update_scene as svc_update_scene,
)


def _scenes_ctx(extra: dict | None = None) -> dict:
    """Template context with form-repopulation defaults always present
    (StrictUndefined — every key the template reads must be defined)."""
    base = {
        "scenes": svc_list_scenes(),
        "error": None,
        "form_name": "",
        "form_description": "",
        "form_items": "",
    }
    base.update(extra or {})
    return _ctx(base)


# ── UI ─────────────────────────────────────────────────────────────────

@admin_ui_bp.get("/scenes")
@admin_required_ui
def list_scenes_page():
    return render_template("scenes_list.html", **_scenes_ctx())


@admin_ui_bp.post("/scenes")
@admin_required_ui
def create_scene_submit():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip()
    raw_items = (request.form.get("items") or "").strip()

    def _re(msg: str):
        return (
            render_template(
                "scenes_list.html",
                **_scenes_ctx({
                    "error": msg,
                    "form_name": name,
                    "form_description": description,
                    "form_items": raw_items,
                }),
            ),
            400,
        )

    try:
        items = json.loads(raw_items) if raw_items else None
    except json.JSONDecodeError as e:
        return _re(f"Scene items must be valid JSON: {e}")
    try:
        svc_create_scene(name=name, description=description or None, items=items)
    except SceneError as e:
        return _re(e.message)
    return redirect(url_for("admin_ui.list_scenes_page"))


@admin_ui_bp.post("/scenes/<scene_id>/delete")
@admin_required_ui
def delete_scene_submit(scene_id: str):
    svc_delete_scene(scene_id)
    return redirect(url_for("admin_ui.list_scenes_page"))


# ── API ────────────────────────────────────────────────────────────────

@admin_api_bp.get("/scenes")
@admin_required_api
def list_scenes_api():
    return ok({"scenes": svc_list_scenes()})


@admin_api_bp.get("/scenes/<scene_id>")
@admin_required_api
def get_scene_api(scene_id: str):
    scene = svc_get_scene(scene_id)
    if scene is None:
        return err("scene_unknown", "Scene not found.", status=404)
    return ok(scene)


@admin_api_bp.post("/scenes")
@admin_required_api
def create_scene_api():
    body = request.get_json(silent=True) or {}
    try:
        scene = svc_create_scene(
            name=body.get("name", ""),
            description=body.get("description"),
            items=body.get("items"),
        )
    except SceneError as e:
        return err(e.code, e.message,
                   status=409 if e.code == "name_conflict" else 400)
    return ok(scene, status=201)


@admin_api_bp.patch("/scenes/<scene_id>")
@admin_required_api
def update_scene_api(scene_id: str):
    body = request.get_json(silent=True) or {}
    try:
        scene = svc_update_scene(
            scene_id,
            name=body.get("name", ""),
            description=body.get("description"),
            items=body.get("items"),
        )
    except SceneError as e:
        return err(e.code, e.message,
                   status=409 if e.code == "name_conflict" else 400)
    if scene is None:
        return err("scene_unknown", "Scene not found.", status=404)
    return ok(scene)


@admin_api_bp.delete("/scenes/<scene_id>")
@admin_required_api
def delete_scene_api(scene_id: str):
    if not svc_delete_scene(scene_id):
        return err("scene_unknown", "Scene not found.", status=404)
    return ok({"deleted": True})
