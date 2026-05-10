"""SMTP sender for transactional mail (invites, password reset).

Pattern lifted from the DevinGPT project. If SMTP is not configured we
log the email body and return — useful in dev/CI and harmless in prod
(an admin who tries to invite gets the link in the response).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import Settings, load_settings

log = logging.getLogger(__name__)


def _live_smtp_config() -> dict:
    """v0.4.25: SMTP config now reads from runtime_settings (DB) →
    env-var → empty. Lets operators rotate creds without recreating
    the container."""
    from app.services import runtime_settings
    return runtime_settings.smtp_config()


def is_configured(settings: Settings | None = None) -> bool:
    """Compat wrapper — kept for callers that still pass Settings.
    The live source of truth is now runtime_settings."""
    cfg = _live_smtp_config()
    return bool(cfg.get("smtp.host") and cfg.get("smtp.from"))


def send_email(to: str, subject: str, html_body: str) -> bool:
    """Return True if sent (or attempted), False if SMTP not
    configured. v0.4.25: pulls live config from runtime_settings
    so a UI-side rotation takes effect immediately."""
    cfg = _live_smtp_config()
    host = cfg.get("smtp.host")
    port = cfg.get("smtp.port") or 587
    user = cfg.get("smtp.user")
    password = cfg.get("smtp.password")
    from_addr = cfg.get("smtp.from")
    helo = cfg.get("smtp.helo")

    if not (host and from_addr):
        log.warning(
            "SMTP not configured — would-have-sent to=%s subject=%s", to, subject
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=15) as server:
        if helo:
            server.ehlo(helo)
        server.starttls(context=ctx)
        if helo:
            server.ehlo(helo)
        if user:
            server.login(user, password)
        server.sendmail(from_addr, [to], msg.as_string())
    log.info("smtp ok to=%s subject=%s", to, subject)
    return True


def send_invite_email(to: str, role: str, redeem_url: str, note: str | None = None) -> bool:
    note_html = (
        f'<p style="margin:0 0 16px;color:#c8c8d4">{note}</p>'
        if note
        else ""
    )
    html = f"""<div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:28px;background:#161a21;color:#e6e8eb;border-radius:12px">
<h2 style="color:#6db3ff;margin:0 0 12px">🤖 Rebooter-Droids — You're invited</h2>
<p style="margin:0 0 16px;color:#c8c8d4">You've been invited to join <strong>Rebooter-Droids</strong> as a <strong style="color:#6db3ff">{role}</strong>.</p>
{note_html}
<p style="margin:0 0 20px;color:#c8c8d4">Click below to set up your account. This link is single-use and expires in 30 days.</p>
<a href="{redeem_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:11px 24px;border-radius:7px;text-decoration:none;font-weight:600">Accept Invitation</a>
<p style="margin:20px 0 0;font-size:12px;color:#8a8f99">Or copy this URL: {redeem_url}</p>
</div>"""
    return send_email(to, "You're invited to Rebooter-Droids", html)


def send_password_reset_email(to: str, reset_url: str) -> bool:
    html = f"""<div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:28px;background:#161a21;color:#e6e8eb;border-radius:12px">
<h2 style="color:#6db3ff;margin:0 0 12px">🤖 Rebooter-Droids — Reset your password</h2>
<p style="margin:0 0 16px;color:#c8c8d4">Someone requested a password reset for this email. If that wasn't you, you can ignore this message.</p>
<p style="margin:0 0 20px;color:#c8c8d4">Otherwise, click below. This link is single-use and expires in 1 hour.</p>
<a href="{reset_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:11px 24px;border-radius:7px;text-decoration:none;font-weight:600">Reset Password</a>
<p style="margin:20px 0 0;font-size:12px;color:#8a8f99">Or copy this URL: {reset_url}</p>
</div>"""
    return send_email(to, "Reset your Rebooter-Droids password", html)


def send_test_email(to: str) -> bool:
    """Smoke-test email — used by Settings → Notifications "send test"."""
    html = f"""<div style="font-family:sans-serif;max-width:520px;margin:0 auto;padding:28px;background:#161a21;color:#e6e8eb;border-radius:12px">
<h2 style="color:#6db3ff;margin:0 0 12px">🤖 Rebooter-Droids — SMTP test OK</h2>
<p style="margin:0;color:#c8c8d4">If you're reading this, your SMTP credentials are correctly configured. Future invites and password-reset emails will be delivered through this same channel.</p>
</div>"""
    return send_email(to, "Rebooter-Droids SMTP test", html)
