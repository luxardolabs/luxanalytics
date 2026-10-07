"""The real client address behind the reverse proxy — the key every rate limit uses.

Trust model (luxarch --doc FLEET-RATE-LIMIT-STANDARD §1.3): the app is reachable only through
nginx on the docker network, so its direct peer is the proxy, never the client. Forwarded
headers are honoured ONLY when that peer is in TRUSTED_PROXIES. X-Forwarded-For is then read
right to left, skipping trusted hops, and the first untrusted address is the client: entries to
its left are whatever the client chose to send, so reading the leftmost would let any caller
pick a fresh rate-limit bucket per request. A peer outside TRUSTED_PROXIES is the client
itself, and its headers are ignored.
"""

from functools import cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.requests import Request

from app.core.config import settings


@cache
def _trusted_networks() -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(
        ip_network(cidr.strip(), strict=False)
        for cidr in settings.TRUSTED_PROXIES.split(",")
        if cidr.strip()
    )


def _is_trusted(address: str) -> bool:
    try:
        ip = ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in _trusted_networks())


def client_ip_from_request(request: Request) -> str:
    """The client's address: the peer, unless the peer is a trusted proxy that forwarded one."""
    peer = request.client.host if request.client else "unknown"
    if not _is_trusted(peer):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    for hop in reversed([h.strip() for h in forwarded.split(",") if h.strip()]):
        if not _is_trusted(hop):
            return hop
    return peer
