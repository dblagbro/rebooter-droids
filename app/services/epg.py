"""EPG (TV programming guide) service — v0.5.64 (B17 Layer 2).

Fetches the US TV schedule from TVMaze (free, no auth) into the
`external_epg_cache` table, and answers "is show X airing now?" for the
`epg_show_airing` watchdog probe.

TVMaze `GET /schedule?country=US&date=YYYY-MM-DD` returns one row per
episode airing that day, each carrying the show name, network, air
timestamp, and runtime. We fetch today + tomorrow on each refresh,
replace that window in the cache, and a janitor drops rows whose airing
already ended > 24 h ago.

See `docs/notes/2026-05-15-b17-layer2-epg-design.md`. The companion
`epg_channel_mappings` table (friendly channel labels) is deferred —
the v1 probe matches the TVMaze network name directly.
"""

from __future__ import annotations

import http.client
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.db import session_scope
from app.models import ExternalEpgCache

log = logging.getLogger(__name__)

TVMAZE_HOST = "api.tvmaze.com"
TVMAZE_TIMEOUT_SECONDS = 12
_PROVIDER = "tvmaze"
_DEFAULT_RUNTIME_MIN = 30  # when TVMaze omits runtime


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _fetch_tvmaze_schedule(date_str: str) -> list[dict]:
    """GET /schedule?country=US&date=<date_str>. Returns the raw episode
    list. Raises RuntimeError on transport / parse error."""
    conn = http.client.HTTPSConnection(
        TVMAZE_HOST, 443, timeout=TVMAZE_TIMEOUT_SECONDS
    )
    try:
        conn.request(
            "GET", f"/schedule?country=US&date={date_str}",
            headers={
                "User-Agent": "rebooter-droids/B17 (https://github.com/dblagbro/rebooter-droids)",
                "Accept": "application/json",
            },
        )
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
    finally:
        conn.close()
    if status != 200:
        raise RuntimeError(f"tvmaze HTTP {status} for {date_str}")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"tvmaze JSON parse failed: {e}") from None
    if not isinstance(data, list):
        raise RuntimeError("tvmaze /schedule returned non-list")
    return data


def _parse_episode(ep: dict) -> dict | None:
    """Flatten one TVMaze schedule episode into an ExternalEpgCache
    row dict. Returns None when the episode is unusable (no channel or
    no air time)."""
    if not isinstance(ep, dict):
        return None
    show = ep.get("show") or {}
    show_title = (show.get("name") or "").strip()
    if not show_title:
        return None
    # Channel: broadcast network first, web channel as fallback.
    network = show.get("network") or show.get("webChannel") or {}
    channel = (network.get("name") or "").strip() if isinstance(network, dict) else ""
    if not channel:
        return None
    airstamp = ep.get("airstamp")
    if not airstamp:
        return None
    try:
        start = datetime.fromisoformat(str(airstamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    start = start.astimezone(timezone.utc)
    try:
        runtime = int(ep.get("runtime") or show.get("averageRuntime") or _DEFAULT_RUNTIME_MIN)
    except (TypeError, ValueError):
        runtime = _DEFAULT_RUNTIME_MIN
    if runtime <= 0:
        runtime = _DEFAULT_RUNTIME_MIN
    end = start + timedelta(minutes=runtime)

    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    genres = show.get("genres") if isinstance(show.get("genres"), list) else []
    return {
        "channel_id": channel[:80],
        "airing_start": start,
        "airing_end": end,
        "show_title": show_title[:300],
        "episode_title": (ep.get("name") or "")[:300] or None,
        "season": _int(ep.get("season")),
        "episode": _int(ep.get("number")),
        "extra": {"genres": genres[:8], "runtime_min": runtime},
    }


def refresh_epg() -> dict:
    """Fetch today + tomorrow's US schedule from TVMaze, replace that
    window in `external_epg_cache`, and run the janitor.

    Idempotent: deletes the refreshed window's rows then re-inserts, so
    a re-run just overwrites. Returns a stats dict.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    stats = {"fetched": 0, "stored": 0, "pruned": 0, "errors": 0}
    rows: list[dict] = []
    for offset in (0, 1):
        d = today + timedelta(days=offset)
        try:
            episodes = _fetch_tvmaze_schedule(d.isoformat())
        except Exception as e:
            log.warning("epg refresh: %s fetch failed: %s", d, e)
            stats["errors"] += 1
            continue
        stats["fetched"] += len(episodes)
        for ep in episodes:
            parsed = _parse_episode(ep)
            if parsed is not None:
                rows.append(parsed)

    if not rows and stats["errors"]:
        # Total failure — don't wipe the cache on a transient outage.
        return stats

    # Window we are replacing: start-of-today onward (UTC).
    window_start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    with session_scope() as session:
        session.execute(
            delete(ExternalEpgCache).where(
                ExternalEpgCache.provider == _PROVIDER,
                ExternalEpgCache.airing_start >= window_start,
            )
        )
        # Dedupe on the unique key within this batch (TVMaze can list a
        # show twice across the day boundary).
        seen: set = set()
        for r in rows:
            key = (r["channel_id"], r["airing_start"], r["show_title"])
            if key in seen:
                continue
            seen.add(key)
            session.add(ExternalEpgCache(provider=_PROVIDER, fetched_at=now, **r))
            stats["stored"] += 1
        # Janitor — drop rows whose airing ended > 24 h ago.
        cutoff = now - timedelta(hours=24)
        pruned = session.execute(
            delete(ExternalEpgCache).where(ExternalEpgCache.airing_end < cutoff)
        )
        stats["pruned"] = pruned.rowcount or 0
        session.flush()
    return stats


def show_airing_now(show: str, *, network: str | None = None) -> dict | None:
    """Return the currently-airing cache row matching `show` (title
    substring, case-insensitive), optionally restricted to a TVMaze
    `network` name. None if nothing matches right now.
    """
    show = (show or "").strip()
    if not show:
        return None
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        stmt = (
            select(ExternalEpgCache)
            .where(
                ExternalEpgCache.provider == _PROVIDER,
                ExternalEpgCache.airing_start <= now,
                ExternalEpgCache.airing_end > now,
                ExternalEpgCache.show_title.ilike(f"%{show}%"),
            )
            .order_by(ExternalEpgCache.airing_start.desc())
        )
        if network:
            stmt = stmt.where(func.lower(ExternalEpgCache.channel_id) == network.lower())
        row = session.scalar(stmt.limit(1))
        if row is None:
            return None
        return {
            "show_title": row.show_title,
            "channel_id": row.channel_id,
            "episode_title": row.episode_title,
            "season": row.season,
            "episode": row.episode,
            "airing_start": _iso(row.airing_start),
            "airing_end": _iso(row.airing_end),
            "fetched_at": _iso(row.fetched_at),
        }


def epg_status() -> dict:
    """Cache summary for the admin UI — row count + last refresh time."""
    with session_scope() as session:
        total = session.scalar(
            select(func.count(ExternalEpgCache.id)).where(
                ExternalEpgCache.provider == _PROVIDER
            )
        ) or 0
        last = session.scalar(
            select(func.max(ExternalEpgCache.fetched_at)).where(
                ExternalEpgCache.provider == _PROVIDER
            )
        )
    return {"provider": _PROVIDER, "cached_rows": int(total), "last_refreshed_at": _iso(last)}
