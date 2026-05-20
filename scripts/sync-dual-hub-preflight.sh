#!/usr/bin/env bash
#
# sync-dual-hub-preflight.sh — operator preflight for the B11 sync flip.
#
# Validates that two rebooter-droids hubs are configured + healthy
# enough to flip `sync.enabled=true` and start converging in both
# directions. Walks the operator through:
#   1. preconditions — peer config, HMAC key, sync.enabled state, cursors
#   2. convergence test — create a marker Site on hub A, poll hub B for
#      it; then delete on A, poll B for the tombstone.
#
# Usage:
#   ./scripts/sync-dual-hub-preflight.sh \
#       --hub-a https://www.voipguru.org/rebooter \
#       --token-a "$REBOOTER_ADMIN_TOKEN_A" \
#       --hub-b https://www2.example.com/rebooter \
#       --token-b "$REBOOTER_ADMIN_TOKEN_B" \
#       [ --read-only ]
#
# --read-only skips the create/delete convergence dance and only reports
# precondition state. Safe to run at any time (no writes). The default
# mode performs writes only if sync.enabled is already true on BOTH
# hubs; otherwise it falls back to read-only with a warning.
#
# Exit codes:
#   0 — preflight passed (or read-only completed cleanly)
#   1 — invalid arguments
#   2 — a precondition check failed
#   3 — convergence did not happen within the timeout
#
# The script never flips sync.enabled itself — that is an explicit
# operator decision in the runbook (docs/runbooks/sync-enable.md).

set -euo pipefail

HUB_A=""
HUB_B=""
TOKEN_A=""
TOKEN_B=""
READ_ONLY=0
TIMEOUT_SECONDS=60
MARKER="qa-b11-preflight-$(date -u +%Y%m%dT%H%M%SZ)-$$"

usage() {
    sed -n '/^# Usage/,/^# Exit/p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

# ── arg parsing ────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --hub-a) HUB_A="$2"; shift 2;;
        --token-a) TOKEN_A="$2"; shift 2;;
        --hub-b) HUB_B="$2"; shift 2;;
        --token-b) TOKEN_B="$2"; shift 2;;
        --read-only) READ_ONLY=1; shift;;
        --timeout) TIMEOUT_SECONDS="$2"; shift 2;;
        -h|--help) usage;;
        *) echo "unknown arg: $1" >&2; usage;;
    esac
done

[[ -n "$HUB_A" && -n "$HUB_B" && -n "$TOKEN_A" && -n "$TOKEN_B" ]] || usage

command -v jq >/dev/null 2>&1 || {
    echo "ERROR: jq is required (apt install jq)" >&2; exit 1;
}

