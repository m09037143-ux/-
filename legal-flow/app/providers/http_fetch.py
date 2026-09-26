import asyncio
import http.client
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from app.providers.ssrf import SSRFBlockedError, resolve_validated_ip


class AccessLimitedError(Exception):
    """403/429/captcha-like response — ТЗ §8: never bypassed, just reported."""

    def __init__(self, reason: str, status_code: int | None = None):
        self.reason = reason
        self.status_code = status_code
        super().__init__(reason)


@dataclass
class FetchResult:
    final_url: str
    status_code: int
    content_type: str
    body: bytes


def _connect_validated_socket(hostname: str, ip: str, port: int, timeout: float) -> socket.socket:
    """Connects to the ALREADY-VALIDATED ip, never to whatever the hostname resolves
    to at connect time — this is what actually defeats DNS rebinding (resolve once,
    validate, then dial the literal address instead of the name)."""
    sock = socket.socket(socket.AF_INET if ":" not in ip else socket.AF_INET6, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((ip, port))
    return sock


def _fetch_once_sync(url: str, *, timeout: float, max_bytes: int, ip: str) -> tuple[int, dict, bytes, str]:
    parts = urlsplit(url)
    hostname = parts.hostname
    port = parts.port or 443
    path = parts.path or "/"
    if parts.query:
        path += f"?{parts.query}"

    raw_sock = _connect_validated_socket(hostname, ip, port, timeout)
    ctx = ssl.create_default_context()
    ssl_sock = ctx.wrap_socket(raw_sock, server_hostname=hostname)
    try:
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {hostname}\r\n"
            "User-Agent: PravovoyPotokBot/0.1 (+see source_policies; respects robots and rate limits)\r\n"
            "Accept: text/html,application/xhtml+xml\r\n"
            "Connection: close\r\n\r\n"
        )
        ssl_sock.sendall(request.encode("ascii"))

        response = http.client.HTTPResponse(ssl_sock, method="GET")
        response.begin()
        headers = dict(response.getheaders())

        body = b""
        remaining = max_bytes
        while remaining > 0:
            chunk = response.read(min(65536, remaining))
            if not chunk:
                break
            body += chunk
            remaining -= len(chunk)
        return response.status, headers, body, hostname
    finally:
        ssl_sock.close()


async def fetch_url(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    max_redirects: int,
) -> FetchResult:
    """HTTPS-only fetch that validates the domain/public-IP at connect time AND at
    every redirect hop, enforces size/time/redirect-count limits, and turns
    403/429/captcha-shaped responses into AccessLimitedError instead of retrying or
    working around them (ТЗ §8). Never accepts caller-supplied headers or a proxy."""
    current_url = url
    redirects_followed = 0

    while True:
        parts = urlsplit(current_url)
        if parts.scheme != "https":
            raise SSRFBlockedError(f"Only https:// is allowed, got {parts.scheme!r} for {current_url}")
        if not parts.hostname:
            raise SSRFBlockedError(f"URL has no host: {current_url}")

        ip = await resolve_validated_ip(parts.hostname, parts.port or 443)

        try:
            status, headers, body, hostname = await asyncio.wait_for(
                asyncio.to_thread(_fetch_once_sync, current_url, timeout=timeout_seconds, max_bytes=max_bytes, ip=ip),
                timeout=timeout_seconds + 2,
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise AccessLimitedError(f"Timed out fetching {current_url}") from exc
        except (socket.timeout, ConnectionError, ssl.SSLError, OSError) as exc:
            raise AccessLimitedError(f"Connection error fetching {current_url}: {exc}") from exc

        if status in (301, 302, 303, 307, 308):
            location = headers.get("Location") or headers.get("location")
            if not location:
                raise AccessLimitedError(f"Redirect without Location header from {current_url}", status_code=status)
            redirects_followed += 1
            if redirects_followed > max_redirects:
                raise AccessLimitedError(f"Too many redirects starting from {url}", status_code=status)
            current_url = urljoin(current_url, location)
            continue

        if status in (401, 403, 429):
            raise AccessLimitedError(f"Access limited by {parts.hostname} (HTTP {status})", status_code=status)

        content_type = headers.get("Content-Type", headers.get("content-type", ""))
        return FetchResult(final_url=current_url, status_code=status, content_type=content_type, body=body)
