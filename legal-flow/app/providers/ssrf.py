import asyncio
import ipaddress
import socket


class SSRFBlockedError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def is_public_ip(ip_str: str) -> bool:
    """True only for an address a public web crawler may legitimately connect to.
    Rejects loopback, RFC1918/ULA private ranges, link-local (incl. the
    169.254.169.254 cloud metadata address), multicast, reserved and unspecified."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def resolve_validated_ip(hostname: str, port: int) -> str:
    """Resolves hostname and returns ONE public IP to connect to. Rejects the whole
    hostname if ANY resolved address is non-public — a resolver that can answer with
    a mix of public/private addresses (or does so on a later query, i.e. DNS
    rebinding) must not be trusted just because one answer looked safe."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFBlockedError(f"DNS resolution failed for {hostname!r}: {exc}") from exc

    if not infos:
        raise SSRFBlockedError(f"No addresses resolved for {hostname!r}")

    resolved_ips = {info[4][0] for info in infos}
    for ip in resolved_ips:
        if not is_public_ip(ip):
            raise SSRFBlockedError(f"{hostname!r} resolves to a non-public address ({ip}); refusing to fetch")

    # Deterministic choice (not "first from a possibly attacker-influenced order")
    # avoids being steered toward a specific one of several public IPs.
    return sorted(resolved_ips)[0]
