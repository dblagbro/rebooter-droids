from __future__ import annotations

from flask import Blueprint

from app.middleware.response import err

bp = Blueprint("device_api", __name__)


@bp.post("/register")
def register():
    return err("not_implemented", "Device registration lands in task #6", status=501)


@bp.post("/heartbeat")
def heartbeat():
    return err("not_implemented", "Heartbeat lands in task #7", status=501)


@bp.get("/commands")
def poll_commands():
    return err("not_implemented", "Command poll lands in task #8", status=501)


@bp.post("/command-result")
def command_result():
    return err("not_implemented", "Command result lands in task #8", status=501)


@bp.post("/events")
def upload_events():
    return err("not_implemented", "Events upload lands in task #12", status=501)


@bp.get("/firmware")
def firmware_assignment():
    return err("not_implemented", "Firmware assignment lands in task #11", status=501)
