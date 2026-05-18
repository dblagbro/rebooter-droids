"""Unit tests — Google Calendar OAuth (v0.5.94 / B17).

`app/services/google_oauth.py` — the OAuth2 plumbing — and
`_poll_google_calendar` — the poller that refreshes the access token
and normalises events into the iCal payload shape. Network calls
(`fetch_json`, `refresh_access_token`) are monkeypatched; the
credential lookup hits `runtime_settings`, so credential-touching
tests take the `hub_db` fixture and set the env fallback.
"""

from __future__ import annotations

import pytest

from app.db import session_scope
from app.models import ExternalSensorSource
from app.services import google_oauth
from app.services.external_sensors import create_source
from app.services.external_sensors._pollers import (
    _normalise_gcal_events,
    _poll_google_calendar,
)


# ── client credentials / is_configured ─────────────────────────────────

def test_is_configured_false_when_unset(hub_db, monkeypatch):
    monkeypatch.delenv("REBOOTER_GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("REBOOTER_GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    assert google_oauth.is_configured() is False


def test_is_configured_true_with_env(hub_db, monkeypatch):
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_ID", "cid-1")
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_SECRET", "secret-1")
    assert google_oauth.is_configured() is True
    assert google_oauth.client_credentials() == ("cid-1", "secret-1")


# ── build_consent_url ──────────────────────────────────────────────────

def test_build_consent_url_carries_the_oauth_params(hub_db, monkeypatch):
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_ID", "cid-1")
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_SECRET", "secret-1")
    url = google_oauth.build_consent_url(
        "https://hub.example/rebooter/app/settings/integrations/google/callback",
        "state-xyz",
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=cid-1" in url
    assert "access_type=offline" in url          # guarantees a refresh token
    assert "state=state-xyz" in url
    assert "calendar.readonly" in url


def test_build_consent_url_raises_when_unconfigured(hub_db, monkeypatch):
    monkeypatch.delenv("REBOOTER_GOOGLE_OAUTH_CLIENT_ID", raising=False)
    with pytest.raises(google_oauth.GoogleOAuthError):
        google_oauth.build_consent_url("https://hub/cb", "s")


# ── exchange_code / refresh_access_token ───────────────────────────────

def test_exchange_code_returns_tokens(hub_db, monkeypatch):
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    monkeypatch.setattr(
        google_oauth, "fetch_json",
        lambda url, **kw: {"access_token": "at", "refresh_token": "rt",
                           "expires_in": 3600},
    )
    tok = google_oauth.exchange_code("auth-code", "https://hub/cb")
    assert tok["refresh_token"] == "rt"


def test_exchange_code_rejects_response_without_refresh_token(hub_db, monkeypatch):
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    monkeypatch.setattr(
        google_oauth, "fetch_json",
        lambda url, **kw: {"access_token": "at"},  # no refresh_token
    )
    with pytest.raises(google_oauth.GoogleOAuthError) as exc:
        google_oauth.exchange_code("auth-code", "https://hub/cb")
    assert exc.value.code == "no_refresh_token"


def test_refresh_access_token(hub_db, monkeypatch):
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("REBOOTER_GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    monkeypatch.setattr(
        google_oauth, "fetch_json",
        lambda url, **kw: {"access_token": "at-fresh", "expires_in": 3600},
    )
    assert google_oauth.refresh_access_token("rt")["access_token"] == "at-fresh"


# ── _normalise_gcal_events (pure) ──────────────────────────────────────

def test_normalise_gcal_events_handles_timed_and_all_day():
    out = _normalise_gcal_events([
        {"summary": "Jeopardy", "start": {"dateTime": "2026-05-18T19:00:00Z"},
         "end": {"dateTime": "2026-05-18T19:30:00Z"}},
        {"summary": "Holiday", "start": {"date": "2026-05-17"},
         "end": {"date": "2026-05-18"}},
        {"start": {}},  # no start → dropped
    ])
    assert [e["summary"] for e in out] == ["Holiday", "Jeopardy"]  # sorted by start
    timed = next(e for e in out if e["summary"] == "Jeopardy")
    assert timed["start"] == "2026-05-18T19:00:00Z"
    allday = next(e for e in out if e["summary"] == "Holiday")
    assert allday["start"] == "2026-05-17"


# ── _poll_google_calendar ──────────────────────────────────────────────

def test_poll_google_calendar_refreshes_token_and_normalises(hub_db, monkeypatch):
    sid = create_source(kind="google_calendar", display_name="Erica's Cal",
                        config={"refresh_token": "rt_abc"})["id"]
    monkeypatch.setattr(
        google_oauth, "refresh_access_token",
        lambda rt: {"access_token": "at_new", "expires_in": 3600},
    )
    seen: dict = {}

    def fake_fetch(url, *, headers=None, form=None, timeout=15):
        seen["url"] = url
        seen["headers"] = headers or {}
        return {"items": [
            {"summary": "Jeopardy",
             "start": {"dateTime": "2026-05-18T19:00:00Z"},
             "end": {"dateTime": "2026-05-18T19:30:00Z"}},
        ]}

    monkeypatch.setattr(google_oauth, "fetch_json", fake_fetch)
    with session_scope() as s:
        payload = _poll_google_calendar(s.get(ExternalSensorSource, sid))
    assert payload["event_count"] == 1
    assert payload["events"][0]["summary"] == "Jeopardy"
    assert seen["headers"]["Authorization"] == "Bearer at_new"
    # The refreshed access token is persisted onto the source config.
    with session_scope() as s:
        assert s.get(ExternalSensorSource, sid).config["access_token"] == "at_new"


def test_poll_google_calendar_reuses_a_fresh_cached_token(hub_db, monkeypatch):
    from datetime import datetime, timedelta, timezone

    sid = create_source(kind="google_calendar", display_name="Cal",
                        config={"refresh_token": "rt_abc"})["id"]
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00")
    with session_scope() as s:
        src = s.get(ExternalSensorSource, sid)
        src.config = {**src.config, "access_token": "at_cached",
                      "access_token_expires_at": future}

    def _no_refresh(rt):
        raise AssertionError("refresh_access_token must not be called")

    monkeypatch.setattr(google_oauth, "refresh_access_token", _no_refresh)
    monkeypatch.setattr(google_oauth, "fetch_json",
                        lambda url, **kw: {"items": []})
    with session_scope() as s:
        payload = _poll_google_calendar(s.get(ExternalSensorSource, sid))
    assert payload == {"event_count": 0, "events": []}


def test_poll_google_calendar_without_refresh_token_raises(hub_db):
    # A real google_calendar source always carries a refresh_token
    # (create_source enforces it) — guard the poller defensively.
    from sqlalchemy import select

    with session_scope() as s:
        s.add(ExternalSensorSource(kind="google_calendar", display_name="Bad",
                                   host="", port=0, config={}))
    with session_scope() as s:
        src = s.scalars(
            select(ExternalSensorSource).where(
                ExternalSensorSource.kind == "google_calendar")
        ).first()
        with pytest.raises(RuntimeError):
            _poll_google_calendar(src)
