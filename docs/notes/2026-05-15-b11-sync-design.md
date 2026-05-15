# B11 Multi-Hub Sync (RFC-004 Option C) — Implementation Design

| Field | Value |
|---|---|
| Date | 2026-05-15 |
| Status | **Design** (engineering implementation spec) |
| Source RFC | `docs/RFC-004-multi-hub-sync.md` §10b (Option C, locked 2026-05-10) |
| Backlog item | `docs/BACKLOG.md` § B11 |
| Live version at design time | `v0.5.34` |
| Gates | **B1 RBAC** must ship the `(site, group, device)` scope claims first |
| Owner | rebooter-droids backend |

This doc turns the RFC-004 §10b decisions into an execution-ready plan
the engineering team can implement against. It deliberately covers
schema, daemon shape, endpoint contract, idempotent-apply algorithm,
sizing, phased rollout, test strategy, and open questions — in that
order — so a future session can pick up at any phase boundary without
re-deriving the architecture.

No code lands from this doc. **Implementation starts at Phase 1 only
after operator sign-off.**

---

## 1. Current state summary

### 1.1 Today's deployment shape

- `https://www.voipguru.org/rebooter`  → nginx on tmrwww01 → local
  `rebooter-droids:8090` container.
- `https://www2.voipguru.org/rebooter` → nginx on tmrwww02 →
  cross-host `proxy_pass` to **tmrwww01's** rebooter-droids over the
  shared LAN.
- **One Postgres, one Flask container, two URLs.** RFC-004 §1 verified
  this end-to-end. A device registered via either URL is immediately
  visible from the other.
- Settings → Sync tab (v0.5.16) documents this and links to RFC-004
  §10b for the future architecture.

### 1.2 Existing infrastructure relevant to the sync work

| Concern | Today's state | Relevance |
|---|---|---|
| Postgres pool | SQLAlchemy default (5 connections + 10 overflow), `pool_pre_ping=True` (`app/db.py`) | Replicator daemon + outbox-write hooks will increase concurrent connection pressure |
| Gunicorn | **1 worker, gthread × 8 threads**, single-worker by design (`gunicorn.conf.py`); APScheduler advisory-lock pattern + in-mem rate-limit bucket assume it | Replicator must run inside that worker (not a sibling Gunicorn worker) OR as a sidecar with no shared in-mem state |
| APScheduler | 5 jobs already: `expire_commands` 30s, `watchdog_tick` 10s, `schedule_tick` 30s, `external_sensors_tick` 30s, `power_rollups_daily` 02:00 UTC (`app/jobs/scheduler.py`) | Replicator pull is the natural 6th job |
| Audit pattern | `app/services/audit.py` + `app/models/audit.py`: `AuditEvent` row per mutation; ~102 callsites today | Outbox writes must hook the same callsites |
| HMAC infra | Coordinator network uses `openssl dgst -sha256 -hmac "$COORDINATOR_HMAC_KEY"` over `"$host$role$hour"` (per `installer.sh`). HMAC key is in `coordinator.env` (`COORDINATOR_HMAC_KEY`) | Replicator auth reuses **the same pattern**, NOT necessarily the same key — sync-specific secret is preferred |
| RBAC scope | v0.5.x has `role_bindings` table with `(user, role, scope_type, scope_id)` where `scope_type ∈ {global,site,group,device}`. Devices/groups/watchdog rules carry `site_id`. **B1 enforcement is shadow-mode** (logs would-have-denied). | Outbox events must carry per-row site/group scope claims; the applier on the receiver enforces before writing |
| Advisory locks | `pg_advisory_lock(4242117309)` for schema bootstrap (`app/services/bootstrap.py`) | Replicator can claim its own advisory lock to guarantee one-replicator-per-DB |
| `ulid` ids | All admin rows use `ULID()` prefixed (`dev_…`, `grp_…`, `rb_…`). ULIDs are monotonic + globally unique | Idempotent apply keys on these natively; no UUID collision risk |

### 1.3 What's locked from RFC-004 §10b

- **Option C**: application-level event-log sync, active-active, symmetric peers.
- Per-hub `outbox_events` append-only table.
- Replicator daemon polls peers' `/api/v1/sync/since?seq=<n>` over HTTPS.
- HMAC-signed bearer auth reusing coordinator HMAC pattern.
- Idempotent apply on UUID-keyed rows; LWW on `event.at`.
- Per-record audit retains both versions.
- Tombstone rows in `outbox_events` for deletes; receiver writes a
  `tombstones` row + refuses to re-create the UUID.
- Steady-state latency target: **~1–3 s end-to-end**.
- No failover procedure — symmetric peers.

---

## 2. `outbox_events` table

### 2.1 DDL

