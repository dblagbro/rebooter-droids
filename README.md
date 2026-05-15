# Rebooter-Droids

Central coordination server for Sonoff S31-based Rebooter devices.

Optional cloud-side companion to a fleet of local-first Rebooter units. Devices
keep working when this server is offline; central coordination is purely
additive (group commands, firmware rollouts, fleet visibility).

## Live deployment

**Primary (www)**:
- Admin UI: <https://www.voipguru.org/rebooter/app/>
- API root: <https://www.voipguru.org/rebooter/api/v1/>
- Firmware: <https://www.voipguru.org/rebooter/firmware/>

**Secondary (www2)** — independent hub; multi-hub sync (RFC-004 / B11)
is wired but not yet a finished active-active path — the outbox
replicator + HMAC peer auth ship, but `apply_outbox_event()` only
applies deletes/tombstones (create/update LWW is still a stub) and
`sync.enabled` defaults to `false`:
- Admin UI: <https://www2.voipguru.org/rebooter/app/>
- API root: <https://www2.voipguru.org/rebooter/api/v1/>
- Firmware: <https://www2.voipguru.org/rebooter/firmware/>

## Stack

Python 3.12 / Flask / Jinja2 / SQLAlchemy 2 / Alembic / Postgres 16 / Gunicorn / APScheduler.

## Repos

- Source: <https://github.com/dblagbro/rebooter-droids>
- Image: `dblagbro/rebooter-droids` on Docker Hub

## Documentation

| Doc | Audience |
|---|---|
| [docs/SPEC.md](docs/SPEC.md) | Canonical contract |
| [docs/API.md](docs/API.md) | Endpoint reference |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Operator runbook |
| [docs/DEVICE_INTEGRATION.md](docs/DEVICE_INTEGRATION.md) | Firmware-team handoff |
| [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) | Admin UI walkthrough |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Quickstart (development)

```bash
docker compose up -d --no-deps rebooter-droids-pg rebooter-droids
curl https://www.voipguru.org/rebooter/api/v1/version
```

## License

MIT.
