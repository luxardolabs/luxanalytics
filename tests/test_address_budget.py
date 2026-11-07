"""The ingest address budget is one per client: an IPv6 address that carries an IPv4 client (a
translator's NAT64, SIIT or IPv4-compatible form, a Teredo tunnel) is that IPv4 client's budget,
not its translator's /64."""

from collections.abc import Callable
from ipaddress import IPv4Address, IPv6Address
from uuid import uuid4

import pytest
from httpx import AsyncClient

import app.core.rate_limiter as rate_limiter
from app.core.config import settings

LIMIT = 5

Form = Callable[[str], str]


def _teredo(client: str) -> str:
    """The client's address in a Teredo address of one server (RFC 4380: port and client
    obfuscated by inversion)."""
    server = int(IPv4Address("192.0.2.1"))
    obfuscated_client = int(IPv4Address(client)) ^ 0xFFFFFFFF
    packed = (0x20010000 << 96) | (server << 64) | (0x8000 << 48) | obfuscated_client
    return str(IPv6Address(packed | ((0x1234 ^ 0xFFFF) << 32)))


FORMS: dict[str, Form] = {
    "ipv4": lambda client: client,
    "ipv4-mapped": lambda client: f"::ffff:{client}",
    "nat64-wkp-rfc6052": lambda client: f"64:ff9b::{client}",
    "siit-ipv4-translated": lambda client: f"::ffff:0:{client}",
    "ipv4-compatible": lambda client: f"::{client}",
    "teredo": _teredo,
}
TRANSLATED = [name for name in FORMS if name != "ipv4"]


@pytest.fixture(autouse=True)
def address_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "IP_RATE_LIMIT_EVENTS", LIMIT)


@pytest.mark.parametrize("form", FORMS)
async def test_two_ipv4_clients_behind_a_translator_are_two_budgets(form: str) -> None:
    translate = FORMS[form]
    app_id = f"app_address_{form}"

    await rate_limiter.enforce_ingest_limits(
        app_id, translate("198.51.100.1"), {"device-a": LIMIT}
    )
    await rate_limiter.enforce_ingest_limits(
        app_id, translate("198.51.100.2"), {"device-b": LIMIT}
    )


@pytest.mark.parametrize("form", TRANSLATED)
async def test_one_ipv4_client_is_one_budget_in_every_form(form: str) -> None:
    app_id = f"app_address_same_{form}"
    await rate_limiter.enforce_ingest_limits(
        app_id, "198.51.100.1", {"device-a": LIMIT}
    )

    with pytest.raises(rate_limiter.AppRateLimitExceeded) as refused:
        await rate_limiter.enforce_ingest_limits(
            app_id, FORMS[form]("198.51.100.1"), {"device-b": 1}
        )

    assert refused.value.error == "Rate limit exceeded"


@pytest.mark.parametrize(
    "addresses",
    [
        ["2001:db8:66:1::1", "2001:db8:66:1::2"],
        ["2001:db8:66:1::1", "2001:db8:66:1:ffff::2"],
        ["2001:1::1", "2001:1::2"],
        ["64:ff9b:1::1", "64:ff9b:1::2"],
    ],
    ids=["one-64", "same-64-far-apart", "not-teredo", "not-the-translated-prefix"],
)
async def test_other_ipv6_hosts_still_share_their_64(addresses: list[str]) -> None:
    await rate_limiter.enforce_ingest_limits(
        "app_address_v6", addresses[0], {"device-a": LIMIT}
    )

    with pytest.raises(rate_limiter.AppRateLimitExceeded):
        await rate_limiter.enforce_ingest_limits(
            "app_address_v6", addresses[1], {"device-b": 1}
        )


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("2002:cb00:7101:1::1", "2002:cb00:7101:2::9", True),
        ("2002:cb00:7101::1", "2002:cb00:7102::1", False),
        ("2002:cb00:7101:1::1", "203.0.113.1", True),
    ],
    ids=["one-site-two-subnets", "two-sites", "same-ipv4-as-native"],
)
def test_a_6to4_address_is_its_sites_ipv4_client(
    first: str, second: str, same: bool
) -> None:
    assert (
        rate_limiter.address_identifier(first)
        == rate_limiter.address_identifier(second)
    ) is same


def test_a_local_use_nat64_prefix_stays_on_its_64() -> None:
    """Where the IPv4 sits in a 64:ff9b:1::/48 address depends on the translator's prefix length,
    which the server is not told: two of its addresses are not read as two IPv4 clients."""
    assert rate_limiter.address_identifier(
        "64:ff9b:1::cb00:7101"
    ) == rate_limiter.address_identifier("64:ff9b:1::cb00:7102")


def test_a_scoped_address_is_keyed_without_its_scope() -> None:
    assert rate_limiter.address_identifier("fe80::1%eth0") == "fe80::/64"


async def test_the_request_limit_counts_one_ipv6_64_as_one_client(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", 3)
    prefix = f"2001:db8:{uuid4().int % 65536:x}:1"

    codes = [
        (
            await client.get("/login", headers={"X-Forwarded-For": f"{prefix}::{i:x}"})
        ).status_code
        for i in range(1, 6)
    ]

    assert codes == [200, 200, 200, 429, 429]


async def test_the_request_limit_counts_a_translated_ipv4_client_as_that_client(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_REQUESTS", 2)
    forms = ["198.51.100.31", "::ffff:198.51.100.31", "64:ff9b::198.51.100.31"]

    codes = [
        (await client.get("/login", headers={"X-Forwarded-For": form})).status_code
        for form in forms
    ]

    assert codes == [200, 200, 429]