```sql
CREATE TABLE outbox_events (
    -- Local monotonically-increasing sequence number. The single
    -- coordinate the replicator polls against ("give me everything
    -- since seq=N from your outbox"). BIGINT (not SERIAL with a
    -- type alias) so we can run > 2B events per hub without panic.
    seq            BIGSERIAL PRIMARY KEY,

    -- Globally unique event id. Used by the receiver for idempotent
    -- apply (write once, ignore duplicates). ULID prefixed `oxe_` to
    -- match the rest of the codebase.
    event_id       VARCHAR(40) NOT NULL UNIQUE,

    -- The origin hub that produced this event. Receivers tag applied
    -- events with this so they can skip echoes ("don't re-emit an
    -- outbox event for a write that originated from a peer").
    origin_hub     VARCHAR(40) NOT NULL,

    -- Wallclock time on the origin hub when the underlying mutation
    -- happened. This is the LWW resolution key. Stored as TIMESTAMPTZ
    -- with microsecond precision; tie-break by (origin_hub, event_id)
    -- if two hubs somehow record identical timestamps.
    event_at       TIMESTAMPTZ NOT NULL,

    -- What changed.
    --  - 'mutation'    — INSERT or UPDATE of a row (payload = row)
    --  - 'tombstone'   — DELETE of a row (payload = {target_id})
    --  - 'audit'       — audit_events row (payload = audit row)
    kind           VARCHAR(20) NOT NULL,

    -- The table-level domain the event refers to. Lets the applier
    -- dispatch to the right idempotent-apply handler.
    -- Examples: 'devices', 'groups', 'sites', 'enrollment_tokens',
    -- 'watchdog_rules', 'schedules', 'users', 'role_bindings',
    -- 'audit_events', 'firmware_releases'. NOT every table — heartbeat
    -- + power-sample firehoses are excluded; see §2.4.
    aggregate      VARCHAR(40) NOT NULL,

    -- The PK of the row this event refers to. ULID for admin rows;
    -- BIGINT cast to string for `audit_events`. Used as the idempotent-
    -- apply key.
    target_id      VARCHAR(40) NOT NULL,

    -- B1 scope claims: which (site, group, device) ownership the row
    -- carries at origin. Receiver enforces scope before applying. NULL
    -- for global-scoped rows (e.g. system-wide runtime_settings).
    scope_site_id    VARCHAR(40),
    scope_group_id   VARCHAR(40),
    scope_device_id  VARCHAR(40),

    -- Full row payload as JSON. The applier upserts based on this.
    -- Tombstones have payload = {}.
    payload        JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- If this event came from a peer (rather than a local mutation),
    -- the origin's event_id and the local seq at which we applied it.
    -- A local mutation has applied_from_event_id IS NULL.
    applied_from_event_id   VARCHAR(40),

    -- Best-effort: who/what initiated the change (user.id, "system",
    -- "watchdog", "schedule", "replicator:tmrwww01"). For diagnostics
    -- only — the LWW resolver does NOT use this.
    actor          VARCHAR(80),

    -- Created-at is implicit (= event_at for local; insertion-time for
    -- applied-from-peer is meaningful for lag analysis).
    inserted_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Replicator query path: "everything since seq=N, ordered".
CREATE INDEX ix_outbox_seq ON outbox_events (seq);

-- Applier dedupe path.
CREATE UNIQUE INDEX ix_outbox_event_id ON outbox_events (event_id);

-- LWW + per-aggregate audit-trail lookups.
CREATE INDEX ix_outbox_aggregate_target ON outbox_events (aggregate, target_id, event_at DESC);

-- Retention sweep.
CREATE INDEX ix_outbox_inserted_at ON outbox_events (inserted_at);
```

### 2.2 Retention policy

- **Default retention: 30 days.** Configurable via runtime setting
  `sync.outbox_retention_days` (DB-backed; env fallback
  `REBOOTER_SYNC_OUTBOX_RETENTION_DAYS`). Same pattern as
  `power.sample_retention_days`.
- Nightly APScheduler job `outbox_prune` at **03:00 UTC**:
  `DELETE FROM outbox_events WHERE inserted_at < now() - INTERVAL '30 days'`.
- The peer's `sync_cursors` row tracks the last `seq` it has applied
  from each peer; the pruner refuses to delete rows newer than
  `min(sync_cursors.last_seq) - safety_margin (= 1000)`. If a peer is
  silent for > 30 days the operator gets an alert (see §6.5) before
  pruning gates fire — they have to re-bootstrap from a snapshot.

### 2.3 Volume estimate

Mutation events per hub today (back-of-envelope from 7-device fleet +
audit-callsite count of ~102):

| Source | Events/day | Notes |
|---|---|---|
| Device admin writes (renames, group assignments, watchdog rules, schedules) | < 50/day | Admin-rare per RFC-004 §4 |
| Audit events (admin actions) | ~50–200/day | Mirrors admin writes |
| Watchdog firings | ~10–50/day | Most don't fire |
| Schedule firings | ~20/day | Daily power-cycle jobs |
| Enrollment-token mints / device adopts | ~1/day | Bring-up phase only |
| Firmware release scans | ~5/day | Mostly no-op |
| **Total mutations** | **~150–500/day** | Even 10× safety margin = 5000/day |
| **Heartbeat / power-sample firehoses** | **NOT synced** | See §2.4 |

At 500 events/day × 30 days retention = 15k rows. Trivial. Even at
100× growth (full small-business deployment, 200 devices, 5 admins
working) we're at 50k rows × 30 days = 1.5M rows; still trivial for
Postgres.

### 2.4 What's NOT in the outbox

Per RFC-004 §4 ("Devices are single-writer") + this is the architecture
decision that keeps the sync cheap:

- **`device_heartbeats`**: NOT synced. Each device picks one hub as
  its heartbeat target via firmware-side URL preference. The peer hub
  discovers liveness via the **device row's `last_heartbeat_at`**,
  which is in the outbox.
- **`device_power_samples`**: NOT synced. Same reason — single-writer,
  high-volume firehose. The peer can read live samples via a fan-out
  API if needed (deferred; not v1).
- **`device_events`** (raw command results, etc.): NOT synced. Single-
  writer. The aggregated audit row IS synced.
