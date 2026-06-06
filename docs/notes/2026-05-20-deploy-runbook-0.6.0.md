# Rebooter Hub (`rebooter-droids`) — Deployment Runbook v0.6.0

**Release:** hub `v0.6.0`
**Release commit:** `origin/main` at `50da75d` ("Add deterministic detect_conflicts() engine, wire into Rules form")
**Target host:** `tmrwww01` (`www.voipguru.org`) — node-1, the primary hub
**Audience:** operator with production SSH + sudo on `tmrwww01`
**Deploy mechanism:** `tools/cut-rebooter-release.sh 0.6.0` run from the source tree on the build host
**Document date basis:** 2026-05-20

> This runbook was verified line-by-line against the actual release files at commit `50da75d`
> (`tools/cut-rebooter-release.sh`, `Dockerfile`, `app/services/bootstrap.py`, `app/config.py`,
> `app/version.py`, `pyproject.toml`, `docs/DEPLOY.md`). Where a fact lives outside the repo
> (the external compose file and `.env`), the step is marked **operator confirms**.

---

## 1. Overview & contents of the release

### 1.1 What v0.6.0 ships

v0.6.0 is the product's **v1 foundation**. It bundles:

- **Organization boundary, phases 1–3** — a multi-tenant `organizations` layer. Every
  Tier-A entity (`sites`, `groups`, `watchdog_rules`, `schedules`, `scenes`,
  `enrollment_tokens`, `external_sensor_sources`, `role_bindings`, `invitations`,
  `audit_events`, `device_announcements`) carries an `organization_id`. On an upgraded
  database a one-shot startup backfill creates a single **"Default Organization"**
  (slug `default`, plan `legacy`) and stamps every existing row to it.
- **Org isolation defaults to SHADOW mode.** The cross-tenant read filter / write
  stamping is wired in but **non-enforcing** at this release. The shadow→enforce flip
  is a **separate, later step** (see §7) and is **NOT part of this deploy.**
- **All Tier-2 features** — scoped API tokens, notifications/webhooks, setup wizard,
  mobile-first dashboard, and related work consolidated onto `main`.
- **v1.x fixes** — Alembic chain linearized after the Tier-2 consolidation; nested-session
  SQLite deadlock in org-boundary hooks fixed; firmware power-summary key-name
  compatibility; internet-mode target cap raised 8→10.
- **Deterministic conflict detection** — `detect_conflicts()` engine wired into the
  Rules form (the tip commit `50da75d`).

726 unit tests are green at this commit.

### 1.2 Schema changes are automatic — there is NO manual Alembic step

`app/services/bootstrap.py::run_startup_bootstrap()` runs on **every container startup**,
before any request is served, and is **idempotent**. It applies:

1. `ensure_schema()` — `Base.metadata.create_all()` under a Postgres advisory lock
   (`CREATE TABLE IF NOT EXISTS`), then `_ensure_columns()` (`ALTER TABLE ... ADD COLUMN
   IF NOT EXISTS` for ~50 additive columns).
2. `ensure_bootstrap_admin()` — reconciles the bootstrap admin (only if the
   `REBOOTER_BOOTSTRAP_ADMIN_*` env vars are set).
3. `ensure_default_organization_backfill()` — **runs first** of the backfills; creates
   the default org and stamps Tier-A rows. Tracked by `runtime_settings` key
   `organization.default_backfilled_at`; runs once per database.
4. `ensure_role_bindings_backfill()` — RBAC role-binding backfill (tracked by
   `rbac.role_bindings_backfilled_at`).
5. `ensure_device_site_id_backfill()` — `Device.site_id` backfill (tracked by
   `device.site_id_not_null_backfilled_at`).
6. `_ensure_constraints()` — applies the `Device.site_id` NOT NULL constraint after the
   backfill.

Each backfill is one-shot and `runtime_settings`-tracked, so restarting the container
re-runs `run_startup_bootstrap()` harmlessly. Backfill exceptions are caught and logged —
they never block the container from coming up.

