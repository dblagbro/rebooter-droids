"""Admin UI + API for config backup / restore — Hub Tier-2 Feature 3.

The Backup Settings sub-page: an Export card (download a versioned JSON
config snapshot, optionally encrypted-with-secrets) and an Import card
(upload → dry-run preview table → typed-confirmation apply).

Export-with-secrets and every import are gated to `super_admin`; a
plain redacted export is `admin`-and-up. The import apply runs through
the `mass_action` typed-confirmation gate like a bulk-delete. All
actions audit; the passphrase is never logged or audited.
"""

from __future__ import annotations

from flask import (
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.blueprints.admin import admin_api_bp, admin_ui_bp
from app.blueprints.admin._common import _ctx
from app.middleware.admin_auth import (
    admin_required_api,
    role_required_api,
    role_required_ui,
)
from app.middleware.response import err, ok
from app.models.users import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services import audit as audit_service
from app.services import config_backup as svc
from app.services import mass_action

# Session key holding the most-recent previewed import plan, so the
# apply step can act on exactly what the operator saw.
_PLAN_SESSION_KEY = "config_import_plan"


def _backup_ctx(extra: dict | None = None) -> dict:
    base = {
        "active": "settings",
        "settings_tab": "backup",
        "preview": None,
        "result": None,
        "error": None,
        "format_version": svc.FORMAT_VERSION,
    }
    base.update(extra or {})
    return _ctx(base)


def _download_name() -> str:
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"rebooter-hub-config-{stamp}.json"


# ── UI ─────────────────────────────────────────────────────────────────


@admin_ui_bp.get("/settings/backup")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def settings_backup_page():
    return render_template("settings/backup.html", **_backup_ctx())


@admin_ui_bp.post("/settings/backup/export")
@role_required_ui(ROLE_SUPER_ADMIN, ROLE_ADMIN)
def settings_backup_export_submit():
    include_secrets = request.form.get("include_secrets") == "on"
    passphrase = request.form.get("passphrase") or ""

    # Including secrets is super-admin-only and requires a passphrase.
    if include_secrets and g.current_user.role != ROLE_SUPER_ADMIN:
        flash("Only a super-admin can export with secrets.", "error")
        return redirect(url_for("admin_ui.settings_backup_page"))

    try:
        payload = svc.export_config(
            include_secrets=include_secrets,
            passphrase=passphrase or None,
        )
    except svc.ConfigBackupError as e:
        flash(str(e), "error")
        return redirect(url_for("admin_ui.settings_backup_page"))

    audit_service.record(
        "config.exported",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="config_backup",
        # Never log the passphrase — only whether secrets were included.
        details={"include_secrets": include_secrets, "encrypted": include_secrets},
    )

    from flask import Response

    return Response(
        payload,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{_download_name()}"',
        },
    )


@admin_ui_bp.post("/settings/backup/import/preview")
@role_required_ui(ROLE_SUPER_ADMIN)
def settings_backup_import_preview_submit():
    upload = request.files.get("backup_file")
    passphrase = request.form.get("passphrase") or ""
    if upload is None or not upload.filename:
        flash("Choose a backup file to upload.", "error")
        return redirect(url_for("admin_ui.settings_backup_page"))

    file_bytes = upload.read()
    try:
        plan = svc.parse_and_plan(file_bytes, passphrase=passphrase or None)
    except svc.ConfigBackupError as e:
        return (
            render_template("settings/backup.html", **_backup_ctx({"error": str(e)})),
            400,
        )

    plan_dict = plan.to_dict()
    # Stash the plan so the apply step acts on exactly what was shown.
    session[_PLAN_SESSION_KEY] = plan_dict
    return render_template(
        "settings/backup.html",
        **_backup_ctx({"preview": plan_dict}),
    )


@admin_ui_bp.post("/settings/backup/import/apply")
@role_required_ui(ROLE_SUPER_ADMIN)
def settings_backup_import_apply_submit():
    plan_dict = session.get(_PLAN_SESSION_KEY)
    if not plan_dict:
        flash("No previewed import to apply — upload and preview first.", "error")
        return redirect(url_for("admin_ui.settings_backup_page"))

    total_writes = int(plan_dict.get("total_writes", 0))
    # Mass-import is high blast radius — typed confirmation, like a
    # bulk-delete. The operator types `import_config` to confirm.
    try:
        mass_action.validate(
            target_count=total_writes,
            expected_typed_value="import_config",
            confirmation_level=request.form.get("confirmation_level"),
            confirmation_typed_value=request.form.get("confirmation_typed_value"),
        )
    except mass_action.ConfirmationRequired as e:
        return (
            render_template(
                "settings/backup.html",
                **_backup_ctx({
                    "preview": plan_dict,
                    "error": str(e),
                }),
            ),
            400,
        )

    plan = _plan_from_dict(plan_dict)
    try:
        result = svc.apply_plan(plan)
    except svc.ConfigBackupError as e:
        return (
            render_template("settings/backup.html", **_backup_ctx({"error": str(e)})),
            400,
        )

    session.pop(_PLAN_SESSION_KEY, None)
    result_dict = result.to_dict()
    audit_service.record(
        "config.imported",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="config_backup",
        details=result_dict,
    )
    return render_template(
        "settings/backup.html",
        **_backup_ctx({"result": result_dict}),
    )


def _plan_from_dict(plan_dict: dict) -> svc.ImportPlan:
    """Rebuild an `ImportPlan` from the session-stashed dict.

    The dict carries the full decrypted `document`; re-running
    `parse_and_plan` on it regenerates a fresh plan against the *current*
    DB state, so the apply reflects any changes since the preview rather
    than a stale snapshot.
    """
    import json

    document = plan_dict.get("document") or {}
    # The stored document is already decrypted plaintext — re-plan it.
    return svc.parse_and_plan(json.dumps(document).encode("utf-8"))


# ── API ────────────────────────────────────────────────────────────────


@admin_api_bp.post("/backup/export")
@admin_required_api
def backup_export_api():
    body = request.get_json(silent=True) or {}
    include_secrets = bool(body.get("include_secrets"))
    passphrase = body.get("passphrase")
    if include_secrets and g.current_user.role != ROLE_SUPER_ADMIN:
        return err("forbidden", "Only a super-admin can export with secrets.",
                   status=403)
    try:
        payload = svc.export_config(
            include_secrets=include_secrets, passphrase=passphrase,
        )
    except svc.ConfigBackupError as e:
        return err("export_failed", str(e), status=400)
    audit_service.record(
        "config.exported",
        actor_user_id=g.current_user.id,
        actor_email_snapshot=g.current_user.email,
        target_type="config_backup",
        details={"include_secrets": include_secrets, "encrypted": include_secrets},
    )
    from flask import Response

    return Response(
        payload,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{_download_name()}"',
        },
    )


@admin_api_bp.post("/backup/import/preview")
@role_required_api(ROLE_SUPER_ADMIN)
def backup_import_preview_api():
    upload = request.files.get("backup_file")
    passphrase = request.form.get("passphrase")
    if upload is None or not upload.filename:
        return err("no_file", "A backup_file upload is required.", status=400)
    try:
        plan = svc.parse_and_plan(upload.read(), passphrase=passphrase)
    except svc.ConfigBackupError as e:
        return err("parse_failed", str(e), status=400)
    return ok({"plan": plan.to_dict()})