- **`device_commands`**: NOT synced. Commands are queued at the hub
  the operator is talking to; if a device's home-hub goes offline the
  device-side firmware multi-URL fallback (already shipped) re-targets
  the secondary, and the secondary's command queue is what the device
  drains from then on. Cross-hub command replication has weird
  semantics (race between "operator clicks cycle on www" and "device
  picks www2") and is deliberately out of scope for v1.
- **`sessions`** (browser sessions): **NOT synced in v1** — operator
  picks one URL per session (Domain=.voipguru.org cookie scope on each
  URL is independent). Cross-hub session validity is a v2 concern.
- **`runtime_settings`**: SYNCED. Operator-configured values
  (SMTP creds, retention knobs, integrations) must converge.

### 2.5 What IS in the outbox

Aggregates synced (mutation + tombstone + per-row audit):

`sites`, `groups`, `devices`, `enrollment_tokens`, `watchdog_rules`,
`schedules`, `users`, `role_bindings`, `firmware_releases`,
`firmware_release_mirrors`, `runtime_settings`, `audit_events`,
`announcements` (pending-adoption), `attention_acks`,
`runtime_flags`, `external_sensor_sources`.

---

## 3. `tombstones` table

```sql
CREATE TABLE tombstones (
    -- The target row's id. Once a tombstone exists for `target_id`,
    -- the applier refuses to (re-)insert any row with that id. The
    -- composite (aggregate, target_id) handles the unlikely case of
    -- id collision across aggregate tables.
    aggregate       VARCHAR(40) NOT NULL,
    target_id       VARCHAR(40) NOT NULL,

    -- When the original delete happened (origin's wallclock). Used to
    -- resolve a "late insert" race: if we receive a mutation event
    -- with event_at < tombstone.deleted_at, we honour the tombstone.
    deleted_at      TIMESTAMPTZ NOT NULL,

    -- The origin hub of the delete + the outbox event_id we tombstoned
    -- from. For diagnostics + dedupe.
    origin_hub      VARCHAR(40) NOT NULL,
    origin_event_id VARCHAR(40) NOT NULL,

    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (aggregate, target_id)
);
```

Tombstones are **never pruned**. They're tiny (one row per ever-
deleted entity), and the consequence of pruning is the classic
distributed-systems footgun: a delete + later replay-of-old-insert
re-creates a deleted row. Even at 1000 deletes/year × 10 years that's
10k rows. Cheap.

---

## 4. Replicator daemon architecture

### 4.1 Decision: in-process APScheduler job (NOT a sidecar container)

Three options were on the table. Trade-offs:

| Option | Pro | Con |
|---|---|---|
| **In-process APScheduler job** | Reuses existing single-worker model; shares the DB engine + pool; one container to operate; no IPC | Adds a long-running tick to the existing Gunicorn worker (concern: thread budget) |
| Separate Python sidecar container | Process isolation; can be restarted without touching the web worker | Extra container in `docker-compose.yml`; two operational surfaces; needs its own log shipping |
| Subprocess inside the Gunicorn worker | Avoids thread cost; isolates from web request path | Spawning a subprocess from a Flask worker is fragile (Gunicorn lifecycle confusion); not a pattern we have elsewhere |

**Picked: in-process APScheduler job**, consistent with the existing
4 tick jobs. Architecture doc §"Operational" calls out single-worker-
by-design; sticking with that is the simplest path. If the thread
budget becomes a concern (§7), we revisit by **adding Redis as the
shared-state backplane** (also from architecture.md: "To scale up:
add Redis (or equivalent) for shared state") and splitting the
replicator into a sidecar then.

### 4.2 Daemon shape

```
app/services/sync/
├─ __init__.py                Public API (start_replicator, etc.)
├─ _outbox.py                 Local outbox-write helpers (emit_mutation,
│                             emit_tombstone, emit_audit). Called from
│                             services/audit.py + the mutation paths.
├─ _peer_client.py            HTTP client: GET /api/v1/sync/since
│                             on each configured peer; HMAC signing.
├─ _applier.py                Idempotent apply + LWW resolution.
├─ _cursors.py                Read/write sync_cursors per peer.
└─ _config.py                 Peer list from runtime_settings + env.

app/jobs/scheduler.py         Adds `sync_replicator_tick` every 1s
                              (configurable via env REBOOTER_SYNC_TICK_SECONDS).

app/blueprints/sync.py        Hosts GET /api/v1/sync/since.

app/models/sync.py            outbox_events + tombstones + sync_cursors
                              ORM models.
```

### 4.3 Cursors table

```sql
CREATE TABLE sync_cursors (
    -- The peer hub we're tracking. e.g. 'tmrwww02'.
    peer_hub        VARCHAR(40) PRIMARY KEY,

    -- Highest peer seq we have successfully applied.
    last_seq        BIGINT NOT NULL DEFAULT 0,

    -- When we last polled the peer (success or not). For lag display.
    last_poll_at    TIMESTAMPTZ,

    -- Last successful application of a peer batch.
    last_apply_at   TIMESTAMPTZ,

    -- Last error message (truncated 1KB) — surfaced on the Sync UI.
    last_error      TEXT,

    -- Health: consecutive_failures. Resets to 0 on success. Drives
    -- exponential backoff (§4.5) and operator-visible alert at >= 30.
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,

    -- Lag estimate: peer.event_at - last_apply_at for the most-recent
    -- applied event. NULL if no events applied yet.
    last_event_lag_seconds  INTEGER
);
```

### 4.4 Tick model

```python
# Conceptual. Runs every 1s by default.
def replicator_tick():
    for peer in get_peers_from_runtime_settings():
        if peer.in_backoff_window():
            continue
        try:
            batch = fetch_peer_batch(peer, since_seq=cursor[peer].last_seq, limit=500)
            if not batch:
                update_cursor_poll_only(peer)
                continue
            applied_n = apply_batch(peer, batch)         # see §6
            advance_cursor(peer, batch[-1].seq, applied_n)
        except PeerUnreachable:
            bump_failures(peer)
        except Exception as e:
            log.exception(...)
            record_error(peer, e)
```

### 4.5 Pull cadence + backoff

- **Steady state**: 1s tick → target end-to-end latency 1–3s
  (origin commit → outbox row → peer pull → peer apply).
- **Batch size**: 500 events per pull. With ~150–500 events/day this
  drains in one cycle in normal ops; the cap is there for catch-up
  after an outage.
- **Backoff** on peer error: 1s → 5s → 30s → 60s → 60s … (capped).
  Resets on first success.
- **Long-outage rejoin**: if `cursor.last_seq` is older than the peer's
  oldest outbox row (peer has pruned ahead of us), the peer returns
  `410 Gone`. Operator gets a Sync-tab alert: "Peer ahead of retention;
  re-bootstrap required." Re-bootstrap (= one-shot full-table dump-
  and-load) is a separate operator-driven path; documented in
  `docs/runbooks/multi-hub-resync.md` (P7).

