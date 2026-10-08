"""Compressed ingest (LUXANALYTI-69): `Content-Encoding: deflate` is zlib (RFC 1950) per RFC 9110,
which SDK >= 1.1.0 sends; raw DEFLATE (RFC 1951) is still accepted from older SDK builds."""

import json
import logging
import zlib

import pytest
from httpx import AsyncClient

from tests.test_ingest_http import EVENT, PROJECT, _dsn_auth

BODY = json.dumps({"events": [EVENT]}).encode()


def _raw_deflate(data: bytes) -> bytes:
    c = zlib.compressobj(wbits=-15)
    return c.compress(data) + c.flush()


async def _post(client: AsyncClient, body: bytes) -> int:
    response = await client.post(
        f"/api/v1/events/{PROJECT}",
        content=body,
        headers={
            **_dsn_auth(),
            "Content-Type": "application/json",
            "Content-Encoding": "deflate",
        },
    )
    return response.status_code


@pytest.mark.db
async def test_a_zlib_body_ingests(client: AsyncClient, sample_app: object) -> None:
    assert zlib.compress(BODY)[:1] == b"\x78"  # the zlib header the current SDK sends
    assert await _post(client, zlib.compress(BODY)) == 200


@pytest.mark.db
async def test_a_raw_deflate_body_from_an_old_sdk_ingests(
    client: AsyncClient, sample_app: object, caplog: pytest.LogCaptureFixture
) -> None:
    raw = _raw_deflate(BODY)
    with caplog.at_level(
        logging.DEBUG, logger="app.core.middleware.request_middleware"
    ):
        assert await _post(client, raw) == 200
    logged = " ".join(str(vars(r)) for r in caplog.records)
    assert "legacy" in logged  # the fallback is visible...
    assert raw[:8].hex() not in logged  # ...and carries no payload bytes


@pytest.mark.db
async def test_undecodable_deflate_is_a_400(
    client: AsyncClient, sample_app: object
) -> None:
    assert await _post(client, b"not compressed at all") == 400


@pytest.mark.db
async def test_an_empty_compressed_body_is_not_a_500(
    client: AsyncClient, sample_app: object
) -> None:
    assert await _post(client, zlib.compress(b"")) == 400
