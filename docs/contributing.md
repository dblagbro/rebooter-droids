# Contributing

Read [`architecture.md`](architecture.md) before any non-trivial change.

## Hard rules (these are not negotiable)

1. **Postgres is private to its node.** No LAN port, no SSH tunnel,
   no cross-host docker bridge.
2. **All cross-component access via the public HTTPS API.** Includes
   future node-2 sync, the mobile app, firmware, and any ops tooling.
3. **SSH only via short hostnames** (`tmrwww01`, `tmrwww02`).
4. **Rolling deploys** — one node at a time, never both simultaneously.
5. **Every version bump = release + push.**
6. **No `docker compose down`.** Use single-container recreate.
7. **No `Co-Authored-By: Claude`** in commit messages.

## Where to put new code

| You're adding… | Put it in… |
|---|---|
| A new device-API endpoint | `app/blueprints/device_api.py` |
| A new admin endpoint (UI + API) for an existing feature | `app/blueprints/admin/<feature>.py` |
| A new admin feature entirely | `app/blueprints/admin/<feature>.py` (UI + API in one file); `app/services/<feature>.py`; `app/models/<feature>.py` |
| Pure business logic / DB I/O | `app/services/<feature>.py` |
| New table / column | `app/models/<feature>.py`. `services/bootstrap.py::ensure_schema()` runs `Base.metadata.create_all()` on every boot — new tables auto-create. |
| Cross-cutting decorator / middleware | `app/middleware/<thing>.py` |
| HTML page | `templates/<page>.html`. Extend `layout.html`. Tables get wrapped in `<div class="table-wrap">` for the responsive pass. |
| Test | `tests/qa/test_<feature>.py`. See [`testing-split.md`](testing-split.md) for the marker conventions. |

## Sizing

- Aim for cohesive medium-sized modules. A file is too big if you have
  to scroll past 5 unrelated handlers to find the one you want.
- A file is too small if it has no clear ownership of its own — fold it
  into the closest feature module.
- Soft limit: 500 lines per blueprint; 250 per service; 200 per model
  module.
- **When a service crosses ~2× its soft limit** and the responsibilities
  inside it are separable, split it into a `services/<x>/` subpackage
  (see `architecture.md` §"Service subpackages"). Existing import
  paths (`from app.services.<x> import …`) must keep working via
  re-exports in `__init__.py`. Precedents: `services/devices/` and
  `services/watchdog_runtime/` (v0.5.15).

## Commit conventions

- Use `feat:` / `fix:` / `chore:` / `docs:` / `refactor:` prefixes.
- Subject ≤ 70 chars, imperative voice ("add X", not "added X").
- Body explains the **why**, not the what.
- **No `Co-Authored-By: Claude` trailer.**

## Release process

For any version bump (even cosmetic):

```bash
# 1. update app/version.py + pyproject.toml + CHANGELOG.md
# 2. commit
git add app/version.py pyproject.toml CHANGELOG.md
git commit -m "vX.Y.Z: <one-line summary>"
git tag vX.Y.Z
git push origin <branch> vX.Y.Z

# 3. create release
gh release create vX.Y.Z --title "<title>" --notes "<changelog excerpt>"

# 4. build + push image
sudo docker compose build rebooter-droids
sudo docker tag dblagbro/rebooter-droids:latest dblagbro/rebooter-droids:X.Y.Z
sudo docker push dblagbro/rebooter-droids:X.Y.Z
sudo docker push dblagbro/rebooter-droids:latest

# 5. recreate container + curl-verify
sudo docker compose up -d --no-deps --force-recreate rebooter-droids
curl -fsS https://www.voipguru.org/rebooter/api/v1/version
```

Skipping any of those steps means the version isn't really released.

## Tests

```bash
python3 -m pytest tests/qa                  # default fast pass
python3 -m pytest tests/qa -m smoke         # smoke only
python3 -m pytest tests/qa -m responsive    # mobile + tablet pass
python3 -m pytest tests/qa -m ""            # everything incl. slow probes
```

The suite hits the live deployment by default. Override with
`REBOOTER_QA_BASE=https://www2.voipguru.org/rebooter` to test the
fallback URL.

QA test data uses the prefix `qa-` (groups, sites) or `QA <thing>`
(display names) so cleanup queries can find it. The suite tries
best-effort cleanup but doesn't have full teardown yet — leftover
artefacts are an open hardening item, not a blocker.

## Code review checklist (self-review before push)

- [ ] Endpoint URLs unchanged (or deprecation noted in `API.md`)?
- [ ] View-function names unchanged (so `url_for(...)` keeps working)?
- [ ] New writes emit `audit_events` rows (see `services/audit.py`)?
- [ ] New tables auto-create via `ensure_schema()`?
- [ ] Behaviour change documented in `CHANGELOG.md`?
- [ ] If a hard rule changed, `architecture.md` is updated too?
- [ ] If structure changed, `refactor-log.md` has an entry?

## When in doubt

Ask. The architecture is opinionated for a reason. The fastest way to
break it is to add a "small exception" that becomes load-bearing.