### 4.6 Peer discovery

Three runtime knobs:

| Setting | Where | Example |
|---|---|---|
| `REBOOTER_HUB_ID` (env) | `coordinator.env`-style per-host env | `tmrwww01` |
| `sync.peers` (runtime_settings JSON) | DB-backed, edit via Settings → Sync | `[{"hub":"tmrwww02","base_url":"https://www2.voipguru.org/rebooter","enabled":true}]` |
| `sync.hmac_secret` (runtime_settings) | DB-backed; rotate without redeploy | `<32-byte hex>` |

The same `sync.hmac_secret` is set on both hubs. Rotation: set both
hubs to support old+new ("primary" + "fallback" keys) for an overlap
window, then drop the old key. **Out of v1 scope** — operator rotates
by setting the new value on both hubs in the same minute and tolerates
≤ 1 tick of pull errors.

### 4.7 Operational guards

- **Advisory lock**: replicator tick acquires `pg_advisory_lock(4242117310)`
  (one above the schema lock) before doing any work. If a future
  multi-worker rollout happens, only one worker runs the tick. Tick
  releases the lock at end.
- **Kill switch**: env `REBOOTER_SYNC_DISABLED=1` short-circuits the
  tick (same pattern as `REBOOTER_SCHEDULER_DISABLED`). Operator can
  flip without redeploying just by `docker compose restart`-ing the
  single container.
- **Observation mode**: env `REBOOTER_SYNC_OBSERVE_ONLY=1` — replicator
  fetches + logs what it would apply but does not actually write.
  Used during P3 rollout (see §8).

---

## 5. `/api/v1/sync/since` endpoint contract

### 5.1 Request

```
GET /api/v1/sync/since?seq=<N>&limit=<L>
Host: www2.voipguru.org
Authorization: Bearer <HMAC>
X-Sync-Hub-Id: tmrwww01
X-Sync-Timestamp: 2026-05-15T14:23:11Z
```

Query params:
- `seq` (required, int): return events with `seq > N`, ordered ascending.
- `limit` (optional, int, default 500, max 5000): max events to return.

Headers:
- `Authorization: Bearer <hmac>` — HMAC-SHA256 of
  `<hub_id>:<iso_timestamp>:since:<seq>` with `sync.hmac_secret`.
  Reuses coordinator pattern: `printf '%s' "$payload" |
  openssl dgst -sha256 -hmac "$secret" | awk '{print $NF}'`.
- `X-Sync-Hub-Id`: the calling hub's `REBOOTER_HUB_ID`. Server uses
  this to (a) verify the HMAC and (b) reject self-fetches (hub
  fetching from itself = config error).
- `X-Sync-Timestamp`: ISO-8601 UTC. Server rejects timestamps that
  are > 60s skew from its own clock (replay-attack window).

### 5.2 Auth flow

```python
def verify_sync_request(headers, query):
    hub_id = headers["X-Sync-Hub-Id"]
    ts     = headers["X-Sync-Timestamp"]
    bearer = headers["Authorization"].removeprefix("Bearer ")
    seq    = query["seq"]

    if abs(now_utc() - parse(ts)) > 60s:
        return 401, "stale_timestamp"

    secret = runtime_settings.get("sync.hmac_secret")
    expected = hmac.new(
        secret.encode(),
        f"{hub_id}:{ts}:since:{seq}".encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, bearer):
        return 401, "hmac_mismatch"

    if hub_id == os.environ["REBOOTER_HUB_ID"]:
        return 400, "self_fetch"

    return 200, None
```

### 5.3 Response (200 OK)

```json
{
  "hub": "tmrwww02",
  "served_at": "2026-05-15T14:23:11.418Z",
  "highwater_seq": 41827,
  "events": [
    {
      "seq": 41801,
      "event_id": "oxe_01JAA…",
      "origin_hub": "tmrwww02",
      "event_at": "2026-05-15T14:23:10.044Z",
      "kind": "mutation",
      "aggregate": "devices",
      "target_id": "dev_01KR8127W5XMP6MDF34J0TXQP9",
      "scope": {"site_id": "site_01J…", "group_id": null, "device_id": "dev_01KR…"},
      "actor": "usr_01J…",
      "payload": { "id": "dev_01KR…", "display_name": "Erica's Subwoofer", … }
    },
    {
      "seq": 41802,
      "event_id": "oxe_01JAA…",
      "kind": "tombstone",
      "aggregate": "watchdog_rules",
      "target_id": "wdr_01J…",
      "event_at": "2026-05-15T14:23:10.061Z",
      "origin_hub": "tmrwww02",
      "scope": {"site_id": "site_01J…", "group_id": "grp_01J…", "device_id": null},
      "actor": "usr_01J…",
      "payload": {}
    }
  ]
}
```

- `highwater_seq`: the peer's current max(seq). The caller uses this
  to compute lag (`highwater_seq - last_seq`) for the Sync UI.
- `events`: empty array if `seq >= highwater_seq` (nothing to ship).
  Caller still records `last_poll_at`.

### 5.4 Error responses

| Status | `error.code` | Meaning |
|---|---|---|
| 400 | `bad_seq` | `seq` is negative or non-integer |
| 400 | `self_fetch` | Caller hub_id == server hub_id |
| 401 | `stale_timestamp` | Timestamp skew > 60s |
| 401 | `hmac_mismatch` | Auth failed |
| 410 | `seq_too_old` | Requested seq is older than the peer's retention window. Caller must re-bootstrap. |
| 429 | `rate_limited` | Caller polled too aggressively (> 10 req/s) |
| 503 | `replicator_disabled` | Peer has `REBOOTER_SYNC_DISABLED=1`. Caller backs off normally. |