> **Repo note (informational, no action):** the docstrings in `bootstrap.py` refer to the
> NOT-NULL / constraint-hardening migration as "alembic 0005"; the actual file in
> `migrations/versions/` is `0007_org_constraint_hardening.py`. The comment numbering is
> stale. It has **no runtime effect** — the startup bootstrap path is what applies the
> schema; Alembic migrations are not invoked by the container entrypoint
> (`CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:create_app()"]`). Do not run Alembic
> manually as part of this deploy.

### 1.3 Prerequisites & access needed

| Requirement | Why |
|---|---|
| SSH to `tmrwww01` (short hostname only, never the FQDN) with sudo | Run the deploy + Docker commands |
| `sudo` on `tmrwww01` | `docker` requires sudo on these hosts; the release script calls `sudo docker ...` |
| Docker Hub login as `dblagbro` (`sudo docker login`) | Script step 6 does `docker buildx build --push` to `dblagbro/rebooter-droids` |
| `gh` CLI authenticated (`gh auth status`) | Script step 5 does `gh release create` |
| Git push auth for `origin` (`rebooter-droids` GitHub) | Script step 4 does `git push origin main` + the tag |
| The source tree present on the build host with `main` synced to `50da75d` | Script bumps version/commits/tags from the working tree |
| External compose file + `.env` present on `tmrwww01` | Script step 7 recreates the container via compose — **operator confirms** |

> **Build host vs. target host.** `tools/cut-rebooter-release.sh` does everything in one
> run: it commits/tags/pushes from the local source tree, builds+pushes the image, and
> then recreates the container on the host it runs on. The script's compose path
> (`/home/dblagbro/docker/docker-compose.yml` + `/home/dblagbro/docker/.env`) and the
> verification curl to `https://www.voipguru.org/rebooter/api/v1/version` mean the
> intended build host **is `tmrwww01` itself**. Run this runbook on `tmrwww01`.
>
> **operator confirms:** the live compose file on `tmrwww01` defines the
> `rebooter-droids` and `rebooter-droids-pg` services. The compose file under
> `/home/dblagbro/docker/` on the coordinator workstation does **not** contain those
> services (it only has `coordinator-hub-helper`) — that workstation is not the deploy
> target. Before running the deploy, confirm on `tmrwww01`:
> ```
> grep -E 'rebooter-droids' /home/dblagbro/docker/docker-compose.yml
> test -f /home/dblagbro/docker/.env && echo ".env present"
> ```
> Both must succeed. If they do not, stop — the host is not provisioned for this deploy.

---

## 2. Pre-flight checks

Run all of §2 on `tmrwww01` before touching anything.

### 2.1 Confirm `origin/main` is the release commit

```
git -C /mnt/s/code/rebooter-droids fetch origin
git -C /mnt/s/code/rebooter-droids rev-parse origin/main
```

**Expected output:**
```
50da75dfe4168245ad82eda6369e0c493611c48e
```

If the SHA is not `50da75d…`, **stop** — the release commit is not what this runbook
documents.

### 2.2 Sync the deploying checkout's local `main` to `50da75d`

The release script bumps the version and commits **in the local working tree**, so the
local `main` must be exactly at `50da75d` first.

> **GOTCHA — `data/pg` permissions break plain `git status` / `checkout`.**
> The source tree contains `data/pg/` (the local Postgres bind-mount), which is
> root-owned `0700` (`drwx------ dnsmasq dblagbro`). A plain `git status` or `git
> checkout` walks into it and errors with `Permission denied` / `could not open
> directory 'data/pg/'`. This is cosmetic — it does not corrupt anything — but it makes
> the output noisy and can mask a real problem. Use the targeted commands below, which
> never need to stat `data/pg/`.

Check where local `main` currently is:

```
git -C /mnt/s/code/rebooter-droids rev-parse main
```

