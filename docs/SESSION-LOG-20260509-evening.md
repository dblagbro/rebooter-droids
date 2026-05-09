# Session Log — 2026-05-09 (evening, post-cleanup)

In-repo, cross-team-visible state. Operator-private comprehensive
pause state lives in
`~/.claude/projects/-home-dblagbro/memory/project_rebooter_droids_pause_state_20260509_v024.md`.

## Live state

| URL | Version |
|---|---|
| <https://www.voipguru.org/rebooter/> | **0.2.4** |
| <https://www2.voipguru.org/rebooter/> | **0.2.4** (transparent proxy to www1) |

## Today's release tally

| Tag | Title |
|---|---|
| v0.1.0 | full-spec MVP |
| v0.1.1 | super-admin role |
| v0.1.2 | login accepts username or email |
| v0.1.3 | QA hardening + 77-test suite |
| v0.1.4 | quick-wins (rate limit, uniqueness, 0-byte reject) |
| v0.2.0 | RBAC + email invites + audit log |
| v0.2.1 | www2 fallback URL live |
| v0.2.2 | 2-day idle session timeout + QA data purge |
| v0.2.3 | UI affordances + duplicate-name 500 fix |
| v0.2.4 | real dashboard + self-service profile |

## DB state (post-cleanup)

Architect + firmware-team's enrollment token only. All QA testing
artifacts purged. Audit log truncated. Firmware blobs cleared.

## Open items

- Firmware team's device is hitting `/api/v1/device/heartbeat` with a
  stale token — needs re-enrollment with the active token (via the
  operator's private channel).
- v0.2.5 "mass-action confirmation gate" is in-progress on the
  filesystem but not committed or deployed.
- v0.3 multi-node sprint queued; depends on RFC-001 redline.

## Active design RFC

`docs/RFC-001-presence.md` — Draft. Awaiting cross-team redlines.