### 5.5 Pagination

The endpoint is **stateless pagination via `seq`**, the standard cursor
pattern. The caller's next request is `?seq=<last_event.seq>`. No
opaque cursors; debug-friendly.

### 5.6 Why not push?

Considered server-push (SSE / WebSocket) for sub-second latency. **Not
worth it for v1**: pull at 1s gives 1–3s steady-state, simpler to
debug, no idle-connection threads, no broken-pipe + reconnect logic.
Defer to v2.

---

## 6. Idempotent apply + LWW spec

### 6.1 Local outbox write (origin side)

Every mutation in `services/<x>.py` that writes a `devices` / `groups`
/ etc. row gets a paired call to `sync.emit_mutation()` inside the
**same `session_scope()`** so the outbox row commits atomically with
the mutation. We hook this at the `audit.record()` site for any
mutation that already audits (which is all of them), with a
side-channel for aggregates that don't naturally audit (`runtime_settings`).

```python
# In app/services/audit.py::record(), after the AuditEvent insert:
def record(action, *, target_type, target_id, details, …):
    ...
    with session_scope() as session:
        session.add(evt)
        # NEW: emit paired outbox event.
        from app.services.sync import _outbox
        _outbox.emit_audit(session, evt)
        # And, if this audit row reflects a mutation on a synced
        # aggregate, emit the mutation outbox event too. The mutation
        # service is responsible for calling _outbox.emit_mutation()
        # explicitly; the audit hook only emits the audit row.
```

Tombstones: every `DELETE FROM <aggregate> WHERE id = …` site adds
a paired `sync.emit_tombstone(session, aggregate, target_id)` call.
The list of mutation sites that need this hook is bounded — about
40 places (per BACKLOG-era audit), each is ~2 lines of code.

### 6.2 Applier algorithm (receiver side)

```python
def apply_batch(peer, events):
    """
    Apply a batch of peer events under a single transaction per event.
    Reasons for one-transaction-per-event (not one-per-batch):
      - A poison-pill event doesn't roll back the whole batch.
      - Per-event audit visibility on what was applied.
      - Cursor advances per-event so a crash mid-batch resumes cleanly.
    """
    applied = 0
    for ev in events:
        try:
            with session_scope() as s:
                # 1. Idempotency: have we seen this event_id?
                if s.execute(
                    select(OutboxEvent.event_id).where(OutboxEvent.event_id == ev.event_id)
                ).first():
                    continue  # already applied

                # 2. Scope check (B1 enforcement on the receiver).
                if not receiver_can_apply_scope(ev.scope):
                    log.warning("rejected scope: %s", ev)
                    record_rejection(s, ev, reason="scope_denied")
                    continue

                # 3. Tombstone check.
                tombstone = get_tombstone(s, ev.aggregate, ev.target_id)
                if tombstone and ev.kind == "mutation" and ev.event_at <= tombstone.deleted_at:
                    log.info("ignored stale insert after tombstone: %s", ev)
                    record_rejection(s, ev, reason="post_tombstone_insert")
                    continue

                # 4. Dispatch by kind.
                if ev.kind == "mutation":
                    apply_mutation(s, ev)
                elif ev.kind == "tombstone":
                    apply_tombstone(s, ev)
                elif ev.kind == "audit":
                    apply_audit_passthrough(s, ev)

                # 5. Record we've applied this event_id locally.
                s.add(OutboxEvent(
                    event_id=ev.event_id,
                    origin_hub=ev.origin_hub,
                    event_at=ev.event_at,
                    kind=ev.kind,
                    aggregate=ev.aggregate,
                    target_id=ev.target_id,
                    scope_site_id=ev.scope.get("site_id"),
                    scope_group_id=ev.scope.get("group_id"),
                    scope_device_id=ev.scope.get("device_id"),
                    payload=ev.payload,
                    applied_from_event_id=ev.event_id,
                    actor=f"replicator:{peer.hub_id}",
                ))
            applied += 1
        except Exception:
            log.exception("apply failed for event %s", ev.event_id)
            # Don't advance past this event; tick will retry the batch
            # next cycle. Stuck-event detection via consecutive_failures.
    return applied
```

### 6.3 LWW resolution

```python
def apply_mutation(s, ev):
    row = s.get(MODELS[ev.aggregate], ev.target_id)
    if row is None:
        # Insert.
        s.add(build_orm_from_payload(MODELS[ev.aggregate], ev.payload))
        record_lww_audit(s, ev, decision="inserted")
        return

    # Resolve LWW. Local row's most-recent outbox event_at is the
    # comparison key — NOT the row's `updated_at`, because the row
    # might have been written by a peer-replayed event with a different
    # event_at than the local clock at insert time.
    local_event_at = s.execute(
        select(OutboxEvent.event_at)
        .where(OutboxEvent.aggregate == ev.aggregate)
        .where(OutboxEvent.target_id == ev.target_id)
        .order_by(OutboxEvent.event_at.desc())
        .limit(1)
    ).scalar() or row.updated_at

    if ev.event_at > local_event_at:
        # Peer wins.
        update_orm_from_payload(row, ev.payload)
        record_lww_audit(s, ev, decision="peer_won", lost_event_at=local_event_at)
    elif ev.event_at == local_event_at:
        # Tie-break: lexicographic on (origin_hub, event_id).
        if (ev.origin_hub, ev.event_id) > (origin_hub_local, event_id_local):
            update_orm_from_payload(row, ev.payload)
            record_lww_audit(s, ev, decision="peer_won_tiebreak")
        else:
            record_lww_audit(s, ev, decision="local_won_tiebreak")
    else:
        # Local wins. Keep the row, but record the loss in audit so
        # the operator can see "we got a stale write from peer X".
        record_lww_audit(s, ev, decision="local_won", peer_event_at=ev.event_at)
```

### 6.4 Per-record audit retains both versions

