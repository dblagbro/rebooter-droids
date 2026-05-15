# B17 Remaining Integrations — Design

**Author**: research pass for the next-session "plan + research" charter
(per operator 2026-05-14: *"next up we plan and research the B1 RBAC,
B11 sync, B17 remaining and B17 layer 2 epg"*).
**Date**: 2026-05-15.
**Status**: design only. No code in this ship.
**Audience**: operator (deciding which to greenlight) and the engineer
who picks up B17 Phase 2.

---

## 1. Current state — what's shipped and the polling-model shape

### Live integrations (v0.5.33)

| Kind | Shipped | Transport | Probe kind | Notes |
|---|---|---|---|---|
| `roku` | v0.5.17 | HTTP GET (Roku ECP `:8060`) | `roku_app_active` | LAN-local, no auth |
| `home_assistant` | v0.5.23 | HTTPS GET `/api/states` + Bearer | `ha_state_is` | long-lived access token |
| `weather` | v0.5.23 | HTTPS GET `api.weather.gov/alerts/active` | `weather_alert_active` | unauth, UA required |
| `ical` | v0.5.23 | HTTPS GET `.ics` feed | `ical_event_active` | webcal:// → https:// rewrite |

### Polling-model contract (v0.5.17 shape, locked)

Two tables — **already generic enough** to absorb webhook-style sources
too, with one caveat (below):

```text
external_sensor_sources
  id, kind, display_name, host, port,
  enabled, poll_interval_seconds,
  last_polled_at, last_success_at, last_error,
  config JSON,           -- per-kind extras bag
  created_at, updated_at

external_sensor_samples
  id (bigserial), source_id (FK CASCADE), sampled_at, payload JSON
```

The dispatch shape is:

1. `_external_sensors_job` (APScheduler, 30 s interval, single worker
   guarded by a Postgres advisory lock — `app/__init__.py::_claim_scheduler_lock`)
   calls `poll_all_due()`.
2. `poll_all_due()` selects enabled sources where `now - last_polled_at
   >= poll_interval_seconds`, calls `poll_source(id)` for each.
3. `poll_source` → `_poll_kind` switch on `src.kind` → returns a payload
   dict → row appended to `external_sensor_samples`.
4. Watchdog probes (`app/services/watchdog_runtime/_probes.py`) read
   `latest_sample(source_id, max_age_seconds=N)` and fail the rule with
   `reason='stale_sample'` if the most recent sample is older than the
   probe-configured cap. **This is the non-negotiable safety rail** —
   any new integration MUST set a sensible `max_sample_age_seconds`.

### What the shape gives us for free
- Per-kind config bag via `config JSON` — no schema migration for a new
  kind that only needs new credentials/URLs.
- Stale-sample protection (operator's #1 concern: "30-minute-old
  'Spectrum TV active' triggers a power-cycle they didn't expect").
- Admin UI: enable/disable/probe/delete — already routes through
  `/app/settings/integrations` blueprint.
- Audit-log entries for every CRUD action.

### What it does NOT give us
- **A way to receive inbound events** (no Flask blueprint accepts
  unauthenticated POST + writes a sample). Plex, Jellyfin, iOS
  Shortcuts, Apple Home — all need an inbound endpoint.
- **A way to hold a long-lived connection** (APScheduler is interval-
  based; MQTT needs a persistent subscriber loop).
- **OAuth refresh-token mechanics** (Google Calendar needs a consent
  flow + refresh-token rotation; no integration today has any auth
  fancier than a static token in `config.token`).

These three gaps are exactly what the remaining candidates need.
The polling-shape extension goes from a "drop a `_poll_<kind>` branch"
exercise to **three different shapes** depending on the integration.

---

## 2. Architectural patterns — three shapes, not one

| Shape | Examples in this design | Fit with v0.5.17? | Net new code |
|---|---|---|---|
| **Poll** | Solar (Enphase local API, SolarEdge cloud), Computer activity (ping host) | Drop a `_poll_<kind>` branch | tiny — ~50–150 LOC per kind |
| **Webhook (inbound)** | Plex, Jellyfin, iOS Shortcuts, Apple Home | New blueprint + auth + `samples` write path | medium — ~150 LOC generic + ~30 per kind |
| **Subscriber (long-lived)** | MQTT | New worker thread inside scheduler process | larger — ~250 LOC + ops decision |
| **OAuth (poll + auth dance)** | Google Calendar | Poll + new consent/refresh handling | medium — ~200 LOC plus credential rotation |

---

## 3. Per-candidate design

### 3.1 MQTT pub/sub

**Architectural pattern**: long-lived subscriber. **NOT** a fit for the
poll model.

**Why it's different**: MQTT is push, not pull. We need a connection
open continuously to the operator's broker (Mosquitto — OSS-only per
operator policy 2026-05-09). Every message arrives async, can land at
any time, and disconnect/reconnect is a first-class concern.

**Three implementation options**:

| Option | Pros | Cons |
|---|---|---|
| **A. Thread inside the scheduler process** (paho-mqtt `loop_start()` in a daemon thread, gated by the same advisory lock) | Reuses the single-worker guarantee. No new container. Shares DB connection pool. Cheapest ops. | Crashes/reconnect cycles share the gunicorn worker lifetime. Memory pressure on whichever worker holds the scheduler lock. paho-mqtt 1.6+ thread-safe but care needed around DB session reuse from the network thread. |
| **B. Sidecar container** (separate `mqtt-bridge` service, writes to hub via internal API) | Process isolation. Can be restarted without bouncing the hub. Crash budget independent. | New container, new compose entry, new IPC surface. Ops complexity rises for a feature whose ROI we don't yet know. |
| **C. Defer entirely** (skip until an operator request lands) | Zero work. | Operator listed MQTT explicitly in the candidate list; expected to land. |

**Recommendation: Option A** (in-process thread), with a clean
abstraction so it could be lifted to a sidecar later. Rationale:
- We already require a single-worker scheduler (advisory lock) — the
  long-lived thread inherits that guarantee for free.
- paho-mqtt's `loop_start()` runs in its own daemon thread; the
  on-message callback can push a payload onto a `queue.Queue` and a
  separate consumer (or even the same callback with a per-call
  `session_scope()`) writes the sample row.
- Mosquitto can run on the same host as a separate compose service or
  on the operator's existing HA host; we do NOT bundle a broker.
- We can graduate to a sidecar (Option B) if and only if we see worker
  thrash or memory pressure.

**Schema fit**: clean. `kind='mqtt'`, `host`+`port` = broker
address, `config = {"username": ..., "password": ..., "topics":
["sensors/+/state", ...], "client_id": "rebooter-droids-XXXX"}`.
Bullet point: passwords get the same `_redact_config` "********"
treatment as the HA token.

**Sample shape**: every received message appends a sample with
`payload = {"topic": "sensors/door/state", "msg": "open",
"received_at": ...}`. Probe-side aggregation reads the most recent
sample per `(source_id, topic)` — meaning we need a new query path
beyond `latest_sample(source_id)` because MQTT messages span many
topics under one source.

**New schema needed**: ideally yes. Two cheap options:
- (a) Add `payload.topic` and index `(source_id, sampled_at desc)`
  filtering in Python — works for low message volume (< 100 msg/min).
- (b) Add `external_sensor_samples.topic_key VARCHAR(200)` migration +
  index `(source_id, topic_key, sampled_at desc)`. Cleaner; needed if
  the operator's topics are chatty.

Defer to (a) on first ship, migrate to (b) if volume warrants.

**Watchdog probe**: `mqtt_topic_equals`. Rule shape:
```json
{ "kind": "mqtt_topic_equals",
  "source_id": "ext_…",
  "topic": "sensors/door/state",
  "expected_value": "open",
  "max_sample_age_seconds": 300 }
```

**Effort estimate**: **6–9 hours** (cheap path) / **10–14 hours**
(with topic_key migration + reconnect-backoff + LWT-aware tests).
The 4–6 h backlog estimate is optimistic given how different this is
from the poll shape.

**Operator value**: 1 sentence — *"if the magnetic door sensor on the
garage publishes `door/state = open`, run the watchdog rule that
power-cycles the garage opener."* Pairs strongly with Tasmota/ESPHome
devices the operator likely already runs in the same HA ecosystem.

**Recommended priority**: **Medium**. High ceiling, medium effort, but
the same operator already runs HA — and HA can republish anything
worth caring about as an HA entity, which our existing `ha_state_is`
probe already consumes. **MQTT is partially redundant with HA today.**
Worth shipping for operators who don't run HA; not the most urgent
next pick.

---

### 3.2 Plex / Jellyfin (webhooks)

**Architectural pattern**: inbound webhook. **NOT** a fit for the poll
model (would have to poll a sessions API; both Plex and Jellyfin
already push webhooks, which is cleaner and lower-latency).

**Why it's different**: Plex/Jellyfin push a JSON payload to a URL when
playback starts/stops/pauses. There's no need for us to poll — we just
need an authenticated inbound endpoint and a way to translate the
push into a row in `external_sensor_samples`.

**Schema fit**: clean — re-use both tables. Source `kind='plex'` or
`kind='jellyfin'`, `config = {"webhook_secret": "<random hex>",
"server_name": "Plex-Living-Room"}`. `host`/`port` empty (we don't
reach out — they reach in).

**The "last sample" semantics shift**: with poll, the sample reflects
the world right now. With webhook, the sample is **the most recent
event** — `playback.started` may be 4 hours old while playback is
still happening. So probes need to:
- Look at the latest sample's *event type* (started / playing / stopped),
- Optionally look at `payload.media_duration_remaining` if the source
  sends it,
- Apply a "presumed-still-playing" window of e.g. movie-length-or-
  default-180-minutes.

**Watchdog probe**: `plex_session_active` (kind covers both Plex and
Jellyfin; we just match on `source.kind in (plex, jellyfin)`). Rule
shape:
```json
{ "kind": "media_session_active",
  "source_id": "ext_…",
  "presumed_duration_seconds": 7200,
  "max_sample_age_seconds": 7200 }
```

If the latest sample is a `media.play`/`playback.start` younger than
`presumed_duration_seconds`, success.

**New schema needed**: no DB migration required. The generic webhook
inbound endpoint (§4) handles routing.

**Effort estimate**: **3–5 hours** for Plex + Jellyfin in one ship,
assuming the generic webhook endpoint (§4) lands first.

**Operator value**: *"don't reboot the living-room AppleTV/Roku/AVR
while a movie is playing on Plex"* — pause-watchdog-while-active
rules. High signal, low false-positive.

**Recommended priority**: **High** as the second integration to ship,
because (a) effort is low once the webhook framework is in, (b) it
exercises the webhook framework with two flavors validating the
abstraction, and (c) operator-recognized value (whole-home media).

---

### 3.3 Google Calendar (OAuth)

**Architectural pattern**: poll, but **gated by an OAuth flow** with
refresh-token rotation.

**Why partially fits**: the poll itself is fine — fetch
`/calendar/v3/calendars/primary/events?timeMin=now` every 60–300 s,
write a sample, done. The hard part is auth: Google requires a one-
time consent flow that mints an access token (60 min) + refresh token,
and we need a place to put both. The closest in-tree precedent is …
nothing — every other integration uses a static credential.

**OSS client**: `google-auth-oauthlib` and `google-api-python-client`
are Apache-2.0, fine under the open-source-only constraint
(operator-locked 2026-05-09).

**Schema fit**: needs minor work. `config` JSONB can hold
`{client_id, client_secret, refresh_token, access_token,
access_token_expires_at, calendar_id}`, but rotation requires the
poller to UPDATE `config` on every refresh. That works (the
`external_sensor_sources.config` column is already mutable in
`set_enabled`/`create_source` paths — just needs a service helper
`update_config(source_id, patch)` that doesn't reset poll_interval).

We also need a one-time consent route — operator clicks "Connect
Google Calendar" → we redirect to Google → callback at
`/app/settings/integrations/google/callback` → exchange code → store
refresh_token. This is two new routes, not just a `_poll_google_cal`
branch.

**Watchdog probe**: re-use `ical_event_active`. Once the poll converts
Google Calendar events into the same shape iCal produces, the probe
doesn't need to know which calendar back-end produced the sample. **Big
architectural win** — both calendars feed one probe.

**Effort estimate**: **6–8 hours**. OAuth consent + refresh + token
storage + the `update_config` service helper + a tested polling
branch.

**Operator value**: *"if a work meeting is on the calendar, pause the
office-power-cycle rule"* OR *"if 'kids-bedtime' calendar event is
active, force-off the TV"*. Calendar-driven home automation is a
classic; pairs naturally with iCal for non-Google users.

**Recommended priority**: **Medium-low**. The operator already has
**iCal** working (v0.5.23) and Google Calendar can export an iCal
secret URL that the existing `ical` kind consumes today. **OAuth
buys us nothing the iCal `secret.ics` URL doesn't.** Ship only if
operator wants the live-edit responsiveness (Google Cal events
appear within seconds; iCal cache can lag the publish by minutes).

> **Open question for operator**: are you using Google Calendar's
> "secret iCal URL" with the `ical` integration today? If yes, this
> can be deferred indefinitely.

---

### 3.4 Solar (Enphase / SolarEdge)

**Architectural pattern**: poll. **Fits the v0.5.17 shape cleanly.**
Two flavors:

| Vendor | Endpoint | Auth | Cadence |
|---|---|---|---|
| Enphase Envoy (local) | `http://<envoy>:80/production.json` | local-only on older Envoys; newer (D7+) need a JWT minted from enlighten.enphaseenergy.com | 60–300 s |
| Enphase cloud | `https://api.enphaseenergy.com/api/v4/systems/{id}/summary` | API key + OAuth refresh | 5–15 min (rate-limited; 10/min) |
| SolarEdge cloud | `https://monitoringapi.solaredge.com/site/{id}/overview` | static `api_key` query param | 5 min (300/day rate limit) |

**Recommendation**: ship **SolarEdge cloud + Enphase Envoy local** as
two separate `kind` values. They share a probe but not a poller. Defer
Enphase cloud (the local Envoy gives the same data without OAuth
complexity).

**Schema fit**: clean. `kind='solaredge'` `config = {"site_id":
"12345", "api_key": "…"}`. `kind='enphase_envoy'` `host` = envoy IP,
`config = {"jwt": "…optional…"}` (legacy Envoys don't need the JWT).

**Watchdog probe**: `solar_production_above` and
`solar_production_below`. Rule shape:
```json
{ "kind": "solar_production_above",
  "source_id": "ext_…",
  "threshold_w": 3000,
  "window_seconds": 600,
  "max_sample_age_seconds": 1800 }
```
Mirrors the B16 `power_above`/`power_below` shape — small win for
operator-mental-model consistency.

**Effort estimate**:
- **SolarEdge alone**: 2–3 hours (static API key, clean JSON, no auth dance).
- **Envoy local alone**: 3–4 hours (older firmware vs newer JWT path
  is the wrinkle).
- **Both + shared probe**: 5–6 hours.

**Operator value**: *"if solar is exporting > 3 kW to the grid, turn
on the water heater"* — pairs **strongly** with B16 power monitoring
(power monitoring measures *load*, solar measures *generation*; the
delta is the actual import/export). The most synergistic with what
just shipped.

**Recommended priority**: **HIGH — best next-ship candidate**.
Rationale below in §6.

---

### 3.5 iOS Shortcuts / Apple Home webhooks

**Architectural pattern**: inbound webhook. Identical shape to
Plex/Jellyfin (§3.2) — operator sets up a Shortcut on their iPhone
that POSTs to `/api/v1/integrations/webhook/<source_id>` with a
shared secret.

**Why it's a strict subset of §3.2**: same generic webhook endpoint,
same `config.webhook_secret`, no new schema. The only kind-specific
work is **the payload shape** the operator's Shortcut sends — and we
can let them send literally anything, because:
- The generic endpoint writes `payload = request.json` (or `request.form`)
  verbatim into `external_sensor_samples.payload`.
- A new probe `webhook_field_equals` does substring/equality matching
  against any JSON path in the payload — operator writes once, runs
  forever.

**Watchdog probe**: `webhook_field_equals`. Rule shape:
```json
{ "kind": "webhook_field_equals",
  "source_id": "ext_…",
  "field_path": "trigger",
  "expected_value": "leaving_home",
  "max_sample_age_seconds": 600 }
```

**Effort estimate**: **2–3 hours** once §4 lands (literally just a new
`kind='ios_shortcuts'` registration UX + the generic probe).

**Operator value**: *"when I tap a Shortcut on my iPhone called
'Movie Time', POST `mode=movie`, and our hub disables the kitchen-
appliance-power-cycle rule for 3 hours."* Cheap, flexible, very
iPhone-native.

**Recommended priority**: **Medium-high** — ride coattails of §3.2.
Ship in the same release once webhook framework exists.

---

### 3.6 Computer activity (wake/sleep ping)

**Architectural pattern**: poll. **Best-fit option = TCP ping a known
host port** (e.g. SSH 22, or the OS's own service like macOS sharing
on 88) and treat reachable = awake.

**Why poll, not webhook**: webhook-from-laptop is fragile (laptop
sleeps, network disconnects, the agent quietly stops sending). Polling
inverts the failure mode — if the laptop is unreachable, we know.

**Schema fit**: cleanest path is `kind='host_reachable'`,
`host`=hostname or IP, `port`=TCP probe port (default 22).
`config = {"probe_kind": "tcp"}` for future-proofing (we may want
"ICMP ping with stale-MAC detect" later).

**Wait** — we already have a `_probe_tcp` in `watchdog_runtime/_probes.py`
that does this for watchdog rules. **The question is whether we want
this as a poll-and-sample external source, or just a direct watchdog
probe**.

Two designs:
- **(A) Direct watchdog probe** — `kind='tcp'` already exists; just add
  a `kind='host_awake'` probe alias and call it a day. **Zero new code
  besides docs.**
- **(B) External-sensor source** — full external_sensor_sources row,
  sampled history, configurable cadence. Useful if operator wants to
  see "when was the laptop awake?" in the integrations UI.

**Recommendation: (A) — direct watchdog probe**. Operator value here
is rule-firing, not history. The integrations table is already
crowded; adding a row for "is the desktop on" is overkill.

**Effort estimate**: **1 hour** (alias + docs + test).

**Operator value**: *"if the work laptop is on, don't reboot the
office switch"* — minor, but lovely as a small win.

**Recommended priority**: **Low**. Tiny, valuable, ship as a one-line
addition any time. Doesn't need its own session.

---

## 4. Generic webhook inbound pattern

Three of the six candidates (Plex, Jellyfin, iOS Shortcuts) and the
adjacent Apple Home category all want the same shape: an inbound POST
endpoint that authenticates and writes a sample.

### Endpoint

```text
POST /api/v1/integrations/webhook/<source_id>
  Header: X-Webhook-Secret: <secret from source.config.webhook_secret>
  Body:   application/json  (or form-encoded for Plex's multipart payloads)
```

### Auth

Per-source shared secret stored in `source.config.webhook_secret`,
checked constant-time against the `X-Webhook-Secret` header. Secret is
shown once on the integration's add page (like a generated API key) +
revealable via a 👁 reveal endpoint (same pattern as the hub uses for
HA tokens). For Plex specifically, which signs payloads with an HMAC,
we'd accept either auth scheme; first ship: shared-secret only.

### Body handling

```python
@bp.post("/api/v1/integrations/webhook/<source_id>")
def webhook_inbound(source_id: str):
    src = lookup_source(source_id)
    if not src or not src.enabled:
        abort(404)
    if not _const_time_eq(request.headers.get("X-Webhook-Secret"),
                          (src.config or {}).get("webhook_secret")):
        abort(401)
    if request.is_json:
        payload = request.get_json(silent=True) or {}
    else:
        # Plex posts multipart with a `payload` field — easy peasy.
        payload = json.loads(request.form.get("payload") or "{}")
    payload["_received_at"] = utc_iso_now()
    payload["_remote_addr"] = request.remote_addr
    append_sample(source_id, payload)
    return ("", 204)
```

### Auth shape rationale

- **No CSRF token** — these endpoints are called by external systems
  with no browser context. They need to be exempt from the admin CSRF
  middleware (`_admin_csrf` doesn't apply to `/api/v1/*` today; good).
- **Per-source secret, not global** — operator may give Plex one
  secret and Apple Home a different secret without coupling them.
- **Constant-time compare** — `secrets.compare_digest()` is stdlib,
  no dependency.

### Storage cap

To prevent a runaway sender (or a malicious one with the secret
leaked) from filling the DB, the inbound handler should:
1. **Rate-limit** per source — e.g. max 60 inbound/min, drop excess
   with 429.
2. **Cap payload size** — reject `Content-Length > 64 KiB`.
3. **Retention** — add to the existing sample-pruning job (today
   `external_sensor_samples` has no TTL; **flag for cleanup**).

> **Operator question**: do we want an explicit retention policy on
> `external_sensor_samples` regardless? With Roku ECP at 30s × hundreds
> of days, the table grows. (Today: not a problem; tomorrow with
> webhooks/MQTT pumping in: real risk.)

### Schema impact

**None** — the existing `external_sensor_sources.config` holds
`webhook_secret`. **Caveat**: the inbound writer needs a new service
helper `append_sample(source_id, payload)` (or `record_webhook_event`)
that mirrors `poll_source`'s flush-row logic without going through the
`_poll_kind` switch. ~30 LOC.

The `last_polled_at` semantics also need a tweak — for webhook sources
we want `last_received_at` set on inbound; the existing column is
fine, just rename the field's *meaning* in webhook context (or add a
peer column if the operator cares about distinguishing poll-success
from receive-event in the UI). First ship: re-use `last_polled_at`.

---

## 5. MQTT — decision recap

Repeating §3.1's decision for visibility:

- **Recommendation**: **Option A** — in-process subscriber thread inside
  the scheduler process, gated by the existing advisory lock that
  guarantees one-worker-only scheduling.
- **Why not sidecar**: ops complexity not justified for a feature
  whose ROI overlaps significantly with the already-shipped HA
  integration. Reserve sidecar for if/when we see worker thrash.
- **Why not defer**: it's on the operator's list. But it should be
  **low-medium priority** — see §3.1 and §6.

If we ship Option A and observe issues, the lift to Option B is
mechanical because:
- The MQTT subscriber writes to `external_sensor_samples` via a
  thin service function;
- A sidecar would just call the same service over an internal HTTP
  endpoint instead of in-process. **The data path doesn't change.**

---

## 6. Sequencing recommendation

The next 3 sessions, in order of value-per-hour:

### Ship 1 — **Solar (SolarEdge + Enphase Envoy local)** [HIGH PRIORITY]
- Effort: 5–6 h.
- Why first: cleanest fit to v0.5.17 shape (drop-in `_poll_kind`
  branches, no new architecture). Synergizes with the just-shipped
  B16 power monitoring (load × generation = export). High operator
  value, low risk, completes a coherent "energy dashboard" story.
- Probe: `solar_production_above` / `solar_production_below` (mirrors
  B16's `power_above` / `power_below`).
- No new architecture; no schema migration.

### Ship 2 — **Generic webhook framework + Plex + Jellyfin + iOS Shortcuts** [MEDIUM-HIGH PRIORITY]
- Effort: 8–11 h (framework ~4 h, Plex ~2 h, Jellyfin ~1.5 h,
  Shortcuts ~1.5 h, retention/rate-limit ~2 h).
- Why second: framework cost amortizes across three integrations; one
  ship delivers three operator-visible features. Validates the
  webhook auth model.
- Probes: `media_session_active`, `webhook_field_equals`.
- Schema migration: **optional**, only `external_sensor_samples`
  retention policy (recommend yes).

### Ship 3 — **MQTT subscriber** [MEDIUM PRIORITY]
- Effort: 6–9 h (cheap path) / 10–14 h (with topic_key migration).
- Why third: the most architecturally different (long-lived thread,
  reconnect/backoff, queue handoff to DB writer). Land after the
  framework ships are clean to keep the architectural variance
  isolated.
- Probe: `mqtt_topic_equals`.
- Schema migration: deferred. First ship uses Python-side filtering
  on `payload.topic`. Add `topic_key` column only if message volume
  warrants.

### Ship 4 (optional) — **Computer activity (`host_awake` probe alias)** [LOW PRIORITY]
- Effort: ~1 h. Just an alias on the existing TCP probe.
- Why optional: tiny enough to bundle with anything.

### Ship 5 (defer / drop) — **Google Calendar OAuth**
- Effort: 6–8 h.
- Recommendation: **defer**, since iCal-secret-URL is functionally
  equivalent today. Reopen if operator explicitly requests live-edit
  responsiveness or multi-calendar OAuth.

---

## 7. Open questions for operator

1. **Google Calendar**: do you use the "secret iCal URL" with our
   existing `ical` integration today? If yes, we can drop the OAuth
   path indefinitely.
2. **MQTT broker host**: do you already run Mosquitto somewhere (e.g.
   on the HA host)? Or do we need to add a Mosquitto compose service
   to the rebooter-droids stack? (Strong preference: use the HA-side
   broker if it exists; don't add another OSS daemon.)
3. **Solar vendor**: Enphase, SolarEdge, both, neither? If only one,
   that's where we cut the design effort in half.
4. **Plex vs Jellyfin**: do you run one, both, or neither? Both
   webhook formats are similar but not identical — confirming this
   trims a small amount of work.
5. **iOS Shortcuts**: confirm you want this. It's a strict subset of
   the generic webhook framework, so it costs ~1 h *if* §3.2 lands.
6. **Sample retention**: do you want a TTL policy on
   `external_sensor_samples`? Especially relevant once webhook/MQTT
   ship since they can spike volume.
7. **Webhook secret rotation**: should the integrations page let
   operator rotate a webhook secret without deleting + re-creating the
   source (which would invalidate Plex's saved URL)?

---

## 8. Effort summary

| Ship | Integrations | Hours | Adds new pattern? |
|---|---|---|---|
| 1 | SolarEdge + Enphase Envoy | 5–6 | No — poll only |
| 2 | Webhook framework + Plex + Jellyfin + iOS Shortcuts | 8–11 | **Yes — inbound webhook** |
| 3 | MQTT | 6–9 (or 10–14 with migration) | **Yes — long-lived subscriber** |
| 4 | `host_awake` alias | ~1 | No |
| 5 | Google Calendar (DEFER) | 6–8 | Yes — OAuth refresh |

**Total if all five ship**: 26–39 hours (≈ 4–5 dev sessions).
**Total if §5 deferred**: 20–31 hours (≈ 3–4 dev sessions).
**Total for the recommended high-value subset (1 + 2 + 4)**: 14–18 hours.

---

## 9. Architectural fit verdict (one paragraph)

The v0.5.17 polling-model shape is **excellent** for Solar (Ship 1)
and for the `host_awake` alias (Ship 4). It is **insufficient** for
Plex/Jellyfin/iOS Shortcuts (need inbound webhook handler — Ship 2)
and **inadequate** for MQTT (needs long-lived subscriber — Ship 3).
Google Calendar fits the poll model fine but adds OAuth refresh
complexity not present anywhere else in the codebase. Two of the six
candidates need genuinely new architecture; three need only a new
`_poll_<kind>` branch; one is a one-line alias. The
`external_sensor_sources` + `external_sensor_samples` table pair
absorbs all six without schema change, modulo an optional
`topic_key` column for MQTT and an optional sample-retention TTL
applicable to all kinds. **The operator's hope of "all share the
external_sensor_samples table pattern" holds — but the polling-tick
half of the pattern doesn't.**
