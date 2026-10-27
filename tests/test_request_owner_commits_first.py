"""The request owner commits BEFORE the response is sent (`Depends(get_db, scope="function")`,
`luxarch --playbook db-mutations` §1, fw.request_owner_commits_before_response).

Declared as a bare `Depends(get_db)`, FastAPI runs get_db's commit after the response has gone, so a
client could read past its own write. httpx's ASGITransport waits for the whole ASGI call, which hides
that order, so this test drives the app itself and looks at the database, from a separate connection,
at the moment the last byte of the response is sent.
"""

import base64
import json
from collections.abc import MutableMapping
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import create_application
from app.models.app_model import App
from app.models.event_model import Event

pytestmark = pytest.mark.db

PROJECT = "6543210987654321"
PUBLIC_ID = "owner_commits_first_public_id"


async def test_an_ingested_event_is_committed_when_the_response_is_sent(
    owner_session: AsyncSession, separate_connection: AsyncSession
) -> None:
    owner_session.add(
        App(
            id=uuid4(),
            public_id=PUBLIC_ID,
            project_id=PROJECT,
            app_id="owner_commits_first",
            name="Owner commits first",
            organization="Test Org",
            is_active=True,
            app_metadata={},
        )
    )
    await owner_session.commit()

    body = json.dumps(
        {"name": "app_opened", "timestamp": "2026-10-06T12:00:00Z", "metadata": {}}
    ).encode()
    token = base64.b64encode(f"{PUBLIC_ID}:".encode()).decode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": f"/api/v1/events/{PROJECT}",
        "raw_path": f"/api/v1/events/{PROJECT}".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"localhost"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"authorization", f"Basic {token}".encode()),
        ],
        "client": ("198.51.100.20", 50000),
        "server": ("localhost", 443),
    }
    sent_body = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_body
        if sent_body:
            return {"type": "http.disconnect"}
        sent_body = True
        return {"type": "http.request", "body": body, "more_body": False}

    status: list[int] = []
    stored_when_sent: list[int] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])
        elif message["type"] == "http.response.body" and not message.get("more_body"):
            # The client has its whole response now: the event must already be committed.
            stored = await separate_connection.execute(
                select(func.count())
                .select_from(Event)
                .where(Event.app_id == "owner_commits_first")
            )
            stored_when_sent.append(stored.scalar_one())

    await create_application()(scope, receive, send)

    assert status == [200]
    assert stored_when_sent == [1]
