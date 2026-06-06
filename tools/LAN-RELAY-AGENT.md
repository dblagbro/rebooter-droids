# LAN Relay Agent — Setup

Sub-second relay control by bypassing the polling architecture.
Subscribes to the hub's SSE stream and POSTs directly to device LAN IPs
the moment a relay command is queued in the UI.

**Latency:** ~400ms steady-state on the LAN (vs prior ~30s polling).

## What you need

- A host on the same LAN as the rebooter devices (operator workstation,
  Raspberry Pi, NAS, etc.). It needs IP reachability to each device's
  local IP (192.168.18.x or wherever).
- Python 3.9+ with the `requests` library: `pip install requests`.
- An hub admin account to mint the agent's bearer token.

## One-time setup

### 1. Mint the agent's API token

Open the hub UI → /app/tokens → "New token":
- Name: `lan-relay-agent` (or hostname)
- Scopes: `read` (the agent only listens to SSE)
- Save the `rbt_…` plaintext somewhere safe — it is shown ONCE.

If you don't have a tokens UI yet, mint via shell on the hub host:
```bash
sudo docker exec rebooter-droids python3 -c "
from app import create_app
from app.services.api_tokens import mint
app = create_app()
with app.app_context():
  row, plaintext = mint(name='lan-relay-agent', scopes=['read'])
  print(plaintext)
"
```

### 2. Drop the env file

```bash
mkdir -p ~/.config
cat > ~/.config/lan-relay-agent.env <<EOF
REBOOTER_HUB_URL=https://www.voipguru.org/rebooter
REBOOTER_API_TOKEN=rbt_<paste-here>
EOF
chmod 600 ~/.config/lan-relay-agent.env
```

### 3. Clone or pull the repo somewhere

```bash
git clone git@github.com:dblagbro/rebooter-droids.git ~/rebooter-droids
# OR if you already have it, just make sure tools/lan-relay-agent.py is there.
```

### 4. Install the systemd-user unit

```bash
mkdir -p ~/.config/systemd/user
cp ~/rebooter-droids/tools/lan-relay-agent.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lan-relay-agent.service
```

### 5. Make it survive logout (one-time)

```bash
sudo loginctl enable-linger $USER
```

## Verify

```bash
systemctl --user status lan-relay-agent.service
# Should be "active (running)"

journalctl --user -u lan-relay-agent -f
# Tail logs. You'll see:
#   connecting to https://www.voipguru.org/rebooter/api/v1/admin/events/commands
#   connected; subscribed to command events
# Now click a relay button in the hub UI. Within ~500ms you should see:
#   delivered relay_off → 192.168.18.190 (412 ms, http 200)
```

## Token rotation

```bash
# Mint a new token (see step 1)
# Edit ~/.config/lan-relay-agent.env with the new token
systemctl --user restart lan-relay-agent.service
```

## Troubleshooting

**SSE connects but no events arrive when you click:**
- Confirm the hub release is ≥ 0.6.21 (the SSE bus + publish hook).
- Confirm the command IS being queued — check `/app/devices/<id>` page
  shows the new command in the recent-commands section.

**Agent reports HTTP failures to the device:**
- Confirm reachability: `curl -X POST http://<device-ip>/api/relay/off`
- Devices auto-recover on reboot but a stuck device may need a manual
  power-cycle.

**400-700ms per delivery is the ESP8266's HTTP processing floor.**
Phase 3 (#179 — persistent TCP / UDP control channel) targets <50ms but
needs a firmware change.

## Architecture

```
            ┌────────────────┐                ┌────────────────────┐
operator ─→ │  hub UI POST   │ ──────┐        │   /api/v1/admin/   │
            │ /devices/<id>/ │       │SSE     │   events/commands  │ ◄── (agent subscribes)
            │   commands     │       └──────► │      stream        │
            └────────────────┘                └────────────────────┘
                    │                                  │
                    │ DB write +                       │ event_bus.publish()
                    │ event_bus.publish()              │
                    └──────────────────────────────────┘
                                  │
                                  ▼
                            ┌──────────────┐
                            │ LAN agent on │
                            │ same network │
                            │  as devices  │
                            └──────┬───────┘
                                   │
                                   │ HTTP POST http://<device-ip>/api/relay/<on|off>
                                   │ (keep-alive Session per device)
                                   ▼
                            ┌──────────────┐
                            │ rebooter     │
                            │  device      │
                            └──────────────┘
```
