"""Tracing is configured from Settings and the OTEL environment: the endpoint Settings holds (a `.env`
file included) reaches the environment the tracing module reads, and an endpoint already in the
environment wins. The default service name is the one Settings declares."""

import http.server
import os
import threading
from collections.abc import Iterator

import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from app.core.config import Settings, settings
from app.core.instrumentation import export_otel_settings
from tests.test_settings import _settings

OTLP_KEYS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
)


class _Collector(http.server.BaseHTTPRequestHandler):
    """Records the path and headers of each export it receives."""

    seen: list[tuple[str, dict[str, str]]] = []

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length") or 0))
        _Collector.seen.append(
            (self.path, {k.lower(): v for k, v in self.headers.items()})
        )
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Quiet."""


@pytest.fixture
def collector() -> Iterator[int]:
    _Collector.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _export_once(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for name in OTLP_KEYS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "1")
    OTLPSpanExporter().export([])


@pytest.mark.parametrize("suffix", ["", "/", "//"])
def test_an_accepted_base_endpoint_is_exported_to_the_traces_path(
    monkeypatch: pytest.MonkeyPatch, collector: int, suffix: str
) -> None:
    base = f"http://127.0.0.1:{collector}{suffix}"
    assert _settings(OTEL_EXPORTER_OTLP_ENDPOINT=base).OTEL_EXPORTER_OTLP_ENDPOINT

    _export_once(monkeypatch, {"OTEL_EXPORTER_OTLP_ENDPOINT": base})

    assert [path for path, _ in _Collector.seen] == ["/v1/traces"]


def test_accepted_headers_reach_the_collector(
    monkeypatch: pytest.MonkeyPatch, collector: int
) -> None:
    headers = "x-api-key=k3y,authorization=Bearer%20t0k"
    assert _settings(OTEL_EXPORTER_OTLP_HEADERS=headers).OTEL_EXPORTER_OTLP_HEADERS

    _export_once(
        monkeypatch,
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{collector}",
            "OTEL_EXPORTER_OTLP_HEADERS": headers,
        },
    )

    sent = _Collector.seen[0][1]
    assert (sent["x-api-key"], sent["authorization"]) == ("k3y", "Bearer t0k")


def test_the_endpoint_in_settings_reaches_the_otel_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "placeholder")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    monkeypatch.setattr(
        settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318"
    )

    export_otel_settings()

    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://collector:4318"


def test_an_endpoint_already_in_the_environment_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://from-env:4318")
    monkeypatch.setattr(
        settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://from-settings:4318"
    )

    export_otel_settings()

    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://from-env:4318"


def test_no_endpoint_in_settings_leaves_the_environment_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "placeholder")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", None)

    export_otel_settings()

    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ


def test_the_default_service_name_is_luxanalytics() -> None:
    assert Settings.model_fields["OTEL_SERVICE_NAME"].default == "luxanalytics"


def test_the_traces_endpoint_in_settings_reaches_the_otel_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "placeholder")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    monkeypatch.setattr(
        settings,
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://collector:4318/v1/traces",
    )

    export_otel_settings()

    assert (
        os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]
        == "http://collector:4318/v1/traces"
    )


def test_the_headers_in_settings_reach_the_otel_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "placeholder=1")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS")
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_HEADERS", "x-api-key=k3y")

    export_otel_settings()

    assert os.environ["OTEL_EXPORTER_OTLP_HEADERS"] == "x-api-key=k3y"


def test_the_traces_headers_in_settings_reach_the_otel_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exporter reads OTEL_EXPORTER_OTLP_TRACES_HEADERS before OTEL_EXPORTER_OTLP_HEADERS."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "placeholder=1")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS")
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_TRACES_HEADERS", "x-api-key=k3y")

    export_otel_settings()

    assert os.environ["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] == "x-api-key=k3y"
