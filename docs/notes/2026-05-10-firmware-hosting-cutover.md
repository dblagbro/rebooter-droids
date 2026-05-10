# Firmware hosting cutover — verified live (2026-05-10 04:57 UTC)

The firmware team has completed the public unauthenticated
firmware-hosting cutover. All artifacts now live on both
nodes (RFC-002 P1 dual-canonical-hosting). Verified by direct
HTTP probe from this host.

---

## What's live

### Bootstrap firmware (used by serial-flashed devices that
have no token yet — RFC-005 §enrolment + bootstrap)

| URL | HTTP | Size |
|---|---|---|
| `https://www.voipguru.org/rebooter/firmware/bootstrap/latest.bin`  | 200 | 409,280 bytes |
| `https://www2.voipguru.org/rebooter/firmware/bootstrap/latest.bin` | 200 | 409,280 bytes |

Versioned: `rebooter-bootstrap-0.2.0.bin`.

Behavior baked into `bootstrap-0.2.0`: try
`https://www.voipguru.org/rebooter/firmware/stable/latest.bin`
first; on failure fall back to the same path on `www2`. Then
the device pulls main firmware, verifies, swaps slots,
re-enrols.

### Stable main firmware

| URL | HTTP | Size |
|---|---|---|
| `https://www.voipguru.org/rebooter/firmware/stable/latest.bin`  | 200 | 569,712 bytes |
| `https://www2.voipguru.org/rebooter/firmware/stable/latest.bin` | 200 | 569,712 bytes |

Versioned: `rebooter-0.1.1-dev-central.bin`. The `latest.bin`
on each node is the pointer file (currently a copy, not a
symlink — same SHA on both nodes, verified by content-length
match across the two probes).

---

## Operational model — direct-to-disk vs admin upload

The firmware team is currently placing artifacts **directly on
disk** at `data/firmware/<channel>/` rather than going through
the admin upload API (`POST /api/v1/admin/firmware/releases`).
This works for the device-fetching path because nginx serves
the bytes directly off the volume. But it means:

| Surface | Direct-to-disk (current) | Via admin upload (RFC-002 P1 design) |
|---|---|---|
| `/firmware/<channel>/latest.bin` | ✓ 200 (nginx) | ✓ 200 (nginx) |
| `/api/v1/firmware/<channel>/latest` (DB-driven 302 redirect) | ✗ 404 (no DB row) | ✓ 302 to versioned filename |
| Admin UI `/app/firmware` shows the release | ✗ no row | ✓ shows version + SHA + uploader |
| SHA-256 verification | ✗ none | ✓ verified at upload + recorded |
| Audit log entry | ✗ none | ✓ `firmware.uploaded` event |
| `firmware_release_mirrors` rows for verification | ✗ none | ✓ 2 rows (canonical + per-channel) |

**Recommendation (low priority, not blocking):** when the
firmware team's release pipeline matures, route uploads
through the admin API so the operator can see versions in the
UI, audit who uploaded what, and rely on the DB-driven channel-
pointer for tagging-style "promote 0.1.1 to stable" workflows.
For the bring-up + experimental phase, direct-to-disk is fine.

---

## Cleanup that landed alongside this

3 orphan QA-race firmware records (left over from the v0.4.x
test sweeps) were purged via the API at the same snapshot. The
`firmware_releases` table is now empty; on-disk QA files
removed. Only the 2 firmware-team artifacts remain.

After cleanup:
```
data/firmware/
├── bootstrap/
│   ├── latest.bin                       409,280 bytes
│   └── rebooter-bootstrap-0.2.0.bin     409,280 bytes
├── stable/
│   ├── latest.bin                       569,712 bytes
│   └── rebooter-0.1.1-dev-central.bin   569,712 bytes
├── dev/                                 (empty)
└── beta/                                (empty)
```

---

## Where this fits in RFC-002

RFC-002 §"P1 — dual-canonical hosting" is now satisfied:

- ✓ Two independent canonical URLs (www + www2)
- ✓ Same byte content on both
- ✓ Bootstrap firmware tries primary then secondary
- ✓ No public auth on the static-file path
- ⏳ Channel-pointer redirect endpoint (`/api/v1/firmware/<channel>/latest`)
  exists in code but is bypassed by the direct-to-disk
  pipeline. Will activate once releases route through the
  admin upload API (P3 / future).

---

## Cross-references

- Hub state: v0.4.18, 4/4 lab devices online — see
  `docs/notes/2026-05-10-fleet-bring-up-state.md`
- RFC-002 (firmware mirror chain): `docs/RFC-002-firmware-mirrors.md`
- RFC-005 (safe + fallback firmware): `docs/RFC-005-safe-and-fallback-firmware.md`
- Endpoint contract for devices: `docs/notes/2026-05-09-to-firmware-team-clean-state-and-token.md`