If this is **not** `50da75d…` (e.g. it reports `bbb9293…`, which is the known
behind-by-24 state), fast-forward it. Fast-forward is safe **only if local `main` is a
strict ancestor of `origin/main`** — verify, then advance:

```
# 1. Verify local main is a strict ancestor of origin/main (fast-forward is safe).
git -C /mnt/s/code/rebooter-droids merge-base --is-ancestor main origin/main \
  && echo "FF-SAFE" || echo "NOT-FF-SAFE — STOP"
```

**Expected:** `FF-SAFE`. If it prints `NOT-FF-SAFE — STOP`, local `main` has diverged —
do not force anything; investigate first.

```
# 2. Make sure you are on main, then fast-forward it to origin/main.
git -C /mnt/s/code/rebooter-droids symbolic-ref --short HEAD
```

**Expected:** `main`. If it is not `main`:

```
git -C /mnt/s/code/rebooter-droids checkout main
```

Then fast-forward (`--ff-only` refuses anything that is not a clean fast-forward):

```
git -C /mnt/s/code/rebooter-droids merge --ff-only origin/main
git -C /mnt/s/code/rebooter-droids rev-parse main
```

**Expected output of the final `rev-parse`:**
```
50da75dfe4168245ad82eda6369e0c493611c48e
```

Confirm the working tree is clean of staged/modified tracked files (untracked design
notes under `docs/notes/` may exist and are harmless — the release script only `git add`s
`pyproject.toml`, `app/version.py`, `CHANGELOG.md`):

```
git -C /mnt/s/code/rebooter-droids status --porcelain --untracked-files=no -- \
  pyproject.toml app/version.py CHANGELOG.md app templates static migrations
```

**Expected:** empty output (no tracked changes). The `-- <pathspecs>` form keeps git from
descending into `data/pg/`, avoiding the permission noise.

### 2.3 Confirm the current source version

```
grep '^version = ' /mnt/s/code/rebooter-droids/pyproject.toml
grep '^__version__' /mnt/s/code/rebooter-droids/app/version.py
```

**Expected output:**
```
version = "0.5.102"
__version__ = "0.5.102"
```

At `50da75d` the tree is still at `0.5.102`; the release script bumps `0.5.102 → 0.6.0`.
If it already reads `0.6.0`, the release was already cut — **stop** and do not re-run.

### 2.4 Disk space

The build produces a fresh Docker image and pushes layers; ensure headroom.

```
df -h / /var/lib/docker /mnt/s
```

**Expected / required:** at least a few GB free on `/var/lib/docker` (image build +
layer cache) and on `/mnt/s` (the RAID6 source tree). Investigate before proceeding if
any filesystem is above ~90%.

### 2.5 Container health — pre-deploy baseline

```
sudo docker ps --filter name=rebooter-droids --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

**Expected:** two rows — `rebooter-droids` and `rebooter-droids-pg` — both `Up` and
`rebooter-droids` showing `(healthy)`. Record the **current image tag** of
`rebooter-droids` (the `latest` digest underneath it) — you need it for rollback (§6):

```
sudo docker inspect --format '{{.Config.Image}} {{.Image}}' rebooter-droids
```

**Record this line.** It is the previous image identity. Also capture the current live
version for comparison after deploy:

```
curl -fsS https://www.voipguru.org/rebooter/api/v1/version
```

**Expected:** a JSON envelope, e.g.
`{"ok":true,"data":{"service":"rebooter-droids","version":"0.5.102","server_time":"..."}}`.

---

## 3. BACKUP — production Postgres (MANDATORY)

Back up the database **before anything else changes**. This dump is the rollback safety
net (§6). The org-boundary backfill mutates many tables on first run; if it goes wrong
this dump is the only clean way back.

### 3.1 Take the dump

The `rebooter-droids-pg` container runs `postgres:16`; the database is `rebooter`, role
`rebooter`. The DB has **no published host port** by design, so dump from inside the
container.

```
mkdir -p /home/dblagbro/backups/rebooter-droids
TS=$(date -u +%Y%m%dT%H%M%SZ)
sudo docker exec rebooter-droids-pg \
  pg_dump -U rebooter -d rebooter --format=custom \
  > /home/dblagbro/backups/rebooter-droids/rebooter-${TS}-pre-0.6.0.dump
