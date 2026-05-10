# RFC-004: Multi-Hub Sync (www ↔ www2)

| Field | Value |
|---|---|
| Status | **Draft** (seeded 2026-05-09 from operator request: "we need those 2 hubs to sync to each other and status and settings for it") |
| Authors | rebooter-droids backend/web team |
| Targets | rebooter-droids backend, nginx routing, Postgres |
| Supersedes | — |
| Superseded by | — |
| Companion | `RFC-002-firmware-mirrors.md` (operationally-independent firmware mirror chain), `PROJECT-STATE-2026-05-09-FULL-SYNC.md` §5 (multi-node direction) |

> **Important context:** today we have **one hub on two URLs**, not
> two hubs. The first job of this RFC is to make that fact clear.
> Then it lays out the architecture options for "real sync" and
> proposes a path. **No code lands until the operator picks an
> option.**

---

## 1. Current state — verified, not assumed

I probed this end-to-end before writing the RFC. Differential test:

```
CREATED on https://www.voipguru.org/rebooter   → visible on www2: True
CREATED on https://www2.voipguru.org/rebooter  → visible on www:  True
```

A device registered via either URL is immediately visible from the
other. Same `server_time` on both within a second. Same version
string. **They are the same logical hub.**

How that's wired up:

- DNS:
  - `www.voipguru.org`  → `24.168.14.36`  (tmrwww01, this host)
  - `www2.voipguru.org` → `198.179.77.190` (tmrwww02, peer host)
- Nginx on tmrwww01 has a `/rebooter/` location block that
  `proxy_pass`es to the local `rebooter-droids:8090` container.
- Nginx on tmrwww02 has a similar `/rebooter/` block, but it
  proxies cross-host to **tmrwww01's** `rebooter-droids` (over
  the shared LAN — both servers see each other on
  192.168.1.x). One Postgres, one Flask container, two URLs.

So the current "multi-URL fallback" the firmware uses is a
**resilience feature** (if tmrwww02's nginx is up but tmrwww01's
nginx is down, the firmware switches URLs), not a real
multi-hub story. The actual backend is single-homed on tmrwww01.

This is consistent with the project pause-state §5 which calls
out "real node-2 + sync API" as a v0.3+ direction but not yet
done. v0.3.0–0.3.5 shipped UI, RBAC, and bulk-action work; the
sync work hasn't started.

## 2. What the operator is asking for

> "if we have both `https://www.voipguru.org` and
> `https://www2.voipguru.org` urls, we need those 2 hubs to sync
> to each other and status and settings for it."

Interpreted: **make each URL be a real, independent hub with its
own backend + DB; have them cross-sync; surface sync state and
configuration in the UI.**

The motivation is consistent with the constitutional invariants
in `webui-redesign-requirements.md` §0.2:
- C1: device must remain useful when central is unreachable
- C2: portal must function on a self-hosted deployment with no
  third-party dependency

Adding a second real hub adds resilience: if tmrwww01 dies (host
failure, datacenter outage, certificate expiry, container OOM),
tmrwww02's hub still serves devices that have been told the
secondary URL.

## 3. Goals + non-goals

### Goals

- **G1.** Each URL serves from its own dedicated backend +
  Postgres so an outage on one host does not take both URLs
  offline.
- **G2.** Devices, audit, deployments, and configuration sync
  between hubs within a defined latency target (default: 30s).
- **G3.** Operator can see sync state on the Settings page —
  is the peer reachable, last-successful-sync time, lag, last
  conflict (if any).
- **G4.** Operator can configure sync from the Settings page —
  enable/disable, peer URL, peer auth credential, sync direction.
- **G5.** A single-hub deployment (self-hosted, one URL) is
  unaffected. Sync is opt-in via configuration.

### Non-goals

- **N1.** Multi-region active-active across globally distributed
  hubs. v1 targets two hubs on the same LAN or same admin domain.
- **N2.** Geographic load-balancing or read-locality. The mobile
  app or a browser still picks one URL and stays there.
- **N3.** Conflict-free distributed concurrency at scale. We do
  not need CRDTs in v1; the conflict surface is small (admin
  writes are rare; device telemetry is single-writer per device).
