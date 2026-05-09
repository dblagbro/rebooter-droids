from __future__ import annotations

from flask import Blueprint

from app.middleware.response import err

bp = Blueprint("auth", __name__)


@bp.post("/login")
def login():
    return err("not_implemented", "Admin auth lands in v0.1.1", status=501)


@bp.post("/logout")
def logout():
    return err("not_implemented", "Admin auth lands in v0.1.1", status=501)


@bp.post("/refresh")
def refresh():
    return err("not_implemented", "Admin auth lands in v0.1.1", status=501)


@bp.get("/me")
def me():
    return err("not_implemented", "Admin auth lands in v0.1.1", status=501)