echo "wrote /home/dblagbro/backups/rebooter-droids/rebooter-${TS}-pre-0.6.0.dump"
```

**Expected:** the `echo` prints the path; no `pg_dump` errors on stderr.

> **operator confirms:** the DB name/role are `rebooter`/`rebooter` per
> `app/config.py` default `REBOOTER_DATABASE_URL`
> (`postgresql+psycopg://rebooter:REMOVED-CREDENTIAL-ROTATED-20260828@rebooter-droids-pg:5432/rebooter`). If the
> live `.env` overrides `REBOOTER_PG_PASSWORD` / the DB name, adjust `-U` / `-d`
> accordingly. `pg_dump` inside the container does not need the password (peer/local
> trust); if it prompts, set `PGPASSWORD` from the `.env` value.

### 3.2 Verify the dump

A custom-format dump must be non-trivial in size and must list its contents cleanly:

```
ls -lh /home/dblagbro/backups/rebooter-droids/rebooter-${TS}-pre-0.6.0.dump
sudo docker exec -i rebooter-droids-pg pg_restore --list \
  < /home/dblagbro/backups/rebooter-droids/rebooter-${TS}-pre-0.6.0.dump | head -20
```

**Expected:** the file is non-empty (typically well over 100 KB for a populated DB); the
`pg_restore --list` output shows a table of contents (`TABLE DATA`, `INDEX`, etc.) with no
error. If `pg_restore --list` errors or the file is near-zero bytes, **stop** — the
backup is invalid; do not deploy.

Keep the value of `$TS` for §6.

---

## 4. Deploy — `tools/cut-rebooter-release.sh 0.6.0`

### 4.1 What the script does (verified against the file at `50da75d`)

`tools/cut-rebooter-release.sh` runs `set -euo pipefail` — **it aborts on the first
failure of any step**, leaving nothing partially shipped past the point of failure. In
order:

1. **Bump version.** Reads the current version from `pyproject.toml`, then `sed`-replaces
   `version = "0.5.102"` → `version = "0.6.0"` in `pyproject.toml` and
   `__version__ = "0.5.102"` → `__version__ = "0.6.0"` in `app/version.py`.
2. **CHANGELOG.** If `CHANGELOG.md` has no `## [0.6.0]` heading, inserts
   `## [0.6.0] - <today UTC>` right under `## [Unreleased]`.
3. **Commit + tag.** `git add pyproject.toml app/version.py CHANGELOG.md`,
   `git commit -m "chore: release v0.6.0"`, `git tag -a v0.6.0 -m "v0.6.0"`.
4. **Push.** `git push origin main` then `git push origin v0.6.0`. → **needs git push
   auth for `origin`.**
5. **GitHub release.** Extracts the `## [0.6.0]` section body from `CHANGELOG.md` and runs
   `gh release create v0.6.0 --title v0.6.0 --notes "<excerpt>"`. → **needs `gh` auth.**
6. **Build + push image.** `sudo docker buildx build --push -t
   dblagbro/rebooter-droids:0.6.0 -t dblagbro/rebooter-droids:latest <repo-root>`. →
   **needs `sudo` + Docker Hub login.** The `Dockerfile` builds `python:3.12-slim`,
   installs `curl ca-certificates iputils-ping snmp`, `pip install .` from
   `pyproject.toml`, copies `app/ templates/ static/ migrations/ alembic.ini
   gunicorn.conf.py`, exposes `8090`, and sets a `HEALTHCHECK` curling
   `/api/v1/version`. Entrypoint: `gunicorn -c gunicorn.conf.py app:create_app()`.