- **N4.** Schema-migration coordination as a first-class feature.
  v1 assumes the operator runs the same software version on both
  hubs at any given time; cutover-with-mixed-version handling is
  a v2 concern.

## 4. Constraints

- **Postgres is already the database** on each hub. Whatever
  sync model we pick should leverage Postgres native facilities
  if possible (logical replication, dump/restore, application-
  level row-replication via `audit_events` shape).
- **Devices are single-writer.** Each device emits heartbeats,
  events, command results. Only ONE device sends those for itself.
  So device-event records do not conflict between hubs as long as
  every device picks ONE hub for its primary heartbeat target.
- **Admin writes are low-volume.** A typical day: a few
  enrollment-token mints, a few device renames, occasional
  firmware-deployment creates. Admin writes are rare relative to
  device writes.
- **Operator privacy:** sync traffic stays within the
  voipguru.org domain or via direct SSH between tmrwww01 and
  tmrwww02. No third-party SaaS sync service.

## 5. Options considered

### 5.1 Option A — Status quo (one hub, two URLs)

What we have today. Document it, don't build sync.

| Pro | Con |
|---|---|
| Zero work | If tmrwww01's container/host dies, both URLs go down. |
| No sync semantics to design | The "multi-URL" promise to firmware is misleading — both URLs share fate. |
| Already fully working | Operator's request explicitly excludes this. |

**Verdict:** does not satisfy the operator's ask.

### 5.2 Option B — Postgres logical replication (active-passive)

tmrwww01's Postgres is the primary; tmrwww02's Postgres is a
read replica using Postgres' native logical replication.
tmrwww02's Flask container runs in a "read-only" mode where
admin write operations are bounced back to www, while device
heartbeats are accepted locally and queued for forwarding to
the primary.

```
[ tmrwww01 nginx ]  →  [ Flask ]  →  [ Postgres primary ]
                                            ↓ logical replication
[ tmrwww02 nginx ]  →  [ Flask (RO) ]  →  [ Postgres replica ]
```

| Pro | Con |
|---|---|
| Postgres native — battle-tested | Read-only secondary feels weird for operators. |
| Zero conflict resolution needed (single writer for admin) | Heartbeat queue + forward-on-recovery is non-trivial. |
| Read paths on tmrwww02 are fully featured | If tmrwww01 dies, write traffic is offline until promotion. |
| Operator already has Postgres on both hosts | Failover requires manual or scripted promotion of the replica. |

**Verdict:** strong contender. Solves G1, G2, G5 cleanly.
G3+G4 are application-layer features on top.

### 5.3 Option C — Application-level event-log sync (active-active)

Both hubs accept all writes. Each hub has its own Postgres.
A new `sync_events` append-only table records every mutation
(device.created, device.updated, device.deleted,
heartbeat.received, audit row, etc.). A background job on each
hub polls the peer's `sync_events` since the last-applied
sequence number and replays into its local DB, idempotently.

Conflict resolution: last-write-wins by `sync_events.at`
timestamp; per-record audit trail keeps both versions for
operator inspection.

| Pro | Con |
|---|---|
| Both hubs are full peers; no read-only feeling | Application code must be aware of sync — every mutation needs to write to `sync_events`. |
| Simple "poll-the-peer" mental model — debuggable | Last-write-wins has well-known edge cases for concurrent admin writes. |
| Survives either-hub outage cleanly | Higher implementation cost than B. |
| No Postgres replication knobs to tune | Risk of divergence if the sync job gets stuck. |

**Verdict:** more code, more risk, but the most operator-
friendly UX. Peers are symmetric.

### 5.4 Option D — Single shared Postgres with two Flask containers

Run one Postgres (on either host) and two Flask containers (one
on each host) both pointing at the same DB. Use Postgres
streaming replication for HA on the database itself.

| Pro | Con |
|---|---|
| No application-layer sync at all | If the shared Postgres dies, both Flask containers are dead. |
| Simple — both Flask containers are stateless | Cross-host DB connections are a security + latency concern. |
| Zero conflict resolution | Doesn't actually solve G1 — Postgres host is the single point of failure. |

**Verdict:** weaker than B because the failure mode of the
shared DB is identical to the single-host story. Skip.

### 5.5 Option E — Per-device home-hub (sharded)

