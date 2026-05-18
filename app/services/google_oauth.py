"""Google Calendar OAuth — v0.5.94 (B17).

OAuth2 plumbing for the `google_calendar` external-sensor kind: the
operator connects a calendar once via Google's consent screen, the hub
stores the refresh token, and the poller (`_poll_google_calendar`)
trades it for a short-lived access token each cycle.

Client credentials — the Google Cloud OAuth app's id + secret — come
from runtime settings with an env fallback (`google.oauth_client_id` /
`REBOOTER_GOOGLE_OAUTH_CLIENT_ID`, and the matching `_secret`). The
operator registers the OAuth app in Google Cloud Console with the
authorized redirect URI set to this hub's
`/app/settings/integrations/google/callback`.

Stdlib HTTP only — OAuth2 + the Calendar REST API are plain HTTP, so
no `google-auth` / `google-api-python-client` dependency, consistent
with the other B17 pollers.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Read-only access to the operator's calendars — the hub never writes.
_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
_HTTP_TIMEOUT = 15


class GoogleOAuthError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def client_credentials() -> tuple[str | None, str | None]:
    """`(client_id, client_secret)` — runtime settings → env → None."""
    from app.services import runtime_settings

    cid = runtime_settings.get(
        "google.oauth_client_id",
        env_var="REBOOTER_GOOGLE_OAUTH_CLIENT_ID", default=None,
    )
    secret = runtime_settings.get(
        "google.oauth_client_secret",
        env_var="REBOOTER_GOOGLE_OAUTH_CLIENT_SECRET", default=None,
    )
    return (str(cid or "").strip() or None, str(secret or "").strip() or None)


def is_configured() -> bool:
    """True once the operator has set both client credentials."""
    cid, secret = client_credentials()
    return bool(cid and secret)


def build_consent_url(redirect_uri: str, state: str) -> str:
    """The Google consent-screen URL. `access_type=offline` +
    `prompt=consent` guarantee a refresh token on connect."""
    cid, _ = client_credentials()
    if not cid:
        raise GoogleOAuthError(
            "not_configured", "Google OAuth client credentials are not set."
        )
    return _AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })


def fetch_json(url: str, *, headers: dict | None = None,
               form: dict | None = None, timeout: int = _HTTP_TIMEOUT) -> dict:
    """GET (or POST when `form` is given) → parsed JSON. Raises
    `GoogleOAuthError` on a transport / HTTP / parse failure."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(
        url, data=data, headers=headers or {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise GoogleOAuthError(
            "http_error", f"Google API HTTP {e.code}: {detail}"
        ) from None
    except urllib.error.URLError as e:
        raise GoogleOAuthError(
            "transport_error", f"Google API unreachable: {e.reason}"
        ) from None
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise GoogleOAuthError(
            "bad_response", f"Google API returned non-JSON: {e}"
        ) from None


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Trade an authorization code for tokens — returns
    `{access_token, refresh_token, expires_in}`."""
    cid, secret = client_credentials()
    if not (cid and secret):
        raise GoogleOAuthError(
            "not_configured", "Google OAuth client credentials are not set."
        )
    tok = fetch_json(_TOKEN_URL, form={
        "code": code,
        "client_id": cid,
        "client_secret": secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    if not tok.get("refresh_token"):
        raise GoogleOAuthError(
            "no_refresh_token",
            "Google did not return a refresh token — reconnect and grant "
            "offline access.",
        )
    return tok


def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a stored refresh token for a fresh access token —
    returns `{access_token, expires_in}`."""
    cid, secret = client_credentials()
    if not (cid and secret):
        raise GoogleOAuthError(
            "not_configured", "Google OAuth client credentials are not set."
        )
    return fetch_json(_TOKEN_URL, form={
        "refresh_token": refresh_token,
        "client_id": cid,
        "client_secret": secret,
        "grant_type": "refresh_token",
    })
