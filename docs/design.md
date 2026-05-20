# Design

> **The "why" document.** `architecture.md` describes how the code is
> structured *today*; the RFCs (`RFC-00x-*.md`) record individual point
> decisions; `refactor-log.md` is the structural-change journal. This
> file is the connective tissue — the design philosophy and the
> cross-cutting decisions a new contributor (human or AI) needs before
> touching the code.

## 1. The central contract — additive, local-first

Rebooter devices are Sonoff S31 smart plugs running local-first
firmware: they detect a wedged router/appliance and power-cycle it
**on their own**, with no server. `rebooter-droids` is the *optional*
cloud-side companion — group commands, firmware rollouts, fleet
visibility, telemetry.

The non-negotiable design rule that follows: **the hub is additive.**
Every feature must degrade cleanly to "device keeps working" when the
hub is down. The hub never becomes a single point of failure for the
core watchdog function. This is why, for example, the firmware's own
recovery logic is authoritative and the hub only *observes* it (P0 —
absorbing the heartbeat status contract), never the reverse.

## 2. Integration model — three ingestion shapes, one table pair

External-world signals (B17) all land in the same table pair —
`external_sensor_sources` (registry) + `external_sensor_samples`
(append-only readings) — but reach it three different ways. The shape
is chosen by what the *source* naturally supports, never imposed:

| Shape | Who initiates | Examples | Code |
|---|---|---|---|
| **poll** | hub fetches on a cadence | Roku ECP, Home Assistant, NWS weather, iCal, SolarEdge, Enphase Envoy, SNMP router/switch | `external_sensors/_pollers.py` |
| **webhook** | external service POSTs to the hub | Plex, Jellyfin, iOS Shortcuts | `external_sensors/_inbound.py` + `blueprints/api/integrations_webhook.py` |
| **subscriber** | hub holds a long-lived connection | MQTT broker | `external_sensors/_inbound.py` + `services/mqtt_subscriber.py` |