Each device picks one hub as its primary. Heartbeats only go to
that hub. Admin views aggregate across hubs via a federated
query API. No replication needed; instead, each hub is
authoritative for its set of devices.

| Pro | Con |
|---|---|
| Zero sync layer at all | Admin UX is much harder — every list view becomes a fan-out query. |
| Each hub's writes are local | Audit log split across hubs. |
| Scales horizontally (Nth hub is easy) | Failover requires re-pointing the device, which the firmware doesn't currently do. |

**Verdict:** clever but operationally heavy. Defer to v2.

## 6. Recommendation

**Recommend Option B (Postgres logical replication, active-passive)
for v1, with a clean migration path to Option C if the operator
wants symmetric active-active later.**

Reasoning:

- **Lowest implementation risk.** Postgres logical replication
  is mature; the code path on the rebooter side is "Flask
  starts in `READ_ONLY=1` mode and bounces writes to a peer
  URL." That's bounded engineering effort.
- **Failure modes are well-understood.** Replica lag, replica
  reconnect, replica promotion are all standard operational
  procedures.
- **Operator UX is acceptable.** From the operator's
  perspective: tmrwww01 is the primary; tmrwww02 is a hot
  read-only fallback that becomes the writer if tmrwww01 dies.
  When tmrwww01 comes back, it re-syncs from tmrwww02 (now
  acting as primary) and resumes primary role.
- **Migration path to active-active.** If the operator later
  wants symmetric active-active, the application-side
  `sync_events` table from Option C can be added incrementally
  on top of the same DB schema, and the read-only-mode flag
  can be removed.

The rest of this RFC assumes Option B unless an operator
redline picks something else.

## 7. Design (Option B specifics)

### 7.1 Postgres setup

- tmrwww01's Postgres: configure as primary publisher.
  ```
  wal_level = logical
  max_wal_senders = 4
  max_replication_slots = 4
  ```
- tmrwww02's Postgres: configure as subscriber.
  ```
  CREATE SUBSCRIPTION rebooter_sync
    CONNECTION 'host=tmrwww01 dbname=rebooter user=replicator password=…'
    PUBLICATION rebooter_publication;
  ```
- Replication user (`replicator`) created on the primary with
  `REPLICATION` role + restricted to the `rebooter` DB.
- TLS between the two Postgres hosts is required.
- A Postgres-replication-network cidr is allowed in the
  primary's `pg_hba.conf` only for the secondary's IP.

### 7.2 Flask read-only mode

A new env var `REBOOTER_HUB_ROLE` ∈ `{primary, secondary,
single}`. Default: `single`.

When `secondary`:

- Every admin-write endpoint (`PATCH`, `POST` that mutates,
  `DELETE`) is rejected with **HTTP 503** + `Location: <primary URL>`
  for browser redirects + `error.code = "secondary_readonly"`
  for API consumers. The body includes guidance: "this hub is a
  read-only secondary; switch to <primary> to make changes."
- Device-API write endpoints (`/api/v1/device/heartbeat`,
  `/events`, command results) are accepted **locally** and
  written to a local `pending_forward` queue table.
- A background worker drains `pending_forward` to the primary
  hub via the existing `/api/v1/device/*` endpoints. On
  successful forward, the row is marked `forwarded_at` and
  garbage-collected after 24h. On failure, retried with
  exponential backoff.

When `primary`:

- All writes are accepted normally.
- Postgres logical replication ships changes to the secondary
  asynchronously.

When `single`:

- v0.3.5 behaviour exactly. No sync, no read-only mode, no
  forward queue.

### 7.3 Sync status / settings UI

New Settings sub-page: `/app/settings/sync` (placeholder stub
in v0.3.6; real implementation in a later phase).

Surfaces:

- **Hub role** (`primary` / `secondary` / `single`).
- **Peer URL** (operator-configured).
- **Peer reachability** — periodic GET to peer's `/api/v1/version`,
  reports last-successful-probe + p95 RTT.
- **Replication lag** — for primary, query Postgres
  `pg_stat_replication.replay_lag`; for secondary, query
  `pg_stat_subscription.last_msg_receipt_time`.
- **Forward queue depth** (secondary only) — count of
  unforwarded `pending_forward` rows.
- **Last forward error** (secondary only) — most recent
  failure reason from the forward worker.
