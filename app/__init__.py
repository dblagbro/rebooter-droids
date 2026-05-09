from __future__ import annotations

import logging

from flask import Flask

from app.config import load_settings
from app.db import init_engine
from app.middleware.response import register_envelope_handlers


def create_app() -> Flask:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)

    app = Flask(
        __name__,
        static_folder="../static",
        template_folder="../templates",
    )
    app.config["SETTINGS"] = settings
    app.config["SECRET_KEY"] = settings.secret_key

    init_engine(settings)

    from app.blueprints.version import bp as version_bp
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.device_api import bp as device_api_bp
    from app.blueprints.admin_api import bp as admin_api_bp
    from app.blueprints.admin_ui import bp as admin_ui_bp

    app.register_blueprint(version_bp, url_prefix="/api/v1")
    app.register_blueprint(auth_bp, url_prefix="/api/v1/auth")
    app.register_blueprint(device_api_bp, url_prefix="/api/v1/device")
    app.register_blueprint(admin_api_bp, url_prefix="/api/v1/admin")
    app.register_blueprint(admin_ui_bp, url_prefix="/app")

    register_envelope_handlers(app)

    @app.get("/")
    def root_redirect():
        from flask import redirect
        return redirect("/app/", code=302)

    return app
