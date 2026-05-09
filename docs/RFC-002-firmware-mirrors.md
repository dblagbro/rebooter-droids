# RFC-002: Firmware Hosting & Mirror Fallback Chain

| Field | Value |
|---|---|
| Status | **Draft** (seeded 2026-05-09 from product/firmware/design directive) |
| Authors | rebooter-droids backend/web team; product-firmware-design |
| Targets | rebooter-droids backend, nginx routing, Sonoff S31 firmware OTA |
| Supersedes | — |
| Superseded by | — |

> **About this RFC:** internal-design-document sense, not IETF. Lives in
> the repo for cross-team redlining. Comments belong as PRs against this
> file.

---

## 1. Summary

Move canonical firmware-binary hosting off the host's root-level nginx
alias and under the rebooter-droids project's own routing tree, then
**publish every release to an ordered chain of mirrors** so a device can
recover even if the primary host (`www.voipguru.org`) is unreachable
*or the business operating it has gone away entirely*. The OTA response
gains an ordered `download_urls` array; the device walks it until one
mirror returns a SHA-256-matching binary.

The recommended chain is:

1. **Primary** — rebooter-droids container itself, served at
   `/rebooter/firmware/<channel>/<filename>` via a project-owned nginx
   snippet (or, if simpler, via the Flask app + sendfile).
2. **Secondary** — **GitHub Releases**, attached to the same git tag
   that ships the release, at a stable per-version URL.
3. **Tertiary (optional, future)** — **jsDelivr CDN over GitHub
   Releases**, giving a CDN-cached path that survives even a brief
   GitHub outage.

GitHub Releases is the load-bearing fallback: it is free, the URL is
stable for the life of the GitHub repo, it is operationally independent
of `voipguru.org` and of any paid hosting plan, and it survives the
"founder stops paying for infrastructure" failure mode.

## 2. Motivation

Today the firmware URL handed to a device looks like:

```
https://www.voipguru.org/rebooter/firmware/rebooter-0.1.2-stable.bin
```

Two structural risks:

1. **Hand-edited host nginx.** The path `/rebooter/firmware/` is served
   by a `location` block hand-added to `/home/dblagbro/docker/config/
   nginx/nginx.conf` aliasing `/mnt/s/code/rebooter-droids/data/
   firmware/`. The block sits inside the **default voipguru.org server
   block**, not inside any rebooter-owned config. If the host's nginx
   config is regenerated, migrated, or the voipguru.org site is
   reorganised, the firmware URL breaks silently — the API will keep
   handing devices a URL that 404s, and devices have no fallback.
2. **Single point of failure.** If the host is down (network, hardware,
   billing, account closure) every device on the fleet loses its only
   path to a firmware update — including the rollback firmware that
   would un-brick a bad release. There is no recovery path that does
   not require the operator to be alive and the business to still be
   paying its bills.

Sub-task (1) of the directive — *move under the managed `/rebooter/`
location tree* — is fixed by making the rebooter-droids project own its
own nginx snippet (or serving the file from Flask). Sub-task (2) — *try
multiple locations in order, at least one operationally independent of
the main business* — is fixed by mirroring every release to GitHub
Releases at upload time and shipping the mirror URLs to the device.

## 3. Scope

In scope for v1:

