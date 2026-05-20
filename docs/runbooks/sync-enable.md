# Runbook — enabling B11 multi-hub sync

> **Status:** v0.5.102. The B11 applier is **code-complete** (v0.5.70–.72
> shipped full create/update LWW + natural-key reconciliation + tombstone
> replay protection; 18 in-process tests gate it). The remaining work
> before flipping `sync.enabled=true` in production is operator-side —
> this runbook is the playbook.

## What the flip does

When `sync.enabled=true` on a hub, that hub's APScheduler runs the
**sync-replicator tick** every ~10 s. The tick:

1. Loads `sync.peer_hubs` from runtime-settings (a JSON list of
   `{id, base_url}`).
2. For each peer, builds an HMAC-bearer token from `sync.hmac_key` +
   the peer's `id`.
3. GETs `${peer.base_url}/api/v1/sync/since?from_seq=${cursor}&limit=100`.
4. Feeds each returned `OutboxEvent` through `apply_outbox_event()` —
   suppressing emission so the applier's own writes don't re-emit.
5. Advances the local `SyncCursor` for that peer.

The applier converges the four syncable entity types: **Device, Site,
Group, User**. Heartbeats, commands, watchdog rules, schedules,
firmware releases, deployments, audit events, and external-sensor
samples are **not** synced — they stay per-hub.

LWW direction: a write with strictly newer `updated_at` wins. Equal
or older is a no-op. Deletes are tombstoned and tombstones are
sticky (re-create of the same id is rejected). Natural-key
reconciliation lets the same logical Site / User / Group exist on both
hubs under different ids and converge to one row per key.

## Preconditions checklist

On **both** hubs, in `Settings → Sync`:

- [ ] **`sync.hub_id`** set and **different** on each hub. The cursor
      key is `(local hub, peer hub_id)`; identical hub_ids would alias.
      Typical: `www` on tmrwww01, `www2` on tmrwww02.
- [ ] **`sync.hmac_key`** set to the same 64-char hex value on both
      hubs. Generate with `openssl rand -hex 32`. Never commit it.
- [ ] **`sync.peer_hubs`** lists the *other* hub as a JSON array:
      `[{"id": "www2", "url": "https://www2.example.com/rebooter"}]`
      on hub-A, and the inverse on hub-B. (Field name is `url`, not
      `base_url` — what the replicator + admin status endpoint read.)
- [ ] **Hub-to-hub reachability** — `curl` the peer's
      `/api/v1/version` from inside each hub's container. nginx +
      HTTPS must both work.
- [ ] **Outbox migrations applied** — `Base.metadata.create_all()`
      runs on every boot; verify the `outbox_events`, `sync_cursors`,
      and `tombstones` tables exist on both hubs.
- [ ] **Fresh backups** — `pg_dump` of both hub DBs into
      `/home/dblagbro/docker/backups/manual/pre-b11-flip-$(date -u +%Y%m%d).sql`.
      Verified restorable.
- [ ] **Image-pin both hubs** — `docker tag …:0.5.102 …:pre-b11-flip-20260519`
      and push, so a single-command image rollback is possible.

## Preflight — run before flipping

The v0.5.102 ship adds `scripts/sync-dual-hub-preflight.sh`. Run it
twice: once **before** flipping (read-only), once **after** (the
convergence test).

### Phase 1 — read-only

Confirms preconditions without writing anything:

```bash
./scripts/sync-dual-hub-preflight.sh \
    --hub-a https://www.voipguru.org/rebooter \
    --token-a "$REBOOTER_ADMIN_TOKEN_A" \
    --hub-b https://www2.example.com/rebooter \
    --token-b "$REBOOTER_ADMIN_TOKEN_B" \
    --read-only
```

The script:
- pings both hubs' `/api/v1/version`,
- reads `/api/v1/admin/sync/status` on each (admin-Bearer-auth wrapper
  added in v0.5.102 for exactly this purpose — the wire endpoint is
  HMAC-only),
- confirms hub_ids differ, peers configured, HMAC key set, and prints
  the current outbox + cursor snapshot.

**Stop here** if any check fails. Fix the underlying config and re-run.

### Phase 2 — the flip

In **Settings → Sync** on both hubs, tick `sync.enabled` and save.
Watch the app logs on both hubs for ~30 s:

