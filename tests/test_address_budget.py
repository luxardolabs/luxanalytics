"""The ingest address budget is one per client: an IPv6 address that carries an IPv4 client (a
translator's NAT64, SIIT or IPv4-compatible form, a Teredo tunnel) is that IPv4 client's budget,
not its translator's /64."""

from collections.abc import Callable
from ipaddress import IPv4Address, IPv6Address

import pytest

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
