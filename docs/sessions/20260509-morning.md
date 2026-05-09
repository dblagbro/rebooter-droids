# Session Log — 2026-05-09

This is a one-page summary in the repo for cross-team visibility.
The full pause-state for resumption lives in operator memory at
`~/.claude/projects/-home-dblagbro/memory/project_rebooter_droids_pause_state_20260509.md`.

## What shipped today

| Tag | Summary |
|---|---|
| v0.1.0 | Full-spec MVP |
| v0.1.1 | Super-admin role + reconciling bootstrap |
| v0.1.2 | Login accepts username or email |
| v0.1.3 | QA hardening (3 race/error fixes) + 77-test suite |
| v0.1.4 | Login rate limit, name uniqueness, 0-byte firmware reject |
| v0.2.0 | RBAC + email invites + audit log + token revocation |
| v0.2.1 | www2 fallback URL live (transparent HTTPS proxy) |

## Live state

- **Primary:** <https://www.voipguru.org/rebooter/>
- **Fallback:** <https://www2.voipguru.org/rebooter/>
- Both URLs return identical responses (single backend on tmrwww01).
- 87-test QA suite passes against both URLs.

## Active design RFC

`docs/RFC-001-presence.md` — Draft. Presence automation. Awaiting
cross-team redline from firmware, design, mobile.

## Open infrastructure

- SMTP for email invites: earthlink, working as of 2026-05-09.
- Healthcheck cron on tmrwww01: probes both URLs every 5 min, posts
  to coordinator-hub channel on transitions only.
- Nightly backups via existing `/home/dblagbro/docker/scripts/backup.sh`
  cover the symlinked source tree + Postgres data dir.

## Next milestone

v0.3 — node-2's own Postgres + inter-node sync API. Blocked on
RFC-001 redline cycle and the firmware team's first live device test.
