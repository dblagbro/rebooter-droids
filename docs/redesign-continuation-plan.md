# UI redesign — continuation plan (2026-05-10)

The original `docs/webui-redesign-plan.md` defined P0..P6. P0–P4 shipped
on schedule across v0.3.x and the early v0.4.x. **P5 + P6 fragmented**
during the firmware bring-up sprint and lots of pieces shipped ad-hoc
under different release tags. The Settings tabs still pointed at
"Coming in P5/P6" placeholders that no longer reflected reality.

This doc:
1. Maps what's shipped back onto the original P5/P6 plan
2. Identifies what's genuinely still queued
3. Re-prioritises the rest based on what enhances (vs replaces) the
   ad-hoc work that landed since
4. Proposes the next 4 shippable chunks

---

## Map: original P5/P6 plan → current reality

### P5 — RBAC + Auth + System

| Item from original plan | Status | Where |
|---|---|---|
| **Server-side session enforce flip** | ✅ shipped v0.4.10 (BUG-005) | `app/middleware/admin_auth.py::_is_jti_revoked` |
| **Password-reset magic-link** | ✅ shipped v0.4.1 | `/app/forgot-password` + `/app/reset-password`, 1-h TTL, audit hooks |
| **Bootstrap admin password persistence** | ✅ shipped v0.4.16 (BUG-046) | `app/services/bootstrap.py`; opt-in legacy via env var |
| **Maintenance-mode toggle (System tab)** | ✅ shipped v0.4.7 (B7) | Status page banner + `runtime_flags` table |
| **Site-as-scope RBAC migration** | ❌ NOT STARTED | Blocked on **B10** redlines: 4 RFC-003 questions for operator |
| **TOTP MFA enrolment** | ❌ NOT STARTED | Blocked on RBAC migration above |
| **Google + GitHub OIDC sign-in** | ❌ NOT STARTED | Blocked on RBAC migration above |
| **Editable Session idle timeout** | ❌ env-var only | Small follow-up after runtime-settings work |
| **Editable Portal name + retention** | ❌ env-var only | Small follow-up after runtime-settings work |

### P6 — Network + broader Settings

| Item from original plan | Status | Where |
|---|---|---|
| **Strict CORS allowlist** | ✅ shipped v0.2.11 | `REBOOTER_CORS_ALLOWED_ORIGINS` env var |
| **Public + secondary URL** | ✅ shipped v0.2.x → v0.3.9 | dual-canonical hosting via env vars + nginx; firmware mirror chain |
| **Security headers (HSTS, X-Frame-Options, CSP)** | ✅ shipped v0.4.11 + v0.4.22 | `@app.after_request` hook in `app/__init__.py`; CSP `script-src 'self'` (no unsafe-inline) |
| **Editable CORS allowlist UI** | ❌ env-var only | Queued — DB-backed with env-var fallback |
| **Editable URL settings UI** | ❌ env-var only | Queued — same shape |
| **Backup config UI** | ❌ NOT STARTED | Coordinator-hub-driven today; defer to standalone push |
| **API tokens** (separate from device-tokens) | ❌ NOT STARTED | Use case unclear in current flows; defer |
| **Webhooks** | ❌ NOT STARTED | Will land when watchdog notifications get their first non-SMTP transport |

### Bonus — landed since the original plan was written

These weren't on the P5/P6 list but shipped during the bring-up
sprint. They enhance rather than replace the redesign:

| Feature | Release | Surface |
|---|---|---|
| Watchdog rules + runtime + maintenance windows | v0.4.0 / v0.4.2 / v0.4.7 | `/app/rules` |
| Schedules (time-driven primitive) | v0.4.8 | `/app/schedules` |
| Pending-adoption flow | v0.4.20 | `/app/pending-adoption` + `POST /api/v1/device/announce` |
| One-click upgrade-to-latest | v0.4.21 | `/app/devices` per-row button |
| Per-firmware-version fleet view | v0.4.19 | `/app/devices` collapsible card |
| On-disk firmware scan | v0.4.19 | `/app/firmware` "Scan now" button |
| Status-inbox attention ack/snooze | v0.4.22 | Status page per-item buttons |
| Notifications tab + send-test | v0.4.1 | `/app/settings/notifications` |
| 30-day invite TTL | v0.4.1 | `/app/invitations` |

---

## Re-prioritised remaining work

### Tier 1 — operator-ready, no operator/firmware-team input needed

Net-new value, all DB-backed-with-env-var-fallback so empty databases keep
picking up env defaults. Each ~2-3 hours.

