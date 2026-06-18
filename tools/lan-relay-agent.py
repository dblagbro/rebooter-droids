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

# 0.6.34 / firmware 0.2.23 Phase 3 (#179) — UDP control channel.
# Device-side listener at port 31416 accepts 29-byte HMAC-SHA256-authed
# packets and answers with a 26-byte ACK. Wire latency <10ms on LAN vs
# ~280ms for HTTP. Per-device secrets are loaded from
# ~/.config/lan-relay-agent-udp-secrets.json (operator-populated, since
# the hub doesn't store device tokens in plaintext).
import hashlib  # noqa: E402
import hmac     # noqa: E402
import os.path  # noqa: E402
import secrets as _secrets  # noqa: E402
import socket   # noqa: E402
import struct   # noqa: E402

UDP_PORT = 31416
UDP_TIMEOUT_S = 0.05      # 50ms first-attempt budget
UDP_MAX_RETRIES = 2       # then give up + fall back to HTTP
UDP_CMD_CODES = {"relay_on": 0x01, "relay_off": 0x02, "relay_toggle": 0x03}

_udp_secrets: dict[str, bytes] = {}
_udp_sockets: dict[str, socket.socket] = {}


def _load_udp_secrets() -> None:
    """Populate _udp_secrets from the operator-managed JSON file.

    File format: {"192.168.18.190": "dt_xxxxxxxxxxxxxxxxxxxxxxxx", ...}
    Token strings are the device.deviceToken shown ONCE at enrollment.
    Missing file or unreadable JSON = empty mapping; agent silently
    stays on the HTTP path for every device.
    """
    path = os.path.expanduser("~/.config/lan-relay-agent-udp-secrets.json")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log.warning("UDP secrets file %s unreadable: %s", path, e)
        return
    for ip, secret in (data or {}).items():
        if isinstance(secret, str) and secret:
            _udp_secrets[ip] = secret.encode("utf-8")
    if _udp_secrets:
        log.info("UDP path armed for %d device(s)", len(_udp_secrets))


def _udp_socket_for(ip: str) -> socket.socket:
    sock = _udp_sockets.get(ip)
    if sock is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(UDP_TIMEOUT_S)
        _udp_sockets[ip] = sock
    return sock


def _try_udp(ip: str, ctype: str) -> bool:
    """Send a UDP control packet + await ACK. Returns True on confirmed
    ack=OK from the device. False on any other outcome (no secret, no
    ACK, bad ACK, timeout) — caller falls back to HTTP."""
    secret = _udp_secrets.get(ip)
    if not secret:
        return False
    cmd_code = UDP_CMD_CODES.get(ctype)
    if cmd_code is None:
        return False
    sock = _udp_socket_for(ip)
    for attempt in range(UDP_MAX_RETRIES + 1):
        nonce = _secrets.token_bytes(8)
        ts = int(time.time()).to_bytes(4, "big")
        cmd = bytes([cmd_code])
        payload = nonce + ts + cmd
        tag = hmac.new(secret, payload, hashlib.sha256).digest()[:16]
        pkt = tag + payload
        try:
            sock.sendto(pkt, (ip, UDP_PORT))
            resp, _ = sock.recvfrom(64)
        except (socket.timeout, OSError):
            continue
        if len(resp) != 26:
            continue
        resp_tag, resp_nonce, ack, relay = resp[:16], resp[16:24], resp[24], resp[25]
        if resp_nonce != nonce:
            continue
        # Verify response HMAC over (nonce ‖ ack ‖ relay).
        expected = hmac.new(secret, resp_nonce + bytes([ack, relay]), hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(resp_tag, expected):
            continue
        if ack == 0:
            return True
    return False


# Per-device persistent sessions so the second POST skips TCP handshake.
_device_sessions: dict[str, requests.Session] = {}


def _session_for(ip: str) -> requests.Session:
    s = _device_sessions.get(ip)
    if s is None:
        s = requests.Session()
        _device_sessions[ip] = s
    return s


_EXPECTED_RELAY_STATE = {"relay_on": True, "relay_off": False}


def _report_state_confirmed(device_id: str | None, relay_on: bool, command_id: str | None) -> None:
    """0.6.50 Phase 2.5: tell the hub the relay flipped, BEFORE the next
    device heartbeat lands. Closes the "click feels instant but status
    chip takes ~60s" gap. Fire-and-forget — failure to reach the hub
    does not propagate to the operator, the next heartbeat is still
    the source of truth.

    relay_toggle is intentionally NOT reported because the agent doesn't
    know what state the device WAS in before — only the device itself
    can resolve that, via the next heartbeat. relay_on / relay_off are
    self-describing so a confirmed delivery == confirmed final state.
    """
    if not device_id or not HUB_URL or not API_TOKEN:
        return
    url = urljoin(HUB_URL + "/", f"api/v1/admin/services/devices/{device_id}/state-confirmed")
    try:
        requests.post(
            url,
            json={"relay_on": relay_on, "command_id": command_id},
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            timeout=2.0,
        )
    except requests.RequestException as e:
        log.debug("state-confirmed callback to hub failed: %s", e)


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
    # Try the UDP fast path first; fall back to HTTP if the device has
    # no UDP secret configured for the agent, or the device didn't ACK.
    device_id = event.get("device_id")
    command_id = event.get("command_id") or event.get("id")
    expected = _EXPECTED_RELAY_STATE.get(ctype)
    t0 = time.monotonic()
    if _try_udp(ip, ctype):
        dt_ms = int((time.monotonic() - t0) * 1000)
        log.info("delivered %s → %s via UDP (%s ms)", ctype, ip, dt_ms)
        if expected is not None:
            _report_state_confirmed(device_id, expected, command_id)
        return
    url = f"http://{ip}{path}"
    try:
        r = _session_for(ip).post(url, timeout=DEVICE_TIMEOUT_S)
        dt_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "delivered %s → %s via HTTP (%s ms, http %s)",
            ctype, ip, dt_ms, r.status_code,
        )
        # Only report when the device actually accepted (2xx). A 5xx /
        # timeout means we don't actually know the final state.
        if expected is not None and 200 <= r.status_code < 300:
            _report_state_confirmed(device_id, expected, command_id)
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
    _load_udp_secrets()
    stream_loop()


if __name__ == "__main__":
    main()
