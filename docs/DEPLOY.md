# Deployment Runbook

This is the operator runbook for **rebooter-droids**.

The container runs alongside other services in the single
`/home/dblagbro/docker/docker-compose.yml`. Public traffic is routed by the
single `/home/dblagbro/docker/config/nginx/nginx.conf` under the
`/rebooter/` URL prefix.

## Source layout

```
/mnt/s/code/rebooter-droids/                ← RAID6, the source
  ├── app/                                  Flask app
  ├── templates/, static/                   server-rendered admin UI
  ├── docs/                                 SPEC, API, DEPLOY, …
  ├── data/firmware/                        nginx-served firmware blobs
  ├── data/uploads/                         legacy/staging (now unused)
  └── data/pg/cluster/                      Postgres data dir

/home/dblagbro/docker/rebooter-droids       ← symlink → /mnt/s/code/rebooter-droids
```

## Containers

| Name | Image | Role |
|---|---|---|
| `rebooter-droids` | `dblagbro/rebooter-droids:latest` | Flask app + APScheduler |
| `rebooter-droids-pg` | `postgres:16` | private DB (no published port) |

Both join the existing `default` docker network.

## Environment

Set in `/home/dblagbro/docker/.env`:

```
REBOOTER_PG_PASSWORD=<random>
REBOOTER_SECRET_KEY=<random hex>
REBOOTER_BOOTSTRAP_ADMIN_EMAIL=<admin email>
REBOOTER_BOOTSTRAP_ADMIN_PASSWORD=<initial admin password>
```

The bootstrap admin is only created on first DB init. Rotate the password
through the API/UI as soon as you sign in.

## Build and deploy

Build:

```
sudo docker compose -f /home/dblagbro/docker/docker-compose.yml build rebooter-droids
```

Recreate just this container (safe — no other containers touched):

```
sudo docker compose -f /home/dblagbro/docker/docker-compose.yml \
  up -d --force-recreate --no-deps rebooter-droids
```

After the recreate, verify on prod:

```
curl -fsS https://www.voipguru.org/rebooter/api/v1/version
```

## Nginx changes

Three locations were appended to the existing voipguru.org main server
block:

- `/rebooter/firmware/` — direct alias to
  `/mnt/s/code/rebooter-droids/data/firmware/` (no upstream).
- `/rebooter/` (catch-all) — proxy_pass to `http://rebooter-droids:8090/`.
- `/rebooter/` (exact) — 302 to `/rebooter/app/`.

Reload nginx after editing the conf:

```
sudo docker exec nginx nginx -s reload
```

Note: when the host file is replaced via atomic rename (most editors do
this), single-file bind mounts inside the running nginx container can stick
to the old inode. If the reload doesn't appear to pick up changes, restart
the nginx container instead — that's a safe operation:

```
sudo docker restart nginx
```

## Permissions on `/mnt/s/code/rebooter-droids`

Nginx workers run as `nginx` inside their container; the file path needs
world-traversable directories from `/mnt/s/` all the way down. Set:

```
sudo chmod 0755 /mnt/s/code/rebooter-droids \
                /mnt/s/code/rebooter-droids/data \
                /mnt/s/code/rebooter-droids/data/firmware
```

(Done once at provisioning time — only the firmware dir matters for nginx
serving.)

## Releases

Run from the source tree on the build host:

```
tools/cut-rebooter-release.sh 0.1.1
```

This:

1. Updates `pyproject.toml` version + appends a `CHANGELOG.md` entry.
2. Commits + tags `v0.1.1`.
3. `gh release create` with the changelog excerpt.
4. `docker buildx build --push -t dblagbro/rebooter-droids:0.1.1 -t :latest`.
5. Force-recreates the local `rebooter-droids` container.
6. Curls the prod `/api/v1/version` to confirm.

If any step fails, the script aborts; nothing partial is shipped.

## Smoke test (post-deploy)

