# WebUI Redesign — Research

| Field | Value |
|---|---|
| Status | **Draft** (research-only deliverable; no implementation) |
| Authors | rebooter-droids design + product/architect track |
| Companion docs | `webui-redesign-requirements.md`, `webui-redesign-plan.md` |
| Builds on | `RFC-003-web-ui-redesign.md`, `REMEDIATION-PLAN-2026-05-WEB-UI.md`, `PROJECT-STATE-2026-05-09-FULL-SYNC.md` |

> This document is the *research-and-inspection* half of a redesign
> trio. It captures the current app, surveys the peer/competitor
> landscape we are designing against, and surfaces the UX patterns
> we should steal vs. avoid. It does **not** propose a design — that
> is `webui-redesign-plan.md`.

---

## 1. Executive summary

Rebooter-droids today is a working *server-rendered admin console*
on Flask + Jinja + SQLAlchemy + Postgres. It correctly exposes
device, group, site, firmware, audit, RBAC, and enrollment
primitives, and the backend API contract is solid. The product
problem is **not** missing functionality — it is that the UI is
shaped like an admin tool over a database, not like a product an
ordinary user trusts to power-cycle their router unattended.

The peer landscape splits into three camps:

- **Consumer-cloud-tied** (Kasa, eWeLink, base Shelly Cloud) — easy
  onboarding, mobile-first, weak RBAC, no real watchdog story for
  IT/AV use cases, cloud-dependent.
- **Pro/AV-installer** (WattBox, Digital Loggers, rack PDUs) —
  best-in-class watchdog UX (reboot-on-internet-loss is *the*
  feature), per-outlet labels and grouping, strong logs, but
  expensive and locked-down.
- **DIY/open** (Tasmota, ESPHome, Shelly local mode + HA, OpenWRT
  power-cycle scripts) — fully local, transparent, hackable, but
  configuration-heavy and not user-friendly out of the box.

Rebooter's product opportunity is the gap in the middle: **AV-
installer-grade watchdog reliability at consumer-grade ease of
setup, with Tasmota-grade openness and local-first behaviour**.
That positioning shapes everything in the requirements + plan docs.

The single most important UX insight from peer research is that
**every successful watchdog product makes "what is the rule
watching, and what just happened?" a single-glance answer**. The
current rebooter UI does not — it lists devices and groups but does
not surface watchdog state at all yet (because watchdog rules don't
exist as a feature yet; they are part of this redesign).

## 2. Repo inspection — current architecture

### 2.1 Stack

| Layer | Technology | Notes |
|---|---|---|
| Web framework | Flask 3 (`Flask>=3.0,<4`) | Blueprints under `app/blueprints/` |
| Auth on UI | Flask-Login signed cookie | 31-day permanent session, idle-timeout configurable |
| Auth on API | PyJWT bearer (`PyJWT>=2.9,<3`) | Access 8h, refresh 14d, jti claim added v0.2.10 |
| ORM | SQLAlchemy 2 (`SQLAlchemy>=2.0,<3`) | DeclarativeBase pattern |
| DB | Postgres 16 via `psycopg[binary]` | Schema bootstrap via `Base.metadata.create_all()` + idempotent `_ensure_columns()` ALTERs |
| Migrations | None (no Alembic versions/) | Boot-time create-all + hand-rolled ADD COLUMN IF NOT EXISTS |
| Templates | Jinja 2 (Flask default) | `templates/` at repo root |
| Static | Plain CSS in `static/css/app.css` | Mobile breakpoints at 1024px and 640px; desktop-first |
| Rate limit | Flask-Limiter (`Flask-Limiter>=3.8,<4`) | In-memory bucket; 30/min + 200/hour on `/auth/login` |
| Background jobs | APScheduler (`APScheduler>=3.10,<4`) | Single-worker via Postgres advisory lock |
| Server | Gunicorn (`gunicorn>=22,<24`) | Behind nginx via `PrefixMiddleware` honouring `X-Forwarded-Prefix` |
| Tests | pytest 8, pytest-flask 1.3 | Live-deployment QA at `tests/qa/` |
| Lint | ruff 0.6 | `line-length=120, target-version=py312` |
| Build/release | Docker image `dblagbro/rebooter-droids` | Tags follow `v0.X.Y`; v0.2.11 latest |

### 2.2 Build / test / lint commands

