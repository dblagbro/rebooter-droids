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


def is_configured(settings: Settings | None = None) -> bool:
    s = settings or load_settings()
    return bool(s.smtp_host and s.smtp_from)


def send_email(to: str, subject: str, html_body: str) -> bool:
    """Return True if sent (or attempted), False if SMTP not configured."""
    s = load_settings()
    if not is_configured(s):
        log.warning(
            "SMTP not configured — would-have-sent to=%s subject=%s",
            to,
            subject,
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as server:
        if s.smtp_helo:
            server.ehlo(s.smtp_helo)
        server.starttls(context=ctx)
        if s.smtp_helo:
            server.ehlo(s.smtp_helo)
        if s.smtp_user:
            server.login(s.smtp_user, s.smtp_password)
        server.sendmail(s.smtp_from, [to], msg.as_string())
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
<p style="margin:0 0 20px;color:#c8c8d4">Click below to set up your account. This link is single-use and expires in 7 days.</p>
<a href="{redeem_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:11px 24px;border-radius:7px;text-decoration:none;font-weight:600">Accept Invitation</a>
<p style="margin:20px 0 0;font-size:12px;color:#8a8f99">Or copy this URL: {redeem_url}</p>
</div>"""
    return send_email(to, "You're invited to Rebooter-Droids", html)
