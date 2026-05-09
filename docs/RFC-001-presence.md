# RFC-001: Presence Automation

| Field | Value |
|---|---|
| Status | **Draft** (seeded 2026-05-09 from the firmware/design team's RFC + backend team's reply) |
| Authors | rebooter-droids design + product-firmware team; backend team |
| Targets | rebooter-droids backend, future mobile app, Sonoff S31 firmware |
| Supersedes | — |
| Superseded by | — |

> **About this RFC:** "RFC" here is in the *internal-design-document* sense
> (like a Google or AWS internal RFC), **not** an IETF RFC. Numbered
> internally; lives in this repo for cross-team redlining. Comments
> belong as PRs against this file.

---

## 1. Summary

Add a presence-automation layer on top of the existing rebooter-droids
device-control surface. **Phones publish enter/exit events; backend runs
a rule engine; rules fan out to the existing device command queue;
Sonoff S31 firmware stays a dumb-but-reliable execution endpoint.**

Local-first device behaviour, the locked v0.1 command schemas, and the
rule "all cross-component access via HTTPS API" are unchanged.

## 2. Motivation

We've shown the local + central plumbing works. The next product step
is location-aware automation:

- *When Alice gets home, turn the router on.*
- *When everyone leaves Home for 10 minutes, turn off the Away group.*
- *When all geofences say "away" and no approved Wi-Fi presence remains,
  cycle Office equipment.*

This is a value-add the device firmware should not own. Putting presence
logic on the central server keeps the device firmware narrow, lets us
iterate on automation without firmware updates, and gives us one place
to enforce privacy + retention.

## 3. Scope

In scope for v1 of presence automation:

- **Places** with geofence geometry.
- **Subjects** (phones, optionally laptops/vehicles).
- **Presence events** (enter / exit / heartbeat-of-tracking-status).
- **Subject presence state** (current best guess, derived from events).
- **Automations** — declarative rules tied to places + subjects + groups.
- **Mobile app** publishes events; backend stores + reasons; backend
  emits commands via the existing `commands` queue.

## 4. Non-goals (v1)

- WLAN / router-table presence aggregation. Documented as a v2 addition;
  see §11.
- Continuous GPS tracking, "live location" features, geofence-density
  heat maps, or anything that requires raw-trail retention.
- Multi-tenant presence (different organisations sharing a server).
- Web-based geofencing — the source of truth is the mobile app.
- Voice/Siri/Assistant integration. Possibly v2+.

## 5. Glossary

| Term | Meaning |
|---|---|
| **Place** | Named geographical region with a geofence (lat/lon + radius_m, polygon optional). E.g. "Home", "Office". |
| **Subject** | Tracked principal — almost always a phone tied to a user, possibly a laptop or vehicle. *Not* a Sonoff S31 device. |
| **Presence event** | Append-only record: `entered_place`, `exited_place`, or `presence_capability_state` heartbeat from a subject. |
| **Subject presence state** | Derived current best-known location for each subject. |
| **Occupancy state** | Per-place derived view: who/what is currently there, how stale the data is. |
| **Automation** | Rule of the form "when *predicate*, issue *device command(s)*", evaluated by the rule engine. |
| **Cooldown window** | Minimum time between two automation-issued commands of the same type targeting the same group. |
| **Dry-run** | New automation runs for N hours emitting "would-have-fired" audit rows but no real device commands. |

## 6. Architecture

```
              ┌──────────────────────┐
              │ Mobile app (iOS/Android)
              │ - geofence APIs      │
              │ - capability heartbeat│
              └──────┬───────────────┘
                     │ HTTPS POST /api/v1/presence/events
                     ▼
       ┌──────────────────────────────────────┐
       │ rebooter-droids (Flask)              │
       │   ├── presence_events (append-only)  │
       │   ├── subject_presence (derived)     │
       │   ├── places, subjects, automations  │
       │   └── APScheduler tick (1–5 s):      │
       │        rule engine drains events,    │
       │        evaluates rules, emits cmds,  │
       │        writes audit_events           │
       └────────┬─────────────────────────────┘
                │ enqueue → existing commands table
                ▼
       ┌─────────────────────────────────┐
       │ Device API (unchanged)          │
       │   /api/v1/device/commands       │
       │   /api/v1/device/command-result │
       └────────┬────────────────────────┘
                │ HTTPS poll
                ▼
       ┌─────────────────────────────────┐
       │ Sonoff S31 firmware             │
       │   pure execution — unchanged    │
       └─────────────────────────────────┘
```

