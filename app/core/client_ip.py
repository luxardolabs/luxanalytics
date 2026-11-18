"""The real client address behind the reverse proxy — the key every rate limit uses.

Trust model (luxarch --doc FLEET-RATE-LIMIT-STANDARD §1.3): the app is reachable only through
nginx on the docker network, so its direct peer is the proxy, never the client. Forwarded
headers are honoured ONLY when that peer is in TRUSTED_PROXIES. X-Forwarded-For is then read
right to left, skipping trusted hops, and the first untrusted address is the client: entries to
its left are whatever the client chose to send, so reading the leftmost would let any caller
pick a fresh rate-limit bucket per request. A peer outside TRUSTED_PROXIES is the client
itself, and its headers are ignored.

TRUSTED_PROXIES names loopback and each proxy's own address, never a range (settings refuse one):
a client inside a trusted range would be skipped as a proxy and keyed on whatever it wrote. Every
X-Forwarded-For line is read, as one list. An entry with a port is keyed on its address, so a hop
that forwards `ip:port` does not make one budget per connection; an entry that is not an address
ends the walk at the trusted hop that passed it on.
"""

from functools import cache
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)

from starlette.requests import Request

from app.core.config import settings


@cache
def _trusted_networks(trusted: str) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(
        ip_network(cidr.strip(), strict=False)
        for cidr in trusted.split(",")
        if cidr.strip()
    )


def _hop_address(hop: str) -> IPv4Address | IPv6Address | None:
    """The address an X-Forwarded-For entry names, its port dropped (`203.0.113.7:40121`,
    `[2001:db8::1]:443`); None for anything else (`unknown`, a name, garbage)."""
    if hop.startswith("["):
        host, bracket, rest = hop[1:].partition("]")
        if not bracket or (rest and not (rest[0] == ":" and rest[1:].isdecimal())):
            return None
        hop = host
    elif hop.count(":") == 1:
        host, _, port = hop.partition(":")
        if not port.isdecimal():
            return None
        hop = host
    try:
        return ip_address(hop)
    except ValueError:
        return None


def _is_trusted(address: IPv4Address | IPv6Address) -> bool:
    return any(
        address in network for network in _trusted_networks(settings.TRUSTED_PROXIES)
    )


def client_ip_from_request(request: Request) -> str:
    """The client's address: the peer, unless the peer is a trusted proxy that forwarded one."""
    peer = request.client.host if request.client else "unknown"
    peer_address = _hop_address(peer)
    if peer_address is None or not _is_trusted(peer_address):
        return peer

    # Every X-Forwarded-For line, in order: a hop that adds its own line leaves the client's
    # line first, so the first line alone would end the walk at the client's own entries.
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    last_trusted = peer_address
    for hop in reversed([h.strip() for h in forwarded.split(",") if h.strip()]):
        address = _hop_address(hop)
        if address is None:
            # Not an address the trusted hop saw: the walk ends at that hop.
            return str(last_trusted)
        if not _is_trusted(address):
            return str(address)
        last_trusted = address
    return str(peer_address)