`record_lww_audit` writes an `AuditEvent` row with
`action='sync.lww_resolution'`, `target_type=ev.aggregate`,
`target_id=ev.target_id`, and `details = {decision, winner_payload,
loser_payload, winner_event_at, loser_event_at}`. Operator can audit
"what did the peer try to write that we rejected?" via the existing
`/app/audit` page.

### 6.5 Stuck-event alerting

A poison-pill event (one that throws on apply) blocks the cursor.
Detection:

- `sync_cursors.consecutive_failures` increments on every failed batch
  apply.
- At `>= 30` (≈ 30 ticks ≈ 30s in steady state) a row is added to
  the Status-page attention inbox: `sync.replicator_stuck` with the
  peer name, the blocking event_id, and the error class.
- Operator can `POST /api/v1/admin/sync/skip-event` with `event_id`
  to advance past the stuck event. The skipped event is recorded in
  audit (`sync.event_skipped`) and surfaces on the Sync UI as a
  yellow banner ("Skipped 1 event from tmrwww02 due to apply error").

---

## 7. Sizing analysis

### 7.1 Outbox-write rate at steady state

From §2.3, max realistic mutation rate is ~500 events/day = **0.006
events/second**. The outbox-write hook adds **1 INSERT inside the
existing session** for each mutation; latency increase per mutation
is ~0.5–1 ms (single index INSERT into a narrow BIGSERIAL table).
Negligible.

### 7.2 Replicator thread budget

- The tick runs in an APScheduler **BackgroundScheduler** thread,
  outside the Gunicorn worker's request thread pool. APScheduler
  spawns its own thread pool (default size = 10). The replicator
  consumes **1 long-running thread** while a tick is in flight; idle
  otherwise.
- Worst-case tick duration: peer 500-event batch with apply ≈
  500 × 5 ms = 2.5s. With a 1s tick interval and a 2.5s tick,
  APScheduler's default `coalesce=True` + `max_instances=1` means
  the next tick is skipped, not stacked. Bounded thread use.
- The Gunicorn worker's 8 request threads are unaffected — sync work
  runs in the APScheduler thread pool. **No request-path thread cost.**

### 7.3 Postgres connection pool

Today: SQLAlchemy default pool_size=5 + max_overflow=10 = **15
max concurrent connections** (`app/db.py`).

New consumers of the pool:

- Replicator tick: 1 connection at a time (one session_scope per
  event in apply_batch). Steady state: < 1% utilization.
- Outbox-write hook in mutation paths: same session_scope as the
  mutation. **Zero net new connections.**
- `/api/v1/sync/since` endpoint: each peer poll = 1 connection. With
  1 peer × 1 req/s = 1 connection-second/s. Even at 5 peers ×
  10 req/s = 50 conn-s/s; still well under 15 max with a 50-100ms
  per-request hold time.

**Recommendation: bump pool to `pool_size=10, max_overflow=20`** (=25
max concurrent) as a conservative safety margin once the replicator
ships. Done in P1 alongside the schema add. Validate by querying
`pg_stat_activity` during a load test.

### 7.4 Endpoint rate-limiting

`/api/v1/sync/since` has its own bucket in `middleware/rate_limit.py`:
**10 req/s per source IP**. Caller polls at 1 req/s in steady state;
the 10× headroom absorbs catch-up bursts.

### 7.5 Disk/IO

- Outbox table at 30-day retention with worst-case 5k events/day:
  ~150k rows × ~2 KB/row JSONB = ~300 MB. Trivial.
- Index size: ~30 MB. Trivial.
- Vacuum pressure: BIGSERIAL primary key + DELETE-only pruning is
  Postgres's happy path; autovacuum handles it without tuning.

---

## 8. Phased rollout

Six ships, each independently reversible until the apply flip.

### P1 — Schema + outbox-write hooks (observation only)
**~3 days. v0.6.0.**

- Add `app/models/sync.py`: `OutboxEvent`, `Tombstone`, `SyncCursor`.
- Migration: tables created by `Base.metadata.create_all()` on next
  start (no Alembic — consistent with existing pattern). Bump
  `pool_size=10, max_overflow=20` in `app/db.py`.
- Add `app/services/sync/_outbox.py` with `emit_mutation`,
  `emit_tombstone`, `emit_audit`. NOT yet wired into mutation sites.
- Hook **`app/services/audit.py::record()`** to emit an audit-kind
  outbox row. **This alone gives us > 80% coverage** because every
  admin mutation already audits.
- Add env `REBOOTER_HUB_ID` (default `single`, must be set explicitly
  per host before any sync feature activates).
- Add Settings → Sync tab section "Outbox" showing local outbox depth
  + last 50 events for debugging.
- No replicator. No endpoint. Pure local instrumentation.

**Test**: take a fresh DB, do 20 admin actions, confirm 20 paired
outbox rows; confirm `seq` is monotonic; confirm tombstones land on
delete paths.

### P2 — Mutation hooks + tombstones for the remaining ~40 sites
**~3 days. v0.6.1.**

- Audit the codebase for every DELETE and every "INSERT/UPDATE that
  isn't audit-driven" (mostly `runtime_settings` + bulk-action
  mutations). Add explicit `emit_mutation` / `emit_tombstone` calls.
- Tests: every aggregate listed in §2.5 has a unit test that asserts
  outbox emission on the canonical mutation path.

**Test**: regression suite confirms outbox covers all aggregates;
delete-then-recreate (same id) produces tombstone + rejected insert.

### P3 — `/api/v1/sync/since` endpoint (read-only; no replicator)
**~2 days. v0.6.2.**

- Add `app/blueprints/sync.py` with the endpoint contract from §5.
- HMAC verification using `sync.hmac_secret` from `runtime_settings`.
- Rate-limit bucket added to `middleware/rate_limit.py`.
- Settings → Sync tab: HMAC-secret editor (super_admin only;
  fingerprint-only display, no echo).
