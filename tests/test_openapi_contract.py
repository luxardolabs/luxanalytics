"""The published OpenAPI document describes the ingest body, so a client's contract check (the
iOS SDK's) can validate against it. The ingest routes parse the raw body themselves, so this is
declared, not inferred: the test proves it is present and every reference resolves."""

from typing import Any

from app.main import create_application

INGEST_PATHS = (
    "/api/v1/events/",
    "/api/v1/events/public",
    "/api/v1/events/{project_id}",
)


def _refs(node: Any) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if "$ref" in node else []
        return found + [r for v in node.values() for r in _refs(v)]
    if isinstance(node, list):
        return [r for v in node for r in _refs(v)]
    return []


def test_every_ingest_route_publishes_its_body() -> None:
    spec = create_application().openapi()
    schemas = spec["components"]["schemas"]
    for path in INGEST_PATHS:
        body = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]
        refs = _refs(body["schema"])
        assert "#/components/schemas/EventCreate" in refs
        assert "#/components/schemas/BatchEventRequest" in refs
    for ref in _refs(spec):
        assert ref.removeprefix("#/components/schemas/") in schemas, ref


def test_the_event_schema_names_the_wire_keys() -> None:
    event = create_application().openapi()["components"]["schemas"]["EventCreate"]
    assert {"id", "name", "timestamp", "user_id", "session_id", "metadata"} <= set(
        event["properties"]
    )
    assert set(event["required"]) == {"name", "timestamp"}
    response = create_application().openapi()["components"]["schemas"]["EventResponse"]
    assert "duplicates" in response["properties"]
