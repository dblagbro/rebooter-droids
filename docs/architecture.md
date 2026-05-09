# Architecture

> **Living document.** Update this whenever a module boundary moves or a
> hard rule is added/removed. The refactor log (`refactor-log.md`) is the
> append-only journal of how the structure changed; this file describes
> how it works *today*.

## What this is

`rebooter-droids` is the optional central coordination server for
Sonoff S31-based Rebooter devices. It is an **additive layer** —
devices remain local-first and keep working when this server is down.

## High-level map

```
                     ┌────────────────────────────┐
  Sonoff S31 ──────► │  Device API                │
  firmware           │  /api/v1/device/*          │
                     │  (Bearer device_token)     │
                     └──────────┬─────────────────┘
                                │
  Mobile / future      ┌────────┴───────────────┐    ┌──────────────┐
  presence client ───► │  rebooter-droids       │ ◄──┤  Postgres    │
  (planned)            │  Flask app + APScheduler│   │  (private)   │
                       │                        │    └──────────────┘
                       │  Admin API + UI        │
                       │  /api/v1/admin/*       │
                       │  /app/*                │
                       └────────┬───────────────┘
                                │
                     ┌──────────┴──────────────────┐
                     │  Firmware blobs (nginx)     │
                     │  /rebooter/firmware/*.bin   │
                     │  RAID6, no app round-trip   │
                     └─────────────────────────────┘
```

## Hard architectural rules (do not violate)

1. **Postgres is private to its node.** Never exposed on the LAN, never
   tunnelled, never bridged across hosts. The only consumer is the
   `rebooter-droids` Flask container on the same node.
2. **All cross-component access via the rebooter-droids HTTPS API only.**
   This includes future node-2 sync, the mobile app, the firmware client,
   and any ops tooling. No back-channel access to the DB or filesystem.
3. **SSH only via short hostnames** (`tmrwww01`, `tmrwww02`); never the
   FQDN.
4. **Rolling deploys.** One node at a time; never both simultaneously.
5. **Every version bump = release + push.** Tag → `gh release create` →
   `docker buildx push <version>+latest` → recreate container →
   curl-verify the version endpoint.
6. **Single-container recreate only.** `sudo docker compose up -d
   --force-recreate --no-deps <name>`. Never `docker compose down`.
7. **No `Co-Authored-By: Claude`** in commit messages.

## Source layout

```
app/
├─ __init__.py            Flask app factory; rate-limit + bootstrap wiring
├─ config.py              env-driven Settings dataclass
├─ db.py                  SQLAlchemy engine + session_scope context-mgr
├─ version.py             single source of truth for the running version
├─ blueprints/
│  ├─ version.py          GET /api/v1/version
│  ├─ auth.py             POST /api/v1/auth/{login,logout,refresh,me} (JSON)
│  ├─ device_api.py       /api/v1/device/* — register, heartbeat, poll, etc
│  └─ admin/              one module per admin feature; both UI + API
│     ├─ __init__.py      defines admin_api_bp + admin_ui_bp; imports submodules
│     ├─ _common.py       shared decorators, _ctx() helper, role re-exports
│     ├─ dashboard.py     GET /app/ (UI only)
│     ├─ auth_ui.py       /app/login, /app/logout (UI session pages)
│     ├─ profile.py       /app/me/* self-service (UI only)
│     ├─ public_invite.py /app/invite/<token> public redeem flow
│     ├─ devices.py       admin UI + API for devices
│     ├─ enrollment_tokens.py
│     ├─ groups.py
│     ├─ sites.py
│     ├─ firmware.py      releases + deployments
│     ├─ users.py
│     ├─ invitations.py
│     ├─ audit.py
│     ├─ unregistered.py  v0.2.5 unregistered-heartbeat tracker
│     └─ events.py        admin events query
├─ middleware/
│  ├─ admin_auth.py       role-aware decorators (admin_required_*, role_required_*)
│  ├─ device_auth.py      device-token resolution + 401 logging
│  ├─ rate_limit.py       Flask-Limiter wiring (memory backend, single-worker)
│  └─ response.py         ok()/err() envelope helpers
├─ services/              business logic; one module per domain aggregate
│  ├─ audit.py
│  ├─ auth.py             JWT issuance + decoding
│  ├─ bootstrap.py        ensure_schema() + ensure_bootstrap_admin()
│  ├─ commands.py
│  ├─ dashboard.py
│  ├─ deployments.py
│  ├─ devices.py
│  ├─ email.py            SMTP send (earthlink config)
│  ├─ enrollment.py
│  ├─ events.py
│  ├─ firmware.py
│  ├─ groups.py
│  ├─ heartbeats.py
│  ├─ invitations.py
│  ├─ mass_action.py      blast-radius confirmation gate (v0.2.5)
│  ├─ sites.py
│  ├─ unregistered.py     v0.2.5 unregistered-auth-attempts tracker
│  └─ users.py
├─ models/                SQLAlchemy ORM; one module per aggregate
│  ├─ _helpers.py         ULID + utcnow + ts_column shortcuts
│  ├─ audit.py
│  ├─ commands.py
│  ├─ devices.py
│  ├─ events.py
│  ├─ firmware.py
│  ├─ groups.py
│  ├─ invitations.py
│  ├─ sites.py
│  ├─ unregistered.py
│  └─ users.py
└─ jobs/
   └─ scheduler.py        APScheduler tick (expire_commands every 30s)

templates/                Jinja templates (server-rendered admin UI)
static/                   CSS + JS (mass_action.js for the gate)
docs/                     Architecture, RFCs, runbooks, session archive
tests/qa/                 Playwright + requests regression suite
```

