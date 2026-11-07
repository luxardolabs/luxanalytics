"""Tracing is configured from Settings and the OTEL environment: the endpoint Settings holds (a `.env`
file included) reaches the environment the tracing module reads, and an endpoint already in the
environment wins. The default service name is the one Settings declares."""

import os

import pytest

from app.core.config import Settings, settings
from app.core.instrumentation import export_otel_settings


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