```
curl -fsS https://www.voipguru.org/rebooter/api/v1/version
curl -fsSI https://www.voipguru.org/rebooter/firmware/    # 403 if empty (expected)
```

## LAN relay agent (sub-second relay control)

The `tools/lan-relay-agent.py` daemon subscribes to the hub's SSE
command stream and dispatches relay commands directly to each device's
LAN IP. Without the agent running, click → relay defaults to the
device's ~30s poll cycle and the WebUI status chip waits up to ~60s
for the next heartbeat. With it running, both happen in ~850ms
end-to-end (verified 0.6.50, 2026-06-18).

**Install on any host that's on the device LAN AND can reach the
hub:**

```
tools/install-lan-relay-agent.sh
```

It prompts for the hub URL + a `rbt_` token (mint at
`/app/tokens` with read+write scopes — write is required for the
0.6.50 state-confirmed callback that powers the real-time UI flip),
probes both endpoints, installs the systemd-user unit with the
ExecStart path rewritten for the actual checkout, enables
user-lingering (so the service survives logout), and starts.

**Verify:**

```
systemctl --user status lan-relay-agent
journalctl --user -u lan-relay-agent -n 20
```

The agent logs every delivered command with the device IP + measured
round-trip latency.

**Where to run it:** any host on the LAN works. We run it on `tmrwww01`
(the hub host itself) since it's always on, always on the LAN, and
already has the repo checked out. The agent does not need to share a
host with the hub — it's a normal SSE subscriber + LAN client.

**Token rotation:** mint a new token, edit
`~/.config/lan-relay-agent.env`, then `systemctl --user restart
lan-relay-agent`.

## Architectural rules

### Postgres is private to its node

The `rebooter-droids-pg` container is **never** exposed beyond the
local docker network — no LAN port, no SSH tunnel, no cross-host
bridge. The DB is consumed only by the `rebooter-droids` Flask
container on the same node.

### Cross-component access is HTTPS API only

Every consumer of rebooter-droids data — including the future node-2
on tmrwww02, ops tooling, the firmware team, the mobile app — uses
the authenticated public REST API at
`https://<node>/rebooter/api/v1/`. Direct database clients are
forbidden by project rule.

When node-2 lands (v0.3+), inter-node sync happens via dedicated
internal HTTPS endpoints on the `rebooter-droids` container —
not via DB replication or shared storage of the cluster directory.

## Fallback URL (live in v0.2.1)

`https://www2.voipguru.org/rebooter/` is wired as a transparent
fallback to the primary `https://www.voipguru.org/rebooter/`. The
nginx config that does this is in **tmrwww02's**
`/home/dblagbro/docker/config/nginx/nginx.conf`, in the
`server_name www2.voipguru.org;` block. It looks like:

- `^~ /rebooter/firmware/` → `alias /mnt/s/code/rebooter-droids/data/firmware/`
  (RAID6 is shared, so blobs are served directly with no proxy hop).
- `= /rebooter/` and `= /rebooter` → 302 to `/rebooter/app/`
- `^~ /rebooter/` → `proxy_pass https://192.168.18.11$request_uri`
  with `Host: www.voipguru.org` so tmrwww01's nginx routes it to the
  rebooter-droids container.

Until v0.3 launches node-2's own Postgres, www2 is **not** an
independent deployment — it's a second front door to the same backend.
That still buys: DNS-level redundancy, separate TLS path, and a real
URL for firmware fallback testing today.

## Multi-node deployment plan (v0.3+)

Two nodes:

- **node-1** on tmrwww01 → <https://www.voipguru.org/rebooter/>
- **node-2** on tmrwww02 → <https://www2.voipguru.org/rebooter/>

Each node runs its own `rebooter-droids` + `rebooter-droids-pg` pair.
Sync between them goes through HTTPS APIs (TBD in v0.3 design). Same
admin credentials and SSH keys on both nodes; never SSH to FQDNs, only
short hostnames `tmrwww01` / `tmrwww02`. Rolling deploys per
`feedback_rolling_deploy.md` — never both nodes simultaneously.