- Project-owned routing for `/rebooter/firmware/`.
- A **mirror-publisher abstraction** with two implementations: local
  (today's behaviour) and GitHub Releases.
- Per-release mirror records in the database.
- `download_urls: [...]` on the OTA response, in addition to the
  legacy `download_url` (preserved for backwards compat).
- Device-firmware contract: walk the list in order; verify SHA-256;
  abort if all fail.
- Per-channel paths so dev / beta / stable do not collide on any
  mirror.

Non-goals for v1:

- Signed URLs / per-device download authentication. Today's contract is
  "URL is unauthenticated, integrity is via SHA-256." Keep that.
- Object storage (R2/S3/B2) as a paid mirror. Useful later but does
  *not* satisfy the "independent of the business" requirement on its
  own — billing is shared infrastructure. Documented in §6.
- Mobile-app over-the-air firmware proxying. Out of scope for the
  hosting layer.
- IPFS / decentralised storage. ESP HTTP OTA cannot dependably resolve
  these without an HTTP gateway, which re-introduces the single-point
  failure we are trying to remove.

## 4. Current state (inventory)

| Area | Today |
|---|---|
| On-disk path | `/mnt/s/code/rebooter-droids/data/firmware/<filename>` |
| Filename format | `rebooter-<version>.bin` (stable) or `rebooter-<version>-<channel>.bin` (dev/beta) |
| Public base | `REBOOTER_FIRMWARE_PUBLIC_BASE=https://www.voipguru.org/rebooter/firmware` |
| Serving | nginx `location ^~ /rebooter/firmware/ { alias /mnt/s/code/rebooter-droids/data/firmware/; }` in the *host* nginx.conf |
| OTA endpoint | `GET /api/v1/device/firmware` (device-token auth) returns one `download_url` |
| Channels | dev / beta / stable (already enforced in the model) |
| Auth on the binary | None. Public URL. SHA-256 verified by device. |
| Cache header | `Cache-Control: public, max-age=300` |

The `/rebooter/firmware/` URL prefix is already correct shape — the
problem is *where the location block lives* and *the lack of a
fallback*.

## 5. Goals

- **G1.** A device can complete a firmware update if `voipguru.org` is
  unreachable, as long as it has working DNS and an outbound HTTPS
  path. (GitHub fallback satisfies this.)
- **G2.** A device can complete a firmware update if the rebooter
  central server goes away permanently, as long as a previously-known
  release URL is reachable. (GitHub Releases tag URLs stay alive.)
- **G3.** The rebooter-droids project owns its own URL contract.
  Disturbing host nginx config does not break OTA.
- **G4.** Mirror sync is a first-class part of "ship a release," not a
  manual afterthought. Releases either succeed at every required mirror
  or are marked `mirror_pending` and surfaced to the operator.
- **G5.** Channel separation (dev/beta/stable) is preserved end-to-end:
  no mirror collapses channels into a single bucket.

## 6. Mirror-candidate evaluation

Each candidate scored against the seven dimensions called out in the
directive: **auth, release-channel separation, URL stability, direct-
download behaviour, rate limits, file size, OTA compatibility**, plus
the new **independence-from-business** axis.

### 6.1 Self-hosted (primary — keep, but project-owned)

| Dimension | Notes |
|---|---|
| Auth | Public URL + SHA-256 (today). No change. |
| Channels | Move to per-channel sub-paths: `/rebooter/firmware/<channel>/<file>`. |
| URL stability | Stable as long as rebooter-droids is up. |
| Direct download | Native — nginx serves the file. No redirects. |
| Rate limits | None besides host bandwidth. |
| File size | Unbounded. ESP firmware is < 2 MB. |
| OTA compat | Best — single GET, raw bytes, content-length set. |
| Independence | **Zero.** This *is* the business infrastructure. |

Action: keep as primary. Move ownership of the nginx snippet into the
rebooter-droids repo (`deploy/nginx/rebooter-firmware.conf`) and
include it from the host nginx config via `include`. Bonus option:
also expose the file through Flask (`send_from_directory` + sendfile)
so even a misconfigured host nginx is recoverable as long as the
container is up.

### 6.2 GitHub Releases (recommended secondary)

| Dimension | Notes |
|---|---|
| Auth | Public release assets. No auth needed for downloaders. Upload uses a fine-grained PAT scoped to `repo:write` on this repo only. |
| Channels | Encoded in the asset filename: `rebooter-<version>-<channel>.bin`. One git tag per release; multiple channel assets per tag if needed. |
| URL stability | `https://github.com/<owner>/<repo>/releases/download/v<version>/<filename>` — stable for the life of the repo. Survives operator's voipguru.org outage *and* survives the rebooter-central server going away. |
| Direct download | The download URL 302-redirects to an `objects.githubusercontent.com` S3-backed URL. ESP HTTP OTA must be configured to follow redirects (it can — both Arduino and ESP-IDF OTA libs support this). One redirect, content-length on final URL, raw bytes. |
| Rate limits | API: 60 unauth/hr per IP. **Asset downloads do not consume API quota** and are effectively unlimited for a normal fleet. |
| File size | Hard cap **2 GB per asset**. Sonoff S31 firmware is ~1 MB — three orders of magnitude headroom. |
| OTA compat | Verified working in ESP-IDF and Arduino HTTPClient OTA. Caveat: TLS handshake against github.com requires a CA bundle that includes the GitHub chain — already standard in ESP-IDF certs. |
| Independence | **Strong.** Hosted by GitHub, billed to the operator's personal GitHub account (or free tier). Survives the business shutting down. URL outlives the company. |

Action: adopt as the v1 fallback.

### 6.3 jsDelivr over GitHub (optional tertiary, future)

| Dimension | Notes |
|---|---|
| Auth | Public CDN. |
| Channels | Same filename scheme. |
| URL stability | `https://cdn.jsdelivr.net/gh/<owner>/<repo>@v<version>/<path>` — pinned to the git tag, stable. |
| Direct download | Native CDN serve, no redirect. |
| Rate limits | Free, soft cap ~50 GB/month per project (way above our fleet). |
| File size | **50 MB hard cap per file.** Comfortably above current and projected firmware size. |
| OTA compat | Single TLS handshake, content-length set, no redirect. |
| Independence | Indirect — relies on GitHub being up. Adds CDN-tier resilience but does not remove the GitHub dependency. |

Action: defer to a future iteration. Worth shipping once the secondary
mirror has bedded in.

### 6.4 Object storage (R2 / S3 / B2) — rejected as the *independent* mirror

| Dimension | Notes |
|---|---|
| Independence | Tied to a billing relationship. If the business stops paying, the bucket goes away. Does **not** satisfy the "operationally independent from the main business" requirement. |

Useful as a *third* mirror for performance/geographic resilience but
not a substitute for GitHub Releases as the independent fallback.
Documented and parked.

### 6.5 raw.githubusercontent.com — rejected

| Dimension | Notes |
|---|---|
| File size | 100 MB hard cap (fine), but this endpoint is intended for source code, not binary distribution. GitHub explicitly recommends Releases for binaries and rate-limits this endpoint more aggressively. |

Use Releases instead.

### 6.6 Personal / operator-owned second domain — rejected as v1

A `dblagbro.com`-style mirror would be operator-funded and survive a
voipguru.org outage, but it is still a piece of infrastructure the
operator has to keep alive. GitHub Releases is strictly better:
free, indexed, and outlives the operator's other domains.

## 7. Design

### 7.1 Data model

Add `firmware_release_mirrors` table:

```
firmware_release_mirrors
├── id                 (ULID, prefix `fmir`)
├── release_id         (FK → firmware_releases.id, ON DELETE CASCADE)
├── kind               (enum: 'local' | 'github_release' | 'jsdelivr_gh' | 'object_storage')
├── url                (full https URL, indexed)
├── status             (enum: 'pending' | 'live' | 'failed')
├── verified_sha256    (string, hex; populated by the post-publish HEAD+GET probe)
├── last_probed_at     (timestamp)
├── last_error         (string, nullable — short reason)
└── created_at         (timestamp)
```

`(release_id, kind)` is unique — one mirror per kind per release.

### 7.2 Mirror-publisher abstraction

```python
class MirrorPublisher(Protocol):
    kind: str
    def publish(self, release: FirmwareRelease, blob_path: Path) -> str:
        """Push the binary to this mirror; return the public URL."""
    def probe(self, url: str) -> str:
        """HEAD+ranged-GET; return sha256 of the bytes the mirror serves."""
```

Implementations land in `app/services/firmware_mirrors/`:
- `local.py` — no-op publish (file is already in `firmware_dir`); probe
  hits the public URL with a small ranged GET to confirm bytes match.
- `github_release.py` — uses the `gh` CLI or the `PyGithub` SDK to (a)
  ensure a release exists for `v<version>`, (b) upload the asset, (c)
  return the asset URL. Auth via fine-grained PAT in
  `REBOOTER_GITHUB_RELEASE_TOKEN`.

A new background job (`publish_mirrors_for_release`) is enqueued at
the end of `upload_release()`. The job iterates configured publishers,
calls `publish` then `probe`, and writes a `firmware_release_mirrors`
row per attempt. Failures are surfaced in the admin firmware-detail
page with a "Retry mirror" button.

### 7.3 OTA response shape

`GET /api/v1/device/firmware` (assigned case) becomes:

```json
{
  "ok": true,
  "data": {
    "assigned": true,
    "channel": "stable",
    "target_version": "0.2.7",
    "sha256": "<hex>",
    "download_url":  "https://www.voipguru.org/rebooter/firmware/stable/rebooter-0.2.7.bin",
    "download_urls": [
      "https://www.voipguru.org/rebooter/firmware/stable/rebooter-0.2.7.bin",
      "https://github.com/dblagbro/rebooter-droids/releases/download/v0.2.7/rebooter-0.2.7.bin"
    ],
    "force": false
  }
}
```

`download_url` stays as the first element of `download_urls` for v0.2.x
firmware compatibility.

### 7.4 Device download algorithm (firmware contract)

```
for url in download_urls:
    bytes = http_get(url, follow_redirects=true, timeout=30s, retries=2)
    if bytes is None: continue
    if sha256(bytes) != response.sha256: continue
    flash(bytes); reboot
    return
report_update_failure(); back_off()
```

SHA-256 is the trust anchor; a malicious or stale mirror cannot poison
the update because the device only flashes a binary that matches the
hash the *authenticated* OTA endpoint already vouched for.

### 7.5 URL layout (per channel)

| Mirror | Stable | Beta | Dev |
|---|---|---|---|
| Primary | `/rebooter/firmware/stable/rebooter-<v>.bin` | `…/beta/…-beta.bin` | `…/dev/…-dev.bin` |
| GitHub Releases | `…/releases/download/v<v>/rebooter-<v>.bin` | same tag, `-beta` suffix | same tag, `-dev` suffix |

One git tag per `version`, multiple assets per tag for the channels we
publish on that version.

### 7.6 Routing ownership (sub-task 1)

- Add `deploy/nginx/rebooter-firmware.conf` to the repo. It defines
  the `location ^~ /rebooter/firmware/` block.
- Host nginx config switches from a hand-written `location` to a
  single `include /opt/rebooter/nginx/rebooter-firmware.conf;`. The
  rebooter-droids project owns the contents.
- Belt-and-braces: Flask also exposes
  `GET /firmware/<channel>/<filename>` (no `/api/v1` prefix; matches
  the public URL shape) using `send_from_directory` so the file is
  reachable even if the nginx alias is misconfigured. Production traffic
  still hits nginx; the Flask path is a recovery escape hatch.

## 8. Phased rollout

| Phase | What ships | Cuts over when |
|---|---|---|
| **P0** | This RFC, redlined and accepted. | Sign-off from product/firmware/design + backend/web. |
| **P1 (backend, no firmware change)** | `firmware_release_mirrors` table; `MirrorPublisher` abstraction; local publisher; per-channel sub-paths on the primary; project-owned nginx include; admin UI shows mirror table per release. | Backend tests green; existing fleet still served by primary; OTA response unchanged. |
| **P2 (backend + ops)** | GitHub Releases publisher; `REBOOTER_GITHUB_RELEASE_TOKEN` settings entry; backfill: run publisher across last N stable releases. | Probe of every backfilled release returns matching SHA-256. |
| **P3 (firmware contract bump)** | OTA response gains `download_urls`. Server still populates `download_url` (= `download_urls[0]`) for old firmware. | Server change deployed. |
| **P4 (firmware-side support)** | Sonoff S31 firmware updated to walk `download_urls` with retry + SHA-256 verify. | Firmware release shipped + at least one rolling fleet upgrade observed using the secondary mirror in a chaos test (intentionally block primary at the device's edge). |
| **P5 (optional)** | jsDelivr publisher; admin UI lets the operator toggle which mirrors are required vs nice-to-have. | After P4 has been stable in production for ≥ 30 days. |

P1 and P2 are reversible (drop the table, remove the include).
P3 and P4 are forward-only contract changes — protect with the
backwards-compat `download_url` field for the firmware fleet that
hasn't taken the P4 update yet.

## 9. Risks and open questions

1. **GitHub PAT custody.** A leak lets an attacker overwrite release
   assets. Mitigations: fine-grained PAT scoped to one repo with
   `contents:write` only; rotated quarterly; stored only in the
   container env, never in the repo. Even with a PAT compromise, the
   device's SHA-256 check (sourced from the authenticated central
   endpoint, not from the mirror) blocks a bad-binary swap unless the
   central DB is *also* compromised.
2. **GitHub takedowns.** GitHub can remove a repo for ToS violation.
   Counter: the operator's personal account hosts the repo; the
   business is the code's customer, not its owner. Probability is low
   for a firmware-distribution repo of this character.
3. **TLS root store on the device.** GitHub's TLS chain must be in the
   device's trust store. ESP-IDF stock trust store includes it; verify
   in firmware QA before P4 cuts over.
4. **Hash desync.** If a mirror serves an older binary under the same
   filename, the device's SHA-256 check will fail and it will move on
   to the next mirror — *but* an operator will see "all mirrors
   probed, GitHub is at hash X, primary is at hash Y" alerts. The
   admin UI should surface this distinctly.
5. **Filename collisions across channels in GitHub.** Solved by the
   channel suffix (`-beta`, `-dev`); stable has no suffix.
6. **Primary URL shape change** (`/rebooter/firmware/<channel>/...`
   vs. today's flat layout). Existing devices already in the field
   know only the *URLs they are handed* by the OTA endpoint, not the
   URL shape — so changing the shape on the server side is safe as
   long as the server keeps generating valid URLs. The local
   publisher writes per-channel sub-directories; old flat-layout files
   are migrated by a one-off script in P1.
7. **Cache poisoning at jsDelivr.** Pin to `@v<version>`; jsDelivr
   only caches per-tag, so a subsequent release does not invalidate an
   older device's pinned URL.

## 10. Open redlines for product/firmware/design

- Confirm the device-side change in P4 is acceptable to the firmware
  team (cost of adding the loop + retry on the embedded side).
- Confirm the channel-suffix asset naming on GitHub (`rebooter-<v>.bin`
  for stable, `-beta.bin` / `-dev.bin` otherwise) — alternative is one
  release tag per channel.
- Confirm the operator-funded GitHub account is the right home (vs.
  a `voipguru` org account). Independence-from-business argues for the
  personal account; brand/visibility argues for the org.

## 11. Appendix — non-design implementation notes

- The `gh` CLI is already installed on the build host; the simplest
  GitHub publisher shells out to `gh release upload --clobber v<v>
  <asset>`. PyGithub is the next step if we want richer error
  surfacing.
- A future `firmware_mirrors_status` admin page should expose
  per-release per-mirror status with a "Republish" button and a
  "Probe now" button.
- The `local` publisher's per-channel sub-paths land behind a feature
  flag during P1 so the cutover can be staged: write to both the flat
  and the per-channel paths, flip the URL generator over, then delete
  the flat-layout files in a follow-up.