7. **Recreate the container.** `sudo docker compose -f
   /home/dblagbro/docker/docker-compose.yml --env-file /home/dblagbro/docker/.env up -d
   --force-recreate --no-deps rebooter-droids`. `--no-deps` means **only the app
   container is recreated** — `rebooter-droids-pg` is untouched. On the new container's
   first boot, `create_app()` runs `run_startup_bootstrap()` (§1.2) and applies the org
   schema + backfills.
8. **Verify prod.** `sleep 3`, then `curl -fsS
   https://www.voipguru.org/rebooter/api/v1/version` and `grep -q '"version":"0.6.0"'`
   the response. If the grep fails, the script exits non-zero.

> **operator confirms:** step 7 depends on `docker compose` working on `tmrwww01`. If
> the host has only the legacy `docker-compose` v1 CLI (which is known to be incompatible
> with newer Docker on at least one host in this fleet), the script's compose step will
> fail. Confirm `sudo docker compose version` succeeds on `tmrwww01` before running.
> If it does not, the deploy must be done with the manual fallback in §4.4.

> **Note — empty GitHub release notes.** At `50da75d` the `## [Unreleased]` section of
> `CHANGELOG.md` is **empty**. Script step 2 inserts the `## [0.6.0]` heading but no body,
> so step 5's changelog extraction yields nothing and `gh release create` falls back to
> the literal notes `Release v0.6.0`. This is cosmetic and does not abort the deploy. If
> you want real release notes, write a `## [Unreleased]` body **before** running the
> script, or edit the GitHub release afterward.

### 4.2 Pre-auth the three credentials

Before running the script, confirm all three auth contexts are live so the script does
not abort mid-way:

```
gh auth status
sudo docker login
git -C /mnt/s/code/rebooter-droids ls-remote --heads origin >/dev/null && echo "git push auth OK"
```

**Expected:** `gh auth status` shows logged in; `docker login` reports `Login Succeeded`
(or already-logged-in); the `git ls-remote` line prints `git push auth OK`.

### 4.3 Run the deploy

From the source tree root on `tmrwww01`:

```
cd /mnt/s/code/rebooter-droids
sudo ./tools/cut-rebooter-release.sh 0.6.0
```

> Run with `sudo` so the script's `sudo docker ...` sub-commands do not prompt
> mid-build. Working directory must be the repo root (the script also `cd`s to its own
> repo root, but starting there avoids ambiguity).

**Success looks like** — the script prints, in order:

```
→ Bumping 0.5.102 → 0.6.0
...
[git commit / git tag / git push / gh release output]
...
→ Building + pushing image
[buildx build output ... pushed]
→ Recreating local container
[compose recreate output]
→ Verifying prod
{"ok":true,"data":{"service":"rebooter-droids","version":"0.6.0","server_time":"..."}}
✓ released v0.6.0
```

The final line `✓ released v0.6.0` means every step succeeded. If the script exits
**without** that line, the deploy failed at the last command printed — go to §6
(Rollback).

### 4.4 Manual fallback (only if `docker compose` is unavailable on `tmrwww01`)

If §4.2 showed `docker compose` does not work on the host, the script aborts at step 7
*after* having already committed, tagged, pushed, and pushed the image. In that case the
git/image side is done; finish the container recreate by hand (per `docs/DEPLOY.md`):

```
sudo docker pull dblagbro/rebooter-droids:0.6.0
sudo docker compose -f /home/dblagbro/docker/docker-compose.yml \
  --env-file /home/dblagbro/docker/.env \
  up -d --force-recreate --no-deps rebooter-droids
```

If `docker compose` truly cannot run, recreate the single container directly with
`docker run` using the same image/env/volumes the compose service defines — **operator
confirms** the exact published port (`8090`), network (`default`), and volume mounts
(`/data` ← firmware/uploads, plus the DB link) from the live compose file. Do not
proceed by guessing; read the compose file.

---

## 5. Verification

Run all of §5 after the script reports success.

### 5.1 Version endpoint returns 0.6.0

```
curl -fsS https://www.voipguru.org/rebooter/api/v1/version
```

