from __future__ import annotations

import logging

from flask import Flask

from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import load_settings
from app.db import init_engine
from app.middleware.rate_limit import init_rate_limit
from app.middleware.response import register_envelope_handlers


_SCHEDULER_LOCK_KEY = 4242117310


def _claim_scheduler_lock() -> bool:
    """Try to claim a Postgres advisory lock so only one worker runs the scheduler."""
    from sqlalchemy import text

    from app.db import get_engine

    try:
        engine = get_engine()
        conn = engine.raw_connection()
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_SCHEDULER_LOCK_KEY,))
        got = cur.fetchone()[0]
        cur.close()
        if not got:
            conn.close()
            return False
        # Hold the connection (and thus the lock) for the lifetime of this worker.
        # Don't close it.
        return True
    except Exception:
        return False


class PrefixMiddleware:
    """Honour X-Forwarded-Prefix from upstream proxy by setting SCRIPT_NAME."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX") or environ.get(
            "HTTP_X_SCRIPT_NAME"
        )
        if prefix:
            prefix = prefix.rstrip("/")
            environ["SCRIPT_NAME"] = prefix
            path = environ.get("PATH_INFO", "")
            if path.startswith(prefix):
                environ["PATH_INFO"] = path[len(prefix) :] or "/"
        return self.app(environ, start_response)


def create_app() -> Flask:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)

    app = Flask(
        __name__,
        static_folder="../static",
        template_folder="../templates",
    )
    from datetime import timedelta

    app.config["SETTINGS"] = settings
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    # 2-day idle timeout: cookie expiry rolls forward on every request
    # (SESSION_REFRESH_EACH_REQUEST is Flask's default = True). Active
    # users stay signed in indefinitely; idle users get kicked after the
    # configured window.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        seconds=settings.session_idle_timeout_seconds
    )
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    # Be lenient about trailing slashes — clients (and humans) often add or
    # omit them; Flask 3's default returns 404 instead of redirecting.
    app.url_map.strict_slashes = False
    app.wsgi_app = ProxyFix(
        PrefixMiddleware(app.wsgi_app), x_for=1, x_proto=1, x_host=1, x_prefix=1
    )

    init_engine(settings)
    init_rate_limit(app)

    from app.services.bootstrap import run_startup_bootstrap
    try:
        run_startup_bootstrap(settings)
    except Exception:
        app.logger.exception("Startup bootstrap failed; the app will continue running")

    # Run the scheduler in only ONE worker (the first to claim a Postgres advisory lock).
    if _claim_scheduler_lock():
        from app.jobs.scheduler import start as start_scheduler
        start_scheduler()

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

    @app.get("/favicon.ico")
    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    def _icon_alias():
        # Browsers request these at the conventional root location regardless
        # of <link rel="icon"> hints. Serve our single ICO for all of them.
        from flask import send_from_directory

        return send_from_directory(app.static_folder, "favicon.ico")

    @app.get("/robots.txt")
    def _robots():
        from flask import Response

        return Response(
            "User-agent: *\nDisallow: /\n", mimetype="text/plain"
        )

    @app.errorhandler(404)
    def _not_found(_e):
        # JSON envelope for /api/v1/* and any other JSON path; HTML for app/*.
        from flask import render_template, request as _req, jsonify

        if _req.path.startswith("/api/") or _req.headers.get(
            "Accept", ""
        ).startswith("application/json"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": {"code": "not_found", "message": "Not found."},
                    }
                ),
                404,
            )
        try:
            return render_template("error.html", code=404, message="Page not found"), 404
        except Exception:
            return ("<h1>404 — Not found</h1>", 404, {"Content-Type": "text/html"})

    @app.errorhandler(403)
    def _forbidden(_e):
        from flask import render_template, request as _req, jsonify

        if _req.path.startswith("/api/"):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": {"code": "forbidden", "message": "Forbidden."},
                    }
                ),
                403,
            )
        try:
            return render_template("error.html", code=403, message="Not allowed"), 403
        except Exception:
            return ("<h1>403 — Forbidden</h1>", 403, {"Content-Type": "text/html"})

    return app
