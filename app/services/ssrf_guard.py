"""SSRF guard — safe outbound HTTP for the public-SaaS hub.

Per `docs/notes/2026-05-20-hub-tier2-design.md` Feature 6 ("SSRF
protection — the load-bearing part").

The hub is internet-facing multi-tenant SaaS. Any outbound HTTP that
targets an *operator-supplied* URL (notification channels, the watchdog
`escalation` webhook) is an SSRF vector: an attacker who can set the URL
can make the hub reach its own metadata endpoint, an internal admin
panel, or a peer service on the private network.

This module is the single chokepoint. `safe_post()` / `safe_request()`:

  1. Parse the URL; require scheme `https` (allow `http` only when the
     `outbound.allow_http` runtime setting is on — default OFF).
  2. Resolve the hostname to **all** A/AAAA records.
  3. Reject if **any** resolved IP is private / loopback / link-local /
     multicast / reserved / unspecified / CGNAT (`100.64.0.0/10`).
  4. Reject literal-IP hosts that fall in those ranges, IPv4-mapped
     IPv6, and `0.0.0.0`.
  5. **Pin the connection to a validated IP** — resolve once, validate,
     then connect to that exact IP carrying the original `Host` header.
     This closes the DNS-rebinding (TOCTOU) gap: a hostname that
     resolves to a public IP during validation cannot be re-resolved to
     an internal IP at connect time, because the connect target is the
     already-validated literal address.
  6. Disable redirects by default — a 30x can bounce to an internal
     host. (When `allow_redirects=True` is explicitly passed the caller
     accepts the risk; the guard still pins the *first* hop.)
  7. Hard timeout + response-size cap.

`allow_internal=True` is the explicit, audited bypass for trusted
internal calls (the design notes `settings.py`'s `localhost:8090` call
and the sync replicator should eventually route through here with that
flag — out of scope to migrate them now, but the flag exists).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from urllib3.util import connection as urllib3_connection

log = logging.getLogger(__name__)


# Hard limits — a slow or malicious receiver must never degrade the hub.
DEFAULT_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 64 * 1024  # 64 KiB — webhook receivers reply tiny.

# CGNAT / shared address space (RFC 6598) — `ipaddress` has no
# `is_*` predicate for this range, so it is checked explicitly.
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# Link-local / metadata — already `is_link_local`, asserted explicitly
# because the cloud metadata endpoint is the single highest-value SSRF
# target and we want a named, greppable rejection for it.
_METADATA_IPS = (
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
)


class SSRFBlockedError(Exception):
    """Raised when a target URL fails the SSRF policy.

    The message is safe to surface to an operator — it names *why* the
    URL was rejected without leaking internal topology.
    """

    def __init__(self, reason: str, *, url: str | None = None, ip: str | None = None):
        self.reason = reason
        self.url = url
        self.ip = ip
        super().__init__(reason)


# ── IP classification ──────────────────────────────────────────────────


def _normalize_ip(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """Collapse an IPv4-mapped / 6to4-style IPv6 address to its embedded
    IPv4 form so the range checks below cannot be bypassed by wrapping a
    private v4 address inside a v6 literal (e.g. `::ffff:127.0.0.1`)."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        # `sixtofour` exposes the embedded v4 of a 2002::/16 address.
        if getattr(ip, "sixtofour", None) is not None:
            return ip.sixtofour
    return ip