Why not force one shape: a laptop-sleep webhook is fragile (it goes
silent and you can't tell why); polling a Plex sessions API is wasteful
when Plex already pushes. The poll model is the default because it
*inverts the failure mode* — an unreachable source is a visible error,
not silence. Webhook/subscriber are used only where the source is
push-native.

**Dependency discipline:** integrations hand-parse where a stdlib
parser suffices (Roku XML, iCal VEVENT, SNMP via the `net-snmp` CLI) and
add a real dependency only where the protocol genuinely demands it
(`paho-mqtt` for MQTT, `requests` for the sync replicator). This keeps
the dependency chain owned and auditable — see the operator's
"open-source only, own the chain" stance.

## 3. The watchdog rule/probe model

A watchdog rule is `(probe, action, thresholds)`. Each tick, `run_probe`
evaluates the probe to `success` / `failure`; a failure *streak* past
the rule's threshold fires the action (typically `relay_cycle`). The
key semantic convention, consistent across all ~25 probe kinds:

> **`failure` = the actionable condition** — the state that should build
> toward firing the action.

So `power_below` "fails" when watts are *under* threshold (the device
looks dead); `media_session_active` "succeeds" while a movie plays (so
a reboot rule fires only when idle). New probes must follow this — a
probe whose `failure` is the *healthy* state will fire backwards.

Probes are pure reads — no DB writes, no side effects — so they are
trivially testable and safe to run every tick.

## 4. The modality model (RFC-006)

Three telemetry modalities now exist — power (B16), network (SNMP), and
the polled sources (solar/appliance-state/weather/calendar/media). They
deliberately use **two physical stores**: a typed `device_power_samples`
table for the high-rate power firehose, and the generic JSON-payload
`external_sensor_samples` for everything else. This fork is *ratified,
not debt* (RFC-006 Decision 2): typed columns keep power aggregation
fast; JSON keeps adding an integration a zero-migration change.

A common **envelope** (`source_ref` / `modality` / `sampled_at` /
`quality` / `metrics` / `metadata`) unifies them at the *query* layer
without merging the stores. The `modality` tag is the cross-modal join
key. The cross-modal query layer itself (`multimodal.py`) is gated on a
schema review — see RFC-006 §9.

## 5. Cross-cutting decisions

- **Single Gunicorn worker, by design.** APScheduler runs under a
  Postgres advisory lock that guarantees one worker owns the schedule;
  the in-memory rate-limit bucket and the MQTT subscriber threads
  inherit that single-owner guarantee for free. Scaling out means
  adding Redis for shared state first — a deliberate deferred cost.
- **Schema by `create_all()` + `_PENDING_COLUMNS`.** New tables appear
  via `Base.metadata.create_all()` on boot; new columns on existing
  tables via idempotent `ADD COLUMN IF NOT EXISTS` entries in
  `bootstrap.py`. No Alembic — the project favored a zero-ceremony
  path that is safe to re-run every container start.
- **Co-locate UI + API per feature** (`blueprints/admin/<x>.py` holds
  both). A contract change touches one file. The anti-fragmentation
  rule in `architecture.md` §"Module-boundary principles".
- **Subpackage when a service crosses ~2× its soft limit** *and* its
  responsibilities are separable — sliced along whatever axis the
  domain actually varies along (read/write for `devices/`; ingestion
  shape for `external_sensors/`). See `architecture.md`
  §"Service subpackages".
- **Behavior-preserving refactors re-export everything.** A split
  service's `__init__.py` re-exports every externally-referenced
  symbol so import paths never churn; verified by a `create_app()`
  smoke test in the built image.

## 6. Multi-hub posture

Two nodes (`tmrwww01` = www, `tmrwww02` = www2). B11 (RFC-004 Option C)
is **code-complete**: the outbox + emission hooks + replicator daemon
+ HMAC peer auth all shipped in v0.5.45–.50, and `apply_outbox_event()`
gained full create/update last-writer-wins, natural-key reconciliation
(per-entity `_NATURAL_KEY` lookup), site-FK remap, tombstone-replay
protection, and suppress-emission guarding in v0.5.70–.72. The applier
covers the four currently-syncable entity types (`Device`, `Site`,
`Group`, `User`) and is pinned by 18 in-process tests across
`test_v0570_b11_applier.py` + `test_v0571_b11_emission.py` +
`test_v0572_b11_natural_key.py`.

**`sync.enabled` is still `false` by default** — the remaining work
before flipping it on is operator-side, not code-side: a dual-hub
end-to-end preflight against the live pair, and a soak window. See
`docs/runbooks/sync-enable.md` for the playbook; the v0.5.102 ship
added the preflight harness `scripts/sync-dual-hub-preflight.sh`.
Until the operator runs the preflight + commits, the two hubs are
independent-with-replication-ready, not yet active-active in
production.

## 7. Deliberately deferred

- Cross-modal analytics query layer (RFC-006 P3b) — gated on a schema
  review + product confirmation.
- B11 `sync.enabled` flip + soak — gated on operator-run preflight
  (see `docs/runbooks/sync-enable.md`); the code is done.
- Pydantic request schemas (`app/schemas/` reserved) — until ≥3
  endpoints feel validation pain.
- Redis-backed shared state — until the single-worker model is
  outgrown.

## See also

- [`architecture.md`](architecture.md) — current structure + hard rules
- [`refactor-log.md`](refactor-log.md) — structural-change journal
- [`RFC-004-multi-hub-sync.md`](RFC-004-multi-hub-sync.md),
  [`RFC-006-multimodal-ingest.md`](RFC-006-multimodal-ingest.md) — the
  load-bearing design RFCs
- `docs/notes/2026-05-15-hub-team-status-sync-and-plan.md` — the
  P0–P3 execution plan that drove the recent arc