**Expected:** `{"ok":true,"data":{"service":"rebooter-droids","version":"0.6.0","server_time":"<UTC ISO>"}}`

### 5.2 Dashboard loads

```
curl -fsS -o /dev/null -w '%{http_code}\n' https://www.voipguru.org/rebooter/
curl -fsS -o /dev/null -w '%{http_code}\n' https://www.voipguru.org/rebooter/app/
```

**Expected:** `/rebooter/` returns `302` (redirect to `/rebooter/app/`); `/rebooter/app/`
returns `200` (or `302` to the login page if not authenticated — also acceptable). Then
open `https://www.voipguru.org/rebooter/app/` in a browser and confirm the dashboard
renders and you can log in.

### 5.3 Firmware endpoint

The public firmware-channel pointer (`GET /api/v1/firmware/<channel>/latest`, channels
`dev`/`beta`/`stable`):

```
curl -fsS -o /dev/null -w '%{http_code}\n' \
  https://www.voipguru.org/rebooter/api/v1/firmware/stable/latest
curl -fsSI https://www.voipguru.org/rebooter/firmware/
```

**Expected:** the `.../firmware/stable/latest` call returns `302` if a stable release
exists, or `404` (`{"code":"no_release",...}`) if the `stable` channel is empty — both
are healthy responses. The static `/rebooter/firmware/` index returns `403` if the
directory listing is empty (expected per `docs/DEPLOY.md`).

### 5.4 Container health + logs

```
sudo docker ps --filter name=rebooter-droids --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

**Expected:** `rebooter-droids` is `Up` and `(healthy)` on image
`dblagbro/rebooter-droids:latest` (now pointing at the `0.6.0` digest);
`rebooter-droids-pg` still `Up`. The `HEALTHCHECK` (curl of `/api/v1/version`, 30s
interval, 20s start-period, 3 retries) reaching `(healthy)` confirms the app is serving.

```
sudo docker logs --since 10m rebooter-droids 2>&1 | tail -80
```

**Expected:** gunicorn boots cleanly, no Python tracebacks, workers listening on
`0.0.0.0:8090`.

### 5.5 Confirm the startup bootstrap applied the org schema cleanly

This is the key v0.6.0-specific check. Grep the container logs from the new container's
boot:

```
sudo docker logs --since 15m rebooter-droids 2>&1 | grep -Ei \
  'bootstrap|organization|backfill|role.bindings|site_id|constraint'
```

**Expected on an upgraded database, first boot of 0.6.0** — lines such as:

- `Running one-shot default-organization backfill (org-boundary phase 1)`
- `default-organization backfill: assigned N <table> row(s) to org org_...` (one per
  Tier-A table that had rows)
- `default-organization backfill: N Tier-A row(s) assigned to org org_... across 11 tables`
- `default-organization backfill: created N organization_membership row(s)`
- `default-organization backfill complete`
- `Running one-shot RBAC role-binding backfill ...` / `RBAC backfill inserted N role_bindings rows`
- `Running one-shot Device.site_id NOT NULL backfill ...` (or `No devices with null site_id`)

**Must NOT see:** `Default-organization backfill failed`, `RBAC role-bindings backfill
failed`, `Device.site_id backfill failed`, `Pending constraints application failed`, or
any traceback. Those backfill exceptions are caught (the container still comes up) but
they mean the org schema did **not** fully apply — treat as a failed verification and go
to §6.

> On a **restart of an already-bootstrapped 0.6.0 container** these backfills are no-ops
> (each is `runtime_settings`-tracked) — you will see no backfill log lines, which is
> also correct. The lines above appear only on the **first** 0.6.0 boot against a given
> database.

Optional direct DB confirmation (the default org exists and is stamped):

```
sudo docker exec rebooter-droids-pg psql -U rebooter -d rebooter -c \
  "SELECT id, name, slug, plan, status FROM organizations;"
sudo docker exec rebooter-droids-pg psql -U rebooter -d rebooter -c \
  "SELECT key, value FROM runtime_settings WHERE key LIKE 'organization.%' OR key LIKE 'rbac.%' OR key LIKE 'device.%';"