# ── helpers ────────────────────────────────────────────────────────
say()  { printf "\n\033[1m== %s ==\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m⚠\033[0m %s\n" "$*"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$*"; }

# curl_a / curl_b: GET the path on the named hub with admin Bearer auth.
# (Pre-fix the helpers passed both "$@" *and* "$HUB_X$1" → curl saw two
# URLs and the response was concatenated, which broke jq downstream.)
curl_a() { curl -sf -H "Authorization: Bearer $TOKEN_A" "$HUB_A$1"; }
curl_b() { curl -sf -H "Authorization: Bearer $TOKEN_B" "$HUB_B$1"; }

# A more careful curl that prints both URL + status on failure.
fetch() {
    local label="$1" url="$2" token="$3"
    local body status
    body=$(curl -sS -H "Authorization: Bearer $token" -w "\n%{http_code}" "$url") || {
        bad "$label: curl failed against $url"; return 1;
    }
    status="${body##*$'\n'}"
    body="${body%$'\n'*}"
    if [[ "$status" != "200" ]]; then
        bad "$label: $url returned HTTP $status"; printf "    body: %s\n" "$body"
        return 1
    fi
    printf "%s" "$body"
}

# ── 1. version + reachability ──────────────────────────────────────
say "1. Reachability + version"
VER_A=$(fetch "hub-A version" "$HUB_A/api/v1/version" "$TOKEN_A" | jq -r .data.version)
VER_B=$(fetch "hub-B version" "$HUB_B/api/v1/version" "$TOKEN_B" | jq -r .data.version)
ok  "hub A: $HUB_A  → v$VER_A"
ok  "hub B: $HUB_B  → v$VER_B"
[[ "$VER_A" == "$VER_B" ]] || warn "version skew — applier compat may differ"

# ── 2. sync.* runtime settings on both hubs ────────────────────────
say "2. Sync settings on both hubs"
# v0.5.102 admin-Bearer-auth wrapper around the wire /api/v1/sync/status —
# returns enabled / hub_id / hmac_key_set / peer_hubs / outbox / cursors
# under {ok, data}. The wire endpoint requires HMAC peer auth and can't
# be queried with an admin token; this one can.
STATUS_A=$(fetch "hub-A sync status" "$HUB_A/api/v1/admin/sync/status" "$TOKEN_A") || exit 2
STATUS_B=$(fetch "hub-B sync status" "$HUB_B/api/v1/admin/sync/status" "$TOKEN_B") || exit 2

EN_A=$(jq -r '.data.enabled // false' <<<"$STATUS_A")
EN_B=$(jq -r '.data.enabled // false' <<<"$STATUS_B")
HUB_ID_A=$(jq -r '.data.hub_id // "?"' <<<"$STATUS_A")
HUB_ID_B=$(jq -r '.data.hub_id // "?"' <<<"$STATUS_B")
echo "  hub A: hub_id=$HUB_ID_A  sync.enabled=$EN_A"
echo "  hub B: hub_id=$HUB_ID_B  sync.enabled=$EN_B"

[[ "$HUB_ID_A" != "$HUB_ID_B" ]] && ok "hub_ids differ ($HUB_ID_A vs $HUB_ID_B)" \
                                 || { bad "hub_ids match — must differ for cursor uniqueness"; exit 2; }

PEERS_A_LEN=$(jq -r '(.data.peer_hubs // []) | length' <<<"$STATUS_A")
PEERS_B_LEN=$(jq -r '(.data.peer_hubs // []) | length' <<<"$STATUS_B")
[[ "$PEERS_A_LEN" -gt 0 ]] && ok "hub A has $PEERS_A_LEN peer(s) configured" \
                          || { bad "hub A has 0 peers"; exit 2; }
[[ "$PEERS_B_LEN" -gt 0 ]] && ok "hub B has $PEERS_B_LEN peer(s) configured" \
                          || { bad "hub B has 0 peers"; exit 2; }

HMAC_A=$(jq -r '.data.hmac_key_set // false' <<<"$STATUS_A")
HMAC_B=$(jq -r '.data.hmac_key_set // false' <<<"$STATUS_B")
[[ "$HMAC_A" == "true" ]] && ok "hub A HMAC key set" \
                        || { bad "hub A HMAC key NOT set"; exit 2; }
[[ "$HMAC_B" == "true" ]] && ok "hub B HMAC key set" \
                        || { bad "hub B HMAC key NOT set"; exit 2; }

# Outbox + cursor snapshot — how far each hub has applied from each peer.
MAX_A=$(jq -r '.data.outbox.max_seq // 0' <<<"$STATUS_A")
TOT_A=$(jq -r '.data.outbox.total_events // 0' <<<"$STATUS_A")
MAX_B=$(jq -r '.data.outbox.max_seq // 0' <<<"$STATUS_B")
TOT_B=$(jq -r '.data.outbox.total_events // 0' <<<"$STATUS_B")
echo "  hub A outbox: max_seq=$MAX_A total_events=$TOT_A"
echo "  hub B outbox: max_seq=$MAX_B total_events=$TOT_B"
echo "  hub A cursors:"
jq -r '.data.cursors[] | "    \(.peer_hub_id) → seq=\(.last_seq)\(if .last_error then " ERR: " + .last_error else "" end)"' <<<"$STATUS_A" 2>/dev/null || echo "    (none yet)"
echo "  hub B cursors:"
jq -r '.data.cursors[] | "    \(.peer_hub_id) → seq=\(.last_seq)\(if .last_error then " ERR: " + .last_error else "" end)"' <<<"$STATUS_B" 2>/dev/null || echo "    (none yet)"

# ── 3. read-only short-circuit ─────────────────────────────────────
if [[ "$READ_ONLY" -eq 1 ]]; then
    say "3. Read-only mode — skipping convergence test"
    ok "preflight checks complete (no writes performed)"
    exit 0
fi

if [[ "$EN_A" != "true" || "$EN_B" != "true" ]]; then
    warn "sync.enabled is NOT true on both hubs — falling back to read-only"
    warn "to run the convergence test, flip sync.enabled=true on both hubs first"
    exit 0
fi

# ── 4. convergence test — create a marker Site on hub-A ────────────
say "3. Convergence test: create a marker Site on hub A"
echo "  marker name: $MARKER"
CREATE_RESP=$(curl -sS -X POST \
    -H "Authorization: Bearer $TOKEN_A" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$MARKER\", \"description\": \"sync preflight\"}" \
    "$HUB_A/api/v1/admin/sites")
CREATE_ID=$(jq -r '.data.id // ""' <<<"$CREATE_RESP")
if [[ -z "$CREATE_ID" ]]; then
    bad "hub-A create-site returned no id: $CREATE_RESP"
    exit 2
fi
ok "created Site $CREATE_ID on hub A"

# Cleanup on any exit from here on.
cleanup() {
    set +e
    curl -sS -X DELETE -H "Authorization: Bearer $TOKEN_A" \
        "$HUB_A/api/v1/admin/sites/$CREATE_ID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── 5. poll hub-B for the create ───────────────────────────────────
say "4. Polling hub B for the marker (timeout ${TIMEOUT_SECONDS}s)"
T0=$(date +%s)
CONVERGED_CREATE=0
# The admin sites list returns `{ok, data: {sites: [...]}}`. jq's `?`
# operator suppresses errors when the path is missing so polling-before-
# converge doesn't crash; we just keep iterating until the marker appears.
while (( $(date +%s) - T0 < TIMEOUT_SECONDS )); do
    HIT=$(curl_b "/api/v1/admin/sites" \
        | jq -r --arg n "$MARKER" '.data.sites[]? | select(.name == $n) | .id' \
        | head -1)
    if [[ -n "$HIT" ]]; then
        ELAPSED=$(( $(date +%s) - T0 ))
        ok "hub B has the marker after ${ELAPSED}s (id=$HIT)"
        CONVERGED_CREATE=1
        break
    fi
    sleep 2
done
if [[ "$CONVERGED_CREATE" -ne 1 ]]; then
    bad "hub B did NOT converge within ${TIMEOUT_SECONDS}s"
    exit 3
fi

# ── 6. delete on A, poll B for the tombstone ───────────────────────
say "5. Deleting marker on hub A, polling B for the tombstone"
curl -sS -X DELETE -H "Authorization: Bearer $TOKEN_A" \
    "$HUB_A/api/v1/admin/sites/$CREATE_ID" >/dev/null
ok "deleted Site $CREATE_ID on hub A"
trap - EXIT  # marker is intentionally gone now

T1=$(date +%s)
CONVERGED_DELETE=0
while (( $(date +%s) - T1 < TIMEOUT_SECONDS )); do
    HIT=$(curl_b "/api/v1/admin/sites" \
        | jq -r --arg n "$MARKER" '.data.sites[]? | select(.name == $n) | .id' \
        | head -1)
    if [[ -z "$HIT" ]]; then
        ELAPSED=$(( $(date +%s) - T1 ))
        ok "hub B reflects the deletion after ${ELAPSED}s"
        CONVERGED_DELETE=1
        break
    fi
    sleep 2
done
if [[ "$CONVERGED_DELETE" -ne 1 ]]; then
    bad "hub B did NOT reflect the delete within ${TIMEOUT_SECONDS}s"
    exit 3
fi

say "PREFLIGHT PASSED"
ok "create + delete converged both ways"
ok "safe to enter the soak window per docs/runbooks/sync-enable.md"
