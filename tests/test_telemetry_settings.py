"""Tracing is configured from Settings, like every other value (LUXANALYTI-87): the endpoint and
service name come from `settings`, so a `.env` file reaches them and the default service name is
the one Settings declares, not a second default in telemetry."""

from typing import Any

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.trace import TracerProvider

from app.core import telemetry
from app.core.config import Settings, settings


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> list[TracerProvider]:
    """setup_telemetry's process-wide side effects, recorded instead of done."""
    installed: list[TracerProvider] = []
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    monkeypatch.setattr(metrics, "set_meter_provider", lambda _provider: None)
    monkeypatch.setattr(telemetry, "instrument_database", lambda _engine: None)
    monkeypatch.setattr(
        telemetry, "OTLPSpanExporter", lambda **_kwargs: _NullExporter()
    )
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    return installed


class _NullExporter:
    def export(self, _spans: Any) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, _timeout_millis: int = 0) -> bool:
        return True


def test_the_endpoint_and_service_name_come_from_settings(
    monkeypatch: pytest.MonkeyPatch, providers: list[TracerProvider]
) -> None:
    monkeypatch.setattr(
        settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317"
    )
    monkeypatch.setattr(settings, "OTEL_SERVICE_NAME", "from-settings")
    telemetry.setup_telemetry()
    assert [p.resource.attributes["service.name"] for p in providers] == [
        "from-settings"
    ]


def test_the_default_service_name_is_the_settings_default(
    monkeypatch: pytest.MonkeyPatch, providers: list[TracerProvider]
) -> None:
    monkeypatch.setattr(
        settings, "OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317"
    )
    monkeypatch.setattr(
        settings,
        "OTEL_SERVICE_NAME",
        Settings.model_fields["OTEL_SERVICE_NAME"].default,
    )
    telemetry.setup_telemetry()
    assert [p.resource.attributes["service.name"] for p in providers] == [
        "luxanalytics"
    ]


def test_no_endpoint_in_settings_means_tracing_stays_off(
    monkeypatch: pytest.MonkeyPatch, providers: list[TracerProvider]
) -> None:
    monkeypatch.setattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", None)
    telemetry.setup_telemetry()
    assert providers == []