- **Manual actions:** "trigger replication probe", "trigger
  promote-to-primary" (super-admin only).

### 7.4 API contract for sync state

```
GET  /api/v1/admin/system/sync   → status JSON (above fields)
POST /api/v1/admin/system/sync/probe   → force a peer probe
POST /api/v1/admin/system/sync/promote → promote secondary to
                                          primary (super-admin
                                          only; one-way + audit)
```

### 7.5 Failover semantics

- **Planned failover** (operator-driven): operator clicks
  "promote to primary" on the secondary. Secondary stops
  applying replication, turns its subscription into a
  publication, the (former) primary is degraded to secondary on
  its next start. Browser sessions continue working because
  cookies are domain-scoped per v0.3.3.
- **Unplanned failover** (primary host dead): same flow but
  initiated when the operator notices. v1 does NOT include
  automatic failover — too risky with a single-tenant fleet of
  the operator's size.
- **Recovery of the original primary**: when it comes back, it
  re-subscribes to the new primary's publication, replays
  changes, then the operator can flip roles back if desired.

## 8. Phased implementation plan

| Phase | Ships | Reversible? |
|---|---|---|
| **P0** | This RFC, redlined and accepted. The operator picks Option B (or another). | n/a |
| **P1** | Settings → Sync sub-page stub. New `REBOOTER_HUB_ROLE` env var, default `single`. No behaviour change for existing deployments. | Yes — stub-only. |
| **P2** | tmrwww02 stands up its own `rebooter-droids` container + Postgres. Test against `single`-mode each. Confirm both can run independently. | Yes — independent containers. |
| **P3** | Postgres logical replication wired. Confirm row-level changes ship from primary → secondary on test workload. | Reversible by dropping the subscription. |
| **P4** | Flask `secondary` mode rejects writes with 503 + Location. Read paths fully functional. | Per-feature flag. |
| **P5** | `pending_forward` queue + background worker for device-side writes on the secondary. | Additive. |
| **P6** | Sync-status surface fully populated (replication lag, peer reachability, queue depth). Manual promotion endpoint. | Additive. |
| **P7** | Operator drill: simulated tmrwww01 outage; secondary promotion via UI; tmrwww01 brought back; failback. Documented in `docs/runbooks/multi-hub-failover.md`. | n/a — exercise. |

Estimated effort: P1 ~1 week, P2 ~1 week (mostly Docker/nginx
config on tmrwww02), P3 ~1 week (Postgres replication + nginx
config + TLS), P4 ~2 weeks (read-only Flask middleware + every
mutation point audited), P5 ~2 weeks (forward queue is the
trickiest piece), P6 ~1 week, P7 ~1 week. **Total ≈ 9 weeks** of
focused work.

## 9. Risks + open questions

### Risks (ranked)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Replication lag during an outage causes secondary to be a stale picture of reality | High | Medium | Surface `replay_lag` on the Settings UI; bounded operator awareness |
| Forward-queue worker on secondary gets stuck; device writes accumulate | Medium | High | Visible queue depth + alert on >N pending; bounded retention with operator-acknowledgeable overflow |
| Schema migration on primary breaks subscriber until the secondary is upgraded | Medium | High | Constitutional invariant: deploy primary BEFORE secondary; subscribers tolerate column-add (already idempotent via `_PENDING_COLUMNS`) but not column-drop. P0 gate: any drop migration is rejected by CI. |
| Promotion (B→A) creates a split-brain if the original primary returns mid-promote | Low | High | One-way promote endpoint; explicit operator confirmation; audit row records the role swap |
| Cross-host Postgres replication credentials leaked | Low | High | Replication user scoped to one DB; TLS enforced; rotated quarterly |

### Open questions for operator redline

1. **Option B vs C vs D.** Plan defaults to B (active-passive
   logical replication). Confirm or override.
2. **Auto-failover vs manual.** Plan is manual. Operator may
   want automatic with a quorum-style watchdog.
3. **Per-feature flag granularity.** Plan is one
   `REBOOTER_HUB_ROLE` env. Operator may want finer toggles
   (read-only-but-allow-firmware-deploy, etc.).
