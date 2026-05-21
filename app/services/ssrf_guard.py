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
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
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


# ── IP-pinned transport — thread-safe, no module-global mutation ───────
#
# DESIGN (load-degradation incident fix, 2026-05-21)
#
# The old implementation monkeypatched the PROCESS-GLOBAL
# `urllib3.util.connection.create_connection` for the duration of every
# request (`_pinned_connection`). Under concurrency — 8 gunicorn threads
# plus the APScheduler webhook-delivery job — the unsynchronized
# save/restore interleaved: a thread could save an already-*patched*
# closure as its "original" and restore to that, permanently losing the
# real function and turning the global into an ever-deepening chain of
# self-referential closures until every outbound call hit `RecursionError`.
# The corruption was cumulative and permanent for the process lifetime.
#
# This re-implementation eliminates the global monkeypatch entirely. The
# IP pin is carried on PER-CALL connection subclasses whose `_new_conn`
# substitutes the validated IP as the connect target. The pinned IP is
# closed over per `safe_request()` call — nothing module-global is ever
# mutated, so the mechanism is thread-safe by construction: concurrent
# requests to different hosts each own their own adapter / pool manager /
# connection classes and cannot cross wires.
#
# The SSRF protection is fully preserved:
#   * the hostname is still resolved and every A/AAAA record validated
#     in `validate_url()` (unchanged);
#   * the connection is still PINNED to the validated IP — `_new_conn`
#     dials the pinned literal address, so a DNS answer that changes
#     between validation and connect (DNS rebinding / TOCTOU) cannot
#     redirect the socket to an internal host;
#   * `self.host` is left as the original hostname, so the TLS SNI /
#     `server_hostname` and the `Host` request header still carry the
#     real hostname — virtual-hosted and TLS-validated receivers work.


def _make_pinned_connection_classes(pinned_ip: str):
    """Build a `(HTTPConnection, HTTPSConnection)` subclass pair whose
    `_new_conn()` dials `pinned_ip` instead of re-resolving the host.

    The classes are created fresh per `safe_request()` call with
    `pinned_ip` closed over — no shared mutable state, so this is safe to
    call from any number of threads concurrently.

    `_new_conn()` is urllib3's single socket-creation chokepoint for both
    plain and TLS connections. We override only the connect *target*
    (the IP), leaving `self.host` / `self.server_hostname` untouched so
    TLS SNI and certificate validation still use the real hostname.
    """

    def _pinned_new_conn(self) -> socket.socket:
        # Mirror urllib3.connection.HTTPConnection._new_conn, but dial
        # the pre-validated IP. `self.host` (and therefore SNI / the Host
        # header) is left exactly as the URL set it.
        from urllib3.exceptions import (
            ConnectTimeoutError,
            NameResolutionError,
            NewConnectionError,
        )

        try:
            sock = urllib3_connection.create_connection(
                (pinned_ip, self.port),
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except socket.gaierror as e:  # pragma: no cover - pinned IP is a literal
            raise NameResolutionError(self.host, self, e) from e
        except socket.timeout as e:
            raise ConnectTimeoutError(
                self,
                f"Connection to {self.host} timed out. "
                f"(connect timeout={self.timeout})",
            ) from e
        except OSError as e:
            raise NewConnectionError(
                self, f"Failed to establish a new connection: {e}"
            ) from e
        return sock

    pinned_http = type(
        "PinnedHTTPConnection",
        (HTTPConnection,),
        {"_new_conn": _pinned_new_conn},
    )
    pinned_https = type(
        "PinnedHTTPSConnection",
        (HTTPSConnection,),
        {"_new_conn": _pinned_new_conn},
    )
    return pinned_http, pinned_https


class _PinnedHTTPAdapter(HTTPAdapter):
    """A `requests` transport adapter that forces every socket opened
    through it to connect to one pre-validated IP.

    The pin is carried entirely on instance-scoped state: a per-adapter
    `PoolManager` whose `HTTPConnectionPool` / `HTTPSConnectionPool`
    subclasses use connection classes that dial the pinned IP. No module
    global is mutated, so any number of `_PinnedHTTPAdapter` instances —
    each pinned to a different IP — can be in flight on different threads
    at once without interfering.

    The `Host` header / TLS SNI still carry the original hostname (the
    URL is unchanged), so virtual-hosted and TLS-validated receivers
    work normally — only the TCP connect target is the validated IP.
    """

    def __init__(self, pinned_ip: str, *args, **kwargs):
        self._pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pinned_http_conn, pinned_https_conn = _make_pinned_connection_classes(
            self._pinned_ip
        )

        # Per-adapter pool subclasses bound to the pinned connection
        # classes. urllib3 keys pools by (scheme, host, port) — we leave
        # the key as the real hostname so SNI / Host stay correct; only
        # the connection's connect *target* is the pinned IP.
        class _PinnedHTTPConnectionPool(HTTPConnectionPool):
            ConnectionCls = pinned_http_conn

        class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
            ConnectionCls = pinned_https_conn

        class _PinnedPoolManager(PoolManager):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                # Instance-level override of the scheme→pool-class map so
                # this PoolManager — and only this one — builds pinned
                # pools. The class-level `urllib3` default is untouched.
                self.pool_classes_by_scheme = {
                    "http": _PinnedHTTPConnectionPool,
                    "https": _PinnedHTTPSConnectionPool,
                }

        self.poolmanager = _PinnedPoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )


class _pinned_connection:
    """Backward-compatible context manager that pins outbound sockets to
    one validated IP for the duration of a `with` block.

    Historically this monkeypatched the process-global
    `urllib3.util.connection.create_connection`, which corrupted under
    concurrency (see the module-level DESIGN note). It is retained ONLY
    as a thin no-op-style shim so any caller / test still referencing it
    keeps working — the real pinning is now done by `_PinnedHTTPAdapter`,
    which `safe_request()` mounts on the per-call `requests.Session`.

    Entering this context yields the pinned IP and mutates NO global
    state, so it is fully thread-safe; the actual connection pinning
    happens in the adapter regardless of whether this is used.
    """

    def __init__(self, pinned_ip: str):
        self._pinned_ip = pinned_ip

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def pinned_ip(self) -> str:
        return self._pinned_ip

    def build_adapter(self) -> "_PinnedHTTPAdapter":
        """Return an IP-pinned `requests` adapter for this IP."""
        return _PinnedHTTPAdapter(self._pinned_ip)


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
    # Mount an IP-pinned transport adapter for BOTH schemes. The adapter
    # carries the pin entirely on instance state (a per-call PoolManager
    # with pinned connection classes) — no module global is mutated, so
    # concurrent `safe_request` calls on other threads are unaffected.
    # This is the thread-safe replacement for the old context-managed
    # monkeypatch of `urllib3.util.connection.create_connection`.
    pinned_adapter = _PinnedHTTPAdapter(pinned_ip)
    session.mount("http://", pinned_adapter)
    session.mount("https://", pinned_adapter)
    try:
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