```

**Expected:** exactly one `organizations` row — `Default Organization`, slug `default`,
plan `legacy`, status `active`; `runtime_settings` shows
`organization.default_backfilled_at`, `rbac.role_bindings_backfilled_at`, and
`device.site_id_not_null_backfilled_at` all set to timestamps.

### 5.6 Git/release artifacts

```
git -C /mnt/s/code/rebooter-droids rev-parse v0.6.0
gh release view v0.6.0 --repo dblagbro/rebooter-droids --json tagName,name,createdAt
sudo docker image ls dblagbro/rebooter-droids --format '{{.Repository}}:{{.Tag}} {{.ID}}'
```

**Expected:** the `v0.6.0` tag resolves; the GitHub release `v0.6.0` exists; both
`dblagbro/rebooter-droids:0.6.0` and `:latest` images are present locally.

---

## 6. ROLLBACK

Trigger rollback if the deploy script aborts past the image-push step, or if §5
verification fails (especially §5.5 — a failed org backfill).

### 6.1 Decide the scope

- **Verification failed but the container is up and the DB looks intact** → an image
  rollback alone (§6.2) may suffice; the previous app version reads the same schema
  fine (the org columns are additive/nullable on the old code path).
- **The org backfill mutated the DB and left it inconsistent** → do the full rollback:
  image (§6.2) **and** database restore (§6.3).

### 6.2 Revert the Docker image to the previous tag

The previous running image identity was recorded in §2.5. Roll `rebooter-droids` back to
the **previous version tag** — the prior release was `0.5.102`:

```
sudo docker pull dblagbro/rebooter-droids:0.5.102
```

Re-point the container at it. The compose service uses the `:latest` tag, so either
recreate from the explicit prior tag or move `:latest` back:

```
# Re-tag the previous version as :latest so compose recreates onto it.
sudo docker tag dblagbro/rebooter-droids:0.5.102 dblagbro/rebooter-droids:latest
sudo docker compose -f /home/dblagbro/docker/docker-compose.yml \
  --env-file /home/dblagbro/docker/.env \
  up -d --force-recreate --no-deps rebooter-droids
```

> **operator confirms:** if the live compose file pins an explicit tag rather than
> `:latest`, edit that pin to `0.5.102` instead of re-tagging. Confirm by reading the
> `image:` line for the `rebooter-droids` service.

### 6.3 Restore the database from the backup

Only if §6.1 calls for it. Use the dump from §3 (`$TS` value).

```
# Stop the app so nothing writes during the restore (DB container stays up).
sudo docker stop rebooter-droids

# Drop and recreate the database, then restore the custom-format dump.
sudo docker exec rebooter-droids-pg psql -U rebooter -d postgres -c \
  "DROP DATABASE rebooter WITH (FORCE);"
sudo docker exec rebooter-droids-pg psql -U rebooter -d postgres -c \
  "CREATE DATABASE rebooter OWNER rebooter;"
sudo docker exec -i rebooter-droids-pg \
  pg_restore -U rebooter -d rebooter --no-owner \
  < /home/dblagbro/backups/rebooter-droids/rebooter-${TS}-pre-0.6.0.dump

