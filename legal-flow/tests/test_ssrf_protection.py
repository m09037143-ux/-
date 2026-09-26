import socket

import pytest

from app.providers import http_fetch
from app.providers.ssrf import SSRFBlockedError, is_public_ip, resolve_validated_ip
from app.providers.http_fetch import AccessLimitedError, fetch_url

# --- is_public_ip: pure unit tests, no network ---

@pytest.mark.parametrize(
    "ip,expected",
    [
        ("127.0.0.1", False),  # loopback
        ("::1", False),  # IPv6 loopback
        ("10.0.0.5", False),  # RFC1918 private
        ("172.16.0.1", False),  # RFC1918 private
        ("192.168.1.1", False),  # RFC1918 private
        ("169.254.169.254", False),  # cloud metadata / link-local
        ("fe80::1", False),  # IPv6 link-local
        ("0.0.0.0", False),  # unspecified
        ("224.0.0.1", False),  # multicast
        ("8.8.8.8", True),  # public
        ("93.184.216.34", True),  # public
    ],
)
def test_is_public_ip(ip, expected):
    assert is_public_ip(ip) is expected


# --- resolve_validated_ip: DNS resolution mocked, no real network needed ---

def _fake_getaddrinfo(ips: list[str]):
    async def _inner(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]

    return _inner


async def test_resolve_rejects_pure_private_address(monkeypatch):
    loop_mock = _fake_getaddrinfo(["127.0.0.1"])
    monkeypatch.setattr("asyncio.get_running_loop", lambda: type("L", (), {"getaddrinfo": loop_mock})())
    with pytest.raises(SSRFBlockedError):
        await resolve_validated_ip("localhost.evil.example", 443)


async def test_resolve_rejects_cloud_metadata_address(monkeypatch):
    loop_mock = _fake_getaddrinfo(["169.254.169.254"])
    monkeypatch.setattr("asyncio.get_running_loop", lambda: type("L", (), {"getaddrinfo": loop_mock})())
    with pytest.raises(SSRFBlockedError):
        await resolve_validated_ip("metadata.evil.example", 443)


async def test_resolve_rejects_mixed_public_private_dns_rebinding_style(monkeypatch):
    """A hostname that CAN answer with a private address (even alongside a public
    one) must be rejected outright — trusting 'the public one it gave us this time'
    is exactly what a DNS-rebinding attack exploits on the next lookup."""
    loop_mock = _fake_getaddrinfo(["93.184.216.34", "10.0.0.1"])
    monkeypatch.setattr("asyncio.get_running_loop", lambda: type("L", (), {"getaddrinfo": loop_mock})())
    with pytest.raises(SSRFBlockedError):
        await resolve_validated_ip("rebinding.evil.example", 443)


async def test_resolve_accepts_pure_public_address(monkeypatch):
    loop_mock = _fake_getaddrinfo(["93.184.216.34"])
    monkeypatch.setattr("asyncio.get_running_loop", lambda: type("L", (), {"getaddrinfo": loop_mock})())
    ip = await resolve_validated_ip("example.com", 443)
    assert ip == "93.184.216.34"


async def test_dns_resolution_failure_is_blocked_not_ignored(monkeypatch):
    async def _raise(*a, **kw):
        raise socket.gaierror("no such host")

    monkeypatch.setattr("asyncio.get_running_loop", lambda: type("L", (), {"getaddrinfo": _raise})())
    with pytest.raises(SSRFBlockedError):
        await resolve_validated_ip("does-not-exist.example", 443)


# --- fetch_url: scheme enforcement needs no network at all ---

async def test_fetch_rejects_non_https_scheme():
    with pytest.raises(SSRFBlockedError):
        await fetch_url("http://example.com/", timeout_seconds=1, max_bytes=1000, max_redirects=2)


async def test_fetch_rejects_url_with_no_host():
    with pytest.raises(SSRFBlockedError):
        await fetch_url("https:///path", timeout_seconds=1, max_bytes=1000, max_redirects=2)


# --- fetch_url: redirect handling and access-limited mapping, DNS+socket mocked ---

async def test_redirect_to_internal_address_is_blocked(monkeypatch):
    """Simulates a public site's redirect Location pointing at an internal address —
    each hop must be independently re-validated, not just the first URL."""

    async def fake_resolve(hostname, port):
        if hostname == "public-site.example":
            return "93.184.216.34"
        raise SSRFBlockedError(f"{hostname} is not public")

    def fake_fetch_once(url, *, timeout, max_bytes, ip):
        return 302, {"Location": "https://169.254.169.254/latest/meta-data/"}, b"", "public-site.example"

    monkeypatch.setattr(http_fetch, "resolve_validated_ip", fake_resolve)
    monkeypatch.setattr(http_fetch, "_fetch_once_sync", fake_fetch_once)

    with pytest.raises(SSRFBlockedError):
        await fetch_url("https://public-site.example/", timeout_seconds=1, max_bytes=1000, max_redirects=3)


async def test_too_many_redirects_is_access_limited(monkeypatch):
    call_count = {"n": 0}

    async def fake_resolve(hostname, port):
        return "93.184.216.34"

    def fake_fetch_once(url, *, timeout, max_bytes, ip):
        call_count["n"] += 1
        return 302, {"Location": f"https://public-site.example/{call_count['n']}"}, b"", "public-site.example"

    monkeypatch.setattr(http_fetch, "resolve_validated_ip", fake_resolve)
    monkeypatch.setattr(http_fetch, "_fetch_once_sync", fake_fetch_once)

    with pytest.raises(AccessLimitedError):
        await fetch_url("https://public-site.example/", timeout_seconds=1, max_bytes=1000, max_redirects=2)


async def test_403_maps_to_access_limited_not_bypassed(monkeypatch):
    async def fake_resolve(hostname, port):
        return "93.184.216.34"

    def fake_fetch_once(url, *, timeout, max_bytes, ip):
        return 403, {}, b"blocked", "public-site.example"

    monkeypatch.setattr(http_fetch, "resolve_validated_ip", fake_resolve)
    monkeypatch.setattr(http_fetch, "_fetch_once_sync", fake_fetch_once)

    with pytest.raises(AccessLimitedError) as exc_info:
        await fetch_url("https://public-site.example/", timeout_seconds=1, max_bytes=1000, max_redirects=2)
    assert exc_info.value.status_code == 403


async def test_429_maps_to_access_limited(monkeypatch):
    async def fake_resolve(hostname, port):
        return "93.184.216.34"

    def fake_fetch_once(url, *, timeout, max_bytes, ip):
        return 429, {}, b"", "public-site.example"

    monkeypatch.setattr(http_fetch, "resolve_validated_ip", fake_resolve)
    monkeypatch.setattr(http_fetch, "_fetch_once_sync", fake_fetch_once)

    with pytest.raises(AccessLimitedError):
        await fetch_url("https://public-site.example/", timeout_seconds=1, max_bytes=1000, max_redirects=2)


async def test_successful_fetch_returns_body_within_limit(monkeypatch):
    async def fake_resolve(hostname, port):
        return "93.184.216.34"

    def fake_fetch_once(url, *, timeout, max_bytes, ip):
        return 200, {"Content-Type": "text/html"}, b"<html>ok</html>", "public-site.example"

    monkeypatch.setattr(http_fetch, "resolve_validated_ip", fake_resolve)
    monkeypatch.setattr(http_fetch, "_fetch_once_sync", fake_fetch_once)

    result = await fetch_url("https://public-site.example/", timeout_seconds=1, max_bytes=1000, max_redirects=2)
    assert result.status_code == 200
    assert result.body == b"<html>ok</html>"
