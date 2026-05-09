# Diagnostic scripts

Standalone scripts used to investigate live-deployment issues. Each
is self-contained — runs against a deployed instance via HTTP /
Playwright and produces stdout-only output. They are NOT part of
the test suite; they're operator-run diagnostic probes.

| Script | Purpose | Used to diagnose |
|---|---|---|
| `diagnose_signouts.py` | Login, inspect cookie attributes, test cross-host carry between www and www2 | The 2026-05-09 "I keep getting signed out" report → fix landed in v0.3.3 (`REBOOTER_COOKIE_DOMAIN` + `rebooter_session` cookie name). |

## Conventions

- Run from anywhere with the live deployment reachable.
- Default credentials read from `REBOOTER_QA_EMAIL` / `REBOOTER_QA_PASS`
  env vars (same as the QA suite). Hard-coded fallback is the
  bootstrap admin; please don't paste real prod creds into a
  script.
- Output is plain stdout — no exit codes promised, no machine-
  readable shape. Read it yourself.
- Playwright headless executable path falls through
  `PLAYWRIGHT_CHROMIUM_PATH` env, default
  `~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome`.

## When to add a new script here

- A live issue takes more than ~10 minutes to investigate AND
- The investigation steps are likely to be useful again

If both: drop the script here with a one-liner row in the table
above. Keep it under ~150 LOC; if it grows past that, promote
it into the `tests/qa/` bucket as a proper regression test
instead.