# Bring the (rolled-back) app back up.
sudo docker start rebooter-droids
```

**Expected:** `DROP DATABASE` / `CREATE DATABASE` succeed; `pg_restore` finishes with no
errors (warnings about already-existing roles are benign with `--no-owner`).

> **operator confirms:** `DROP DATABASE ... WITH (FORCE)` requires PostgreSQL 13+
> (`postgres:16` supports it). If any other client holds a connection, `FORCE` terminates
> it — acceptable here since the app is stopped.

### 6.4 Confirm the rollback

```
curl -fsS https://www.voipguru.org/rebooter/api/v1/version
sudo docker ps --filter name=rebooter-droids --format 'table {{.Names}}\t{{.Status}}'
sudo docker logs --since 5m rebooter-droids 2>&1 | tail -40
```

**Expected:** `/api/v1/version` reports `"version":"0.5.102"`; both containers `Up`,
`rebooter-droids` `(healthy)`; logs show a clean boot with no tracebacks.

> The `v0.6.0` git tag and GitHub release remain after a rollback — they are harmless
> history. If you intend to re-cut `0.6.0` later, delete the tag/release first
> (`git push origin :refs/tags/v0.6.0`, `gh release delete v0.6.0`) or the script's
> tag step will fail.

---

## 7. Post-deploy notes

- **Org isolation stays in SHADOW mode.** v0.6.0 ships the organization boundary
  **non-enforcing**. The shadow→enforce rollout is a **separate, later operation** — it
  requires **at least 7 days of clean shadow-mode logs** before the enforce flip, and it
  is **explicitly NOT part of this deploy.** Do not flip enforcement as part of, or
  immediately after, this release. Track shadow-mode logs over the soak window; the
  enforce flip is its own change with its own runbook.
- **Firmware `0.2.0` on the `dev/` channel is out of scope.** Staging firmware `0.2.0`
  to the `dev` channel is a **firmware-team task** and is **not** covered by this hub
  deploy. This runbook ships the *hub* `v0.6.0` only.
- **`sync.enabled` is not touched.** Multi-hub sync state is unchanged by this deploy.
  Per the v0.5.102 changelog the live cluster already has `sync.enabled=true` on both
  hubs and converged; no action here.
- **Bootstrap admin password.** If this deploy ever resulted in a fresh database, the
  bootstrap admin is created from `REBOOTER_BOOTSTRAP_ADMIN_*` env vars on first init —
  rotate that password via the UI immediately. On an upgrade-in-place (the normal case)
  the existing admin password is preserved (`ensure_bootstrap_admin` only reconciles
  privileges unless `REBOOTER_BOOTSTRAP_ADMIN_FORCE_PASSWORD_ON_STARTUP=1`).
- **Keep the §3 backup** for at least the shadow soak window in case a latent
  org-boundary issue surfaces later.

---

## Appendix — facts the repo could not confirm (operator must verify on `tmrwww01`)

1. **The live compose file content.** The external compose file + `.env` live at
   `/home/dblagbro/docker/` on `tmrwww01` and are **not in the repo**. The compose file
   under `/home/dblagbro/docker/` on the coordinator workstation contains only
   `coordinator-hub-helper` — not `rebooter-droids`/`rebooter-droids-pg` — confirming
   that workstation is **not** the deploy target. The operator must confirm on
   `tmrwww01` that the compose file defines both `rebooter-droids` and
   `rebooter-droids-pg` services, the `:latest` image pin (vs. an explicit tag), the
   published port `8090`, the `default` network, and the `/data` volume mounts.
2. **`.env` values.** `REBOOTER_PG_PASSWORD`, `REBOOTER_SECRET_KEY`, the DB name/role
   (defaults `rebooter`/`rebooter`), and `REBOOTER_BOOTSTRAP_ADMIN_*` are in the live
   `.env` only.
3. **`docker compose` availability on `tmrwww01`.** The release script step 7 and the
   rollback both call `docker compose`. At least one host in this fleet runs a legacy
   `docker-compose` v1 CLI incompatible with the installed Docker. The operator must
   confirm `sudo docker compose version` works on `tmrwww01`; if not, use the §4.4
   manual fallback.
4. **Docker Hub / `gh` / git push credentials** are environmental on the build host and
   cannot be checked from the repo — verify per §4.2.
5. **Stale comment, no action:** `app/services/bootstrap.py` docstrings reference
   "alembic 0005" for the org NOT-NULL hardening; the actual migration file is
   `migrations/versions/0007_org_constraint_hardening.py`. The startup bootstrap, not
   Alembic, applies the schema at container start — the comment numbering has no runtime
   effect.
</content>
</invoke>