4. **Cookie + session semantics across hubs.** With v0.3.3's
   `Domain=.voipguru.org`, a session created on www works on
   www2. But if both hubs run their own session table (shadow-
   shipped v0.2.10), the session ID is unknown to the peer.
   Plan: replicate `user_sessions` via the same Postgres
   replication. Confirm that's acceptable or pick another path.
5. **Audit log dedupe.** When a row replicates from primary to
   secondary, if the secondary's audit code path also writes a
   "received from peer" audit row, we double-count. Plan: tag
   replicated-in audit rows so the secondary-side handler
   skips them. Confirm.
6. **Test fleet on tmrwww02.** v0.2.8 QA fixtures use auto-
   detect by display-name prefix. With two hubs, each has its
   own fleet but tests target the URL. If tests run against
   www2 (secondary) and try to mutate, they'll get 503. The
   QA suite must always target the primary; CI config needs
   updating. Confirm.
7. **Failover impact on the firmware multi-URL fallback.** The
   firmware tries primary, then secondary. If the operator
   manually promotes www2 to primary, the firmware's
   `central_base_url` is still pointing at www first → it gets
   503 → falls through to www2 → works. Good. Confirm this is
   the right model.

## 10. What lands NOW vs LATER

**Now** (this RFC + small surface):
- This RFC committed to disk.
- No code change to the live deployment.
- Settings page gets a stub `Sync` tab with text: "Single-hub
  deployment. Multi-hub sync is designed in RFC-004; not yet
  implemented." So the operator has somewhere to land in the UI
  matching their mental model.

**Later** (gated on operator redline):
- P1 → P7 per §8.

## 10b. DECIDED 2026-05-10 — operator picked Option C (peer-to-peer outbox)

**Operator redline supersedes Section 6.** Sections 7 and 10 (Option-B
specifics) are retained for historical context but should be read as
the rejected design, not the target.

Final architecture (locked):

- **Option C — Application-level event-log sync (active-active).**
  Each hub keeps an append-only `outbox_events` table mirroring the
  audit-event mental model we already use. A small replicator daemon
  on each hub:
  - Polls peer hubs' `/api/v1/sync/since?seq=<n>` over HTTPS with an
    HMAC-signed bearer.
  - Receives a batch of events, applies them idempotently into the
    local DB (UUID-keyed rows; conflict policy = last-writer-wins on
    `event.at` with the existing audit row preserving both versions).
  - Stores the peer's last-applied seq in a `sync_cursors` table.
  - Emits its own outbox entry for every mutation as a side-effect
    of the existing audit-record path (cheap — one extra row per
    audit row).
- **Deletes**: tombstone rows in `outbox_events` with `tombstone_for`
  pointing at the original UUID. The applier writes a row to a
  `tombstones` table and refuses to re-create the same UUID. Avoids
  the classic "delete + later replicate of an old insert" footgun.
- **Auth**: HMAC-signed bearer reusing the existing coordinator HMAC
  pattern (operator already has the secrets infrastructure). No new
  CA.
- **Steady-state latency**: target ~1–3s end-to-end.
- **Partition tolerance**: any single hub can run for arbitrary time
  with the peer offline; on rejoin, both replay each other's outbox
  in seq order with no operator intervention.

Section 6's Option-B reasoning that called Option C "more code, more
risk, but most operator-friendly UX" was the right call — operator
explicitly preferred the symmetric, no-failover-procedure-required
posture even at higher implementation cost. The mental model also
matches what we already do with audit events, so the cognitive load
is smaller than it looks on paper.

**Implementation lands after RFC-003 RBAC ships** (B10 work) because
the outbox events need to carry the new site/group/device scope
claims so a peer hub's applier can enforce scope before writing.

## 11. Constitutional invariants (operator-locked, do not violate)

- **C1 (from webui-redesign-requirements.md §0.2).** Devices
  remain useful when central is unreachable. Multi-hub sync
  preserves this — devices still hit local relay paths
  regardless of central state.
- **C2.** Self-hosted single-host deployments still work
  unchanged. `REBOOTER_HUB_ROLE=single` (default) is the
  identity transformation.
- **C5.** API stability: the existing `/api/v1/device/*`
  contract is frozen. Forward-queue is server-internal; no
  device-visible change.
- **C6.** Open-source DIY-friendly. Nothing in this design
  requires a paid SaaS or third-party dependency. Postgres
  logical replication is in-tree.