Decision: **rule engine lives inside the rebooter-droids container**
for v1 (already running APScheduler). Promotion to a separate worker
service is a v2 decision when rule volume justifies it.

## 7. Data model (proposed)

All `id` columns are ULIDs with a stable type prefix (e.g. `plc_`, `sub_`).
Timestamps are `TIMESTAMPTZ`, server-clock authoritative on receive.

### 7.1 places

| Column | Type | Notes |
|---|---|---|
| id | `varchar(40)` PK | `plc_…` |
| name | `varchar(120)` UNIQUE | "Home", "Office" |
| lat, lon | `double precision` | center point |
| radius_m | `integer` | for circular geofence |
| polygon | `geography(Polygon)` | optional, future-proofing |
| description | `text` | |
| created_at, updated_at | `timestamptz` | |

### 7.2 subjects

| Column | Type | Notes |
|---|---|---|
| id | `varchar(40)` PK | `sub_…` |
| user_id | FK `users.id` | who owns this subject |
| display_name | `varchar(120)` | "Alice's iPhone" |
| subject_kind | `varchar(20)` | enum: `phone`, `laptop`, `vehicle`, `network_presence_source`. Drives privacy classifier. |
| platform | `varchar(20)` | `ios`, `android`, `windows`, `mac`, `other` |
| created_at, updated_at | `timestamptz` | |

### 7.3 subject_credentials

Mirrors `device_credentials`. Mobile app obtains a `subject_token` after
user sign-in; phone uses it to authenticate to the presence API.

### 7.4 presence_events (append-only)

| Column | Type | Notes |
|---|---|---|
| id | `bigserial` PK | |
| received_at | `timestamptz` | server clock |
| subject_id | FK | |
| place_id | FK nullable | null for capability-state events |
| event_type | `varchar(40)` | `entered_place`, `exited_place`, `presence_capability_state` |
| client_timestamp | `timestamptz` | metadata only — never trust the client clock |
| capability_state | `varchar(20)` nullable | `granted`, `denied`, `restricted`, `unknown` |
| accuracy_m | `integer` nullable | reported by mobile OS |
| details | `jsonb` | platform extras |

Index: `(subject_id, received_at DESC)`, `(place_id, received_at DESC)`,
`(event_type)`.

**Retention:** 90-day TTL by default; opt-in admin toggle for
long-term retention. Background pruner runs daily.

### 7.5 subject_presence (derived; updated by rule engine)

| Column | Type | Notes |
|---|---|---|
| subject_id | PK | |
| at_place_id | FK nullable | null = unknown / not at any tracked place |
| state | `varchar(20)` | `present`, `away`, `unknown` |
| since | `timestamptz` | when entered the current state |
| last_event_at | `timestamptz` | freshness signal |
| last_capability_state | `varchar(20)` | track whether tracking is healthy |

### 7.6 automations

| Column | Type | Notes |
|---|---|---|
| id | `varchar(40)` PK | `auto_…` |
| name | `varchar(120)` | |
| description | `text` | |
| trigger | `jsonb` | declarative predicate (see 7.6.1) |
| action | `jsonb` | command spec to fan out to a group/device |
| dry_run_until | `timestamptz` nullable | until this passes, no real commands fire |
| cooldown_seconds | `integer` | per (group, command_type) — see §8.4 |
| is_active | `bool` | |
| created_by_user_id | FK | |
| created_at, updated_at | `timestamptz` | |

#### 7.6.1 trigger shape (initial proposal)

```json
{ "kind": "subject_entered_place",
  "place_id": "plc_…",
  "subject_id": "sub_…"      // omit for "any subject"
}
```

```json
{ "kind": "all_subjects_left_place",
  "place_id": "plc_…",
  "for_minutes": 10
}
```

```json
{ "kind": "no_presence_at_place",
  "place_id": "plc_…",
  "for_minutes": 30
}
```

