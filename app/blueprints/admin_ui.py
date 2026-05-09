from __future__ import annotations

from flask import Blueprint, render_template

from app.version import __version__

bp = Blueprint("admin_ui", __name__)


@bp.get("/")
def index():
    return render_template("dashboard.html", version=__version__)


@bp.get("/login")
def login_page():
    return render_template("login.html", version=__version__)
