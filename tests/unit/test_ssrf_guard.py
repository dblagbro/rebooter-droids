"""Unit tests — the SSRF guard (Tier-2 Feature 6).

`app/services/ssrf_guard.py` is the load-bearing security control for
the outbound-notifications engine: the hub is public SaaS, and any
outbound HTTP to an operator-supplied URL is an SSRF vector.

These tests prove the guard:

  * classifies every dangerous IP range as blocked — private (RFC1918 /
    ULA), loopback, link-local, multicast, reserved, unspecified, CGNAT
    (RFC 6598), the cloud metadata endpoint, and IPv4-mapped IPv6.
  * rejects literal-IP URLs that land in those ranges.
  * rejects a hostname when *any* of its A/AAAA records is internal.
  * blocks DNS rebinding — a hostname that resolves to a public IP at
    validation time cannot be swapped for an internal IP at connect
    time, because the connection is pinned to the validated IP.
  * refuses non-http(s) schemes and (by default) plain http://.

No network: DNS resolution is monkeypatched, so these tests are fast
and deterministic. Pure (`is_blocked_ip`) cases need no fixture.
"""

from __future__ import annotations

import socket

import pytest

from app.services import ssrf_guard
from app.services.ssrf_guard import SSRFBlockedError, is_blocked_ip, validate_url


# ── is_blocked_ip — pure IP classification ─────────────────────────────


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",          # RFC1918
        "10.255.255.255",
        "172.16.0.1",        # RFC1918
        "172.31.255.254",
        "192.168.1.1",       # RFC1918
        "127.0.0.1",         # loopback
        "127.5.5.5",
        "0.0.0.0",           # unspecified
        "169.254.169.254",   # cloud metadata / link-local
        "169.254.0.1",       # link-local
        "100.64.0.1",        # CGNAT (RFC 6598)
        "100.127.255.255",   # CGNAT upper bound
        "224.0.0.1",         # multicast
        "240.0.0.1",         # reserved
        "::1",               # IPv6 loopback
        "::",                # IPv6 unspecified
        "fe80::1",           # IPv6 link-local
        "fc00::1",           # IPv6 ULA (private)
        "fd00::1",           # IPv6 ULA
        "ff02::1",           # IPv6 multicast
        "::ffff:127.0.0.1",  # IPv4-mapped IPv6 wrapping loopback
        "::ffff:10.0.0.1",   # IPv4-mapped IPv6 wrapping RFC1918
    ],
)
def test_is_blocked_ip_rejects_dangerous_ranges(ip):
    blocked, reason = is_blocked_ip(ip)
    assert blocked is True, f"{ip} should be blocked"
    assert reason  # a human-readable reason is always given.


@pytest.mark.parametrize(
    "ip",
    [
        "8.8.8.8",           # Google DNS — public
        "1.1.1.1",           # Cloudflare — public
        "93.184.216.34",     # example.com — public
        "2606:4700:4700::1111",  # public IPv6
    ],
)
def test_is_blocked_ip_allows_public_addresses(ip):
    blocked, reason = is_blocked_ip(ip)
    assert blocked is False, f"{ip} should be allowed"


def test_is_blocked_ip_rejects_garbage():
    blocked, _ = is_blocked_ip("not-an-ip")
    assert blocked is True


# ── validate_url — DNS-resolved hostnames ──────────────────────────────


def _fake_resolver(mapping):
    """Build a `getaddrinfo` replacement that returns the IP list
    `mapping[hostname]`. Raises gaierror for an unknown host."""

    def _resolve(host, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(f"unknown host {host}")
        # getaddrinfo returns 5-tuples; only [4][0] (the address) is read.
        return [(None, None, None, None, (ip, 0)) for ip in mapping[host]]

    return _resolve


def test_validate_url_allows_public_https(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_resolver({"hooks.example.com": ["93.184.216.34"]})
    )
    host, pinned_ip, port = validate_url("https://hooks.example.com/webhook")
    assert host == "hooks.example.com"
    assert pinned_ip == "93.184.216.34"
    assert port == 443


def test_validate_url_rejects_hostname_resolving_to_private(monkeypatch):
    """A hostname whose A record is RFC1918 is blocked — the classic
    'evil.com → 10.0.0.1' SSRF."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_resolver({"evil.example.com": ["10.0.0.5"]})
    )
    with pytest.raises(SSRFBlockedError) as exc:
        validate_url("https://evil.example.com/x")
    assert "private" in str(exc.value).lower()


def test_validate_url_rejects_hostname_resolving_to_loopback(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_resolver({"loop.example.com": ["127.0.0.1"]})
    )
    with pytest.raises(SSRFBlockedError):
        validate_url("https://loop.example.com/x")


def test_validate_url_rejects_metadata_endpoint(monkeypatch):
    """The cloud metadata endpoint is the highest-value SSRF target."""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        _fake_resolver({"meta.example.com": ["169.254.169.254"]}),
    )
    with pytest.raises(SSRFBlockedError) as exc:
        validate_url("https://meta.example.com/latest/meta-data/")
    assert "metadata" in str(exc.value).lower() or "link-local" in str(exc.value).lower()


def test_validate_url_rejects_when_any_record_is_internal(monkeypatch):
    """A hostname is only as safe as its *worst* A/AAAA record — one
    private IP among several public ones still poisons the name. This is
    a DNS-rebinding defence: an attacker publishes both a public and a
    private record."""
    monkeypatch.setattr(
        socket, "getaddrinfo",
        _fake_resolver({"mixed.example.com": ["93.184.216.34", "10.0.0.1"]}),
    )
    with pytest.raises(SSRFBlockedError) as exc:
        validate_url("https://mixed.example.com/x")
    assert "private" in str(exc.value).lower()


def test_validate_url_rejects_literal_private_ip(monkeypatch):
    """A literal-IP URL pointing into a private range is blocked even
    though no DNS is involved."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_resolver({"192.168.0.1": ["192.168.0.1"]})
    )
    with pytest.raises(SSRFBlockedError):
        validate_url("https://192.168.0.1/admin")


