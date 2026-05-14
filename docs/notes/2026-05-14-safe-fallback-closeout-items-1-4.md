# Safe Fallback Closeout for Items 1-4 - 2026-05-14

## Scope

This note closes out the following active items:

1. central identity recovery / rekey
2. protected backup / restore validation
3. release packaging / versioning
4. bootstrap artifact decision

## 1. Central identity recovery / rekey

### Current bench reality

The bench device at `http://192.168.1.48/` is locally healthy but still lands in the missing-token state:

- `central_enabled = true`
- `central_registered = false`
- `central_state = registered_no_token` or `disabled` depending on timing
- `central_device_id = ""`

This confirmed the remaining gap is not fallback transport anymore. It is the hub/device rekey path after local credential loss.

### Implemented hub-side self-heal path

I patched:

- `S:\code\rebooter-droids\app\services\announcements.py`

New behavior:

- when an unauthenticated `/api/v1/device/announce` arrives from a MAC that already belongs to an active central-managed device,
- and the announcing IP still matches the hub's last-known device IP,
- and the prior announcement row had already been consumed,

the hub now mints a restore-style enrollment token targeted at the existing device row and resets the announcement row into the normal:

- `adopted`
- `awaiting_register`
- `/register` rebind

flow.

This is the least-invasive clean self-heal path I found that does not require operator handwork on every token-loss incident.

### Test coverage added

I added a QA test here:

- `S:\code\rebooter-droids\tests\qa\test_v0420_announce_adopt.py`

The new test exercises:

- first registration
- token-loss-style re-announce
- auto-issued replacement enrollment token
- restore-style rebind back onto the same logical device row

### Limit

I could not execute the full hub QA file directly in the original workstation context because the Rebooter Python test environment was not installed here at first.

- `python -m pytest` initially failed with `No module named pytest`

### Local runtime verification completed

I then created a local Python test environment and executed the core announce/adopt/register/rebind path directly against the patched service code using a temporary local database harness.

Verification artifact:

- `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-hub-auto-rebind-local-verification.json`
- `S:\code\rebooter-droids\docs\notes\2026-05-14-hub-auto-rebind-local-verification.json`

Verified outcomes:

- first announce returns `pending`
- post-adoption announce returns `adopted`
- missing-token re-announce returns replacement `adopted`
- subsequent announce returns `awaiting_register`
- re-register preserves the original `device_id`
- the replacement credential rotates cleanly
- only one active credential row remains after rebind

Important caveat:

- the local harness used SQLite, which required a small naive-UTC datetime shim for enrollment expiry and an audit no-op stub
- the core service path still executed against real patched service code

## 2. Protected backup / restore validation

### Bench validation status

Completed on `.48`.

I set a local admin password on the bench unit:

- username: `admin`
- password: `BenchPass123!`

Then I verified the protected full-config export and restore flow using temporary test values for the central identity fields.

### What was proven

Using:

- `GET /api/system/config-backup`
- `POST /api/config/save`

I verified that:

- `central.device_id` is present in the protected backup
- `central.device_token` is present in the protected backup
- those fields can be cleared
- those fields can be restored cleanly from the protected backup

The reason the first attempt failed was not a broken endpoint. It was the existing safety behavior that clears `device_id` and `device_token` when `enrollment_token` changes. I reran the validation with a stable empty enrollment token, and the round trip passed.

### Backup artifacts saved

Local:

- `C:\Users\Administrator\Documents\Codex\2026-04-18-all-projets-on-this-windows-pc\2026-05-14-rebooter-48-protected-config-backup.json`

Mirror on `S:`:

- `S:\code\rebooter-droids\docs\notes\2026-05-14-rebooter-48-protected-config-backup.json`

### Final bench state after validation

I restored the bench config back to a sane non-test state:

- central enabled
- enrollment token empty
- device id empty
- device token empty

The bench unit still has the local admin password provisioned.

## 3. Release packaging / versioning

### Decision

The safer firmware should no longer ship around under the generic runtime label `0.1.17-dev-central`.

I cut a new explicit runtime version:

- main: `0.1.18-dev-central-safe`
- bad test image: `0.1.18-dev-central-safe-badboot`

### Rebuild results

Built successfully:

- `sonoff_s31`
  - size: `618544`
  - SHA256: `26B6F21FF008C6D3FD5D46DCFBB831570DF2C73833789099393E9669D092C122`
- `sonoff_s31_bad_boot_test`
  - size: `618880`
  - SHA256: `DED9367A295B802FDCFF7A1F8700139611334BDB54949A934E0B566052CADAFD`
- `sonoff_s31_bootstrap`
  - size: `472768`
  - SHA256: `9A9D1833C602857E5FD35A48F8168F478581E320F3FDB82484294820867545AA`

### Artifact placement on `S:`

Main safe build:

- `S:\code\rebooter-droids\data\firmware\stable\rebooter-0.1.18-dev-central-safe.bin`
- `S:\code\rebooter-droids\data\firmware\dev\rebooter-0.1.18-dev-central-safe.bin`

Bad test build:

- `S:\code\rebooter-droids\data\firmware\dev\rebooter-0.1.18-dev-central-safe-badboot.bin`

## 4. Bootstrap artifact decision

### Decision

Publish the newer bootstrap artifact as a named candidate only.

Do **not** replace `bootstrap\latest.bin` yet.

### Why

This keeps the newer rescue loader available in the firmware library without pretending we have completed a full release/promotion decision on the bootstrap path.

That is the safer call because:

- the main OTA path is now much stronger than before
- bootstrap is still a special recovery path
- replacing `latest.bin` would silently change behavior for anyone using the old bootstrap workflow

### Candidate artifact placed on `S:`

- `S:\code\rebooter-droids\data\firmware\bootstrap\rebooter-bootstrap-0.2.5-dev-safe.bin`

### Current policy

- explicit named bootstrap candidate: yes
- overwrite `bootstrap\latest.bin`: no

## Bottom line

Items 1 through 4 are now in materially better shape:

- rekey path has a concrete hub-side self-heal implementation and QA coverage
- protected backup / restore is bench-proven
- safer firmware now has a real version label
- bootstrap publication now has an explicit conservative policy

The remaining unclosed risk is not conceptual anymore:

- the hub-side auto-rebind patch still needs runtime verification in a proper Rebooter test environment