## Module-boundary principles

- **One feature, one file.** Each domain (devices, groups, …) has its
  own `models/<x>.py`, `services/<x>.py`, and `blueprints/admin/<x>.py`.
  Co-location speeds AI lookup and human navigation.
- **UI + API live together** under `blueprints/admin/<x>.py`. A change
  to the device contract changes one file, not three.
- **Cross-cutting concerns live in `middleware/`.** Decorators
  (`role_required_api`, `device_auth_required`), the response envelope,
  and rate limiting are imported from there into every blueprint.
- **No business logic in blueprints.** They translate HTTP ↔ service
  calls and emit audit-log entries on writes.

## Surface contracts

| Contract | Owner | Spec |
|---|---|---|
| Device API (`/api/v1/device/*`) | `blueprints/device_api.py` | `docs/SPEC.md`, `docs/DEVICE_INTEGRATION.md` |
| Admin API (`/api/v1/admin/*`) | `blueprints/admin/*` | `docs/API.md` |
| Admin UI (`/app/*`) | `blueprints/admin/*` | `docs/ADMIN_GUIDE.md` |
| Firmware delivery (`/rebooter/firmware/*.bin`) | nginx alias | `docs/SPEC.md` §"Firmware Hosting Rules" |
| Presence (planned) | RFC-001 | `docs/RFC-001-presence.md` |

## Auth model

- **Admin**: email/username + password → Flask signed-cookie session
  + JWT (HS256, 8h access, 14d refresh). Server-side revocation via
  `users.tokens_valid_after`.
- **Device**: single-use enrollment token → opaque `device_token`
  (sha256 stored). Bearer on every device-API call.
- **Roles**: `super_admin`, `admin`, `operator`, `viewer`. Enforced
  by `middleware/admin_auth.py::role_required_*`.

## Operational

- **Deployment**: 2 nodes (`tmrwww01` + `tmrwww02`). Currently
  active-passive — node-2 is a transparent HTTPS proxy to node-1
  until v0.3 lands the inter-node sync API.
- **Single Gunicorn worker** by design — APScheduler advisory-lock
  pattern + in-memory rate-limit bucket assume a single worker. To
  scale up: add Redis (or equivalent) for shared state.
- **Backups** via `/home/dblagbro/docker/scripts/backup.sh` nightly.
- **Healthcheck**: `/home/dblagbro/bin/rebooter-droids-healthcheck.sh`
  on `*/5 * * * *` posts to coordinator-hub on transitions.
- **Schema bootstrap**: `services/bootstrap.py::ensure_schema()` runs
  `Base.metadata.create_all()` under a Postgres advisory lock on every
  container start (idempotent; auto-creates new tables added in later
  releases).

## Out-of-scope today

- Pydantic validation schemas (directory `app/schemas/` exists empty;
  earmarked).
- Inter-node HTTPS sync API + Redis-backed shared state (v0.3).
- Presence automation (RFC-001 implementation).
- Signed firmware URLs (deferred to `v0.3.0-firmware-trust`).

## See also

- [`contributing.md`](contributing.md) — workflow + commit + release rules
- [`refactor-log.md`](refactor-log.md) — chronological structural changes
- [`SPEC.md`](SPEC.md) — canonical device/admin contract
- [`API.md`](API.md) — endpoint reference
- [`DEPLOY.md`](DEPLOY.md) — operator runbook
- [`DEVICE_INTEGRATION.md`](DEVICE_INTEGRATION.md) — firmware-team handoff
- [`RFC-001-presence.md`](RFC-001-presence.md) — active design RFC