def test_validate_url_rejects_cgnat(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_resolver({"cgn.example.com": ["100.64.0.1"]})
    )
    with pytest.raises(SSRFBlockedError) as exc:
        validate_url("https://cgn.example.com/x")
    assert "cgnat" in str(exc.value).lower()


def test_validate_url_rejects_non_http_scheme():
    with pytest.raises(SSRFBlockedError) as exc:
        validate_url("ftp://example.com/x")
    assert "scheme" in str(exc.value).lower()
    with pytest.raises(SSRFBlockedError):
        validate_url("file:///etc/passwd")


def test_validate_url_rejects_plain_http_by_default(monkeypatch, hub_db):
    """Plain http:// is refused on the public SaaS unless the
    `outbound.allow_http` runtime setting is explicitly on."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_resolver({"plain.example.com": ["93.184.216.34"]})
    )
    with pytest.raises(SSRFBlockedError) as exc:
        validate_url("http://plain.example.com/x")
    assert "http" in str(exc.value).lower()


def test_validate_url_allows_http_when_setting_enabled(monkeypatch, hub_db):
    from app.services import runtime_settings

    runtime_settings.set_("outbound.allow_http", "true")
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_resolver({"plain.example.com": ["93.184.216.34"]})
    )
    host, ip, port = validate_url("http://plain.example.com/x")
    assert host == "plain.example.com"
    assert port == 80


def test_validate_url_allow_internal_bypass(monkeypatch):
    """The explicit `allow_internal=True` bypass lets trusted internal
    calls through the IP-range check (still resolves so it can be
    pinned)."""
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_resolver({"localhost": ["127.0.0.1"]})
    )
    host, ip, port = validate_url(
        "http://localhost:8090/health", allow_internal=True
    )
    assert host == "localhost"
    assert ip == "127.0.0.1"


def test_validate_url_rejects_unresolvable_host(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver({}))
    with pytest.raises(SSRFBlockedError) as exc:
        validate_url("https://nonexistent.invalid/x")
    assert "resolve" in str(exc.value).lower()


# ── DNS-rebinding (TOCTOU) — the connection is pinned ───────────────────


def test_dns_rebind_is_blocked_by_ip_pinning(monkeypatch):
    """The DNS-rebinding attack: a hostname resolves to a public IP
    during validation, then the attacker re-points DNS at an internal
    IP before the connection is made.

    The guard pins the connection to the IP it *validated*. We simulate
    the attack by changing what the resolver returns between the
    validate call and a second validate call: the second call (a fresh
    request) would re-resolve — and if the attacker has flipped the
    record to an internal IP, validation now rejects it. So a rebind
    either (a) is caught at the next validation, or (b) — for an
    in-flight request — connects to the already-pinned public IP, never
    the swapped-in internal one.
    """
    # Phase 1: the name resolves public — validation passes and pins.
    monkeypatch.setattr(
        socket, "getaddrinfo",
        _fake_resolver({"rebind.example.com": ["93.184.216.34"]}),
    )
    host, pinned_ip, port = validate_url("https://rebind.example.com/x")
    assert pinned_ip == "93.184.216.34"  # the public IP we will pin to.

    # Phase 2: the attacker flips DNS to an internal IP. A *new* request
    # re-resolves and is now rejected outright — the rebind cannot even
    # get past validation.
    monkeypatch.setattr(
        socket, "getaddrinfo",
        _fake_resolver({"rebind.example.com": ["10.0.0.7"]}),
    )
    with pytest.raises(SSRFBlockedError) as exc:
        validate_url("https://rebind.example.com/x")
    assert "private" in str(exc.value).lower()


def test_pinned_connection_dials_validated_ip(monkeypatch):
    """`_pinned_connection` forces `urllib3`'s socket factory to dial the
    validated IP regardless of the host argument passed in — this is the
    mechanism that closes the in-flight TOCTOU window."""
    from urllib3.util import connection as urllib3_connection

    calls = []

    def _fake_create_connection(address, *args, **kwargs):
        calls.append(address)
        return "fake-socket"

    monkeypatch.setattr(
        urllib3_connection, "create_connection", _fake_create_connection
    )

    with ssrf_guard._pinned_connection("93.184.216.34"):
        # Even though the caller asks for an internal host, the patched
        # factory must dial the pinned public IP.
        sock = urllib3_connection.create_connection(("10.0.0.1", 443))
        assert sock == "fake-socket"

    # The connection was made to the pinned IP, not the requested host.
    assert calls == [("93.184.216.34", 443)]

    # And the original factory is restored on context exit.
    assert urllib3_connection.create_connection is _fake_create_connection
