"""The client address behind the proxy: every X-Forwarded-For line, hops with a port, hops that are
not addresses, and the trust list's default (FLEET-RATE-LIMIT-STANDARD §1.3)."""

import pytest
from starlette.requests import Request

from app.core.client_ip import client_ip_from_request
from app.core.config import Settings, settings

PROXY = "172.31.0.10"


def _request(peer: str, *forwarded: str) -> Request:
    headers = [(b"x-forwarded-for", line.encode()) for line in forwarded]
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


@pytest.fixture
def one_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment's shape: loopback and the one proxy's own address."""
    monkeypatch.setattr(settings, "TRUSTED_PROXIES", f"127.0.0.1,::1,{PROXY}")


@pytest.mark.usefixtures("one_proxy")
def test_every_forwarded_for_line_is_read() -> None:
    # A trusted hop that adds its own line leaves the client's line first: the rightmost entry
    # over all lines is the one the hop wrote.
    request = _request(PROXY, "203.0.113.1", "198.51.100.4")
    assert client_ip_from_request(request) == "198.51.100.4"


@pytest.mark.usefixtures("one_proxy")
@pytest.mark.parametrize(
    ("hop", "client"),
    [
        ("203.0.113.79:40121", "203.0.113.79"),
        ("[2001:db8::1]:443", "2001:db8::1"),
        ("[2001:db8::2]", "2001:db8::2"),
    ],
)
def test_a_hop_with_a_port_is_keyed_on_its_address(hop: str, client: str) -> None:
    assert client_ip_from_request(_request(PROXY, hop)) == client


@pytest.mark.usefixtures("one_proxy")
def test_one_client_on_many_ports_is_one_key() -> None:
    keys = {
        client_ip_from_request(_request(PROXY, f"203.0.113.79:{port}"))
        for port in range(40000, 40010)
    }
    assert keys == {"203.0.113.79"}


@pytest.mark.usefixtures("one_proxy")
@pytest.mark.parametrize(
    "forwarded", ["not-an-address", "198.51.100.4, unknown", "198.51.100.4, 1.2.3:x"]
)
def test_an_unparseable_hop_ends_the_walk_at_the_last_trusted_peer(
    forwarded: str,
) -> None:
    # Whatever the hop is, it is not a client the proxy saw: the trusted peer that passed it on is.
    assert client_ip_from_request(_request(PROXY, forwarded)) == PROXY


@pytest.mark.usefixtures("one_proxy")
def test_an_unparseable_hop_behind_another_trusted_hop_is_keyed_on_that_hop() -> None:
    request = _request(PROXY, "198.51.100.4, garbage, 127.0.0.1")
    assert client_ip_from_request(request) == "127.0.0.1"


@pytest.mark.usefixtures("one_proxy")
def test_an_address_is_keyed_in_one_spelling() -> None:
    assert client_ip_from_request(_request(PROXY, "2001:DB8::0:1")) == "2001:db8::1"


def test_the_default_trusts_only_loopback() -> None:
    """A peer on a private range is not a proxy unless the deployment names it, so it cannot pick
    its budget through X-Forwarded-For."""
    default = Settings.model_fields["TRUSTED_PROXIES"].default
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(settings, "TRUSTED_PROXIES", default)
        for peer in ("10.0.0.3", "172.18.0.5", "192.168.1.50"):
            assert client_ip_from_request(_request(peer, "203.0.113.9")) == peer
        assert client_ip_from_request(_request("127.0.0.1", "203.0.113.9")) == (
            "203.0.113.9"
        )
        assert client_ip_from_request(_request("::1", "203.0.113.9")) == "203.0.113.9"
