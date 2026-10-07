"""Tests for authentication paths: HMAC, DSN, project_id."""

import hashlib
import hmac
import json
import time
from base64 import b64encode
from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_hmac_auth_valid(client):
    """Valid HMAC signature should be accepted."""
    from app.core.config import settings

    # Need at least one HMAC key configured
    hmac_keys = settings.hmac_keys_dict
    if not hmac_keys:
        pytest.skip("No HMAC keys configured")

    key_id = next(iter(hmac_keys))
    secret = hmac_keys[key_id]

    body = json.dumps({
        "name": "test_event",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {},
    }).encode()

    timestamp = str(int(time.time()))
    message = body + timestamp.encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

    response = await client.post(
        "/api/v1/events/",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-HMAC-Signature": signature,
            "X-Key-ID": key_id,
            "X-Timestamp": timestamp,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["events_received"] == 1


@pytest.mark.asyncio
async def test_hmac_auth_invalid_signature(client):
    """Invalid HMAC signature should be rejected."""
    body = json.dumps({
        "name": "test_event",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {},
    }).encode()

    timestamp = str(int(time.time()))

    response = await client.post(
        "/api/v1/events/",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-HMAC-Signature": "invalid_signature",
            "X-Key-ID": "nonexistent_key",
            "X-Timestamp": timestamp,
        },
    )
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_hmac_auth_expired_timestamp(client):
    """Expired timestamp should be rejected."""
    from app.core.config import settings

    hmac_keys = settings.hmac_keys_dict
    if not hmac_keys:
        pytest.skip("No HMAC keys configured")

    key_id = next(iter(hmac_keys))
    secret = hmac_keys[key_id]

    body = json.dumps({
        "name": "test_event",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {},
    }).encode()

    # 10 minutes ago — outside 5-minute window
    timestamp = str(int(time.time()) - 600)
    message = body + timestamp.encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

    response = await client.post(
        "/api/v1/events/",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-HMAC-Signature": signature,
            "X-Key-ID": key_id,
            "X-Timestamp": timestamp,
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dsn_auth_valid(client, sample_app):
    """Valid DSN basic auth should be accepted."""
    body = json.dumps({
        "name": "test_event",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {},
    }).encode()

    auth_string = f"{sample_app.public_id}:"
    auth_header = "Basic " + b64encode(auth_string.encode()).decode()

    response = await client.post(
        f"/api/v1/events/{sample_app.project_id}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": auth_header,
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dsn_auth_invalid_project(client):
    """Invalid project_id should return 404."""
    body = json.dumps({
        "name": "test_event",
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": {},
    }).encode()

    response = await client.post(
        "/api/v1/events/nonexistent_project",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_requires_auth(client):
    """Dashboard pages should require authentication."""
    response = await client.get("/dashboard/overview", follow_redirects=False)
    # Should redirect to login or return 401/403
    assert response.status_code in (302, 401, 403)
