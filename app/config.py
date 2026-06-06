from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    firmware_dir: str
    uploads_dir: str
    public_base_url: str
    firmware_public_base: str
    bootstrap_admin_email: str | None
    bootstrap_admin_password: str | None
    bootstrap_admin_force_password_on_startup: bool
    log_level: str
    heartbeat_interval_seconds: int
    poll_interval_seconds: int
    announce_pending_retry_after_seconds: int
    enrollment_token_ttl_seconds: int
    invitation_ttl_seconds: int
    password_reset_ttl_seconds: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_helo: str
    session_idle_timeout_seconds: int
    cors_allowed_origins: tuple[str, ...]
    cookie_domain: str | None
    session_cookie_secure: bool
    # UI redesign feature flags (post-brutal-review). Each is a per-surface
    # opt-in so the operator can revert one without touching the others.
    # Defaults are True so test fixtures that construct Settings() without
    # passing these still build; production sets them via env (see
    # load_settings below) and the brutal-review redesign is always-on
    # in prod with revert paths documented in CHANGELOG.
    ui_hero_v2: bool = True   # PR-1: hero sentence + recents
    ui_row_v2: bool = True    # PR-2: collapse badge soup
    ui_a11y_v2: bool = True   # PR-6: skip-link, landmarks, focus, contrast
    ui_copy_v2: bool = True   # PR-8: kill jargon, sentence-case, plain verbs
    ui_table_v2: bool = True  # PR-3: single responsive table (no mobile-card duplicate)


@lru_cache
def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "REBOOTER_DATABASE_URL",
            "postgresql+psycopg://rebooter:REMOVED-CREDENTIAL-ROTATED-20260828@rebooter-droids-pg:5432/rebooter",
        ),
        secret_key=os.environ.get("REBOOTER_SECRET_KEY", "dev-insecure-change-me"),
        firmware_dir=os.environ.get("REBOOTER_FIRMWARE_DIR", "/data/firmware"),
        uploads_dir=os.environ.get("REBOOTER_UPLOADS_DIR", "/data/uploads"),
        public_base_url=os.environ.get(
            "REBOOTER_PUBLIC_BASE_URL", "https://www.voipguru.org/rebooter"
        ),
        firmware_public_base=os.environ.get(
            "REBOOTER_FIRMWARE_PUBLIC_BASE",
            "https://www.voipguru.org/rebooter/firmware",
        ),
        bootstrap_admin_email=os.environ.get("REBOOTER_BOOTSTRAP_ADMIN_EMAIL"),
        bootstrap_admin_password=os.environ.get("REBOOTER_BOOTSTRAP_ADMIN_PASSWORD"),
        # v0.4.16 (BUG-046): default behavior changes — startup
        # only sets the password on initial create. To keep the
        # legacy "force-reset to env var on every restart" path
        # (useful for "I forgot my password" recovery), set
        # REBOOTER_BOOTSTRAP_ADMIN_FORCE_PASSWORD_ON_STARTUP=1.
        bootstrap_admin_force_password_on_startup=os.environ.get(
            "REBOOTER_BOOTSTRAP_ADMIN_FORCE_PASSWORD_ON_STARTUP", "0"
        ).strip().lower() in ("1", "true", "yes", "on"),
        log_level=os.environ.get("REBOOTER_LOG_LEVEL", "INFO").upper(),
        heartbeat_interval_seconds=int(
            os.environ.get("REBOOTER_HEARTBEAT_INTERVAL_SECONDS", "60")
        ),
        poll_interval_seconds=int(
            os.environ.get("REBOOTER_POLL_INTERVAL_SECONDS", "30")
        ),
        # v0.5.10: how long an unadopted device should wait before re-
        # announcing while it sits on /pending-adoption. Pre-v0.5.10 this
        # was hardcoded 30s, which made adoption feel sluggish — operator
        # clicks "Adopt" and the device takes up to 30s to notice. 5s is
        # the new default; raise via env if a large fleet's pending
        # backlog floods the endpoint.
        announce_pending_retry_after_seconds=int(
            os.environ.get("REBOOTER_ANNOUNCE_PENDING_RETRY_AFTER_SECONDS", "5")
        ),
        enrollment_token_ttl_seconds=int(
            os.environ.get("REBOOTER_ENROLLMENT_TOKEN_TTL_SECONDS", "86400")
        ),
        invitation_ttl_seconds=int(
            os.environ.get("REBOOTER_INVITATION_TTL_SECONDS", str(60 * 60 * 24 * 30))
        ),
        password_reset_ttl_seconds=int(
            os.environ.get("REBOOTER_PASSWORD_RESET_TTL_SECONDS", str(60 * 60))
        ),
        smtp_host=os.environ.get("REBOOTER_SMTP_HOST", ""),
        smtp_port=int(os.environ.get("REBOOTER_SMTP_PORT", "587")),
        smtp_user=os.environ.get("REBOOTER_SMTP_USER", ""),
        smtp_password=os.environ.get("REBOOTER_SMTP_PASSWORD", ""),
        smtp_from=os.environ.get("REBOOTER_SMTP_FROM", ""),
        smtp_helo=os.environ.get("REBOOTER_SMTP_HELO", ""),
        session_idle_timeout_seconds=int(
            os.environ.get(
                "REBOOTER_SESSION_IDLE_TIMEOUT_SECONDS", str(60 * 60 * 24 * 2)
            )
        ),
        cors_allowed_origins=tuple(
            o.strip()
            for o in os.environ.get("REBOOTER_CORS_ALLOWED_ORIGINS", "").split(",")
            if o.strip()
        ),
        # v0.3.3 (P3.1): set to e.g. ".voipguru.org" so the session
        # cookie carries across www → www2 subdomains. Default None =
        # host-scoped (the v0.3.2 behaviour). The cookie name is
        # always rebooter_session in v0.3.3+ — a unique name avoids
        # collisions with peer apps on shared subdomains using the
        # Flask default `session`.
        cookie_domain=(os.environ.get("REBOOTER_COOKIE_DOMAIN", "").strip() or None),
        # Secure-only session cookie. Defaults ON (production is HTTPS).
        # The CI gate boots the app on plain http://localhost, where a
        # Secure cookie is never sent back — it sets this to 0 so the
        # cookie-authenticated HTTP tests can run in the `-m ci` gate.
        session_cookie_secure=(
            os.environ.get("REBOOTER_SESSION_COOKIE_SECURE", "1").strip().lower()
            not in ("0", "false", "no", "off")
        ),
        # UI redesign flags — default ON in 0.6.24+. Each can be turned off
        # independently via REBOOTER_UI_<NAME>=0 if a regression hits at 02:00.
        ui_hero_v2=os.environ.get("REBOOTER_UI_HERO_V2", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        ui_row_v2=os.environ.get("REBOOTER_UI_ROW_V2", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        ui_a11y_v2=os.environ.get("REBOOTER_UI_A11Y_V2", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        ui_copy_v2=os.environ.get("REBOOTER_UI_COPY_V2", "1").strip().lower()
        not in ("0", "false", "no", "off"),
        ui_table_v2=os.environ.get("REBOOTER_UI_TABLE_V2", "1").strip().lower()
        not in ("0", "false", "no", "off"),
    )