The rule engine resolves these against `subject_presence`. New trigger
kinds added as we discover real use cases — keep the union small.

## 8. API surface (proposed)

All endpoints under `/api/v1/presence/` and `/api/v1/admin/presence/`.
Versioned **separately** from device command schemas — they are different
contracts evolving on different cadences.

### 8.1 Subject auth

`POST /api/v1/auth/subject-login` (JSON: `email`, `password`) → returns
`subject_token`. Mobile app uses this token in `Authorization: Bearer …`
on every subsequent presence call.

### 8.2 Mobile → server

```
POST /api/v1/presence/events
Authorization: Bearer <subject_token>
{
  "events": [
    { "type": "entered_place",
      "place_id": "plc_…",
      "client_timestamp": "2026-05-09T13:14:15Z",
      "accuracy_m": 35 },
    { "type": "presence_capability_state",
      "capability_state": "granted",
      "client_timestamp": "2026-05-09T13:14:15Z" }
  ]
}
```

### 8.3 Admin

| Method | Path | Role |
|---|---|---|
| `GET, POST` | `/admin/presence/places` | admin+ |
| `GET, PATCH, DELETE` | `/admin/presence/places/{id}` | admin+ |
| `GET, POST` | `/admin/presence/subjects` | admin+ |
| `GET, POST` | `/admin/presence/automations` | admin+ |
| `GET, PATCH, DELETE` | `/admin/presence/automations/{id}` | admin+ |
| `POST` | `/admin/presence/automations/{id}/end-dry-run` | admin+ |
| `GET` | `/admin/presence/events` | admin+ (filtered by subject/place/type/time) |

### 8.4 Cooldowns and conflict resolution

When two automations want to act on the same `(group_id, command_type)`
within the cooldown window:

1. The **later** one wins, the earlier one's pending command is marked
   `superseded`. (Same supersede pattern we already use for firmware
   deployments.)
2. If both fire simultaneously and we cannot order them by the
   millisecond, neither fires; both produce an audit row with
   `result = "conflict"`.

Default cooldown: **60 seconds** for relay-related commands. Admin can
override per-automation.

### 8.5 Dry-run mode

New automations default to `dry_run_until = now() + 24h`. While in
dry-run, the rule engine evaluates triggers and writes
`audit_events.action = "automation.would_have_fired"` rows but issues
no real device commands. Admin "promotes" the rule with the
`/end-dry-run` endpoint, which sets `dry_run_until = NULL`.

### 8.6 Audit integration

Every command issued by the rule engine carries
`audit_events.details.triggered_by = "automation:<id>"`. Existing audit
log surface (UI + API) gains a filter for `triggered_by`.

## 9. Privacy + retention

- **No raw-trail storage by default.** We store enter/exit and
  capability-state events only; no continuous GPS tracks.
- **90-day TTL on `presence_events`**; admin toggle for longer
  retention with a clear "you are now retaining N more days of
  movement history" warning.
- **`subject_kind` drives the privacy classifier** — different defaults
  for `phone` (strict) vs `network_presence_source` (lax).
- **Subject deletion is hard-delete** — when a user is deactivated, all
  their subjects + presence events are purged. (Audit log keeps a
  redacted record: actor + action + timestamp.)
- **Future: encryption at rest** for `presence_events.details` when we
  start receiving location accuracy data. Out of scope for v1.

## 10. Failure modes (decisions captured)

1. **GPS-permission revoked silently** — mobile app heartbeats
   `presence_capability_state`. Absence-of-heartbeat-for-N-hours →
   automations disabled for that subject + admin alert.
2. **Mobile-server network partition** — mobile buffers events with
   monotonic local clock; server accepts past-dated events up to **24h**
   horizon; rule engine decides whether to act on stale state per its
   trigger kind.
3. **Phone clock skew** — server timestamps every event on receive;
   client timestamp is metadata only.
4. **Multi-user occupancy edge cases** — "all left for 10 min" requires
   every tracked subject to have an event newer than 5 minutes. If
   ≥1 subject is `unknown`, the predicate evaluates to `unknown`, not
   `away`, and the automation does not fire.
5. **iOS 20-region geofence cap** — UI warns admins past 15 places,
   blocks at 20 unless they delete one. Mobile app implements
   active-region rotation past that.