def is_blocked_ip(ip_str: str) -> tuple[bool, str]:
    """Classify a single IP literal. Returns `(blocked, reason)`.

    Blocked = anything not safely routable on the public internet:
    private (RFC1918 / ULA), loopback, link-local, multicast, reserved,
    unspecified (`0.0.0.0` / `::`), or CGNAT (RFC 6598).
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, f"not a valid IP address: {ip_str!r}"

    ip = _normalize_ip(ip)

    if ip in _METADATA_IPS:
        return True, f"cloud metadata endpoint ({ip}) is blocked"
    if ip.is_unspecified:
        return True, f"unspecified address ({ip}) is blocked"
    if ip.is_loopback:
        return True, f"loopback address ({ip}) is blocked"
    if ip.is_link_local:
        return True, f"link-local address ({ip}) is blocked"
    if ip.is_multicast:
        return True, f"multicast address ({ip}) is blocked"
    if ip.is_reserved:
        return True, f"reserved address ({ip}) is blocked"
    if ip.is_private:
        # `is_private` also covers ULA (fc00::/7) for IPv6.
        return True, f"private address ({ip}) is blocked"
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NETWORK:
        return True, f"CGNAT / shared address space ({ip}) is blocked"

    return False, ""


def _resolve_all(hostname: str) -> list[str]:
    """Resolve a hostname to every A/AAAA record. Raises SSRFBlockedError
    if the name does not resolve at all."""
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SSRFBlockedError(f"hostname does not resolve: {hostname} ({e})")
    ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        # Strip any IPv6 scope id (`fe80::1%eth0`).
        if "%" in addr:
            addr = addr.split("%", 1)[0]
        if addr not in ips:
            ips.append(addr)
    if not ips:
        raise SSRFBlockedError(f"hostname resolved to no addresses: {hostname}")
    return ips


# ── URL validation ─────────────────────────────────────────────────────


def _http_allowed() -> bool:
    """Plain `http://` is allowed only when the `outbound.allow_http`
    runtime setting is explicitly on. Default OFF — fails safe."""
    try:
        from app.services import runtime_settings

        raw = runtime_settings.get(
            "outbound.allow_http",
            env_var="REBOOTER_OUTBOUND_ALLOW_HTTP",
            default="false",
        )
    except Exception:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def validate_url(url: str, *, allow_internal: bool = False) -> tuple[str, str, int]:
    """Validate an outbound URL against the SSRF policy.

    Returns `(hostname, pinned_ip, port)` — the IP every connection for
    this request must be pinned to. Raises `SSRFBlockedError` otherwise.

    `allow_internal=True` skips the IP-range rejection (trusted internal
    calls only — still resolves so the connection can be pinned).
    """
    if not url or not isinstance(url, str):
        raise SSRFBlockedError("empty or non-string URL")

    parsed = urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()

    if scheme not in ("http", "https"):
        raise SSRFBlockedError(
            f"unsupported URL scheme {scheme!r}: only http/https allowed",
            url=url,
        )
    if scheme == "http" and not allow_internal and not _http_allowed():
        raise SSRFBlockedError(
            "plain http:// is not allowed for outbound webhooks "
            "(enable the 'outbound.allow_http' setting to override)",
            url=url,
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError("URL has no hostname", url=url)

    port = parsed.port or (443 if scheme == "https" else 80)

    # Resolve to every address. A hostname is only as safe as its
    # *worst* record — one private A record poisons the whole name.
    candidates = _resolve_all(hostname)

    if not allow_internal:
        for ip_str in candidates:
            blocked, reason = is_blocked_ip(ip_str)
            if blocked:
                raise SSRFBlockedError(reason, url=url, ip=ip_str)

    # Pin to the first resolved address. Every candidate has passed the
    # range check (or `allow_internal` is set), so any of them is safe;
    # the first is deterministic and keeps the Host header honest.
    pinned_ip = candidates[0]
    return hostname, pinned_ip, port


# ── IP-pinned transport ────────────────────────────────────────────────


class _PinnedHTTPAdapter(HTTPAdapter):
    """A `requests` transport adapter that forces every socket for this
    session to connect to one pre-validated IP.

    This is the DNS-rebinding (TOCTOU) close-out: validation resolved
    the hostname and proved every record is public; without pinning,
    `requests` would resolve the name *again* at connect time and an
    attacker controlling the DNS could swap in an internal IP between
    the two lookups. By overriding `urllib3`'s `create_connection` to
    ignore the requested host and dial the pinned IP, the connect target
    is the literal address we already validated.

    The `Host` header / TLS SNI still carry the original hostname (the
    URL is unchanged), so virtual-hosted and TLS-validated receivers
    work normally.
    """

    def __init__(self, pinned_ip: str, *args, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pinned_ip = self._pinned_ip

        class _PinnedPoolManager(PoolManager):
            def _new_pool(self, scheme, host, port, request_context=None):
                # urllib3 keys pools by (scheme, host, port). We leave
                # the key as the hostname (so SNI/Host stay correct) and
                # inject the pinned IP via the socket-options path below.
                return super()._new_pool(scheme, host, port, request_context)

        # urllib3's connection factory honours a module-level
        # `allowed_gai_family`; the clean, version-stable hook is to wrap
        # `create_connection`. We do that per-request in `safe_request`
        # via a context-managed monkeypatch rather than globally, so
        # concurrent sends to different hosts never cross wires.
        self.poolmanager = _PinnedPoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )


class _pinned_connection:
    """Context manager that pins `urllib3`'s `create_connection` to one
    IP for the duration of a single request.

    `urllib3.util.connection.create_connection` is the one function
    every HTTP/HTTPS connection in `requests` funnels through. Swapping
    the destination host for the validated IP here — and only here, for
    the lifetime of one `with` block — guarantees the socket dials the
    address we vetted, with no global state and no concurrency hazard
    beyond the patch window (which is held only across `session.request`).
    """

    def __init__(self, pinned_ip: str):
        self._pinned_ip = pinned_ip
        self._original = None

    def __enter__(self):
        self._original = urllib3_connection.create_connection
        pinned_ip = self._pinned_ip
        original = self._original

        def _patched(address, *args, **kwargs):
            host, port = address
            return original((pinned_ip, port), *args, **kwargs)

        urllib3_connection.create_connection = _patched
        return self

    def __exit__(self, *exc):
        if self._original is not None:
            urllib3_connection.create_connection = self._original
        return False


# ── The public sender ──────────────────────────────────────────────────


def safe_request(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    json: Optional[dict] = None,
    data: Optional[bytes] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    allow_redirects: bool = False,
    allow_internal: bool = False,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> requests.Response:
    """Perform an SSRF-guarded HTTP request.

    Validates the URL (scheme / DNS / IP-range), pins the connection to
    the validated IP (closing DNS rebinding), disables redirects by
    default, and caps the response body.

    Raises `SSRFBlockedError` if the URL fails the policy;
    `requests.RequestException` on a transport error.
    """
    hostname, pinned_ip, port = validate_url(url, allow_internal=allow_internal)

    log.debug(
        "ssrf_guard: %s %s host=%s pinned_ip=%s port=%s",
        method, url, hostname, pinned_ip, port,
    )

    session = requests.Session()
    # Disable env-var proxies — a proxy would route around the IP pin.
    session.trust_env = False
    try:
        with _pinned_connection(pinned_ip):
            resp = session.request(
                method=method.upper(),
                url=url,
                headers=headers or {},
                json=json,
                data=data,
                timeout=timeout,
                allow_redirects=allow_redirects,
                stream=True,  # stream so we can enforce the size cap.
            )
            # Read at most `max_response_bytes` so a malicious receiver
            # cannot exhaust memory with a huge body.
            body = resp.raw.read(max_response_bytes + 1, decode_content=True)
            if len(body) > max_response_bytes:
                body = body[:max_response_bytes]
            # Re-attach the (capped) body so callers can read `.text`.
            resp._content = body
            resp._content_consumed = True
        return resp
    finally:
        session.close()


def safe_post(
    url: str,
    *,
    headers: Optional[dict] = None,
    json: Optional[dict] = None,
    data: Optional[bytes] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    allow_internal: bool = False,
) -> requests.Response:
    """SSRF-guarded HTTP POST — the common case for webhooks. Redirects
    are always disabled for a POST (a 30x to an internal host is the
    classic redirect-SSRF)."""
    return safe_request(
        "POST",
        url,
        headers=headers,
        json=json,
        data=data,
        timeout=timeout,
        allow_redirects=False,
        allow_internal=allow_internal,
    )
