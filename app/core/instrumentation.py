"""The app's tracing set-up around the emitted module, its instrumentors and its OTEL settings.

`app/core/telemetry.py` (emitted) configures the provider from the standard `OTEL_*` environment;
`app/main.py` calls `configure_app_tracing` and then the instrumentors.
"""

import os

from opentelemetry.context import Context, get_current
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import TracerProvider
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import settings
from app.core.telemetry import configure_tracing

# Span attributes that name an end user or their device. Services forward `user_id` and
# `session_id` to their spans at the source (the observability rules require it); the exporter
# leaves them, and the caller's address and agent, out of every span, event and status.
PERSONAL_ATTRIBUTES = frozenset(
    {
        "user_id",
        "session_id",
        "event_id",
        "enduser.id",
        "user.id",
        "tenant.id",
        "net.peer.ip",
        "net.peer.port",
        "network.peer.address",
        "network.peer.port",
        "client.address",
        "client.port",
        "http.client_ip",
        "http.user_agent",
        "user_agent.original",
    }
)


class _NoCallerContext(TextMapPropagator):
    """Reads no trace context from a request and writes none to a response: a request starts a
    trace of its own, sampled by the app's sampler."""

    def extract(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        getter: Getter[CarrierT] = default_getter,
    ) -> Context:
        """The context the request arrived in, untouched by its headers."""
        return context if context is not None else get_current()

    def inject(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        setter: Setter[CarrierT] = default_setter,
    ) -> None:
        """Nothing is written."""

    @property
    def fields(self) -> set[str]:
        """The headers it reads or writes: none."""
        return set()


# Captured headers (OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*) carry cookies, tokens, the agent
# and the forwarded address under keys of this shape.
CAPTURED_HEADER_PREFIXES = ("http.request.header.", "http.response.header.")


def drop_personal_attributes(fields: dict[str, object]) -> dict[str, object]:
    """`fields` without the end user's id, the caller's address and agent, and any captured header."""
    return {
        k: v
        for k, v in fields.items()
        if k not in PERSONAL_ATTRIBUTES and not k.startswith(CAPTURED_HEADER_PREFIXES)
    }


def configure_app_tracing(
    exporter: SpanExporter | None = None,
) -> SdkTracerProvider | None:
    """The app's one tracing set-up: the provider (None when tracing is off), with no exception
    text and no personal attribute exported, and no trace context taken from the caller.

    A caller's `traceparent` could otherwise pick the trace id (which then lands on log lines)
    and, with its sampled flag off, switch off the spans of its own request.
    """
    provider = configure_tracing(
        settings.OTEL_SERVICE_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        redact=drop_personal_attributes,
        exporter=exporter,
        exception_text="withhold",
    )
    set_global_textmap(_NoCallerContext())
    return provider


def export_otel_settings() -> None:
    """Hand the endpoints Settings holds (a `.env` file included) to the OTEL environment, which
    the tracing module and the exporter read. An endpoint already in the environment wins."""
    for name in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
    ):
        value: str | None = getattr(settings, name)
        if value:
            os.environ.setdefault(name, value)


def instrument_database(
    engine: AsyncEngine, tracer_provider: TracerProvider | None = None
) -> None:
    """A span per statement on this engine (its sync core: an async engine is not instrumentable
    itself). `tracer_provider` defaults to the global one."""
    # skip_dep_check: instrumentor 0.66b1 declares sqlalchemy < 2.1 and so instruments nothing on the
    # fleet's 2.1; the pair is measured and the bypass sanctioned (luxarch --playbook
    # otel-instrumentors). The rule reds it once a covering release is locked: then it comes out.
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine,
        tracer_provider=tracer_provider,
        skip_dep_check=True,
    )