6. **Bad rule first deploys** — dry-run default 24h.
7. **Single-node fallback (today)** — until v0.3, tmrwww02's
   `/rebooter/*` is a transparent proxy to tmrwww01. Failure of
   tmrwww01 takes both URLs down. Firmware client should treat
   sustained dual-URL failure as "central is down" rather than "I am
   broken".

## 11. Cross-team responsibilities

### Device firmware (Sonoff S31)

**Owns:** local execution, local web UI, local OTA, central registration,
heartbeat, command poll, command execution, *reporting local
relay-state changes that were initiated locally* (so
`subject_presence` for devices doesn't drift), accepting an ordered
list of `central_base_urls` and falling back per
`docs/DEVICE_INTEGRATION.md`.

**Does not own:** phone geofence interpretation, multi-user occupancy
logic, router presence aggregation, automation rule evaluation.

### Backend (rebooter-droids)

**Owns:** places, subjects, presence events, subject presence,
automations, rule evaluation + command emission, audit log, firmware
rollout orchestration, retention.

### Mobile app

**Owns:** geofence subscriptions, presence event publishing,
capability-state heartbeats, opt-in permission UX, user-facing
automation controls (later), buffering during partition.

## 12. Open questions — decisions needed before lock

| # | Question | Owner |
|---|---|---|
| Q1 | Cooldown default — **60 s** for relay commands? Firmware team to confirm safe minimum after `relay_cycle`. | firmware |
| Q2 | iOS active-region rotation strategy and concrete UX. | mobile |
| Q3 | Confirm `subject_kind` enum is complete: `phone`, `laptop`, `vehicle`, `network_presence_source`. Add a `kiosk` kind? | design |
| Q4 | Do we ever do `apply_config` from an automation, or are automations strictly group/device commands? Default proposal: **only group/device commands in v1**, no `apply_config` from automations. | design |
| Q5 | Multi-user occupancy: how do we handle "guest" phones (someone visits Home, briefly registers, leaves)? Don't enrol guests as subjects? | design |
| Q6 | Mobile app distribution — App Store + Play Store, internal TestFlight first, web-PWA fallback? | mobile |
| Q7 | Locale + timezone: assume UTC server-side, render in user's TZ in mobile + admin UI? | design |

## 13. Out of scope (deferred to v2 or later)

- WLAN / router-table presence aggregation. Requires LAN access from
  the central server, which our locked rule forbids. Path forward when
  it matters: a small "router agent" that pushes presence into our
  HTTPS API, not the central server reaching into routers.
- Voice integration.
- Multi-tenant presence (multiple orgs).
- Continuous-location tracking modes.
- Cross-account sharing ("invite a friend to my Home").
- Presence-driven *configuration changes* (only commands in v1; see Q4).

## 14. Implementation order (recommended)

1. **Backend** — `places`, `subjects`, `subject_credentials`,
   `presence_events`, `subject_presence`, `automations` schemas +
   endpoints.
2. **Backend** — rule engine (APScheduler tick) + audit integration +
   dry-run mode + cooldowns.
3. **Mobile** — single platform first, single-user, single-place,
   geofence-only.
4. **Backend** — multi-user occupancy semantics + retention TTL +
   capability-heartbeat-stale detection.
5. **Mobile** — second platform, multi-place.
6. **WLAN-presence aggregation** + occupancy refinements (separate
   later sprint, possibly its own RFC).

## 15. Decision log

- **2026-05-09** — RFC seeded from firmware/design team's RFC + backend
  team's reply. Status = Draft.

## 16. References

- `docs/SPEC.md` — central server contract.
- `docs/DEVICE_INTEGRATION.md` — device-side handoff (firmware-team
  authoritative for the device half).
- `docs/API.md` — endpoint reference.
- Firmware team's source-of-truth docs: `CENTRAL_SERVER_SPEC.md`,
  `DEVICE_CENTRAL_INTEGRATION_NOTES.md`.

---

**To redline this RFC:** open a PR against `docs/RFC-001-presence.md`
on `github.com/dblagbro/rebooter-droids`. The Decision log (§15) is
where every accepted change records its date + author + summary.
