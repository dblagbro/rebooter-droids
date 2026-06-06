#!/usr/bin/env python3
"""LAN relay agent (0.6.21, #178 Phase 1).

Subscribes to the hub's SSE stream at /api/v1/admin/events/commands and,
the moment a `command_queued` event lands for a relay_on / relay_off /
relay_toggle command, POSTs directly to the device's local IP. End-to-end
latency drops from the polling architecture's ~30s median to ~100-300ms.

Run on any host that:
  - Has IP reachability to the device fleet on the LAN (e.g. the operator's
    workstation, or a Raspberry Pi on the device subnet).
  - Can reach the hub URL.
  - Has an `rbt_`-prefixed API token with read scope.

Quick start:
    export REBOOTER_HUB_URL=https://www.voipguru.org/rebooter
    export REBOOTER_API_TOKEN=rbt_xxxxxxxxxxxx
    python3 lan-relay-agent.py

Run as a systemd-user service for unattended operation. The agent
auto-reconnects on stream errors; sustained connection failures back off
exponentially.

Notes:
  - Per-device requests.Session() with HTTP keep-alive — second and
    subsequent POSTs to the same device skip TCP handshake (~50ms saved).
  - Device responses are not awaited beyond a short timeout — the agent's
    job is delivery, not reporting. Audit + result reporting still flow
    through the existing /device/commands → /command-result path.
  - relay_cycle is delivered as a relay_off+wait+relay_on pair; on devices
    that already implement /api/relay/cycle natively we just POST that.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    sys.stderr.write("requests is required: pip install requests\n")
    sys.exit(2)

log = logging.getLogger("lan-relay-agent")

HUB_URL = os.environ.get("REBOOTER_HUB_URL", "").rstrip("/")
API_TOKEN = os.environ.get("REBOOTER_API_TOKEN", "")
DEVICE_TIMEOUT_S = float(os.environ.get("LAN_AGENT_DEVICE_TIMEOUT", "2.0"))
RECONNECT_INITIAL_S = 1.0
RECONNECT_MAX_S = 30.0

RELAY_PATHS = {
    "relay_on": "/api/relay/on",
    "relay_off": "/api/relay/off",
    "relay_toggle": "/api/relay/toggle",
}

# Per-device persistent sessions so the second POST skips TCP handshake.
_device_sessions: dict[str, requests.Session] = {}


def _session_for(ip: str) -> requests.Session:
    s = _device_sessions.get(ip)
    if s is None:
        s = requests.Session()
        _device_sessions[ip] = s
    return s


def deliver(event: dict) -> None:
    ctype = event.get("type")
    ip = event.get("device_local_ip")
    if not ip:
        log.debug("event missing device_local_ip: %s", event)
        return
    path = RELAY_PATHS.get(ctype)
    if path is None:
        # Not a relay command — leave it to the existing hub→device
        # heartbeat-piggyback path. Other command types aren't latency
        # sensitive in the same way.
        return
    url = f"http://{ip}{path}"
    t0 = time.monotonic()
    try:
        r = _session_for(ip).post(url, timeout=DEVICE_TIMEOUT_S)
        dt_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "delivered %s → %s (%s ms, http %s)",
            ctype, ip, dt_ms, r.status_code,
        )
    except requests.RequestException as e:
        dt_ms = int((time.monotonic() - t0) * 1000)
        log.warning(
            "deliver %s → %s failed after %s ms: %s",
            ctype, ip, dt_ms, e,
        )


def stream_loop() -> None:
    if not HUB_URL or not API_TOKEN:
        log.error(
            "REBOOTER_HUB_URL and REBOOTER_API_TOKEN must be set."
        )
        sys.exit(2)
    url = urljoin(HUB_URL + "/", "api/v1/admin/events/commands")
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    backoff = RECONNECT_INITIAL_S
    while True:
        try:
            log.info("connecting to %s", url)
            with requests.get(url, headers=headers, stream=True, timeout=(5, None)) as r:
                if r.status_code != 200:
                    log.error("SSE got HTTP %s; retrying after %.1fs", r.status_code, backoff)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, RECONNECT_MAX_S)
                    continue
                log.info("connected; subscribed to command events")
                backoff = RECONNECT_INITIAL_S
                event_name = "message"
                data_buf: list[str] = []
                for raw in r.iter_lines(decode_unicode=True):
                    if raw is None:
                        continue
                    if raw == "":
                        # end of event
                        if data_buf and event_name == "command_queued":
                            try:
                                payload = json.loads("".join(data_buf))
                                deliver(payload)
                            except Exception:
                                log.exception("failed to deliver event")
                        event_name = "message"
                        data_buf = []
                        continue
                    if raw.startswith(":"):
                        continue  # comment / heartbeat
                    if raw.startswith("event:"):
                        event_name = raw[len("event:"):].strip()
                    elif raw.startswith("data:"):
                        data_buf.append(raw[len("data:"):].strip())
                # Stream ended cleanly; reconnect.
                log.warning("SSE stream closed; reconnecting")
        except requests.RequestException as e:
            log.warning("SSE error: %s; retry in %.1fs", e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_S)
        except KeyboardInterrupt:
            log.info("shutting down")
            return


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    stream_loop()


if __name__ == "__main__":
    main()
