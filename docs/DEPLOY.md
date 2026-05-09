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