- No replicator client yet. The endpoint can be hit manually via curl
  to validate the contract.

**Test**: curl-based contract test from operator's laptop using the
HMAC openssl one-liner; confirms 200/400/401/410 paths.

### P4 — Replicator daemon in **observation-only** mode
**~3 days. v0.6.3.**

- Add `app/services/sync/_peer_client.py`, `_applier.py`, `_cursors.py`.
- APScheduler tick at 1s. Reads `sync.peers` from runtime_settings
  (default empty → no-op).
- `REBOOTER_SYNC_OBSERVE_ONLY=1` (default in this ship): replicator
  fetches peer events, logs what it would apply, but does NOT write
  anything to the local DB except the `sync_cursors.last_poll_at` +
  lag stats.
- Sync UI surfaces: peer reachable y/n, lag seconds, last error, last
  N "would-have-applied" decisions.

**Test on a single hub**: stand up a sibling Docker container (same
codebase) on the same host with a separate Postgres, configure them
as peers, watch the observation log on each side fill up. Validates
HMAC, batching, cursor advancement, contract correctness — all on
one machine.

**Test on real second node**: blocked until P5; operator stands up
the second host.

### P5 — Stand up real second hub on tmrwww02
**~3 days (mostly ops, not Python). v0.6.4.**

- New `rebooter-droids` container on tmrwww02 with its own Postgres.
- Nginx on tmrwww02 stops cross-host proxying to tmrwww01 and starts
  serving from local container.
- **Data migration**: pg_dump from tmrwww01 → pg_restore on tmrwww02
  during a maintenance window. After restore, both hubs have identical
  state and a starting `outbox_events.seq = current_max_seq`.
- Both hubs run in `REBOOTER_SYNC_OBSERVE_ONLY=1` for **at least 7
  days** (operator can extend). The Sync UI shows what would-have-
  been-applied; operator audits for surprises.

**Reversible**: until the flip, tmrwww02 can be torn back down and
nginx re-pointed at tmrwww01. No data loss because no live sync
writes have happened yet.

### P6 — Flip to apply mode
**~1 day. v0.6.5.**

- Unset `REBOOTER_SYNC_OBSERVE_ONLY` (default flips to apply).
- Replicator now writes events into the local DB per §6.
- LWW resolution is active.
- Tombstone-honour is active.
- Stuck-event alerting wired to the Status attention inbox.
- Operator-facing runbook `docs/runbooks/multi-hub-sync.md` published:
  observation → apply → re-bootstrap → manual skip-event.

**Test plan**: §9.

### P7 — Operator drill + runbook
**~1 day. v0.6.6.**

- Operator simulates tmrwww01 outage (`docker compose stop` the
  rebooter container; nginx 502s; mobile app falls through to www2).
- Confirms www2 keeps serving admin + device traffic.
- tmrwww01 brought back; replicator catches up; confirm UIs converge.
- Drill notes added to runbook.

---

## 9. Test approach

### 9.1 Testable on a single hub (P1–P4)

- **Outbox emission**: unit-test every mutation path; assert paired
  outbox row.
- **Tombstone semantics**: insert → delete → re-insert (locally);
  assert tombstone row + rejected re-insert.
- **Endpoint contract**: pytest + Flask test client against
  `/api/v1/sync/since`; cover 200/400/401/410/429.
- **HMAC**: shell-equivalent test using `openssl dgst -sha256 -hmac`
  to confirm interoperability with the coordinator pattern.
- **Sibling-container observation**: docker-compose with two
  rebooter-droids services + two Postgres, on the same host, peered
  to each other. Exercises the full replicator including
  cursor-advancement and LWW resolution under controlled conditions.
- **Idempotent apply**: replay the same batch twice; assert no
  duplicate writes.
- **LWW**: stage two conflicting mutations with hand-crafted
  event_at values; assert the right winner.

### 9.2 Requires real second node (P5+)

- **Real cross-host latency**: confirm 1–3s end-to-end target with
  production-realistic network. The LAN between tmrwww01 and tmrwww02
  is fast (sub-ms) so the test approximates upper-bound; for true
  WAN deployment that's a future concern.
- **Failover behaviour**: stop one hub; confirm devices fall over;
  confirm admin sessions on the surviving hub keep working.
- **Long-outage rejoin**: stop one hub for > 30 minutes; confirm
  catch-up batch drains within ~1 minute of restart.
- **Schema-drift safety**: deploy a new version on one hub only;
  confirm the older peer's applier doesn't crash on unknown payload
  fields (graceful-degrade: extra payload fields are ignored, never
  errored on — already a constitutional invariant for `_PENDING_COLUMNS`
  too).

### 9.3 QA fixtures

- The existing `tests/qa/` Playwright suite targets `www.voipguru.org`
  today; in the P5+ world it must target the primary hub explicitly.
  Add a `REBOOTER_QA_BASE_URL` knob to the suite (default www) so QA
  runs work pre- and post-P5.
- New fixture: `qa_sync_peer_pair()` spins up the sibling-container
  pair from §9.1 for integration tests.

---

## 10. Open questions for the operator

1. **Sync HMAC secret rotation cadence.** Default = "rotate when the
   coordinator-network secret rotates" (= quarterly, per master's
   policy)? Or independent / "never until breach"?
