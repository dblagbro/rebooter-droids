# Runbook — firmware www vs www2 divergence

**Trigger:** firmware team reports two public hosts serving
different content for the same `/firmware/<channel>/latest.bin`
URL after a fresh upload.

## Root cause (fixed on tmrwww01 2026-05-10 20:18 UTC)

`Cache-Control: public, max-age=300` was applied to every
response from `/rebooter/firmware/`, including the moving-pointer
file `latest.bin`. After an atomic replace, intermediaries
(browsers, CDNs, ISP caches, corporate proxies) keep serving
the previous bytes for up to 300 s on whichever path was
recently accessed. Whichever public host had a fresh edge
cache returned new bytes; the other returned old.

`open_file_cache off` was already set for the location and
correctly bypassed the per-nginx-process inode cache; that's
NOT what produced the divergence. The intermediary cache was.

## Fix shape (single location, conditional Cache-Control)

```nginx
location ^~ /rebooter/firmware/ {
    alias /mnt/s/code/rebooter-droids/data/firmware/;
    autoindex off;
    open_file_cache off;

    set $rebooter_fw_cc "public, max-age=86400, immutable";
    if ($uri ~ "/latest\.bin$") {
        set $rebooter_fw_cc "no-store, no-cache, must-revalidate, max-age=0";
    }
    add_header Cache-Control $rebooter_fw_cc always;
}
```

The patch file: `docs/runbooks/nginx-firmware-cachecontrol-fix.patch`.

### Why this shape

- **Single location, not nested regex**: nested `location` with
  `alias` is a well-known nginx pitfall — the inner `alias`
  resolution gets the captured-path semantics wrong and you get
  301s with a 169-byte body. Avoided.
- **`if` directive only sets a variable**: nginx's "if is evil"
  rule applies to `if` that does rewrites or proxy_pass; using
  `if` only to assign a variable is safe and idiomatic for this
  exact case.
- **`add_header` with `always`**: the bare `add_header` without
  `always` is suppressed for non-2xx responses; we want the
  Cache-Control on 304s + 404s too.

## Apply on each nginx host that fronts /rebooter/firmware/

The hosts are not config-replicated; **edit each one
independently.**

```bash
# 1. Identify which physical host is which
getent hosts www.voipguru.org www2.voipguru.org

# 2. SSH to each host that resolves above (currently:
#    24.168.14.36 = tmrwww01, 198.179.77.190 = tmrwww02)

# 3. Apply the patch (path on the host where the bind-mounted
#    nginx.conf lives — typically /home/dblagbro/docker/config/nginx/nginx.conf)
patch -p0 /home/dblagbro/docker/config/nginx/nginx.conf < nginx-firmware-cachecontrol-fix.patch

# 4. CRITICAL: do NOT use Edit tool / sed -i / mv to write the
#    file — those use atomic-replace, which changes the inode and
#    breaks the docker bind mount. Use `patch` (in-place) or a
#    Python `open(..., "w")` rewrite that preserves inode.
#    Verify after:
stat -c %i /home/dblagbro/docker/config/nginx/nginx.conf
sudo docker exec nginx stat -c %i /etc/nginx/nginx.conf
# Both numbers MUST match.

# 5. Validate + reload (no socket close):
sudo docker exec nginx nginx -t
sudo docker exec nginx nginx -s reload

# 6. Verify both endpoints serve the right Cache-Control:
curl -sI https://<host>/rebooter/firmware/stable/latest.bin       | grep -i cache-control
# expect: no-store, no-cache, must-revalidate, max-age=0

curl -sI https://<host>/rebooter/firmware/stable/rebooter-X.Y.Z.bin | grep -i cache-control
# expect: public, max-age=86400, immutable
```

## Status as of 2026-05-10 20:18 UTC

| Host | Cache-Control on latest.bin | Status |
|---|---|---|
| `www.voipguru.org`  → tmrwww01 (this host) | `no-store, no-cache, must-revalidate, max-age=0` | ✓ fixed |
| `www2.voipguru.org` → tmrwww02 (other host) | `public, max-age=300` | ⚠ pending — apply same patch on tmrwww02 |

Once both hosts have the same patch, future divergences are
prevented. Pre-fix divergences had a TTL of 300 s and would
have self-resolved within 5 minutes anyway, but a misbehaving
device that downloaded the stale bytes during that window
would flash an obsolete image.

## Bonus side-effect

Versioned filenames (`rebooter-X.Y.Z.bin`) now get
`Cache-Control: public, max-age=86400, immutable`. That gives
intermediaries explicit permission to cache the bytes for a
day, which:

- Reduces hub bandwidth for 100+ devices fetching the same
  immutable artifact
- Allows browsers to skip revalidation entirely (`immutable`)
- Is safe-by-convention because `rebooter-X.Y.Z.bin` is never
  rewritten in place — operators delete + re-upload under a
  new version number

## Future hardening (not in this fix)

Consider:
- A repo-side script that produces a SHA manifest at upload
  time so devices can verify-by-content
- An nginx `add_header Vary "*";` on `latest.bin` to defeat
  any intermediate cache that ignores `no-store`
- ETags suppressed on `latest.bin` (we currently emit the
  inode-based ETag, which lets `If-None-Match` short-circuit;
  for a moving pointer that's actually a feature, not a bug)
