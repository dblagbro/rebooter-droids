# B17 Layer 2 — EPG (Electronic Program Guide) integration design

**Status:** research + design, no code yet
**Author session:** 2026-05-15 (research charter from operator 2026-05-14:
"next up we plan and research the B1 RBAC, B11 sync, B17 remaining and
B17 layer 2 epg")
**Baseline:** v0.5.34 live at `https://www.voipguru.org/rebooter`
**Sized in BACKLOG as:** ~8-12 h total (this doc breaks it into 4 phased ships)
**Probe kind to add:** `epg_show_airing`
**New tables:** `external_epg_cache`, `epg_channel_mappings`
**No new source kind in `EXTERNAL_SOURCE_KINDS`** — EPG is a *back-end cache
layer* that the probe reads directly, not a per-host polled source. See
"Why EPG is not a `kind` in EXTERNAL_SOURCE_KINDS" below.

---

## 1. Current state — what shipped, why Layer 2 is needed

### 1.1 What B17 already gives us

| Layer | Ship | What it does | Probe kind |
|---|---|---|---|
| L1 — Roku ECP | v0.5.17 | LAN GET `/query/active-app` against a Roku at port 8060; knows *which app is open* | `roku_app_active` |
| L1 adjacent — Home Assistant | v0.5.23 | HA REST `/api/states` with bearer token | `ha_state_is` |
| L1 adjacent — NWS weather | v0.5.23 | `api.weather.gov/alerts/active?point=…` | `weather_alert_active` |
| L1 adjacent — iCal/WebCal | v0.5.23 | `.ics` feed fetch + minimal VEVENT parser | `ical_event_active` |

All four kinds share the same scaffolding:

- `app/services/external_sensors.py` — `_poll_kind(src)` dispatcher,
  `_validate_kind_config()`, `latest_sample()` / `latest_active_app()`
  staleness gates.
- `app/services/watchdog_runtime/_probes.py` — `_probe_<kind>` functions
  that read the latest sample and return `(outcome, details)`.
- Tables: `external_sensor_sources` (one row per registered source) +
  `external_sensor_samples` (append-only history, indexed
  `(source_id, sampled_at DESC)`).
- Stale-sample failure gate is the load-bearing invariant: every probe
  defaults to returning `failure / reason='stale_sample'` if the latest
  sample is older than `max_sample_age_seconds`. Layer 2 must preserve
  this — a dead EPG fetch must not let a "Jeopardy airing" rule stick
  true indefinitely.

### 1.2 The Layer-2 gap

Roku ECP tells us *"the device is on the Spectrum TV app right now"*.
It does **not** tell us *what channel* or *what show*. Today the
operator workaround is `ical_event_active` — keep a Google Calendar
where each show airing is a calendar event, and treat "calendar event
'Jeopardy 7-7:30pm' is currently active" as a heuristic. This is brittle:

- The calendar has to be hand-curated, which is the exact toil B17 was
  meant to eliminate.
- It has no real channel awareness — if Jeopardy moves networks or the
  schedule shifts, the rule silently misfires.
- It is one bidirectional source of truth (calendar) instead of
  pairing two independent signals (device on Spectrum + EPG says
  Jeopardy is airing on the Spectrum channel the operator pinned).

### 1.3 The Layer-2 goal — paired-signal Jeopardy detection

Operator's stated 95%-reliability target:

```
(roku_app_active source=living_room app="Spectrum TV")
  AND
(epg_show_airing source=tvmaze show="Jeopardy!" channel="WJLA-ABC-DC")
  → action: notify / cycle / whatever
```

Rules can already be composed via separate watchdog rules (one fires
when both have been in a "failing" state for N ticks). The piece we are
missing is the EPG-side probe. This doc designs that piece.

---

## 2. Provider research — TVMaze vs Schedules Direct

### 2.1 TVMaze API

- **URL:** `https://api.tvmaze.com/`
- **Auth:** none for the public dataset. Submission/edit API uses HTTP
  Basic with a free site account; we only need the read side.
- **Rate limit:** ~20 req/10s per IP (documented soft limit; no API key
  to throttle against). Easy to stay under at 4-6h cadence.
- **Schedule endpoint:** `GET /schedule?country=US&date=YYYY-MM-DD` returns
  the next-24h schedule, one episode per row, with a `show.network` and
  `airstamp` (UTC ISO timestamp). Per-show schedule: `GET /shows/{id}/episodes`.
- **Coverage shape:** TVMaze indexes by **network/web channel**, NOT
  by cable-provider channel slot. So "ABC", "CBS", "Game Show Network"
  exist as networks; "Spectrum channel 27 in Charlottesville VA" does
  not exist as a directly-addressable entity. The operator has to map
  *"my Spectrum says ch.27 = WJLA-DC = ABC affiliate"* themselves.
- **What it does well:** US primetime + cable specialty (HBO, ESPN,
  Discovery, etc.). Game shows (Jeopardy, Wheel) are reliably indexed
  because they air on syndicated networks the database knows about.
- **What it does badly:** *local affiliate slot detail*. TVMaze knows
  "Jeopardy airs at 7pm ET on ABC syndication"; it does not know
  "your local ABC affiliate (WJLA-DC) is on Spectrum channel 27".
  We fill that gap with `epg_channel_mappings` (§5 below).
- **Stability:** community-maintained but the API has been stable since
  2014; multiple OSS projects (Sonarr, Sickbeard) depend on it.

### 2.2 Schedules Direct

- **URL:** `https://json.schedulesdirect.org/20141201/` (versioned)
- **Auth:** username + sha1(password) → bearer token via `/token`; tokens
  rotate every 24h.
- **Cost:** **$25/yr per account**, verified on schedulesdirect.org
  pricing page. Single-user license; multi-server-of-same-user is fine.
- **Rate limit:** 5000 service-credit-units/day default; each lineup
  pull is ~1 unit, schedule pulls are ~1 unit per channel per day. A
  10-channel whitelist polled every 4h ≈ 60 units/day — far under cap.
- **Coverage shape:** the canonical NA EPG dataset most retail DVRs
  (TiVo, MythTV, Plex DVR, etc.) consume. Lineups are addressed by
  region + provider, e.g. `USA-VA67703-X` = "Spectrum, Charlottesville
  VA". Each lineup enumerates the actual channel-number-to-stationID
  mapping the operator sees on their box. **This is the killer feature
  for our use case** — the operator's "Spectrum channel 27" maps
  natively to a Schedules Direct `stationID`.
- **Endpoints we'd use:**
  - `GET /headends?country=USA&postalcode=22901` → list of available
    lineups in the operator's ZIP
  - `PUT /lineups/<lineupID>` → subscribe (counted against an account's
    4-lineup cap; well within free)
  - `GET /lineups/<lineupID>` → channel map (`channel`, `stationID`,
    `name`, `callsign`) — this is the channel mapping handed to us
    for free
  - `POST /schedules` with `[{"stationID": "...", "date": ["YYYY-MM-DD"]}, …]`
    → schedule blob; `POST /programs` with programID list → titles/episode
    metadata
- **SaaS policy borderline call:**
  - `feedback_open_source_only.md` is locked: "build vs buy defaults
    to OSS; reject Cloudflare Tunnel / HiveMQ Cloud / paid SaaS for
    new components; we own the dependency chain".
  - Schedules Direct is a **non-profit data co-op**, not a vendor
    SaaS. Operator-owned credential, no vendor lock-in beyond the
    annual data refresh, data lives in `external_epg_cache` (we own
    the storage). Closest comparable in the existing stack: NWS
    (free, operator-owned, also a "you have to talk to their
    endpoint" dependency).
  - **Decision below in §3 — flag it but proceed with TVMaze as default.**

### 2.3 What both providers don't solve

- **Regional schedule drift:** Jeopardy can air on different stations
  in different markets. Both providers handle this via the
  channel-mapping step; the operator must do this once per location.
- **DST + timezone:** both return UTC; conversion is on us. Operator's
  region (mostly US East) is stable enough that we just convert at
  query time.
- **Last-minute schedule changes:** sports overruns, breaking news.
  Neither provider real-times this. The 4-6h cache TTL is the right
  trade — we accept the occasional 4h-stale "Jeopardy airing" miss in
  exchange for never DOSing the provider.

---

## 3. Provider recommendation

**Pick TVMaze for v1 (Phase 2A). Add Schedules Direct as an optional
second provider in a later ship if accuracy proves insufficient.**

### Rationale

| Dimension | TVMaze | Schedules Direct | Winner |
|---|---|---|---|
| Cost | free | $25/yr | TVMaze |
| Auth complexity | none | username + sha1 + 24h token rotation | TVMaze |
| OSS-policy fit | clear (free public API) | borderline (paid co-op, non-profit) | TVMaze |
| Cable-channel-slot mapping | not given — operator types it | given in `lineups/<id>` JSON | Schedules Direct |
| Show metadata depth (season/ep) | good | gold-standard | Schedules Direct |
| Jeopardy detection ("is it airing right now somewhere on Spectrum") | sufficient (we know "Jeopardy" airs on ABC@7pm; operator pins "Spectrum 27 = ABC affiliate") | better (we know "Spectrum channel 27 stationID = WJLA, schedule says Jeopardy 19:00-19:30 ET") | Schedules Direct |
| Survives provider going dark | community-maintained, single backend | dues-funded non-profit | tie — both have outage risk |

The Jeopardy use case is **achievable on TVMaze** with one-time
operator mapping. The cost + policy + simplicity wins make TVMaze the
right v1. The schema is provider-agnostic from day one
(`external_epg_cache.provider` column, `epg_channel_mappings.provider`
column) so a later Schedules Direct addition is purely additive — no
migration on TVMaze data.

### Flag for operator

The Schedules Direct $25/yr is a **borderline** OSS-policy call:
non-profit + operator-owned credential + no vendor lock-in, vs. the
locked-in "never paid SaaS" rule. **Default: don't pay.** If after a
month of TVMaze the Jeopardy rule misfires more than the operator
tolerates, revisit. The provider-agnostic schema means swap-in is
a small ship, not a redesign.

---

## 4. `external_epg_cache` schema

Provider-agnostic cache of "what's airing on which channel at which
time". One row per (provider, channel_id, airing_start) tuple. Append-
only during fetch; periodic janitor deletes rows older than NOW - 24h
to keep the table small.

```python
# app/models/external_epg.py  (new file — sibling to external_sensors.py)

class ExternalEpgCache(Base):
    __tablename__ = "external_epg_cache"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True, autoincrement=True,
    )

    # "tvmaze" | "schedulesdirect" — matches the entry in EPG_PROVIDERS.
    provider: Mapped[str] = mapped_column(String(40), nullable=False)

    # Provider-native channel identifier. For TVMaze this is the network
    # slug ("ABC", "GSN"); for Schedules Direct it's the stationID
    # ("WJLA"). Operator never sees this directly — they pick a friendly
    # display via the channel-mapping UI.
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)

    # UTC airing window. Half-open [start, end). End is mandatory; if a
    # provider returns only duration we compute it before insert.
    airing_start: Mapped[datetime] = mapped_column(nullable=False)
    airing_end: Mapped[datetime] = mapped_column(nullable=False)

    # Display fields. show_title is the *search key* the probe matches
    # against. Episode metadata is for the events log so the operator
    # can see "fired on S40E142 The College Championships" not just
    # "Jeopardy".
    show_title: Mapped[str] = mapped_column(String(300), nullable=False)
    episode_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Bag-of-extras from the provider — we keep this so a probe can
    # later filter on "show.genre = 'Game Show'" without a migration.
    # Capped at 2 KiB at insert time.
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Insertion bookkeeping. last_refreshed_at lets the janitor and the
    # admin UI tell "data is fresh" from "data is leftover".
    fetched_at: Mapped[datetime] = ts_column()

# Indexes — query-driven design:
#   (1) "what's on channel X right now" → (provider, channel_id, airing_start)
#       lookup with WHERE airing_start <= now < airing_end
Index(
    "ix_epg_cache_lookup",
    ExternalEpgCache.provider,
    ExternalEpgCache.channel_id,
    ExternalEpgCache.airing_start.desc(),
)
#   (2) janitor cleanup by airing_end
Index("ix_epg_cache_airing_end", ExternalEpgCache.airing_end)
#   (3) uniqueness — prevent dupes if a refresh runs twice
UniqueConstraint(
    "provider", "channel_id", "airing_start",
    name="uq_epg_cache_provider_channel_start",
)
```

### Why a half-open `[start, end)` window

The probe's hot path is:

```sql
SELECT show_title, airing_end FROM external_epg_cache
WHERE provider = :p AND channel_id = :c
  AND airing_start <= :now AND airing_end > :now
ORDER BY airing_start DESC LIMIT 1
```

`(provider, channel_id, airing_start DESC)` covers the ORDER BY and
the equality+range predicates; Postgres uses an Index Scan + filter on
`airing_end`. At ~10 channels × 50 rows/channel ≈ 500 rows per
refresh window, this is sub-millisecond. SQLite (dev) same shape.

### Why no FK to a "provider" or "channel" table

Provider is a small enum (initially `("tvmaze",)`; later
`("tvmaze", "schedulesdirect")`). Channel IDs are provider-native
strings — no canonical channel table because each provider invents its
own IDs. The mapping table (§5) bridges this.

---

## 5. `epg_channel_mappings` — the operator's "Spectrum 27 = WJLA" facts

The hardest part of B17 Layer 2 is **not** the EPG fetch; it's the
**channel naming bridge**. The operator's TV remote shows "27"; TVMaze
indexes by "ABC"; Schedules Direct indexes by stationID "WJLA". The
operator has to teach the hub the bridge once, per Roku location.

### Schema

```python
# app/models/external_epg.py (continued)

class EpgChannelMapping(Base):
    __tablename__ = "epg_channel_mappings"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=partial(new_id, "epgm")
    )

    # Friendly label the operator types — appears in the rule editor
    # dropdown. "Spectrum 27 (ABC Washington)", "GSN", "ESPN".
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Provider + provider-native channel_id this maps to. Matches
    # external_epg_cache(provider, channel_id) for the join.
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)

    # Free-form regional / cable-context hint. Not load-bearing; kept
    # for the admin UI to render "(Spectrum, Charlottesville VA)".
    # Schedules Direct populates this from lineup metadata; TVMaze
    # leaves it blank.
    region_hint: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Operator-typed local channel number ("27"). Stored as text — some
    # cable lineups use "27.1" or "C27". Purely informational; the
    # probe never joins on it.
    local_channel_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Soft-delete via enabled flag (lets the operator hide a mapping
    # without breaking historical event-log entries that reference it).
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = ts_column()
    updated_at: Mapped[datetime] = ts_column()

UniqueConstraint(
    "provider", "channel_id",
    name="uq_epg_channel_mapping_provider_channel",
)
Index("ix_epg_channel_mapping_enabled", EpgChannelMapping.enabled)
```

### Why a separate table — *not* folded into source.config

I considered stuffing the channel mapping into `external_sensor_sources.config`
(matching the `ical → {"url": "..."}` pattern). Rejected because:

1. EPG channels are **not 1:1 with an external_sensor_source row**.
   There is no host to poll; mapping is shared across all rules in
   the install.
2. The list grows monotonically — a household watches 5-20 channels.
   Cramming an array into a JSON blob makes the rule editor harder
   to build (no FK, no dropdown query).
3. Schedules Direct's lineup endpoint *gives us* the channel list for
   free — we want to bulk-insert it. A JSON blob in a source config
   doesn't model bulk-insert well.

### Bootstrapping the mapping table

**Phase 2B (UI):**
- Settings → External Sensors → **EPG Channels** tab
- Two flows:
  - **Manual add** — provider dropdown, channel_id text field
    (autocomplete against TVMaze `GET /shows?q=…` to help the operator
    find the right network), display_name, local_channel_number.
  - **Bulk import from lineup** (Schedules Direct only, ship later) —
    operator pastes their ZIP, hub fetches `/headends`, operator picks
    "Spectrum, Charlottesville", hub fetches lineup, bulk-inserts rows
    with `enabled=False` and lets the operator tick on the channels
    they actually watch.

For TVMaze v1 the operator types ~10 channels by hand. That's the
"one-time setup per Roku location" the BACKLOG noted.

---

## 6. EPG fetch — cadence, whitelist, code shape

### 6.1 Cadence

- **TVMaze:** every 6 h, fetch the next-24h schedule **per channel in
  the active mapping table**. Light load — 4 fetches/day × 10
  channels = 40 requests/day, well under any TVMaze limit.
- **Schedules Direct (future):** every 6 h, do a single bulk
  `/schedules` POST with all whitelisted stationIDs + the next 24h
  date list. One API call covers everything.

### 6.2 Whitelisting — only fetch what the operator cares about

The whitelist *is* the `epg_channel_mappings` table (where
`enabled=True`). No separate config. This solves the BACKLOG's "the
whole guide is huge data" concern — we never fetch beyond ~20 channels
the operator has explicitly added.

### 6.3 Scheduler hook

Add a new APScheduler job alongside `_external_sensors_job`:

```python
# app/jobs/scheduler.py  (extension)

def _epg_refresh_job():
    """v0.5.x (B17 L2): refresh EPG cache for whitelisted channels.

    Runs every EPG_REFRESH_INTERVAL_SECONDS (default 6h). Per-provider
    backoff on transport failure; never raises into the scheduler.
    """
    from app.services import external_epg
    external_epg.refresh_all_due()
```

`refresh_all_due()` iterates enabled providers, calls a per-provider
`_fetch_provider_24h(channel_ids)` function, upserts results into
`external_epg_cache`, and runs the janitor (DELETE WHERE
`airing_end < NOW() - 24h`).

### 6.4 Service module

New file `app/services/external_epg.py` (sibling to `external_sensors.py`):

- `EPG_PROVIDERS = ("tvmaze",)` — extension point, mirrors
  `EXTERNAL_SOURCE_KINDS`.
- `refresh_all_due()` — top-level scheduler entry point.
- `_fetch_tvmaze_schedule_24h(channel_ids: list[str]) -> list[dict]`
- `_upsert_cache(provider, rows)` — uses
  `INSERT … ON CONFLICT (provider, channel_id, airing_start) DO UPDATE`
  via SQLAlchemy `Insert.on_conflict_do_update()` (Postgres). On
  SQLite (dev), fall through to `INSERT OR REPLACE`.
- `_janitor()` — purge rows where `airing_end < NOW - 24h`.
- `latest_airing(provider, channel_id, *, max_age_seconds=21600)
  -> dict | None` — the function `_probe_epg_show_airing` calls.
  Returns the row whose `[start, end)` window contains NOW. Returns
  `None` if no row matches OR if the most-recent `fetched_at` for
  this `(provider, channel_id)` is older than `max_age_seconds`
  (default 6h = one refresh window).
- `list_mappings()` / `create_mapping()` / `delete_mapping()` /
  `set_mapping_enabled()` — CRUD for the admin API, mirrors the
  pattern in `external_sensors.py`.

### 6.5 Why EPG is *not* a `kind` in `EXTERNAL_SOURCE_KINDS`

The four existing kinds (roku, home_assistant, weather, ical) all map
"one operator-registered host or feed URL → one polled endpoint → one
per-poll sample row in `external_sensor_samples`". EPG breaks this
model:

- No host to register — TVMaze is one global API endpoint.
- No 1:1 source-to-sample shape — one fetch produces ~500 rows across
  ~10 channels; storing that as a single JSON blob in
  `external_sensor_samples.payload` would be lookup-hostile.
- The probe wants a per-(channel, time) lookup, not a
  "give me the latest sample" lookup.

So EPG gets its own table and its own scheduler tick. The probe shape
*does* mirror the other probes (stale-cache gate, `(outcome, details)`
return), so the operator UX is consistent even though the storage is
different.

---

## 7. `epg_show_airing` probe kind — spec

### Rule shape

```python
probe = {
    "kind": "epg_show_airing",
    "provider": "tvmaze",              # required; matches EPG_PROVIDERS
    "channel_mapping_id": "epgm_…",    # required; FK-by-string to epg_channel_mappings.id
    "show_title": "Jeopardy",          # required; substring or exact (see match_mode)
    "match_mode": "substring",         # optional; "substring" (default) | "exact"
    "max_sample_age_seconds": 21600,   # optional; default 6h = one refresh
}
```

### Implementation sketch (drop into `_probes.py`)

```python
def _probe_epg_show_airing(probe: dict) -> tuple[str, dict]:
    """v0.5.x (B17 L2): rule fires when the EPG cache says the named
    show is currently airing on the named channel.

    Rule shape: see docstring above.

    Stale-cache gate: if no row has fetched_at younger than
    `max_sample_age_seconds`, return failure with reason='stale_cache'
    — mirrors the stale-sample gate on all v0.5.23 probes.
    """
    provider = (probe.get("provider") or "").strip()
    mapping_id = (probe.get("channel_mapping_id") or "").strip()
    show_title = (probe.get("show_title") or "").strip()
    match_mode = (probe.get("match_mode") or "substring").strip().lower()
    if not provider or not mapping_id or not show_title:
        return "failure", {
            "reason": "missing provider / channel_mapping_id / show_title"
        }
    try:
        max_age = int(probe.get("max_sample_age_seconds") or 21600)
    except (TypeError, ValueError):
        max_age = 21600

    from app.services.external_epg import latest_airing, get_mapping

    mapping = get_mapping(mapping_id)
    if mapping is None or not mapping.get("enabled"):
        return "failure", {
            "reason": "channel_mapping_not_found_or_disabled",
            "channel_mapping_id": mapping_id,
        }
    if mapping.get("provider") != provider:
        return "failure", {
            "reason": "provider_mismatch",
            "rule_provider": provider,
            "mapping_provider": mapping.get("provider"),
        }

    airing = latest_airing(
        provider=provider,
        channel_id=mapping["channel_id"],
        max_age_seconds=max_age,
    )
    if airing is None:
        return "failure", {
            "reason": "stale_cache_or_no_airing",
            "provider": provider,
            "channel_id": mapping["channel_id"],
            "max_sample_age_seconds": max_age,
        }

    actual_title = (airing.get("show_title") or "")
    if match_mode == "exact":
        match = actual_title.lower() == show_title.lower()
    else:
        match = show_title.lower() in actual_title.lower()

    details = {
        "provider": provider,
        "channel_mapping_id": mapping_id,
        "channel_display_name": mapping.get("display_name"),
        "expected_show": show_title,
        "actual_show": actual_title,
        "episode_title": airing.get("episode_title"),
        "season": airing.get("season"),
        "episode": airing.get("episode"),
        "airing_start": airing.get("airing_start"),
        "airing_end": airing.get("airing_end"),
        "fetched_at": airing.get("fetched_at"),
        "match_mode": match_mode,
    }
    return ("success" if match else "failure"), details
```

### Dispatcher wiring

In `run_probe()` in `_probes.py`, add one line alongside the existing
v0.5.23 integration probes:

```python
if kind == "epg_show_airing":
    return _probe_epg_show_airing(probe)
```

---

## 8. Channel-mapping operator workflow (Phase 2B UI sketch)

### Navigation

Settings → External Sensors → **EPG Channels** tab (new). Sits next to
the existing Sources table.

### Page layout

```
┌──────────────────────────────────────────────────────────────────┐
│ EPG Channels                                       [+ Add channel]│
├──────────────────────────────────────────────────────────────────┤
│ ✓  Spectrum 27 (ABC Washington)   tvmaze   ABC      [edit] [⏸] │
│ ✓  GSN                            tvmaze   GSN      [edit] [⏸] │
│ ✓  Spectrum 35 (ESPN)             tvmaze   ESPN     [edit] [⏸] │
│ ⏸  Discovery (paused)             tvmaze   Discovery [edit] [▶] │
├──────────────────────────────────────────────────────────────────┤
│ Cache freshness: TVMaze last refresh 2026-05-15 14:00 UTC (12m)   │
│                  Next refresh: 2026-05-15 20:00 UTC               │
│                  [Refresh now]                                    │
└──────────────────────────────────────────────────────────────────┘
```

### Add-channel modal (TVMaze)

- **Display name** — required, free text.
- **Provider** — dropdown `[TVMaze]`. Disabled in v1 (only one provider).
- **TVMaze network** — type-ahead against
  `GET /search/shows?q=` to suggest the network slug. Picker shows
  "ABC", "ABC (US)", "ABC News", etc. Operator picks; we store the
  network name as `channel_id`.
- **Local channel number** — optional, free text ("27", "27.1").
- **Region hint** — optional, free text ("Spectrum, Charlottesville VA").

### Refresh-now button

POSTs `/admin/external-epg/refresh` → `external_epg.refresh_all_due()`,
shows a flash with the row count delta. Same UX shape as the existing
"Test poll" button on the Roku/HA sources.

---

## 9. Phased rollout — 4 ships

### Phase 2A — TVMaze fetch, minimal table, one channel
- New tables: `external_epg_cache`, `epg_channel_mappings` (migration).
- New service module: `app/services/external_epg.py` with TVMaze
  fetch + upsert + janitor.
- Scheduler job: `_epg_refresh_job` every 6h.
- Admin API: `GET /admin/external-epg/mappings`, `POST` /
  `DELETE` / `PATCH` (enable/disable). No UI yet — operator
  curls a single mapping to validate the pipeline.
- Admin API: `POST /admin/external-epg/refresh` for manual trigger.
- Sanity test: one mapping for "ABC", manual refresh, query
  `external_epg_cache` and verify Jeopardy shows at the expected
  air time.
- **Estimated effort: 4-5h.**

### Phase 2B — Channel-mapping admin UI
- Settings → External Sensors → EPG Channels tab (new template).
- Add / edit / delete / enable-toggle modals.
- TVMaze network autocomplete (hits `api.tvmaze.com/search/shows` via
  a thin proxy endpoint to avoid CORS issues).
- Cache-freshness banner + refresh-now button.
- **Estimated effort: 2-3h.**

### Phase 2C — `epg_show_airing` probe kind
- `_probe_epg_show_airing` in `_probes.py`.
- Probe-kind contract entry (rules form per-kind field set — this is
  the Phase 2A probe-kind contract from v0.5.25 the operator already
  built).
- Per-kind form fields on `rules/create.html` (provider dropdown,
  channel mapping dropdown, show title text, match-mode radio,
  max-sample-age number).
- Reference card on `rules/edit.html` (matches v0.5.30 pattern).
- Tests: probe with fresh cache + matching show; probe with stale
  cache; probe with no airing; probe with disabled mapping; probe with
  provider mismatch.
- **Estimated effort: 2-3h.**

### Phase 2D — Jeopardy end-to-end demo + pairing pattern docs
- Document the **two-rule pairing pattern**: rule A
  (`roku_app_active app="Spectrum TV"`) + rule B
  (`epg_show_airing show="Jeopardy"`) both have to be in a "failing"
  streak to trigger the action. Today this works via two rules with
  the same action target; if the operator wants AND-logic natively
  inside the watchdog, that's a separate ship (B25-ish "compound
  rules") and out of scope for B17 L2.
- Backlog the "compound rules" item if the two-rule pattern proves
  ergonomically clunky.
- Add a screenshot to BACKLOG.md showing a real Jeopardy event firing
  and the linked action.
- **Estimated effort: 1-2h, mostly docs + curated runtime test.**

### Total: 9-13 h, matching the BACKLOG estimate of ~8-12 h.

---

## 10. Open questions for operator

1. **Schedules Direct $25/yr — pay or not?** Default in this design
   is "no, TVMaze is enough". If after running TVMaze for a couple
   weeks the Jeopardy rule misfires more than tolerable, revisit. Is
   that acceptable, or does the operator want Schedules Direct as a
   second provider from day one (extra ~3h work)?
2. **TVMaze's "ABC" granularity — good enough?** For Jeopardy this is
   fine (airs nationwide-syndicated at 7pm ET; operator's TZ is
   stable). For local news ("Channel 7 News at 6") TVMaze probably
   *won't* index a local affiliate's news block. Is the Jeopardy
   use case the only one Layer 2 needs to solve in v1, or are there
   others?
3. **Compound (A AND B) rules — file as B25?** The two-rule pairing
   pattern works today (both rules wired to the same action target,
   both have to be in failing state when watchdog evaluates). If
   that's too clunky, we'd need real boolean composition in the rule
   model — a real new feature. File it if the operator wants it.
4. **Match mode default — substring or exact?** Substring matches
   "Jeopardy" → "Jeopardy! The College Championships", which is
   probably the right default. Exact matches would force the
   operator to update the rule each season. Confirm.
5. **Janitor retention — 24h after airing_end, or longer?** Longer
   retention helps the event log render past airings ("rule fired
   when S40E142 was on"). Cost is small (~500 rows × 30 days ≈ 15k
   rows). Default in this doc is 24h; bump to 30d if the operator
   wants the event-log integration to render past episodes.
6. **Per-show alias table?** TVMaze might list it as "Jeopardy!",
   Schedules Direct as "Jeopardy! The Greatest of All Time". A
   per-rule `match_mode=substring` covers most of this. A formal
   alias table is overkill for v1.

---

## 11. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| TVMaze data is too coarse for non-Jeopardy use cases | medium — affects scope creep | Ship Phase 2A with one channel, validate Jeopardy works, then talk to operator before expanding |
| Operator never sets up the channel mappings → empty cache → rules silently never fire | medium — silent-fail is worst kind | Settings tab shows "Cache freshness" banner + "0 channels mapped — click here to add" empty state |
| TVMaze API outage breaks all paired rules | low — `stale_cache` returns `failure` cleanly, no false positives | Already designed in (stale gate matches v0.5.23 pattern) |
| Schedules Direct $25 charge accidentally lands in the design without operator approval | low | Explicitly flagged in §3; default is "don't pay" |
| Lineup mapping is regionally fragile (operator moves house) | low | Mapping table is operator-editable; one-time-per-location is acceptable |
| `external_epg_cache` grows unbounded | low | Janitor runs every refresh; UniqueConstraint prevents dupes |
| Probe latency on rule eval (DB query on every tick) | low | Indexed lookup, ~500 rows/channel, sub-millisecond; same shape as the other v0.5.23 probes' latest-sample lookups |

---

## 12. Summary one-liner

**v1 = TVMaze + `epg_channel_mappings` operator-curated whitelist +
`external_epg_cache` 6h-refresh + `epg_show_airing` probe with
stale-cache gate. 4 ships, ~9-13 h total. Schedules Direct deferred
behind a borderline OSS-policy decision the operator should make
explicitly after a few weeks of TVMaze operation.**
