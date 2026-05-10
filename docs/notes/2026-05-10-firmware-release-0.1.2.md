# Firmware release — `0.1.2-dev-central` + `bootstrap-0.2.1` (2026-05-10 20:18 UTC)

Firmware team pushed a new release bundle. Hub-side verification
+ nginx caching fix included.

---

## What's now live

### Stable main firmware

| URL | HTTP | Size | sha256 |
|---|---|---|---|
| `https://www.voipguru.org/rebooter/firmware/stable/latest.bin`  | 200 | 587,488 | `1b1b6b840bff…` |
| `https://www2.voipguru.org/rebooter/firmware/stable/latest.bin` | 200 | 587,488 | `1b1b6b840bff…` (same ETag, identical bytes) |

Versioned filename on disk: `rebooter-0.1.2-dev-central.bin`.

### Bootstrap firmware

| URL | HTTP | Size |
|---|---|---|
| `https://www.voipguru.org/rebooter/firmware/bootstrap/latest.bin`  | 200 | 409,536 |
| `https://www2.voipguru.org/rebooter/firmware/bootstrap/latest.bin` | 200 | 409,536 |

Versioned filename: `rebooter-bootstrap-0.2.1.bin` (was `0.2.0` /
409,280 bytes — see prior cutover note).

---

## What changed in `0.1.2-dev-central`

Per the firmware team's release note:

- Inline help icons in the local UI
- Watchdog mode sections in **both** the fallback UI and the
  data UI
- Config-save fix for watchdog fields (was previously losing
  edits on save)
- AP fallback note added to the local UI
- Dual default Wi-Fi behavior (two SSIDs configurable + one
  used as fallback)

These are all device-side / local-UI changes; no hub contract
impact (heartbeat, register, command-poll, failsafe shapes are
unchanged from `0.1.1`).

---

## Hub-side response — nginx cache-policy fix

While verifying public delivery, found and fixed a recurring
bug pattern:

**Symptom**: after the firmware team atomically replaced
`/data/firmware/stable/latest.bin`, `www.voipguru.org` served
the *previous* artifact's bytes (577,168 from
`0.1.1-dev-central-ui`) for up to ~5 minutes while
`www2.voipguru.org` (different physical node) served the new
587,488-byte file. The two URLs disagreed on what "stable
latest" was during the cache window.

**Root cause**: `/home/dblagbro/docker/config/nginx/nginx.conf`
had `Cache-Control: public, max-age=300` on the entire
`/rebooter/firmware/` location, applied identically to both
versioned artifacts AND the `latest.bin` channel-pointer.
Versioned filenames are immutable by convention so a 5-min
cache is fine for them; `latest.bin` is a moving pointer that
gets atomic-replaced on every release and MUST be `no-cache`
or intermediaries (CDN, browser, ISP cache) will hold stale
bytes for the cache window.

**Fix applied (tmrwww01 only)**: split cache policy by URI
pattern:

| Pattern | Cache-Control |
|---|---|
| `*/latest.bin` (channel-pointer) | `no-store, no-cache, must-revalidate, max-age=0` |
| `rebooter-X.Y.Z*.bin` (versioned, immutable) | `public, max-age=86400, immutable` |
| anything else | `no-store, no-cache, must-revalidate, max-age=0` (conservative default) |

`open_file_cache off` retained on the location (handles
nginx-process-internal inode cache).

In-place edit (preserved inode 7106947 — the docker bind mount
is single-file, atomic-replace would have broken it). nginx
config validated + reloaded without socket close.

**Outstanding gap**: tmrwww02 (`198.179.77.190`) still has the
original `Cache-Control: public, max-age=300` policy because
this hub-Claude has no SSH path to that host. Bytes match
across both nodes right now (same ETag, same SHA), but the
next atomic replace could re-introduce up to 5 min of stale
bytes on the www2 path until tmrwww02's nginx gets the same
treatment. Operator action required.

---

## Verification at 20:18 UTC

```
on-disk     sha256 = 1b1b6b840bffbb617521a3e60ea88df11ac498015628e0f1979e42072fa58682
on-disk     size   = 587488

www         HTTP 200, size 587488, sha 1b1b6b84… ✓ MATCH on-disk
            Cache-Control: no-store, no-cache, must-revalidate, max-age=0  ← new policy
            ETag: "6a00e628-8f6e0"

www2        HTTP 200, size 587488, sha 1b1b6b84… ✓ MATCH on-disk
            Cache-Control: public, max-age=300                              ← old policy (unchanged)
            ETag: "6a00e628-8f6e0"

versioned   www HTTP 200, size 587488, sha matches
            Cache-Control: public, max-age=86400, immutable                 ← new policy
```

Bytes consistent. Headers asymmetric pending tmrwww02 update.

---

## Cross-references

- Prior hosting cutover (`bootstrap-0.2.0` / `0.1.1-dev-central`):
  `docs/notes/2026-05-10-firmware-hosting-cutover.md`
- Fleet bring-up state (lab-30/207/67 online, lab-225 still
  offline pre-OTA-failure):
  `docs/notes/2026-05-10-fleet-bring-up-state.md`
- v0.4.19 hub release (per-firmware fleet view + on-disk scan)
  registered the previous `0.1.1-dev-central` artifacts in the
  admin UI; running the scan again post this push will register
  `rebooter-0.1.2-dev-central.bin` as well.
