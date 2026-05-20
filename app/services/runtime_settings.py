"""Runtime settings service — v0.4.25.

Tiny key/value abstraction with env-var fallback. Used for any
config the operator should be able to change without recreating
the container — currently SMTP creds (v0.4.25); v0.4.26 extends to
network + system settings.

Pattern:
    runtime_settings.get('smtp.host', env_var='REBOOTER_SMTP_HOST')

Reads DB row first; falls back to the env-var if not set; falls
back to a hard default if neither. So a fresh deployment keeps
picking up env-var defaults until the operator edits via the UI.

Writes always audit-log via the caller; this service just stores.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db import session_scope
from app.models import RuntimeSetting


_SENTINEL = object()


def get(name: str, *, env_var: str | None = None, default: Any = None) -> Any:
    """Look up a setting. DB → env-var → default."""
    with session_scope() as session:
        row = session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == name)
        )
        if row is not None:
            v = (row.value or {}).get("v", _SENTINEL)
            if v is not _SENTINEL:
                return v
    if env_var:
        env = os.environ.get(env_var)
        if env is not None:
            return env
    return default


def has_db_value(name: str) -> bool:
    """Whether this setting has a DB-side override (regardless of
    env-var fallback)."""
    with session_scope() as session:
        row = session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == name)
        )
        return row is not None


def set_(name: str, value: Any, *, user_id: str | None = None) -> None:
    """Upsert. Use `delete()` to remove the override and revert
    to env-var fallback. NOTE: empty string is *not* a delete —
    operators can explicitly want a setting cleared (e.g.
    blank `helo`). Use `delete()` to fall back to env."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        row = session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == name)
        )
        if row is None:
            row = RuntimeSetting(name=name, value={"v": value}, updated_at=now)
        else:
            row.value = {"v": value}
            row.updated_at = now
        row.updated_by_user_id = user_id
        session.add(row)
        session.flush()


def delete(name: str) -> bool:
    """Drop the DB row → reverts to env-var fallback."""
    with session_scope() as session:
        row = session.scalar(
            select(RuntimeSetting).where(RuntimeSetting.name == name)
        )
        if row is None:
            return False
        session.delete(row)
        session.flush()
        return True


def list_keys() -> list[dict]:
    """Operator-side audit view: enumerate all DB rows + their
    metadata. Never returns secret values verbatim — see the
    blueprint for masked rendering."""
    with session_scope() as session:
        rows = list(session.scalars(select(RuntimeSetting)))
        return [
            {
                "name": r.name,
                "updated_at": r.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "updated_by_user_id": r.updated_by_user_id,
            }
            for r in rows
        ]


# ── known SMTP keys + a typed helper for the email service ────────


SMTP_KEYS = (
    ("smtp.host", "REBOOTER_SMTP_HOST"),
    ("smtp.port", "REBOOTER_SMTP_PORT"),
    ("smtp.user", "REBOOTER_SMTP_USER"),
    ("smtp.password", "REBOOTER_SMTP_PASSWORD"),
    ("smtp.from", "REBOOTER_SMTP_FROM"),
    ("smtp.helo", "REBOOTER_SMTP_HELO"),
)


# v0.4.26: Network keys
NETWORK_KEYS = (
    ("network.public_base_url",       "REBOOTER_PUBLIC_BASE_URL"),
    ("network.firmware_public_base",  "REBOOTER_FIRMWARE_PUBLIC_BASE"),
    ("network.cors_allowed_origins",  "REBOOTER_CORS_ALLOWED_ORIGINS"),
    ("network.rate_limit_exempt_ips", "REBOOTER_RATE_LIMIT_EXEMPT_IPS"),
    ("network.cookie_domain",         "REBOOTER_COOKIE_DOMAIN"),
)

# v0.4.26: System keys
SYSTEM_KEYS = (
    ("system.portal_name",                "REBOOTER_PORTAL_NAME"),
    ("system.invitation_ttl_seconds",     "REBOOTER_INVITATION_TTL_SECONDS"),
    ("system.password_reset_ttl_seconds", "REBOOTER_PASSWORD_RESET_TTL_SECONDS"),
    ("system.session_idle_timeout_seconds", "REBOOTER_SESSION_IDLE_TIMEOUT_SECONDS"),
    ("system.enrollment_token_ttl_seconds", "REBOOTER_ENROLLMENT_TOKEN_TTL_SECONDS"),
    # v0.5.36 (B1 RBAC P2): audit retention in days; events older than this
    # are soft-pruned into audit_events_archive by the nightly job. Default 90.
    ("system.audit_retention_days",       "REBOOTER_AUDIT_RETENTION_DAYS"),
)