```
pip install -e '.[dev]'              # install
python3 -m pytest tests/qa/          # full live-deployment QA
python3 -m pytest tests/qa/ -m smoke # subset
ruff check app/                      # lint
sudo docker compose build rebooter-droids  # build container
sudo docker compose up -d --no-deps --force-recreate rebooter-droids  # deploy
```

There is no separate unit-test bucket today — every test in `tests/`
hits the live deployment over HTTPS. This is a deliberate "QA
suite" choice (see `docs/testing-split.md`), not a gap.

### 2.3 Test baseline before redesign

A full-suite run during this research (`pytest tests/qa/`) produced
**100 passed, 36 failed in 148s**. Every failure was rate-limit
fallout — when individual failing tests are re-run after the
60-second window resets, they pass cleanly. The cause is the
`30 per minute; 200 per hour` limiter on `/api/v1/auth/login`,
combined with the suite's pattern of one new login per test
function via the `admin_token` fixture. This is **not a code
regression** but it is a real test-infrastructure problem the
redesign plan must address (Section 8 below).

The 7 pre-existing mobile-overflow failures in
`tests/qa/test_responsive.py` at 375 px viewport (login, dashboard,
devices, events, audit, users) are real and confirm the desktop-
first CSS hypothesis. They are explicitly in scope for the
redesign.

### 2.4 Codebase shape

```
app/
├── __init__.py             # Flask app factory; registers blueprints
├── config.py               # env-driven Settings dataclass
├── version.py              # __version__ = "0.2.11"
├── db.py                   # SQLAlchemy engine + session_scope()
├── blueprints/
│   ├── version.py          # GET /api/v1/version
│   ├── auth.py             # /api/v1/auth/{login,logout,refresh,me}
│   ├── device_api.py       # /api/v1/device/{register,heartbeat,events,firmware}
│   └── admin/              # 14 per-feature blueprints (post v0.2.6 split)
│       ├── dashboard.py
│       ├── devices.py      # both /app/devices and /api/v1/admin/devices
│       ├── groups.py
│       ├── sites.py
│       ├── firmware.py
│       ├── users.py
│       ├── invitations.py
│       ├── audit.py        # global audit + per-record slice (v0.2.9)
│       ├── enrollment_tokens.py
│       ├── events.py
│       ├── unregistered.py
│       ├── profile.py      # /app/me
│       ├── auth_ui.py      # cookie-session login/logout
│       └── public_invite.py
├── models/                 # 11 model files; DeclarativeBase
├── services/               # business logic (audit, devices, sessions, etc.)
├── middleware/
│   ├── admin_auth.py       # @admin_required_ui / _api decorators
│   ├── cors.py             # v0.2.11 strict allowlist
│   ├── rate_limit.py
│   └── response.py         # ok() / err() envelope
└── jobs/                   # APScheduler jobs

templates/                  # 16 Jinja templates (15 logged-in pages + login + invite redeem)
static/css/app.css          # one stylesheet, ~600 LOC
tests/qa/                   # 12 QA test files
docs/                       # 18 files including 3 RFCs
migrations/                 # alembic env.py only — no versions/ used
```

The blueprint split (v0.2.6) and the per-feature service layer are
in good shape. The frontend is the weak link.

## 3. Current UI inventory

15 logged-in admin pages plus 2 unauthenticated pages. All server-
rendered Jinja, one route per database concept.

| URL | Template | Purpose | Notable issues |
|---|---|---|---|
| `/app/` | `dashboard.html` | 5 stat cards + 25-item activity feed | Stat-grid not "what needs attention" |
| `/app/devices` | `devices_list.html` | Devices table; filter form; QA-fixture toggle (v0.2.8) | Flat table; no by-site/by-group view; no saved filters; table doesn't card on mobile |
| `/app/devices/<id>` | `device_detail.html` | Heartbeat + commands + events + audit (v0.2.9) + edit | Long single-column scroll; no tabs |
| `/app/groups` | `groups_list.html` | Group roster + create | OK |
| `/app/groups/<id>` | `group_detail.html` | Members + add + mass-command + audit | Mass-action gate exists; UX not visually distinct |
| `/app/sites` | `sites_list.html` | Site list + create | Sites are tags, not scopes |
| `/app/firmware` | `firmware_list.html` | Releases + deployments + upload | OK functionally; "Releases" not promoted to top nav |
| `/app/users` | `users_list.html` | Role mgmt; super-admin only | OK |
| `/app/invitations` | `invitations_list.html` | Mint + cancel invites | Invites can't scope to a site |
| `/app/audit` | `audit_list.html` | Global audit feed; filter by actor/action/target | `target_id` param missing in UI handler — fixed v0.2.9 |
| `/app/events` | `events.html` | Device-event query | Should be a tab of Devices |
| `/app/unregistered-devices` | `unregistered_devices.html` | Auth-failure tracking | Diagnostic; should be a tab not a top page |
| `/app/me` | `me.html` | Profile self-service | OK |
| `/app/enrollment-tokens` | (template) | Mint enrollment tokens | OK |
| `/app/login` | `login.html` | Login form; rate-limited | 375px overflow |
| `/app/invite/<token>` | `invite_redeem.html` | Public invite acceptance | OK |