```bash
docker logs -f rebooter-droids 2>&1 | grep -i sync
```

You should see `sync_replicator.tick` lines logging every ~10 s and
`Sync: applied N events from peer www2` lines once any outbox event
is emitted (initially: every CRUD on a syncable entity).

### Phase 3 — convergence test

Re-run the preflight **without** `--read-only`. It will create a
short-lived marker Site on hub-A, poll hub-B until the Site appears,
delete it on hub-A, and poll hub-B until the deletion is reflected.
Both convergences should complete in under 30 s.

```bash
./scripts/sync-dual-hub-preflight.sh \
    --hub-a https://www.voipguru.org/rebooter \
    --token-a "$REBOOTER_ADMIN_TOKEN_A" \
    --hub-b https://www2.example.com/rebooter \
    --token-b "$REBOOTER_ADMIN_TOKEN_B"
```

Expected last lines:

```
== PREFLIGHT PASSED ==
  ✓ create + delete converged both ways
  ✓ safe to enter the soak window per docs/runbooks/sync-enable.md
```

If convergence does not complete within the 60 s timeout, **flip
`sync.enabled` off on both hubs immediately** (kill switch — see
below) and investigate before re-trying.

## Soak window

After a clean preflight, **leave `sync.enabled=true` for 24 hours**
without making any other changes to either hub. Check at the 1 h,
4 h, and 24 h marks:

- `/api/v1/admin/sync/status` on each hub should show `cursors[].last_error`
  empty and `last_seq` advancing.
- Row counts for the syncable entity types should converge — pick
  Users (smallest, most stable) and compare `count(*)` on both hubs.
- No new `Sync: LWW skip` log lines unless someone is actively editing
  the same entity on both hubs.

If anything looks off, kill-switch and capture logs before
investigating.

## Kill switch

Flip `sync.enabled=false` on **both** hubs (Settings → Sync, untick,
save). The replicator tick checks the flag at the top of every tick;
the next tick (within 10 s) will short-circuit. **No data revert is
needed** — the kill switch only stops new convergence; rows already
applied stay applied.

For an emergency kill from the command line (no UI):

```bash
docker exec rebooter-droids python3 -c "
from app import create_app
from app.services import runtime_settings as rs
app = create_app()
with app.app_context():
    rs.set_('sync.enabled', False)
print('sync.enabled = False')
"
```

## Rollback

If the soak shows correctness problems (not just convergence lag):

1. **Kill switch first** (above) — stops further drift.
2. **Image revert** if the issue is suspected in code:
   ```bash
   docker pull dblagbro/rebooter-droids:pre-b11-flip-20260519
   docker stop rebooter-droids && docker rm rebooter-droids
   docker run -d --name rebooter-droids ... pre-b11-flip-20260519
   ```
3. **Data revert** is the heavy hammer — restore one table at a time
   from the pre-flip `pg_dump`:
   ```bash
   pg_restore --table=sites --data-only \
       /home/dblagbro/docker/backups/manual/pre-b11-flip-20260519.sql \
       | docker exec -i rebooter-droids-pg psql -U rebooter
   ```
   Never restore the whole DB unless absolutely necessary — it would
   roll back every non-synced write since the backup too.

## What's not covered (yet)

- **Three-or-more-hub topology.** The applier is per-pair and the
  natural-key reconciliation is pairwise; a three-hub mesh would work
  but has not been soaked.
- **Wider syncable model set.** Today: Device, Site, Group, User.
  Adding (e.g.) `WatchdogRule` to `_SYNCABLE_MODELS` would require a
  fresh review of LWW semantics for that entity + a new natural-key
  decision.
- **Sync-replicator metrics.** Today the only visibility is log lines
  + `/api/v1/admin/sync/status`. A Prometheus exporter or a richer
  dashboard tile is a follow-up.

## See also

- `docs/RFC-004-multi-hub-sync.md` — the design decisions (Option C
  outbox-replicator with LWW).
- `app/services/sync.py` — applier source.
- `app/services/sync_replicator.py` — replicator tick.
- `tests/qa/test_v0570_b11_applier.py` /
  `test_v0571_b11_emission.py` /
  `test_v0572_b11_natural_key.py` — the gated coverage.
- `scripts/sync-dual-hub-preflight.sh` — the harness this runbook walks
  the operator through.
