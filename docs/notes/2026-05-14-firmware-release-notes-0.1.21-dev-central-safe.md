# Release Notes: `0.1.21-dev-central-safe`

Date: 2026-05-14

## Summary

This release is the current deep-retested firmware checkpoint for the Sonoff S31
Rebooter line. It focuses on local auth correctness, safe public-vs-protected
API behavior, recovery/OTA hardening, and stronger QA coverage.

## Artifact

- firmware: `rebooter-0.1.21-dev-central-safe.bin`
- SHA256:
  - `59D06F4F4279CC41ADA3A866B23E9CA4F64152D6399C08F109780FB91DE739F9`

## Included fixes and hardening

1. Local auth and UI behavior
   - public `GET /api/config` stays available but redacts central identity and secrets
   - local browser UI now supports locked/unlocked protected actions correctly
   - actual device-served `app.js` now preserves `X-Rebooter-Auth` even when request
     options also provide headers

2. Central defaults and diagnostics
   - removed the broken legacy `www2.voipguru.org` fallback default from shipped config
   - central diagnostics now reflect only the live default target

3. Event history clarity
   - persisted events now expose:
     - `seq`
     - `boot_id`
     - `ts_basis`
   - this keeps cross-boot history interpretable

4. API protocol polish
   - protected endpoints reject unauthenticated and bad-token access consistently
   - common wrong-method reads now return `405`
   - `/favicon.ico` no longer produces avoidable noise

5. QA guardrails
   - added:
     - `scripts/qa-api-regression.ps1`
     - `scripts/qa-deep-api-retest.ps1`
     - `scripts/qa-ota-stress.ps1`
     - `scripts/qa-ui-auth-static-regression.mjs`

## Deep retest coverage on `.48`

Validated on live bench device `http://192.168.1.48`:

- API regression pass: `21/21`
- deep API retest: `33/33`
- static UI auth regression: `5/5`
- actual device-served UI deep retest: `7/7`
- OTA stress: `3` successful OTA cycles with:
  - no recovery-mode relapse
  - no `last_known_good_restored` surprise
  - monotonic event sequence retention

## Important follow-up learned during retest

During the deep retest, the repo-side `app.js` fix turned out to be ahead of the
actual device-served LittleFS asset by one build. `0.1.21-dev-central-safe`
exists partly to close that gap and ensure the live served UI matches the source.

## Not fully closed yet

These are still open validation items, not hidden problems:

- physical short press / 3s / 10s / 30s button verification
- destructive recovery/factory-reset retest with operator hands on the bench
- power-sample upload validation against a real receiving hub

## Recommended rollout posture

- safe to use as the current default `stable/latest.bin`
- use this line as the shared QA baseline before the next functional firmware bump
