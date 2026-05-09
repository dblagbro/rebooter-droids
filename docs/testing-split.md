# Testing strategy — categorised splits

**Status:** v0.3 sprint addition (2026-05-09). Companion to `docs/test-plan.md`.
The base plan defines *what* we test; this doc defines *how to slice* the
suite for fast feedback vs full coverage.

## Why a split

The QA suite at `tests/qa/` grew from 87 tests in v0.1.3 to ~110 tests
heading into v0.3. A flat `pytest tests/qa` run on the live deployment
takes ~2 minutes end-to-end, dominated by Playwright cold starts and
networkidle waits. A developer iterating on a CSS change shouldn't have
to re-run the rate-limit drain probe; a QA engineer doing a release-gate
sweep needs the whole thing including the slow probes.

This split lets contributors run the slice that matches their loop.

## The five buckets

| Bucket | Marker | Files | Approx. count | Run time | When to run |
|---|---|---|---|---|---|
| **smoke** | `@pytest.mark.smoke` | `test_smoke.py` | ~6 | ~5s | Every save during dev |
| **api** | (no marker — default) | `test_admin_api.py`, `test_device_api.py`, `test_auth_negative.py`, `test_v02_rbac_invites.py`, `test_v025_mass_action_gate.py` | ~50 | ~15s | Before every commit |
| **ui** | (no marker — default) | `test_ui_flows.py` | ~20 | ~30s | Before every PR |
| **responsive** | `@pytest.mark.responsive` | `test_responsive.py` | ~12 | ~25s | Before every UI/CSS PR |
| **slow** | `@pytest.mark.slow` | `test_hardening_probes.py::test_rate_limit_drain` and similar | ~3 | ~70s (rate-limit window) | Release gate only |

The default `pytest tests/qa` runs **api + ui + responsive** (everything
*except* `slow`). `smoke` is implicit — it's a subset of api and runs as
part of the default.

## Pytest configuration

Markers are registered in `pyproject.toml` so unknown-marker warnings
don't fire:

```toml
[tool.pytest.ini_options]
markers = [
    "smoke: very fast core-up checks (subset of api)",
    "responsive: mobile/tablet viewport regression tests",
    "slow: tests that block on rate-limit windows or sleep",
]
addopts = "-m 'not slow'"
```

`addopts = "-m 'not slow'"` deselects the slow bucket by default.

## Common run patterns

```bash
# Default (everything except slow). 95% of contributors only need this.
cd /mnt/s/code/rebooter-droids
python3 -m pytest tests/qa

# Just smoke — fastest possible "is the app even up" check.
python3 -m pytest tests/qa -m smoke

# Just responsive — what to run after editing CSS or a template.
python3 -m pytest tests/qa/test_responsive.py
# or
python3 -m pytest tests/qa -m responsive

# Just API (no browser) — fastest gate that doesn't need Playwright at all.
python3 -m pytest tests/qa --ignore=tests/qa/test_ui_flows.py \
                            --ignore=tests/qa/test_responsive.py

# Full suite including slow probes — release-gate.
python3 -m pytest tests/qa -m ""
```

## Running against node-1 vs node-2

All buckets honour `REBOOTER_QA_BASE`. Both URLs MUST pass the full
suite before a release ships:

```bash
REBOOTER_QA_BASE=https://www.voipguru.org/rebooter   python3 -m pytest tests/qa
REBOOTER_QA_BASE=https://www2.voipguru.org/rebooter  python3 -m pytest tests/qa
```

The responsive bucket is especially important on node-2 because the
fallback is a transparent HTTPS proxy — a misconfigured nginx
`proxy_set_header` would only show up as a missing CSS file, not a 500.
The console-error + 5xx-response watchers in the conftest fixtures catch
that class of regression.

## Fixture inventory (responsive)

`tests/qa/conftest.py` exposes these viewport-aware fixtures:

| Fixture | Viewport | Auth | Use for |
|---|---|---|---|
| `page` | Default (1280×720) | none | Existing desktop tests |
| `logged_in_page` | Default | admin | Existing desktop tests |
| `mobile_page` | 375×667 | none | Mobile login / public pages |
| `mobile_logged_in_page` | 375×667 | admin | Mobile admin pages |
| `tablet_page` | 768×1024 | none | Tablet login / public pages |
| `tablet_logged_in_page` | 768×1024 | admin | Tablet admin pages |

Each fixture installs the same `console errors` and `5xx responses`
watchers as the desktop `page` fixture, exposed at
`p._console_errors` and `p._failed_responses` for asserts.

## Adding a test to the right bucket

Decision tree:

1. **Does it need a browser?** No → put it in an `_api`/`_negative` file.
2. **Does it run at a non-default viewport (mobile/tablet)?**
   Yes → `tests/qa/test_responsive.py`, no marker needed (the file is
   the marker).
3. **Does it block on a real-time window (rate-limit, idle timeout, etc)?**
   Yes → mark it `@pytest.mark.slow` so it's deselected by default.
4. **Is it the "is the app even up" check?**
   Yes → put it in `test_smoke.py`, mark it `@pytest.mark.smoke`.
5. **Otherwise** → it's a default ui/api test, no marker.

## Open items

- **CI integration:** the buckets above are designed for local dev;
  CI is not yet wired (no GitHub Actions workflow). When CI lands, the
  default + responsive buckets should run on every push, slow on
  scheduled cron.
- **Coverage report:** `coverage` not yet integrated. Add when CI lands.
- **Visual regression:** the responsive bucket asserts on layout
  *behaviour* (no horizontal scroll, grid columns, etc.), not pixel
  diffs. A future slice may add screenshot-diff fixtures using
  Playwright's built-in image-compare; out of scope for v0.3 CSS pass.
