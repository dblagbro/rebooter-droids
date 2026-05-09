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
    log_level: str
    heartbeat_interval_seconds: int
    poll_interval_seconds: int
    enrollment_token_ttl_seconds: int
    invitation_ttl_seconds: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_from: str
    smtp_helo: str


@lru_cache
def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "REBOOTER_DATABASE_URL",
            "postgresql+psycopg://rebooter:rebooter@rebooter-droids-pg:5432/rebooter",
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
        log_level=os.environ.get("REBOOTER_LOG_LEVEL", "INFO").upper(),
        heartbeat_interval_seconds=int(
            os.environ.get("REBOOTER_HEARTBEAT_INTERVAL_SECONDS", "60")
        ),
        poll_interval_seconds=int(
            os.environ.get("REBOOTER_POLL_INTERVAL_SECONDS", "30")
        ),
        enrollment_token_ttl_seconds=int(
            os.environ.get("REBOOTER_ENROLLMENT_TOKEN_TTL_SECONDS", "86400")
        ),
        invitation_ttl_seconds=int(
            os.environ.get("REBOOTER_INVITATION_TTL_SECONDS", str(60 * 60 * 24 * 7))
        ),
        smtp_host=os.environ.get("REBOOTER_SMTP_HOST", ""),
        smtp_port=int(os.environ.get("REBOOTER_SMTP_PORT", "587")),
        smtp_user=os.environ.get("REBOOTER_SMTP_USER", ""),
        smtp_password=os.environ.get("REBOOTER_SMTP_PASSWORD", ""),
        smtp_from=os.environ.get("REBOOTER_SMTP_FROM", ""),
        smtp_helo=os.environ.get("REBOOTER_SMTP_HELO", ""),
    )