1. **Runtime-editable Notifications (SMTP) settings.** Today's tab is
   read-only env-var display. Operator's pain point earlier today was
   "the SMTP password was stale and rotating it required a docker recreate."
   Adds a `runtime_settings` table with a tiny key-value abstraction; SMTP
   credentials read DB → env-var fallback. Send-test button stays.
   **Bonus payoff:** the operator support call ~04:00 UTC about SMTP
   delivery never happens again.

2. **Runtime-editable Network settings.** Same shape — public URL,
   secondary URL, CORS allowlist as a list editor. Read DB → env-var
   fallback. Adds an "apply changes" button that audit-logs as
   `network_settings.updated`. Big operator win for first-time deploys.

3. **Runtime-editable System settings.** Portal name, retention windows,
   timezone defaults. Same DB-backed pattern. The maintenance toggle is
   already on Status page; this tab reads/writes only the static knobs.

4. **API documentation refresh.** `docs/API.md` has drifted across
   ~22 releases. New endpoints documented:
   `/api/v1/device/announce`, `/api/v1/admin/pending-adoption/*`,
   `/api/v1/admin/firmware/scan`, `/api/v1/admin/maintenance`,
   `/api/v1/admin/attention/<id>/ack`, the schedules surface, etc.

### Tier 2 — operator-input-gated

Wait on these until you have a moment.

5. **B10 redlines** — 4 RFC-003 questions:
   - Site-as-scope contract: can a `(user, role, site_id=A)` admin see
     org-wide audit filtered to their site, or is audit org-wide-only?
   - Migration plan: existing admins → `(role, site_id=NULL)` or split
     per-site at migration time?
   - Audit-log retention policy: with v0.4.9 per-device fanout, the
     audit table grows ~Nx faster. Cap N days? Monthly partitions?
   - Invite shape: do invites get a `site_id?` field?
6. **B11** — RFC-004 multi-hub sync architecture pick (5 options, B
   recommended)
7. **B12** — RFC-005 firmware-team Q1..Q9 redlines (firmware-team
   action, not yours)

### Tier 3 — useful but lower urgency

Each ~1-2 hours.

8. **History page filter coverage** — the History tab predates the
   v0.4.7+ audit actions (`maintenance_mode.toggled`, `schedule.*`,
   `attention.acked`, `device_announcement.*`, `firmware.scanned`,
   `device.upgrade_initiated`). Verify each filters correctly + add a
   first-class chip for them.
9. **Backup config visibility** — at minimum, surface "last successful
   backup at <ts>" on the System tab. Today the coordinator-hub runs
   the backup; we could subscribe to its status.
10. **Profile page change-password flow** — never deeply tested. Edge
    cases probably exist.

### Deferred / waiting on a real use case

11. **TOTP MFA + OIDC** — gated on B10 RBAC migration. Not blocking
    operator work today.
12. **API tokens (programmatic access)** — no current consumer.
13. **Webhooks** — gated on first non-SMTP notification transport
    requested.

---

## Proposed next 4 ships

| Ship | Items | Effort | Notes |
|---|---|---|---|
| v0.4.23 | This doc + Settings tab text refresh + CHANGELOG entry | 30 min | Already in flight |
| v0.4.24 | Runtime-editable Notifications/SMTP (Tier-1 #1) | 3 h | Pays off the SMTP pain immediately |
| v0.4.25 | Runtime-editable Network + System settings (Tier-1 #2 + #3) | 3 h | Bundle both since they share `runtime_settings` infra |
| v0.4.26 | API.md refresh + History page filter coverage (Tier-1 #4 + Tier-3 #8) | 2 h | Doc + small UI polish |

Total: ~9 hours of work to fully retire the "Coming in P5/P6" copy
from the Settings UI without touching anything that needs your redline.

The **B10 redlines + RBAC migration** still gate the auth-side
features (TOTP / OIDC / site-scope), but everything else moves in
parallel.

---

## What this preserves

The continuation explicitly **does not undo or replace** any of the
ad-hoc work shipped during the bring-up sprint. Every Tier-1 ship
above adds DB-backed editing on top of the existing env-var-driven
layer; if the DB has nothing set, env-vars still win. So:

- The watchdog runtime keeps ticking
- Schedules keep firing
- Pending adoption keeps catching new devices
- Tightened CSP + security headers stay
- Attention ack/snooze stays
- Real devices stay online

Operator gets a richer settings surface; nothing they're depending on
moves.