2. **Cross-hub session validity.** v1 plan: NOT synced; operator's
   browser session on www doesn't grant www2 access until they re-
   login on www2. Acceptable? (Per `Domain=.voipguru.org` cookie scope,
   the session cookie IS shared but the server-side session row is
   per-hub, so the peer 401s on the session_id it doesn't know.)
3. **Re-bootstrap procedure.** §4.5 says "operator-driven full dump-
   and-load when peer is ahead of retention." Document a one-liner
   `pg_dump | pg_restore` runbook, or build an admin endpoint? Plan
   defaults to runbook (operational tool, not API).
4. **Command queue cross-hub.** §2.4 explicitly excludes
   `device_commands` from sync. If operator clicks "cycle relay" on
   www but the device's home is www2, the command is queued on www
   and the device never sees it. Plan: surface a warning chip on the
   device detail page when the device's most-recent heartbeat came
   from the peer hub ("This device is being served from tmrwww02 right
   now; commands enqueued here may not be picked up. Open www2 to
   command this device.") Acceptable for v1, or do we need actual
   cross-hub command forwarding (much more code)?
5. **What to do with `audit_events.id` collisions.** Today
   `AuditEvent.id` is `BigInteger autoincrement` — each hub's
   sequence is independent. Two hubs writing audit rows concurrently
   will collide in the synced peer. Plan: switch audit `id` to ULID
   (`aud_<ULID>`) — additive, breaks no current query. Confirm.
6. **Roll-forward-only?** Plan assumes both hubs are on the same
   software version at any time (RFC-004 §3 non-goal N4). Concretely:
   the rolling-deploy policy means there's a brief window where
   tmrwww01 is on v0.6.5 while tmrwww02 is still on v0.6.4. The
   applier must be liberal in what it accepts (ignore unknown JSON
   keys silently). Confirm we're OK with the "older peer silently
   drops fields it doesn't understand" semantics.
7. **Alerting hook.** Stuck-event detection (§6.5) lands as a
   Status-page attention item by default. Want a coordinator-hub
   post-on-transition too (same pattern as the existing healthcheck
   script)? Trivial to add.
8. **Operator-facing scope rejection.** When a peer pushes an event
   whose scope claims fail the receiver's RBAC check (§6.2 step 2),
   the applier rejects silently and logs. Should this surface to the
   operator UI (e.g. "Peer tmrwww02 attempted to write a device row
   for a site you don't own — rejected")? Useful for debugging
   misconfig but noisy in normal ops.

---

## 11. Estimated effort summary

| Phase | Ship | Effort | Reversible? |
|---|---|---|---|
| P1 | v0.6.0 — schema + audit-hook outbox emit | 3 d | Yes — outbox is write-only, no consumers |
| P2 | v0.6.1 — remaining mutation hooks + tombstones | 3 d | Yes — same as P1 |
| P3 | v0.6.2 — read-only `/api/v1/sync/since` | 2 d | Yes — no caller of endpoint |
| P4 | v0.6.3 — replicator daemon in observation mode | 3 d | Yes — flag-gated, no writes |
| P5 | v0.6.4 — real second hub stood up | 3 d | Yes — until apply flip, tear down + nginx re-point |
| P6 | v0.6.5 — flip apply mode + LWW + tombstone-honour | 1 d | One-way after first event applied; tested in P4/P5 |
| P7 | v0.6.6 — operator drill + runbook | 1 d | n/a — documentation |
| **Total** | **7 ships across v0.6.x** | **~16 days focused work** | Reversible through P5 |

Compared to RFC-004 §8's Option-B estimate of 9 weeks, Option C
lands in ~3 weeks because it skips the read-only-Flask-middleware
audit (every mutation point) and the pending-forward queue plumbing
(every device-API write point). The Option-C complexity is
concentrated in the LWW + tombstone logic, which is contained inside
`services/sync/`.

---

## 12. Risks (ranked)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Replicator-stuck on a poison-pill event silently lags the peer hub by hours | Medium | High | §6.5 alerting + skip-event admin endpoint; default-tested by P4 sibling-container chaos test |
| LWW silently overwrites operator-intended change (concurrent edit on both hubs) | Medium | Medium | Per-record audit retains both versions (§6.4); operator can see+undo via /app/audit. Document the "rare edits race" semantic for operators |
| Tombstone forgotten somewhere → deleted row gets re-created from peer | Low | Medium | P2 audit + per-aggregate test; tombstones table is the source of truth on every receive path |
| HMAC secret leak | Low | High | Stored in runtime_settings (DB), never logged, rotation procedure documented |
| Schema drift between peers crashes applier | Medium | Medium | Liberal applier (ignore unknown keys); CI gate that no removed column is in any synced aggregate |
| Outbox-write doubling local mutation cost noticeably | Low | Low | §7.1: ~0.5–1 ms per write; trivial |
| `device_commands` not synced → operator confusion | High (UX, not data) | Low | UI chip on device detail (§10 Q4) |
| Re-bootstrap procedure scary for operator | Medium | Medium | Runbook + dry-run on sibling container before first real use |

---

## 13. Constitutional invariants preserved

From `webui-redesign-requirements.md` §0.2 + RFC-004 §11:

- **C1**: devices remain useful when central is unreachable —
  unchanged. Sync is hub-to-hub, device firmware is unaware.
- **C2**: single-hub self-hosted deployment unchanged —
  `REBOOTER_HUB_ID=single` + empty `sync.peers` = identity
  transformation. No new container, no new dependency.
- **C5**: `/api/v1/device/*` contract is frozen — sync touches only
  `/api/v1/admin/*` and the new `/api/v1/sync/*` namespace.
- **C6**: open-source DIY-friendly — no SaaS, no managed service;
  Postgres + Python only.

---

## 14. References

- `docs/RFC-004-multi-hub-sync.md` — source-of-truth, §10b carries
  the locked decisions.
- `docs/architecture.md` — single-worker Gunicorn note, APScheduler
  pattern, hard rules.
- `docs/BACKLOG.md` — B11 entry + B1 RBAC dependency.
- `app/services/audit.py` — pattern that `outbox_events` mirrors.
- `app/jobs/scheduler.py` — tick pattern the replicator plugs into.
- `app/models/role_bindings.py` — scope-claims plumbing (B1).
- `app/models/audit.py` + `app/models/_helpers.py` — ULID + ts_column
  conventions.
- Coordinator HMAC pattern: `printf '%s' "$payload" | openssl dgst
  -sha256 -hmac "$secret" | awk '{print $NF}'` (per `installer.sh`).
