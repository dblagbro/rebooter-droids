"""UDP syslog collector for firmware diagnostic packets (#206, 0.6.37).

Listens on UDP port 51514, parses incoming JSONL packets from the
firmware diag_syslog module, and appends each one to a per-device
JSONL file at /data/diag/<mac>.jsonl. Designed to capture data RIGHT
UP to the moment a device drops off the LAN — the firmware sends one
UDP packet per event/wifi-state-change/heap-snapshot/breadcrumb, and
UDP doesn't depend on the BearSSL/HTTPS path that's the primary
suspect for the .185 silent-failure cascade.

Operator runtime use:
    sudo docker exec rebooter-droids tail -F /data/diag/<mac>.jsonl
    sudo docker exec rebooter-droids ls -la /data/diag/

File-per-device + JSONL means a missing packet (UDP is lossy) shows
up as a gap in `ms` field timestamps but doesn't corrupt anything
upstream. Rotation cap = 5 MB per file; older content shifted to
<mac>.jsonl.1 on cross.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import socket
import threading
import time

log = logging.getLogger(__name__)

UDP_PORT = 51514
DIAG_ROOT = pathlib.Path("/data/diag")
MAX_FILE_BYTES = 5 * 1024 * 1024  # rotate at 5 MB
MAX_PACKET_BYTES = 1500  # one Ethernet frame; we never send larger

# Module-level singleton so re-imports don't double-bind.
_started = False
_started_lock = threading.Lock()


def _safe_mac(s: str) -> str:
    """Coerce any operator-side path-traversal attempt into a flat token.
    Firmware sends e.g. '0C:F7:4B:AB:CD:EF' or '0cf74babcdef'; either
    is fine, but a `dev` field of '../../etc/passwd' would otherwise
    let an attacker pick our write target."""
    out = []
    for ch in s.lower():
        if ch.isalnum() or ch in ":-":
            out.append(ch)
    return ("".join(out) or "unknown").replace(":", "")


def _write_packet(parsed: dict, peer_ip: str) -> None:
    dev = _safe_mac(str(parsed.get("dev") or peer_ip or "unknown"))
    target = DIAG_ROOT / f"{dev}.jsonl"
    parsed["_recv_ts"] = time.time()  # server-side wallclock for skew analysis
    parsed["_peer_ip"] = peer_ip
    line = json.dumps(parsed, separators=(",", ":")) + "\n"
    # Best-effort rotate before write.
    try:
        if target.exists() and target.stat().st_size >= MAX_FILE_BYTES:
            rotated = target.with_suffix(".jsonl.1")
            if rotated.exists():
                rotated.unlink()
            target.rename(rotated)
    except OSError as e:
        log.warning("diag-syslog rotate failed for %s: %s", target, e)
    try:
        with open(target, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        log.warning("diag-syslog write failed for %s: %s", target, e)


def _listener_loop() -> None:
    DIAG_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", UDP_PORT))
    except OSError as e:
        log.error("diag-syslog UDP bind failed on port %d: %s — collector disabled", UDP_PORT, e)
        return
    log.info("diag-syslog collector listening on UDP %d, writing to %s", UDP_PORT, DIAG_ROOT)
    while True:
        try:
            data, addr = sock.recvfrom(MAX_PACKET_BYTES)
        except OSError as e:
            log.warning("diag-syslog recv error: %s", e)
            time.sleep(1)
            continue
        try:
            parsed = json.loads(data.decode("utf-8", errors="replace"))
            if not isinstance(parsed, dict):
                continue
        except (ValueError, UnicodeDecodeError):
            # Drop malformed packet — a probe scanner, not the firmware.
            continue
        _write_packet(parsed, addr[0])


def start_collector() -> None:
    """Idempotent. Spawn the daemon listener thread once per process."""
    global _started
    with _started_lock:
        if _started:
            return
        _started = True
        t = threading.Thread(target=_listener_loop, name="diag-syslog-collector", daemon=True)
        t.start()
        log.info("diag-syslog collector thread started")