# v0.5.35 (B1 RBAC Phase 1): RBAC keys. `rbac.enforce_mode` is one of
# {"shadow", "enforce"} — default "shadow" (absence of a DB row).
# Toggled live from the System tab during the A8 cut-over; consumed by
# app/services/role_bindings.py::enforce_mode(). Listed here only for
# discoverability — the RBAC service reads it directly via get().
RBAC_KEYS = (
    ("rbac.enforce_mode", "REBOOTER_RBAC_ENFORCE_MODE"),
)


def smtp_config() -> dict:
    """Returns the live SMTP config the email service should use.
    Each value comes from `runtime_settings` if a DB override
    exists, else from the env var, else from a sensible default
    (port → 587, others → empty string)."""
    out = {}
    for name, env in SMTP_KEYS:
        out[name] = get(name, env_var=env, default="")
    # Coerce port to int
    try:
        out["smtp.port"] = int(out["smtp.port"]) if out["smtp.port"] else 587
    except (TypeError, ValueError):
        out["smtp.port"] = 587
    return out


def network_config() -> dict:
    """v0.4.26: live network config. CORS + cookie_domain are
    consumed at app-start; changes show up here after edit but
    *don't take effect* until next container restart (the
    Flask-CORS init reads once). Public URLs + rate-limit
    exempt IPs are consumed per-request and ARE live."""
    out = {}
    for name, env in NETWORK_KEYS:
        out[name] = get(name, env_var=env, default="")
    return out


def system_config() -> dict:
    """v0.4.26: live system config. TTLs are consumed at point-
    of-use and ARE live. portal_name is just a display label."""
    out = {}
    for name, env in SYSTEM_KEYS:
        v = get(name, env_var=env, default=None)
        # Coerce numeric TTLs
        if name.endswith(("_seconds", "_ttl_seconds")) and v is not None:
            try:
                v = int(v)
            except (TypeError, ValueError):
                pass
        out[name] = v
    return out


# ── resolved network URLs (DB override → env → config default) ────


def resolve_public_base_url() -> str:
    """S1-4/S1-5: the *live* public base URL.

    Resolution order: `network.public_base_url` DB override (set via
    the Settings → Network UI) → `REBOOTER_PUBLIC_BASE_URL` env var →
    the `Settings.public_base_url` config default.

    Pre-fix, consumers (e.g. `announcements.upsert_announcement`
    building `central_register_url`) read `load_settings().public_base_url`
    directly — env-only — so the Network-settings UI override was a dead
    write that never reached devices. Route URL-building through this
    helper so the override actually takes effect.
    """
    from app.config import load_settings

    v = get("network.public_base_url", env_var="REBOOTER_PUBLIC_BASE_URL", default=None)
    if v:
        return str(v)
    return load_settings().public_base_url


def resolve_firmware_public_base() -> str:
    """S1-4/S1-5: the *live* firmware public base URL.

    Same DB override → env → config-default resolution as
    `resolve_public_base_url`.
    """
    from app.config import load_settings

    v = get(
        "network.firmware_public_base",
        env_var="REBOOTER_FIRMWARE_PUBLIC_BASE",
        default=None,
    )
    if v:
        return str(v)
    return load_settings().firmware_public_base


def _host_of(url: str | None) -> str:
    from urllib.parse import urlparse

    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def voipguru_www_warnings() -> list[str]:
    """S1-4/S1-5: warn-only check for the voipguru.org-without-www case.

    Returns a list of human-readable warnings — one per resolved URL
    (`public_base_url`, `firmware_public_base`) whose host is exactly
    `voipguru.org` with no `www.` prefix. The live site is served at
    `www.voipguru.org`; a bare-apex URL handed to a device fails to
    resolve / redirect-loops.

    WARN ONLY — this never rewrites the value. Used by the startup
    validator and the Network-settings on-save check.
    """
    warnings: list[str] = []
    checks = (
        ("public_base_url", resolve_public_base_url()),
        ("firmware_public_base", resolve_firmware_public_base()),
    )
    for label, url in checks:
        if _host_of(url) == "voipguru.org":
            warnings.append(
                f"{label} host is 'voipguru.org' without a 'www.' prefix "
                f"({url!r}). The live site is www.voipguru.org — devices "
                f"may fail to resolve this URL. Update Settings -> Network "
                f"(or the REBOOTER_*_BASE env var) to use 'www.voipguru.org'."
            )
    return warnings


def is_live_editable(name: str) -> bool:
    """Whether changing this setting takes effect without a
    container restart. Used by the UI to label fields with
    "live" vs "restart-required" badges."""
    # CORS and cookie_domain are wired at Flask app-init time.
    if name in ("network.cors_allowed_origins", "network.cookie_domain"):
        return False
    # Public URLs + rate-limit-exempt-IPs + TTLs are read per use.
    return True
