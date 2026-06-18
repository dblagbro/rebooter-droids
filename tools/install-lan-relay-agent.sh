#!/usr/bin/env bash
# Install + start the LAN relay agent as a systemd-user service.
#
# Run on any host that:
#   - has IP reachability to the device fleet on the LAN, AND
#   - can reach the hub URL.
#
# Usage:
#   tools/install-lan-relay-agent.sh                  # interactive — prompts for hub url + token
#   tools/install-lan-relay-agent.sh --hub URL --token TOKEN
#
# After install, verify with:
#   systemctl --user status lan-relay-agent
#   journalctl --user -u lan-relay-agent -n 20
#
# To rotate the token:
#   1. Mint a new rbt_ token (read+write scopes) via /app/tokens or the
#      api_tokens service. Write scope is REQUIRED for the 0.6.50
#      state-confirmed callback that powers the real-time UI flip.
#   2. Edit ~/.config/lan-relay-agent.env
#   3. systemctl --user restart lan-relay-agent
#
# Why this exists: 0.6.50 surfaced that the SSE → UDP push architecture
# was built (#178/#179, v0.6.21+) but nothing started the agent on a
# fresh hub host. Result: click → relay defaulted to the device's ~30s
# poll cycle. This script makes the install one command so the gap
# can't silently reopen on a new deploy.

set -euo pipefail

HUB_URL=""
TOKEN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hub)   HUB_URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Resolve the agent + service file relative to this script. The agent
# can live anywhere on disk; we patch the unit file to point at the
# actual checkout instead of relying on a $HOME-relative default.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_PY="$SCRIPT_DIR/lan-relay-agent.py"
UNIT_SRC="$SCRIPT_DIR/lan-relay-agent.service"
[[ -f "$AGENT_PY" ]]  || { echo "missing $AGENT_PY"  >&2; exit 1; }
[[ -f "$UNIT_SRC" ]]  || { echo "missing $UNIT_SRC"  >&2; exit 1; }

if [[ -z "$HUB_URL" ]]; then
  read -r -p "Hub URL [https://www.voipguru.org/rebooter]: " HUB_URL
  HUB_URL="${HUB_URL:-https://www.voipguru.org/rebooter}"
fi
if [[ -z "$TOKEN" ]]; then
  echo "Mint a token at $HUB_URL/app/tokens (read + write scopes required)."
  read -r -s -p "Bearer token (rbt_…): " TOKEN
  echo
fi
[[ "$TOKEN" =~ ^rbt_ ]] || { echo "Token must start with rbt_" >&2; exit 1; }

# Sanity-check the hub is reachable before we wire anything up. A 401
# response would be the operator passing a wrong token — caught here
# instead of in journalctl 5s later.
echo "→ probing $HUB_URL/api/v1/version"
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HUB_URL/api/v1/version")
[[ "$HTTP_CODE" =~ ^2 ]] || { echo "hub not reachable ($HTTP_CODE)" >&2; exit 1; }

# Verify the token actually authenticates. The SSE endpoint requires a
# resolvable principal; if it returns 401 the agent will loop forever.
echo "→ verifying token via SSE endpoint"
SSE_PROBE_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: text/event-stream" \
  "$HUB_URL/api/v1/admin/events/commands" || true)
# 200 = connected (the stream is held open by curl, --max-time triggers
# a clean exit; curl returns the response code from the headers).
if ! [[ "$SSE_PROBE_CODE" =~ ^2 ]]; then
  echo "token does not authenticate against $HUB_URL ($SSE_PROBE_CODE)" >&2
  exit 1
fi

# Probe the state-confirmed endpoint for the write-scope half. The
# probe POST sends an obviously-invalid device_id; 404 is the success
# signal (we reached the handler with write scope; the row lookup
# failed). 403 = write scope missing; 401 = token wrong. Any 5xx
# means the hub is misbehaving and we abort.
echo "→ verifying write scope via state-confirmed endpoint"
PROBE_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"relay_on":false}' \
  "$HUB_URL/api/v1/admin/services/devices/dev_INSTALLER_PROBE/state-confirmed" || true)
case "$PROBE_CODE" in
  404)  ;;  # hit the handler, row missing — exactly what we expect
  403)  echo "token missing write scope (got 403). Re-mint with read+write." >&2; exit 1 ;;
  401)  echo "token rejected (401). Check it was minted on this hub." >&2; exit 1 ;;
  5??)  echo "hub returned $PROBE_CODE — hub-side problem, aborting" >&2; exit 1 ;;
  *)    echo "unexpected probe response $PROBE_CODE — aborting" >&2; exit 1 ;;
esac

# Write the env file with 0600 perms before anything reads it. systemd
# refuses to start a unit whose EnvironmentFile is world-readable in
# some hardened distros; not the case here but cheap to be careful.
ENV_FILE="$HOME/.config/lan-relay-agent.env"
mkdir -p "$(dirname "$ENV_FILE")"
umask 077
cat > "$ENV_FILE" <<EOF
REBOOTER_HUB_URL=$HUB_URL
REBOOTER_API_TOKEN=$TOKEN
EOF
umask 022

# Install the systemd-user unit with the ExecStart path rewritten to
# point at the actual checkout. The upstream template assumes
# %h/rebooter-droids/tools/ which only matches the original install
# layout; this script supports any checkout location.
UNIT_DST="$HOME/.config/systemd/user/lan-relay-agent.service"
mkdir -p "$(dirname "$UNIT_DST")"
sed "s|%h/rebooter-droids/tools/lan-relay-agent.py|$AGENT_PY|" \
  "$UNIT_SRC" > "$UNIT_DST"

# Survive logout if we're running on a long-lived host. lingering is
# what lets systemd-user services keep running when the operator's
# interactive shell exits.
if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
  if command -v sudo >/dev/null; then
    echo "→ enabling user-lingering (so the agent survives logout)"
    sudo loginctl enable-linger "$USER" || echo "  (lingering enable failed; agent will stop when you log out)"
  fi
fi

systemctl --user daemon-reload
systemctl --user enable --now lan-relay-agent.service
sleep 1

# Final confirm — the agent should log "connected; subscribed" on
# successful SSE connect.
if journalctl --user-unit lan-relay-agent --since "10 seconds ago" --no-pager 2>&1 | grep -q "connected; subscribed"; then
  echo "✓ lan-relay-agent installed + connected"
else
  echo "✗ lan-relay-agent installed but did NOT connect — check"
  echo "  journalctl --user -u lan-relay-agent -n 30"
  exit 1
fi
