# Diagnostic syslog — operator runbook (#206)

When you have physical/office access and want to recover the fleet
while CAPTURING what caused the failure, follow this.

## Pre-recovery checklist (already in place)

- Hub 0.6.37 deployed with UDP collector listening on port 51514 ✓
- Firmware 0.2.28 sitting at `data/firmware/dev/rebooter-0.2.28-dev-central-safe.bin` ✓
- `/data/diag/` volume mounted, writable, verified with loopback packet ✓
- Docker NAT rule maps host UDP 51514 → container ✓
- Test packet from inside the container persisted as `testmac.jsonl` ✓

## What the firmware sends

Once a device is on the LAN + running 0.2.28+, it sends one UDP packet
per:
- `event_log.add()` — every event-log entry (heartbeat failures, WiFi
  transitions, factory-resets, the works)
- WiFi state changes
- Every 15s — heap snapshot (`free`, `mfb`, `frag`, `up`)
- Breadcrumb writes around the 5 HTTPS public methods
- Boot reset reason — sent IMMEDIATELY at boot so it's captured even
  if the device dies before any other packet fires

Packet format (one JSON object per UDP packet, ≤512B):
```json
{"dev":"0cf74babcdef","fw":"0.2.28","ms":12345,"k":"event","type":"central","msg":"Heartbeat transport failed; backing off"}
```

## Recovery procedure when you have access

For each unreachable device (`.185` `.188` `.190` currently):

1. **Open the tail in one terminal:**
   ```bash
   ssh tmrwww01
   sudo docker exec rebooter-droids ls /data/diag/   # see which MACs already wrote files
   sudo docker exec rebooter-droids tail -F /data/diag/*.jsonl
   ```
   The wildcard tail will pick up new files as devices come online.

2. **Connect to the device's setup-AP** (e.g. `Rebooter-Setup-0CF74B`)
   and enter WiFi credentials.

3. **Watch the tail** as the device joins the LAN:
   - First packet should be a `k:reset` with the reset reason
   - Then `k:wifi` events as it associates
   - Then `k:event` for every event_log line
   - Then heap snapshots every 15s
   - Then `k:breadcrumb` writes around heartbeat / commands

4. **If the device fails AGAIN** before you finish recovery (the
   cascade reasserts itself), the JSONL file captures everything up
   to the moment it stopped sending. That's the data we need.

5. **Match the MAC to an IP:**
   - The MAC is in the JSONL filename
   - The first `k:wifi` packet includes `ssid` + `ip`
   - The hub `/api/v1/admin/devices` lists each device's MAC

## Failure-mode interpretation

| Symptom in JSONL | Likely root cause |
|---|---|
| Heap snapshots showing `mfb` falling steadily from ~18K → ~10K | Heap fragmentation leak |
| Heap snapshots show `mfb` healthy but ends abruptly | Hardware fault / power loss |
| `k:event type:central msg:"Heartbeat transport failed; backing off"` repeating without `k:reset` | HTTPS path failing — BearSSL alloc failure |
| `k:event type:wifi msg:"...disconnect..."` then no further packets | WiFi association lost — physical/RF |
| Multiple `k:reset` per minute with different reasons | Crash loop |
| Last packet is `k:breadcrumb op:1` (heartbeat) then nothing | Crashed inside `sendHeartbeat()` HTTPS call |

## Cleanup after a session

```bash
sudo docker exec rebooter-droids ls -la /data/diag/      # what we have
sudo docker exec rebooter-droids du -h /data/diag/       # disk usage
# Rotation cap is 5 MB per file → .jsonl.1 archive
# Manual archive once root cause is identified:
sudo docker exec rebooter-droids bash -c 'mv /data/diag/<mac>.jsonl /data/diag/_archive_<date>_<mac>.jsonl'
```

## If the collector isn't catching anything

```bash
# Confirm the collector thread is alive
sudo docker logs rebooter-droids 2>&1 | grep diag-syslog
# Should show:
#   diag-syslog collector thread started
#   diag-syslog collector listening on UDP 51514, writing to /data/diag

# Confirm the port mapping
sudo docker inspect rebooter-droids --format '{{json .NetworkSettings.Ports}}'
# Should show "51514/udp": [{"HostIp":"0.0.0.0","HostPort":"51514"}]

# Send a test packet from the host LAN
python3 -c "
import socket, json
s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(json.dumps({'dev':'manualtest','fw':'0','ms':0,'k':'event','type':'test','msg':'hello'}).encode(),
         ('192.168.18.11', 51514))
print('sent')
"
sudo docker exec rebooter-droids cat /data/diag/manualtest.jsonl  # should exist now
```

## Things you can't do without this

- Discover *why* `.185` lost WiFi credentials (#197)
- Discover *why* `.188` keeps power-cycling beyond the "shared circuit" hypothesis (#202)
- Confirm the proactive-restart fix (#172/0.2.26) is working in real-world heap pressure
- Verify the 0.2.25 atomic-write fixes are actually catching the windows we suspected

The harness was built for `.185` but stays useful for the whole fleet.
