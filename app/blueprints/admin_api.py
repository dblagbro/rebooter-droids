from __future__ import annotations

from flask import Blueprint

from app.middleware.response import err

bp = Blueprint("admin_api", __name__)


@bp.get("/devices")
def list_devices():
    return err("not_implemented", "Admin devices lands in task #7", status=501)


@bp.get("/devices/<device_id>")
def get_device(device_id: str):
    return err("not_implemented", "Admin device detail lands in task #7", status=501)


@bp.patch("/devices/<device_id>")
def patch_device(device_id: str):
    return err("not_implemented", "Admin device update lands in task #7", status=501)


@bp.post("/devices/<device_id>/commands")
def send_device_command(device_id: str):
    return err("not_implemented", "Send device command lands in task #8", status=501)


@bp.post("/groups")
def create_group():
    return err("not_implemented", "Groups land in task #9", status=501)


@bp.post("/groups/<group_id>/members")
def add_group_members(group_id: str):
    return err("not_implemented", "Groups land in task #9", status=501)


@bp.delete("/groups/<group_id>/members/<device_id>")
def remove_group_member(group_id: str, device_id: str):
    return err("not_implemented", "Groups land in task #9", status=501)


@bp.post("/groups/<group_id>/commands")
def send_group_command(group_id: str):
    return err("not_implemented", "Group commands land in task #9", status=501)


@bp.post("/firmware/releases")
def create_firmware_release():
    return err("not_implemented", "Firmware releases land in task #10", status=501)


@bp.post("/firmware/deployments")
def create_firmware_deployment():
    return err("not_implemented", "Firmware deployments land in task #11", status=501)


@bp.get("/events")
def query_events():
    return err("not_implemented", "Events query lands in task #12", status=501)