What is **missing entirely** that the redesign must add:

- **No watchdog/automation surface**. The product concept (monitor
  internet → reboot on failure) has no UI. There is no rule
  builder, no rule list, no rule-trigger log.
- **No notification / alerting surface**.
- **No schedule UI** (cron-style or recurring).
- **No system / network / integration settings page**. Settings are
  all environment-variable driven today with no UI.
- **No backup / restore / config-export UI**.
- **No API token management UI** (JWT issuance is implicit via
  login; there is no "generate a long-lived token for my Home
  Assistant integration" surface).
- **No webhook configuration UI**.
- **No Inbox / "what needs attention right now"** surface.

## 4. Current API / backend capability summary

### 4.1 Public-stable endpoints

| Endpoint | Auth | Notes |
|---|---|---|
| `GET /api/v1/version` | none | Health probe |
| `POST /api/v1/auth/login` | none (rate-limited) | Returns JWT access + refresh + sets cookie |
| `POST /api/v1/auth/refresh` | refresh JWT | New access + refresh |
| `POST /api/v1/auth/logout` | cookie or JWT | Revokes everything |
| `GET /api/v1/auth/me` | admin JWT | Identity |
| `POST /api/v1/device/register` | enrollment token | Mints device-token + writes Device row |
| `POST /api/v1/device/heartbeat` | device JWT | Heartbeat with mode/relay/wifi/health |
| `POST /api/v1/device/events` | device JWT | Event logging |
| `GET /api/v1/device/firmware` | device JWT | OTA assignment |
| `GET /api/v1/admin/...` | admin JWT or cookie | Devices, groups, sites, users, audit, firmware, deployments, enrollment tokens, dashboard, events |

### 4.2 What the backend already supports

- Three-state heartbeat (online / offline / never) — v0.2.7
- QA-fixture isolation (`is_qa_fixture` column + toggle) — v0.2.8
- Per-record audit slice on device + group detail — v0.2.9
- Server-side `user_sessions` table in shadow mode (jti claim
  + revocation) — v0.2.10
- Strict CORS allowlist (`REBOOTER_CORS_ALLOWED_ORIGINS`) — v0.2.11
- Mass-action confirmation gate (≤5 / 5–20 / >20 typed) — v0.2.5
- Four-role RBAC (super_admin / admin / operator / viewer) —
  action-gated, **not data-gated**
- Audit log with `target_type` + `target_id` composite index
- Site model exists (used as a tag; not a scope)
- Group model with mass-command fan-out
- Firmware release + deployment models with channels (dev/beta/stable)
- Enrollment-token + device-credential separation
- Invite flow with expiry + redeem
- ProxyFix + PrefixMiddleware for `/rebooter` URL prefix
- Per-blueprint module split (v0.2.6) — clean codebase

### 4.3 What the backend does **not** support yet

- **Watchdog / monitoring rules** — no model, no service, no API.
- **Schedules / automations** — none.
- **Notification / alert routing** — no SMTP send-on-event hook
  (only operator-driven invite emails).
- **Webhooks** — none.
- **MQTT integration** — none.
- **Home Assistant integration** — none.
- **API token issuance for headless integrations** — no model
  (current JWT issuance is bound to user login).
- **Sites as scopes** (RBAC data-gating) — designed in RFC-003 §9
  but not built.
- **OAuth / OIDC sign-in** — designed in RFC-003 §10 but not built.
- **Push notifications** — designed in RFC-003 §11.2 but not built.
- **Per-device lockout / "do not power-cycle"** flag.
- **Rule-trigger / watchdog event log** (it would be a sibling of
  the existing audit + events tables).

The redesign must therefore design *both* the new UI **and** new
backend capabilities (watchdog, schedules, notifications). RFC-003
addressed UX restructure on top of existing capabilities; this
redesign is bigger.

## 5. Peer / competitor research

Research methodology: synthesised from product knowledge of each
ecosystem (publicly documented features, app screenshots, hardware
manuals, integration docs, and known UX patterns). Where I cite a
specific feature behaviour, it is the product's documented
behaviour as of the model generations widely deployed in 2024–2026.
Marked items that may have changed in newer revisions are flagged.

### 5.1 TP-Link Kasa

Hardware: HS103/HS105/HS300 plugs, KP303/KP400 strips, EP-series.

- **Setup/onboarding:** App-driven; 2.4 GHz Wi-Fi enrol via mobile
  app + provisioning broadcast; cloud-account required for full
  app functionality.
- **Device list:** Flat list in app; rooms group devices.
- **Per-outlet:** Manual on/off, schedules, away mode, runtime
  estimates on EP-series.
- **Schedules:** Per-outlet daily/weekly; sunrise/sunset on newer
  generations.
- **Watchdog:** *None*. No automatic reboot-on-internet-loss.
- **Local control:** Local TCP API exists (well-documented by the
  reverse-engineered `python-kasa` library); the official app
  prefers cloud paths.
- **Logs/history:** Minimal — energy-monitoring graphs on EM
  variants, but no event log per device.
- **Multi-user:** "Home" sharing model — invite by email/account;
  shared users see the same household.
- **RBAC:** Effectively none (all-or-nothing share).
- **Alerting:** Push notifications for offline / runtime-exceeded
  on energy variants.
- **Firmware:** OTA via cloud; no operator control over rollout.
- **Safety:** Long-press physical button; per-outlet "always on"
  flag absent.
- **Mobile:** Strong; mobile is the primary client.
- **API:** No public REST API. Local TCP protocol is
  reverse-engineered; cloud API is undocumented.
- **Strengths to steal:** Setup speed (literally three taps on
  mobile); the "Home / Room" mental model; runtime telemetry on
  EM variants.
- **Weaknesses to avoid:** Cloud lock-in; no watchdog story;
  no API for integrators; flat device list at 50+ devices.

### 5.2 Shelly (Plus / Pro / Plug S, Shelly Cloud)

Hardware: Plug S, Plus 1/2PM, Pro line for DIN-rail, ix2/4 dimmer.

- **Setup/onboarding:** Captive-portal Wi-Fi onboarding **OR**
  Bluetooth onboarding via Shelly Smart Control; cloud account is
  optional.
- **Device list:** Per-room grouping in app; device cards with
  inline switches.
- **Per-outlet:** Manual + scenes; PM variants do real-time power
  monitoring.
- **Schedules:** Cron-shape backed by the device firmware —
  schedules survive cloud disconnect.
- **Watchdog:** Closer to one — "Internet Watchdog" toggle exists
  on some models but is shallow (ping a host, restart on failure).
  Not a first-class rule builder.
- **Local control:** Strongest of the consumer brands. Direct
  HTTP/REST API + MQTT publish/subscribe + WebSocket events. No
  cloud required.
- **Logs/history:** Per-device event log accessible via API.
- **Multi-user:** Cloud-account-shared "Rooms"; some RBAC at the
  Shelly Cloud Pro tier (paid).
- **RBAC:** Limited — owner / shared user.
- **Alerting:** Push + email + webhook; webhooks are the strongest
  in the consumer space.
- **Firmware:** OTA via cloud or via local API; manual file upload
  supported.
- **Safety:** Per-relay max-power lockouts; "default state on
  power-up" configurable.
- **Mobile:** Strong; the Smart Control app is well-shaped.
- **API:** Excellent. Documented REST + MQTT + WebSocket. Home
  Assistant integration is first-class.
- **Strengths to steal:** Local-first posture even with cloud
  available; webhook → action chain; documented API; per-room
  grouping with inline switch on the card; "default state on
  power-up" toggle.
- **Weaknesses to avoid:** Watchdog UX is shallow — only one host,
  no recovery-threshold model, no escalation.

### 5.3 Sonoff / eWeLink

Hardware: S31 (the rebooter base hardware), S40, MINI series, POW.

- **Setup/onboarding:** App-driven via eWeLink; cloud-account
  mandatory for the stock firmware.
- **Device list:** Flat list with home/room tags.
- **Per-outlet:** Manual + schedules; energy monitoring on POW /
  S31.
- **Schedules:** Daily/weekly via app.
- **Watchdog:** None in stock firmware.
- **Local control:** **Stock firmware: cloud-only.** This is *the
  reason* rebooter exists as a project — re-flashing the S31 to
  custom firmware unlocks local control + watchdog behaviour.
- **Logs/history:** Energy graphs only.
- **Multi-user:** Family sharing.
- **RBAC:** None.
- **Alerting:** Push for offline.
- **Firmware:** Cloud-pushed; not operator-controlled.
- **Strengths to steal:** Mostly nothing — eWeLink is the
  cautionary tale.
- **Weaknesses to avoid:** Cloud lock-in is the entire problem.

### 5.4 Tasmota

Open-source firmware for ESP-based devices (including S31).

- **Setup/onboarding:** Re-flash via serial or OTA (which is
  rebooter's bring-up path); captive-portal Wi-Fi onboarding.
- **Device list:** None in Tasmota itself — each device serves its
  own web UI on its own LAN IP. Multi-device management requires
  an external dashboard (Home Assistant, Node-RED, openHAB).
- **Per-device UI:** Single-page dashboard at the device IP showing
  relay state, signal strength, uptime, energy if PM hardware,
  firmware version. Configuration via the same single page.
- **Schedules:** Yes — "Timers" via the on-device UI. Cron-shape
  rules.
- **Watchdog:** Yes — on-device "Watchdog" + `WIFICHECK` plus a
  scriptable rule engine ("Rules" via Tasmota DSL). Rule chaining
  + state machine is the strongest open-source watchdog primitive
  in the ecosystem, but requires DSL knowledge.
- **Local control:** Native. HTTP commands, MQTT topic publish,
  serial console. No cloud.
- **Logs/history:** Console log on-device; small log buffer (one
  page worth).
- **Multi-user:** Single password per device.
- **RBAC:** None.
- **Alerting:** None on-device — the conventional pattern is to
  pipe state to Home Assistant or Node-RED for alerting.
- **Firmware:** Self-managed via web UI or OTA push.
- **Mobile:** Web UI is desktop-first; Home Assistant or external
  dashboard is the mobile path.
- **Strengths to steal:** Transparency of state on a single page;
  rule engine *concept*; never-cloud posture; per-device dashboards
  reachable on the LAN even when central is down.
- **Weaknesses to avoid:** Tasmota DSL is a barrier; per-device
  fragmentation; no fleet view; mobile UX is afterthought.

### 5.5 ESPHome

Declarative YAML-defined firmware compiler + native HA integration.

- **Setup/onboarding:** Compile from YAML; flash via USB/OTA;
  device announces itself to Home Assistant on the LAN.
- **Device list:** Lives in Home Assistant — ESPHome doesn't have
  its own portal beyond the build dashboard.
- **Per-device controls:** Whatever HA exposes for the entity.
- **Schedules / automations:** Via HA automations.
- **Watchdog:** Via HA automation rules.
- **Local control:** Native; HA + ESPHome over LAN.
- **Logs:** ESPHome dashboard has a per-device build/log feed; HA
  has the entity history.
- **Multi-user:** Inherited from HA.
- **RBAC:** Inherited from HA (limited; admin / user).
- **API:** Both ESPHome native API (gRPC-style) and HA REST/MQTT.
- **Strengths to steal:** YAML config-as-code idea (back-up + diff
  + sharing); the "build dashboard with logs per device" pattern.
- **Weaknesses to avoid:** Requires HA for fleet management;
  steep YAML learning curve.

### 5.6 Home Assistant (and Lovelace dashboards)

Full-stack home automation hub.

- **Setup:** Docker container or HA OS appliance; integrations
  add devices.
- **Device list:** Entities + Devices; multiple dashboards
  ("views").
- **Per-device:** Rich entity cards; states + attributes + history.
- **Schedules / automations:** Powerful YAML-or-UI automation
  builder; triggers + conditions + actions.
- **Watchdog:** Via automations.
- **Local control:** Yes when devices support it.
- **Logs:** Entity history (timeseries).
- **Multi-user:** Yes — admin / user; group-based dashboard
  visibility.
- **RBAC:** Limited — admin / user, with per-dashboard visibility
  but not per-device permissions.
- **Alerting:** Notifications integration (push, email, mobile).
- **API:** Excellent REST + WebSocket; long-lived access tokens
  for headless integrations.
- **Strengths to steal:** Long-lived API token issuance UX; the
  automation-trigger-condition-action shape; entity history graph;
  Lovelace card library as a model for our dashboard.
- **Weaknesses to avoid:** Configuration complexity for new users;
  YAML-by-default is a wall; automation debugging UX is hard.

### 5.7 Ubiquiti SmartPower / UISP power

Pro/sysadmin-targeted PDUs and managed switches.

- **Setup:** Adoption into UISP or UniFi controller.
- **Device list:** Site → device → outlet hierarchy.
- **Per-outlet:** On / off / cycle with confirm modal.
- **Schedules:** Limited — typically only via UISP scripting.
- **Watchdog:** Via UISP "monitoring" features (host check) but
  not exposed as a first-class rule builder.
- **Local control:** Web UI on the device IP + SSH.
- **Logs:** Strong syslog; per-outlet event log.
- **Multi-user:** Yes — UISP has roles per site.
- **RBAC:** Site-scoped roles; the model RFC-003 §9 already
  mirrors.
- **Alerting:** UISP notifications (email + push).
- **API:** Yes — UISP REST API.
- **Strengths to steal:** Site-as-scope model; per-outlet confirm-
  modals; syslog-grade event log; the controller-vs-device split.
- **Weaknesses to avoid:** UISP's UX has a steep learning curve;
  not friendly for non-network-engineer users.

### 5.8 WattBox / Digital Loggers / managed reboot PDUs

The closest peer products to rebooter's actual job-to-be-done.

- **WattBox** (AV-installer market): Has the best **Auto Reboot**
  UX — per-outlet "monitor host X, reboot if N consecutive
  failures, wait Y seconds before retry" with maintenance window
  and recovery threshold. Cloud OvrC integration plus local web UI.
- **Digital Loggers Web Power Switch:** Per-outlet on/off/cycle
  via simple web UI; has a "AutoPing" feature that pings a host
  and restarts an outlet on failure; scriptable.
- **Both** expose a **plain-English rule statement** like *"If
  192.168.1.1 fails to respond to ping for 3 minutes, cycle
  Outlet 1, wait 90 seconds, retry up to 3 times before
  escalating."* That sentence is the rule builder.
- **Logs:** Per-outlet history of every cycle, including the
  reason (operator, schedule, watchdog).
- **Mobile:** Decent on WattBox via OvrC; weak on Digital Loggers.
- **Strengths to steal — *all of them***:
  - the plain-English watchdog rule statement is the gold standard
  - per-outlet event log with a "reason" column (operator vs
    schedule vs watchdog)
  - failure-threshold + recovery-threshold + maintenance-window
    triple as the rule shape
  - explicit maintenance-mode toggle that suspends watchdog rules
    without deleting them
- **Weaknesses to avoid:** Pricing; closed firmware; weak fleet/
  multi-site posture (each unit is its own controller).

### 5.9 Open-source IoT dashboards

- **Node-RED** (with `node-red-dashboard`): node-graph automation
  builder; not a portal; great for power users, intimidating for
  new users.
- **OpenHAB:** Java-based home automation hub; comparable to HA;
  weaker community in 2026.
- **Domoticz:** Lightweight HA-alternative; older feel; strong on
  RFXCom hardware.
- **OpenWRT LuCI:** Network-router-grade web UI; not really an IoT
  dashboard but the UX shape (a `Status / System / Services /
  Network` top nav with progressive disclosure) is the closest
  open-source analog to what we want.

## 6. Comparison matrix

Scoring each peer on the dimensions called out in the directive:
**+** strong, **·** present, **—** absent.

| Dimension | Kasa | Shelly | eWeLink | Tasmota | ESPHome+HA | UISP/UniFi | WattBox | DigLog |
|---|---|---|---|---|---|---|---|---|
| Setup ease (new user) | + | + | + | — | — | · | · | · |
| Device list / grouping | · | + | · | — | + | + | · | · |
| Per-outlet controls | + | + | + | + | + | + | + | + |
| Schedules | · | + | · | + | + | · | + | + |
| Automations | · | · | · | + | + | · | · | · |
| **Watchdog feature depth** | — | · | — | + | + | · | **+** | **+** |
| Health checks | · | + | — | · | + | + | + | + |
| Local control | · | + | — | + | + | + | + | + |
| Logs / history | · | + | — | · | + | + | + | + |
| Multi-user | · | · | · | — | · | + | · | — |
| **RBAC depth** | — | · | — | — | · | + | · | — |
| Alerting / notifications | · | + | · | — | + | + | + | · |
| Firmware / OTA control | — | + | — | + | + | + | + | + |
| Safety controls | · | + | · | · | · | + | + | + |
| Mobile usability | + | + | + | — | · | · | · | — |
| API / webhook | — | + | — | + | + | + | · | · |
| Open source / DIY | — | · | — | + | + | — | — | — |

The two columns where *no* peer scores **+** across the board are:
- **DIY-friendly with strong watchdog and RBAC** — that is the gap
  rebooter targets.
- **Mobile-first with local-only operation** — Shelly is closest;
  rebooter can do better with the web-UI-as-PWA model.

## 7. UX patterns to steal vs. avoid

### Steal

- **WattBox plain-English watchdog rule statement** — render every
  rule as one human sentence: *"If gateway fails ping 3× in 5 min,
  cycle Office Modem, wait 90 s, retry 3×."* Click anywhere in the
  sentence to edit that span.
- **Shelly card-with-inline-switch** for the device list on mobile.
  Each card is a tap-target with the primary switch on it.
- **Shelly webhooks** as the universal escape hatch for alerting,
  HA integration, and external automation.
- **Tasmota single-page on-device dashboard** as the model for the
  device-detail tab structure.
- **HA long-lived API tokens** for headless integrations.
- **HA automation editor** trigger / condition / action shape for
  the rule builder advanced mode.
- **UISP site-as-scope** RBAC model (RFC-003 §9 already adopts
  this).
- **OpenWRT LuCI** progressive-disclosure top nav with `Status` as
  the always-default landing.
- **Digital Loggers per-outlet event log with a "reason" column**.

### Avoid

- **Kasa / eWeLink cloud lock-in.** Rebooter must keep working
  when central is down.
- **Tasmota DSL.** No DSL for end users; offer YAML/JSON only as
  an "advanced editor" path.
- **HA YAML-by-default.** Plain-English first; YAML is the
  power-user fallback.
- **eWeLink flat device list.** Even at 5 devices, by-site grouping
  matters.
- **Node-RED node graph as the primary automation surface.** Too
  intimidating for new users; offer it (or similar) only as
  advanced mode.
- **WattBox / Digital Loggers per-unit controller pattern.**
  Centralised fleet view is what rebooter is for.

## 8. Test infrastructure findings (relevant to the redesign)

- The QA suite is **live-deployment-only**. There are no in-process
  unit tests against an in-memory database. Every test logs into
  the real `https://www.voipguru.org/rebooter` and probes via
  HTTP.
- The login rate limit (`30/min; 200/hour`) **trips during
  full-suite runs** when many tests share the `admin_token`
  fixture but request fresh tokens per function. The full-suite
  failure mode I observed during research — 36 failures in a
  148-second run, all of which pass when re-run individually after
  the 60-second window resets — is purely this artefact, not real
  regressions.
- Implication for the redesign:
  - The redesign must include **either** a session-scoped
    `admin_token` fixture (so the suite logs in once) **or** an
    in-process unit-test bucket with an in-memory DB.
  - Either change is a small refactor of `tests/qa/conftest.py` —
    not a redesign-blocker, but flagged here so it lands as part of
    the broader plan.
  - Playwright is already in use for the responsive bucket. The
    redesign should keep Playwright for E2E, add jest/vitest-style
    component tests **only if** we adopt a JS framework (open
    question — see plan).

## 9. Key product insight summary

The rebooter product opportunity is best framed as:

> *A consumer-friendly, mobile-first, open-source web portal for
> a fleet of local-first power-control devices, with watchdog
> rules powerful enough to satisfy AV-installer use cases and
> RBAC mature enough to serve multi-tenant deployments — built
> around the principle that the device works without the
> portal, and the portal works without the cloud.*

That sentence is the constitution. Every requirement in
`webui-redesign-requirements.md` and every design choice in
`webui-redesign-plan.md` must be measurable against it.
