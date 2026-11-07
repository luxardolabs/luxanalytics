# luxarch:telemetry asset v2 - DO NOT edit this marker line; it is how repo.emitted_assets_current knows your copy is current. Re-emit with `luxarch --emit telemetry`.
"""The fleet's ONE tracing setup: an OpenTelemetry provider configured from the standard `OTEL_*`
environment, the span helpers the `obs.*` rules name, redaction at the exporter, and trace ids on log
lines.

Use, once per process, in the entry point (`app/main.py`'s lifespan, a processor's `__main__`), after
`configure_logging()` and before serving:

    from app.core.logging_config import configure_logging
    from app.core.telemetry import configure_tracing, current_trace_ids, shutdown_tracing

    configure_logging(service="demo", version=settings.version, trace_ids=current_trace_ids)
    configure_tracing("demo-portal", version=settings.version, environment=settings.environment)
    ...                                   # the app's instrumentors go here, right after it
    shutdown_tracing()                    # in the lifespan's teardown

Then, in services (`luxarch --playbook observability`):

    with create_service_span("OrderCoreService", "place_order", order_id=order_id):
        ...
    with create_client_span("StripeClient", "create_payment_intent"):
        ...                               # an outbound call nothing instruments
    enrich_span_from_user(current_user)   # first statement of a handler taking the user

CONFIGURATION is the OpenTelemetry environment, read by the SDK and the exporter themselves, so an
operator tunes tracing without a code change and nothing here imports the app's settings:
`OTEL_EXPORTER_OTLP_ENDPOINT` (the collector's base URL, `http://otel-collector:4318`; the exporter
adds `/v1/traces`) or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (the full URL), `OTEL_EXPORTER_OTLP_HEADERS`,
`OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER` / `_ARG` (default
`parentbased_always_on`), `OTEL_BSP_*`. Spans are exported only when an endpoint is set, and never when
`OTEL_TRACES_EXPORTER` names no `otlp` exporter (`none`, `console`) or `OTEL_SDK_DISABLED=true`: a
laptop or a test run with no collector exports nothing and retries nothing. If another component already
installed a tracer provider (the `opentelemetry-instrument` launcher, a library), nothing is installed
and an error is logged: its spans would bypass this module's redaction. The protocol is OTLP over HTTP (`http/protobuf`), the
specification's default: the gRPC exporter costs 19 MB of grpc and ~0.5 s of import for nothing a
collector needs. Every collector accepts both on 4318 (HTTP) and 4317 (gRPC); point the endpoint at 4318.

THE SPAN HELPERS never check configuration. With tracing off the API hands out non-recording spans at
next to no cost, so `create_service_span` always yields a `Span` (never None) and callers never branch.

EXCEPTIONS are recorded once per span. `obs.record_before_raise` requires `record_exception_in_span(e)`
before `raise`, and OpenTelemetry's span records an escaping exception again by default: two identical
events on one span (measured, SDK 1.45.1), which the semantic conventions say not to do. These helpers
record an escaping `Exception` only when nothing recorded it on that span yet, mark the span ERROR with
`error.type`, and leave cancellation (`BaseException`) alone; an exception group fails the span when it
holds a real error. On a span an instrumentor owns (FastAPI's server span), the instrumentor records the
escaping exception again: the exporter keeps one event, with the longer stack trace. (Two different
exceptions of one type, with one message, raised at one line, recorded and then escaping one span, read
as one.) The status
description is the exception's type; its message is on the exception event, where the redactor sees it.

REDACTION happens at the exporter, the one place every span crosses, including spans recorded by
instrumentation the app does not own. Spans carry templates, not values: a URL or path attribute
(`url.full`, `http.url`, `url.path`, `http.target`), and the span's name and its links' attributes, are
exported with the span's route template in place of its path (`/auth/magic/S3CR3T` becomes
`/auth/magic/{token}`), and a span with no route (a 404, a redirect, an outbound call) keeps no path.
The route is the instrumentation's: FastAPIInstrumentor on FastAPI before 0.137.2 reports a `Host`
route's raw path as its route, so that path is exported as written. Query values are always dropped, keys kept
(`?page=REDACTED&token=REDACTED`); user:password and fragments are never exported. Nothing guesses which
values are secret, so there is nothing to slip past. The concrete ids a trace needs ride as span
attributes, which `obs.entity_id_forwarded` already requires. A value that cannot be parsed is replaced
whole, and never costs the batch its other spans.

Free text (an exception's message and stack trace, the status description, a captured header) is
exported as written, exactly as the logging module writes it: pass `configure_tracing(redact=scrub)`,
the same `Redactor` `configure_logging(redact=)` takes, and one scrubber serves logs and traces. It sees
span attributes, event attributes and the status description; a redactor that raises fails closed (the
fields are dropped, never exported unscrubbed).

An exception's text cannot be scrubbed reliably: its message is free text, and a message can hold a line
shaped exactly like a traceback frame, so no filter of the formatted text can tell them apart. With
`configure_tracing(exception_text="withhold")` no exception text is exported at all. An exception these
helpers record is built from the exception object, not from its text: `exception.message` is its type,
and `exception.stacktrace` lists, for it and for each exception chained to it (`__cause__`,
`__context__`) or held in its group, the type and each frame's file, line and function. An exception
event recorded by anything else (an instrumentor's, on the span it owns) keeps only its type, and is
dropped where these helpers recorded the same exception on the span; an error status description is
replaced by the span's `error.type` (or its last exception event's type), never read for one. A second
`configure_tracing(exception_text="withhold")` after tracing was configured exporting the text is
refused.

TRACE IDS ON LOG LINES belong to the fleet logging module, the one logging setup: pass
`configure_logging(..., trace_ids=current_trace_ids)`, and a line logged inside a span carries
`trace_id` / `span_id`; outside a span, neither (never a "0" id). Do not add `LoggingInstrumentor`: it
installs a second root handler and stamps "0" on every line logged outside a span.

INSTRUMENTORS (FastAPI, SQLAlchemy, httpx, redis ...) are called by the entry point, right after
`configure_tracing()`, not here: each is a dependency only some apps have, its arguments are app facts
(`engine=<engine>.sync_engine`), and `repo.otel_instrumentors_effective` judges them where the app
starts (`luxarch --playbook otel-instrumentors`).

CONTEXT crosses `asyncio.create_task` and `asyncio.to_thread` by itself; `loop.run_in_executor` and a
raw thread pool do not (run the callable under `contextvars.copy_context().run`). A scheduled job is a
trace of its own: `create_service_span("Jobs", "<name>", root=True)` starts a new trace, linked to the
span that dispatched it.

TESTS: set `os.environ["OTEL_TRACES_EXPORTER"] = "none"` in `conftest.py` before the app is imported,
so an endpoint inherited from compose exports nothing. To assert on spans, one session-scoped fixture
calls `configure_tracing(..., exporter=InMemorySpanExporter())` (spans are then exported synchronously)
and each test calls `exporter.clear()`. The app's own `configure_tracing` then returns that provider,
and its `shutdown_tracing` only flushes it, so the next test still records. The provider can be set once
per process: never build a `TracerProvider` in a test.

Resource attributes: `service.name` (unless `OTEL_SERVICE_NAME` or `OTEL_RESOURCE_ATTRIBUTES` sets it,
so the operator wins), `service.version`, `deployment.environment.name`, `service.namespace` when given.
Name each deployable unit (`<app>-portal`, `<app>-processor`) and pass the app as `namespace=`.

Migration keys, written for one version so saved searches and dashboards can move: the span attribute
`service.name` (the class name; it shadows the resource's `service.name` in unscoped queries, so search
on `code.function.name` or the span name), the resource's `deployment.environment`, and `user.id` beside
`enduser.id`.

Dependencies: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`.
Nothing else, and nothing from the app.

Emitted, not shared: `luxarch --emit telemetry`. Drop it in at app/core/telemetry.py. Nothing imports
luxarch at runtime. Do not hand-edit: re-emit to update; repo.emitted_assets_current reds a stale copy.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar, Literal, Protocol, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Link, Span, SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

_tracer = trace.get_tracer(__name__)

# A repo's redactor, the same contract as the fleet logging module's: a flat mapping in, the mapping to
# export out.
Redactor = Callable[[dict[str, Any]], Mapping[str, Any]]

# Span attributes that hold a URL, a path (with or without its query) or a query.
_URL_KEYS = frozenset({"url.full", "http.url"})
_PATH_KEYS = frozenset({"url.path", "http.target"})
_QUERY_KEYS = frozenset({"url.query"})
_QUERY_SEPARATORS = re.compile(r"[&;]")
_REDACTED = "REDACTED"

# The key on an exception object listing the span ids it was recorded on, so it is recorded once each.
_RECORDED = "_otel_recorded_on"
# Marks an exception event these helpers recorded, so the exporter can drop an instrumentor's second
# recording of the same exception. Removed before export.
_OURS = "telemetry.recorded_by_helper"

# What `configure_tracing(exception_text=)` accepts.
ExceptionText = Literal["export", "withhold"]
_EXCEPTION_TEXT: frozenset[str] = frozenset({"export", "withhold"})


class _Tracing:
    """This process's tracing: the provider configure_tracing installed, and whether a test injected
    its exporter (then shutdown_tracing only flushes)."""

    lock: ClassVar[threading.Lock] = threading.Lock()
    provider: ClassVar[TracerProvider | None] = None
    injected: ClassVar[bool] = False
    withhold_exception_text: ClassVar[bool] = False


class HasId(Protocol):
    """What `enrich_span_from_user` needs from the caller object: its opaque id."""

    @property
    def id(self) -> object: ...


# --------------------------------------------------------------------------------------------- set-up


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _off_reason() -> str | None:
    """Why spans are not exported, or None when they are."""
    if _env("OTEL_SDK_DISABLED").lower() == "true":
        return "OTEL_SDK_DISABLED=true"
    exporters = {
        n.strip().lower() for n in _env("OTEL_TRACES_EXPORTER").split(",") if n.strip()
    }
    if exporters and "otlp" not in exporters:
        return f"OTEL_TRACES_EXPORTER={_env('OTEL_TRACES_EXPORTER')}: names no otlp exporter"
    if not (
        _env("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or _env("OTEL_EXPORTER_OTLP_ENDPOINT")
    ):
        return "no OTEL_EXPORTER_OTLP_ENDPOINT"
    protocol = _env("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL") or _env(
        "OTEL_EXPORTER_OTLP_PROTOCOL"
    )
    if protocol and protocol.lower() != "http/protobuf":
        return f"OTEL_EXPORTER_OTLP_PROTOCOL={protocol}: this module exports http/protobuf (port 4318)"
    return None


def _service_name_from_env() -> bool:
    if _env("OTEL_SERVICE_NAME"):
        return True
    pairs = (p.partition("=") for p in _env("OTEL_RESOURCE_ATTRIBUTES").split(","))
    return any(k.strip() == "service.name" and v.strip() for k, _, v in pairs)


def configure_tracing(
    service_name: str,
    *,
    version: str,
    environment: str | None = None,
    namespace: str | None = None,
    redact: Redactor | None = None,
    exporter: SpanExporter | None = None,
    exception_text: ExceptionText = "export",
) -> TracerProvider | None:
    """Install this process's tracer provider. Call once, in the entry point; a second call returns the
    first provider.

    Returns None, and installs nothing, when spans would not be exported (see the module docstring) or
    another component already installed a provider, unless `exporter` is given: a test's in-memory
    exporter, used synchronously, whatever the environment says. Every exporter is wrapped by the
    redacting one. `exception_text="withhold"` exports no exception message or formatted traceback
    (see the module docstring); any other value than "export" or "withhold" is refused.
    """
    if exception_text not in _EXCEPTION_TEXT:
        raise ValueError(
            f"exception_text={exception_text!r}: expected 'export' or 'withhold'"
        )
    withhold = exception_text == "withhold"
    with _Tracing.lock:
        if _Tracing.provider is not None:
            if withhold and not _Tracing.withhold_exception_text:
                # A privacy mode asked for and not applied would export what it was asked to withhold.
                raise ValueError(
                    "exception_text='withhold': tracing is already configured exporting exception "
                    "text, by an earlier configure_tracing call; pass it there"
                )
            return _Tracing.provider
        if exporter is None:
            reason = _off_reason()
            if reason is not None:
                logger.info(
                    "tracing_off",
                    extra={"reason": reason, "service_name": service_name},
                )
                return None
        if isinstance(trace.get_tracer_provider(), TracerProvider):
            logger.error(
                "tracing_not_configured",
                extra={
                    "reason": "another component already installed a tracer provider; its spans "
                    "would bypass this module's redaction",
                    "service_name": service_name,
                },
            )
            return None

        attributes: dict[str, str] = {"service.version": version}
        if not _service_name_from_env():
            attributes["service.name"] = service_name
        if environment:
            attributes["deployment.environment.name"] = environment
            attributes["deployment.environment"] = environment
        if namespace:
            attributes["service.namespace"] = namespace

        provider = TracerProvider(resource=Resource.create(attributes))
        if exporter is None:
            provider.add_span_processor(
                BatchSpanProcessor(
                    RedactingSpanExporter(
                        OTLPSpanExporter(), redact, withhold_exception_text=withhold
                    )
                )
            )
        else:
            provider.add_span_processor(
                SimpleSpanProcessor(
                    RedactingSpanExporter(
                        exporter, redact, withhold_exception_text=withhold
                    )
                )
            )
        trace.set_tracer_provider(provider)
        if trace.get_tracer_provider() is not provider:
            provider.shutdown()
            logger.error(
                "tracing_not_configured",
                extra={
                    "reason": "the global tracer provider was already set",
                    "service_name": service_name,
                },
            )
            return None
        _Tracing.provider = provider
        _Tracing.injected = exporter is not None
        _Tracing.withhold_exception_text = withhold
    logger.info(
        "tracing_configured",
        extra={
            "service_name": service_name,
            "exporter": type(exporter).__name__ if exporter else "OTLPSpanExporter",
        },
    )
    return provider


def shutdown_tracing(timeout_millis: int = 5000) -> None:
    """Flush the spans still queued and shut the provider down. Call from the process's teardown.
    Safe to call when tracing is off, and more than once. A provider a test built with `exporter=` is
    only flushed: the test that built it owns it."""
    with _Tracing.lock:
        provider = _Tracing.provider
        if provider is None:
            return
        provider.force_flush(timeout_millis)
        if _Tracing.injected:
            return
        _Tracing.provider = None
    provider.shutdown()


def current_trace_ids() -> tuple[str, str] | None:
    """The current span's (trace id, span id) as hex, or None outside a span. The fleet logging
    module's `configure_logging(trace_ids=)` source."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


# ----------------------------------------------------------------------------------------------- spans

_Scalar = str | bool | int | float


def _coerce(value: object) -> _Scalar | tuple[_Scalar, ...] | None:
    """A span attribute value: scalars as they are, ids, dates, enums and decimals as text, a sequence
    of scalars as a tuple. None is dropped."""
    if value is None:
        return None
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, UUID | datetime | date | time | Decimal):
        return str(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        items = [_coerce(v) for v in value]
        if all(isinstance(v, str | bool | int | float) for v in items):
            return tuple(cast(list[_Scalar], items))
    return str(value)


def _attributes(
    attributes: Mapping[str, object],
) -> dict[str, _Scalar | tuple[_Scalar, ...]]:
    out: dict[str, _Scalar | tuple[_Scalar, ...]] = {}
    for key, value in attributes.items():
        coerced = _coerce(value)
        if coerced is not None:
            out[key] = coerced
    return out


def _recorded_on(exc: BaseException, span: Span) -> bool:
    span_id = span.get_span_context().span_id
    seen = exc.__dict__.get(_RECORDED)
    return isinstance(seen, set) and span_id in seen


def _mark_recorded(exc: BaseException, span: Span) -> None:
    seen = exc.__dict__.setdefault(_RECORDED, set())
    if isinstance(seen, set):
        seen.add(span.get_span_context().span_id)


def _fail(
    span: Span, exc: BaseException, attributes: Mapping[str, object] | None = None
) -> None:
    """Mark `span` failed by `exc`, recording the exception event once."""
    if not span.is_recording():
        return
    error_type = type(exc).__qualname__
    span.set_attribute("error.type", error_type)
    span.set_status(Status(StatusCode.ERROR, error_type))
    if not _recorded_on(exc, span):
        # The marker only on spans of this module's provider: its exporter strips it before export.
        provider = _Tracing.provider
        ours = (
            provider is not None
            and getattr(span, "resource", None) is provider.resource
        )
        marker = {_OURS: True} if ours else {}
        extra = _attributes(attributes or {})
        if ours and _Tracing.withhold_exception_text:
            span.add_event(
                "exception",
                {
                    **extra,
                    "exception.type": _type_name(exc),
                    "exception.message": _type_name(exc),
                    "exception.stacktrace": _frames_only(exc),
                    **marker,
                },
            )
        else:
            span.record_exception(exc, attributes={**extra, **marker})
        _mark_recorded(exc, span)


def _type_name(exc: BaseException) -> str:
    cls = type(exc)
    return (
        cls.__qualname__
        if cls.__module__ == "builtins"
        else f"{cls.__module__}.{cls.__qualname__}"
    )


def _frames_only(exc: BaseException) -> str:
    """`exc`'s traceback without a word of its text: for it and each exception chained to it or held
    in its group, the type and each frame's file, line and function. Read from the exception and its
    frames' code objects, so nothing the exception's message says can appear in it."""
    lines: list[str] = []
    seen: set[int] = set()
    todo: list[tuple[str, BaseException]] = [("", exc)]
    while todo:
        how, e = todo.pop(0)
        if id(e) in seen:
            continue
        seen.add(id(e))
        lines.append(f"{how}{_type_name(e)}")
        # The frames' code objects, never their source lines (which `extract_tb` reads from disk).
        for frame, lineno in traceback.walk_tb(e.__traceback__):
            code = frame.f_code
            lines.append(
                f'  File "{code.co_filename}", line {lineno}, in {code.co_name}'
            )
        if e.__cause__ is not None:
            todo.append(("caused by: ", e.__cause__))
        elif e.__context__ is not None and not e.__suppress_context__:
            todo.append(("during handling of: ", e.__context__))
        if isinstance(e, BaseExceptionGroup):
            todo.extend(("in group: ", member) for member in e.exceptions)
    return "\n".join(lines)


@contextmanager
def _failures_recorded(span: Span) -> Iterator[None]:
    """Mark `span` failed by an escaping exception, or by a group holding one; cancellation is not a
    failure."""
    try:
        yield
    except Exception as exc:
        _fail(span, exc)
        raise
    except BaseExceptionGroup as group:
        if group.split(Exception)[0] is not None:
            _fail(span, group)
        raise


@contextmanager
def create_service_span(
    service_name: str, method_name: str, /, *, root: bool = False, **attributes: object
) -> Iterator[Span]:
    """A span for one service method, named `<service_name>.<method_name>`. `obs.method_span` requires
    it on every core and view service method that awaits, and on every public one.

    `service_name` is the literal enclosing class name (`obs.span_name_matches_class`). Keyword
    arguments become span attributes under their own names; the entity ids the repo lists in
    `[observability].entity_id_params` ride here (`obs.entity_id_forwarded`). `root=True` starts a new
    trace, linked to the span that was current: a scheduled job's first statement
    (`obs.job_root_span`).

    Always yields a Span, non-recording when tracing is off.
    """
    function = f"{service_name}.{method_name}"
    attrs = _attributes({"service.name": service_name, **attributes})
    attrs["code.function.name"] = function
    links: list[Link] = []
    parent: Context | None = None
    if root:
        parent = Context()
        dispatcher = trace.get_current_span().get_span_context()
        if dispatcher.is_valid:
            links.append(Link(dispatcher))
    with (
        _tracer.start_as_current_span(
            function,
            context=parent,
            kind=SpanKind.INTERNAL,
            attributes=attrs,
            links=links,
            record_exception=False,
            set_status_on_exception=False,
        ) as span,
        _failures_recorded(span),
    ):
        yield span


@contextmanager
def create_client_span(
    service_name: str, method_name: str, /, **attributes: object
) -> Iterator[Span]:
    """A CLIENT span for an outbound call that no instrumentor records (`obs.client_span_kind`): a
    vendor SDK on its own transport, a raw socket. Named `<service_name>.<method_name>`, like a service
    span, so the dependency map and its latency are attributed to the call."""
    function = f"{service_name}.{method_name}"
    with (
        _tracer.start_as_current_span(
            function,
            kind=SpanKind.CLIENT,
            attributes={**_attributes(attributes), "code.function.name": function},
            record_exception=False,
            set_status_on_exception=False,
        ) as span,
        _failures_recorded(span),
    ):
        yield span


def record_exception_in_span(exc: BaseException, /, **attributes: object) -> None:
    """Record `exc` on the current span and mark it ERROR. `obs.record_exception` requires it in a
    spanned `try`/`except`, and `obs.record_before_raise` requires it BEFORE the `raise`: after it, the
    line never runs. Re-raising it afterwards does not record it twice. No-op with tracing off."""
    _fail(trace.get_current_span(), exc, attributes)


def enrich_span_from_user(user: HasId | None) -> None:
    """Put the caller's opaque id on the current (HTTP) span: `enduser.id`, plus `tenant.id` when the
    object has a `tenant_id`. `obs.route_enrich_user` requires it in a handler taking the user, within
    its first three statements (`obs.enrich_early`). Never an email, a name or a picture: a trace
    backend is not a place for personal data."""
    if user is None:
        return
    span = trace.get_current_span()
    if not span.is_recording():
        return
    user_id = str(user.id)
    span.set_attribute("enduser.id", user_id)
    span.set_attribute("user.id", user_id)
    tenant_id = getattr(user, "tenant_id", None)
    if tenant_id is not None:
        span.set_attribute("tenant.id", str(tenant_id))


def set_span_attribute(key: str, value: object) -> None:
    """Set one attribute on the current span. No-op with tracing off, or for a None value."""
    span = trace.get_current_span()
    coerced = _coerce(value)
    if coerced is not None and span.is_recording():
        span.set_attribute(key, coerced)


def add_event_to_span(name: str, /, **attributes: object) -> None:
    """Add a timestamped event to the current span. No-op with tracing off."""
    span = trace.get_current_span()
    if span.is_recording():
        span.add_event(name, _attributes(attributes))


# ------------------------------------------------------------------------------------------- redaction


def _keys_only(query: str) -> str:
    """`query` with every value dropped and every key kept. A piece with no `=` may be the value
    itself, so it goes too."""
    if not query:
        return query
    return "&".join(
        f"{piece.partition('=')[0]}={_REDACTED}" if "=" in piece else _REDACTED
        for piece in _QUERY_SEPARATORS.split(query)
        if piece
    )


def _template_path(path: str, route: str | None) -> str:
    """The route template in place of `path`; with no route, no path."""
    if route:
        return route
    return path if path in ("", "/") else f"/{_REDACTED}"


def _template_url(url: str, route: str | None) -> str:
    """`url` as scheme, host, port, the route template and the query's keys: nothing a caller wrote."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    netloc = f"{host}:{port}" if port is not None else host
    path = _template_path(parts.path, route)
    query = _keys_only(parts.query)
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def _templated(attributes: Mapping[str, Any], route: str | None) -> dict[str, Any]:
    out = dict(attributes)
    for key, value in out.items():
        if not isinstance(value, str):
            continue
        try:
            if key in _URL_KEYS:
                out[key] = _template_url(value, route)
            elif key in _PATH_KEYS:
                path, question, query = value.partition("?")
                templated = _template_path(path, route)
                out[key] = f"{templated}?{_keys_only(query)}" if question else templated
            elif key in _QUERY_KEYS:
                out[key] = _keys_only(value)
        except ValueError:
            out[key] = _REDACTED
    return out


def _through(redact: Redactor | None, attributes: Mapping[str, Any]) -> dict[str, Any]:
    """`attributes` through the repo's redactor. Fails closed: if it raises or returns something that
    is not a mapping, every field is dropped and the reason kept."""
    if redact is None or not attributes:
        return dict(attributes)
    try:
        out = cast(object, redact(dict(attributes)))
    except Exception as exc:
        return {"redaction_error": type(exc).__qualname__}
    if not isinstance(out, Mapping):
        return {"redaction_error": f"returned {type(out).__name__}, not a mapping"}
    return {k: v for k, v in out.items() if _coerce(v) is not None}


def _raise_site(stacktrace: object) -> str:
    """The innermost frame line of a formatted traceback: where the exception was raised."""
    if not isinstance(stacktrace, str):
        return ""
    frames = [line for line in stacktrace.splitlines() if line.startswith("  File ")]
    return frames[-1] if frames else ""


def _without_exception_text(events: Sequence[Event]) -> list[Event]:
    """The span's events with no exception text: an exception event these helpers recorded (built
    from the exception object) as it is, any other exception event reduced to its type, and dropped
    where a helper's event records the same exception (its type, raised at the same frame; a helper's
    event with no frame yet matches on type)."""
    helpers: set[tuple[object, str]] = set()
    for e in events:
        attrs = e.attributes or {}
        if e.name == "exception" and attrs.get(_OURS) is not None:
            helpers.add(
                (
                    attrs.get("exception.type"),
                    _own_raise_site(attrs.get("exception.stacktrace")),
                )
            )
    out: list[Event] = []
    for event in events:
        attrs = dict(event.attributes or {})
        mine = attrs.pop(_OURS, None) is not None
        if event.name == "exception" and not mine:
            kind = attrs.get("exception.type")
            site = _raise_site(attrs.get("exception.stacktrace"))
            if (kind, site) in helpers or (kind, "") in helpers:
                continue
            attrs = {
                k: v
                for k, v in attrs.items()
                if k in ("exception.type", "exception.escaped")
            }
            attrs["exception.message"] = str(kind or "")
        out.append(Event(event.name, attrs, event.timestamp))
    return out


def _own_raise_site(stacktrace: object) -> str:
    """The innermost frame of the exception itself in a helper's `_frames_only` text: the last frame
    line before the first chained or grouped exception's block."""
    if not isinstance(stacktrace, str):
        return ""
    site = ""
    for line in stacktrace.splitlines()[1:]:
        if not line.startswith("  File "):
            break
        site = line
    return site


def _error_type(span: ReadableSpan) -> str | None:
    """The type a failed span's description may name when exception text is withheld: its
    `error.type`, else the type of its last exception event. Never parsed out of the description,
    which an instrumentor may set to the exception's message alone."""
    own = (span.attributes or {}).get("error.type")
    if isinstance(own, str):
        return own
    for event in reversed(span.events):
        kind = (event.attributes or {}).get("exception.type")
        if event.name == "exception" and isinstance(kind, str):
            return kind
    return None


def _one_per_exception(events: Sequence[Event]) -> list[Event]:
    """The span's events, with an instrumentor's second recording of an exception these helpers already
    recorded folded into the first: one event, the longer stack trace. A helper's recording made
    before its first raise has no raise site yet, so it is matched on type and message alone."""
    out: list[Event] = []
    ours: dict[tuple[object, object, str], int] = {}
    for event in events:
        attrs = dict(event.attributes or {})
        mine = attrs.pop(_OURS, None) is not None
        if event.name == "exception":
            kind = (attrs.get("exception.type"), attrs.get("exception.message"))
            site = _raise_site(attrs.get("exception.stacktrace"))
            if mine:
                ours[(*kind, site)] = len(out)
            else:
                at = ours.get((*kind, site), ours.get((*kind, "")))
                if at is not None:
                    kept = out[at]
                    kept_attrs = dict(kept.attributes or {})
                    longer = attrs.get("exception.stacktrace")
                    if isinstance(longer, str) and len(longer) > len(
                        str(kept_attrs.get("exception.stacktrace", ""))
                    ):
                        kept_attrs["exception.stacktrace"] = longer
                        out[at] = Event(kept.name, kept_attrs, kept.timestamp)
                    continue
        out.append(Event(event.name, attrs, event.timestamp))
    return out


def _raw_paths(attributes: Mapping[str, Any]) -> set[str]:
    """Every raw path the span's attributes carry, to find it again in the span's name."""
    paths: set[str] = set()
    for key, value in attributes.items():
        if not isinstance(value, str):
            continue
        try:
            if key in _URL_KEYS:
                paths.add(urlsplit(value).path)
            elif key in _PATH_KEYS:
                paths.add(value.partition("?")[0])
        except ValueError:
            continue
    return {p for p in paths if p not in ("", "/")}


def _templated_name(name: str, attributes: Mapping[str, Any], route: str | None) -> str:
    """`name` with each raw path from the span's attributes replaced the way the attributes are: the
    ASGI middleware names a span `GET /reset/<token>`."""
    for raw in sorted(_raw_paths(attributes), key=len, reverse=True):
        if raw in name:
            name = name.replace(raw, _template_path(raw, route))
    return name


def _redacted_span(
    span: ReadableSpan, redact: Redactor | None, withhold_exception_text: bool = False
) -> ReadableSpan:
    raw = span.attributes or {}
    own = raw.get("http.route")
    route = own if isinstance(own, str) else None
    status = span.status
    description = status.description
    if withhold_exception_text and status.status_code is StatusCode.ERROR:
        description = _error_type(span) if description else description
    if description and redact is not None:
        scrubbed = _through(redact, {"status.description": description}).get(
            "status.description"
        )
        description = scrubbed if isinstance(scrubbed, str) else None
    return ReadableSpan(
        name=_templated_name(span.name, raw, route),
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_through(redact, _templated(raw, route)),
        events=tuple(
            Event(
                e.name,
                _through(redact, _templated(e.attributes or {}, route)),
                e.timestamp,
            )
            for e in (
                _without_exception_text(span.events)
                if withhold_exception_text
                else _one_per_exception(span.events)
            )
        ),
        links=tuple(
            Link(
                link.context, _through(redact, _templated(link.attributes or {}, None))
            )
            for link in span.links
        ),
        kind=span.kind,
        status=Status(status.status_code, description),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def _withheld(span: ReadableSpan, reason: str) -> ReadableSpan:
    """`span` with nothing it carried: the first word of its name (an HTTP method, a service span's
    name), its timing, kind, status code and the reason."""
    return ReadableSpan(
        name=span.name.partition(" ")[0],
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes={"redaction_error": reason},
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class RedactingSpanExporter(SpanExporter):
    """Wraps the real exporter, so no span reaches it unredacted. A span that cannot be redacted is
    exported without its content; it never costs the batch its other spans."""

    def __init__(
        self,
        inner: SpanExporter,
        redact: Redactor | None = None,
        *,
        withhold_exception_text: bool = False,
    ) -> None:
        self._inner = inner
        self._redact = redact
        self._withhold_exception_text = withhold_exception_text

    def _one(self, span: ReadableSpan) -> ReadableSpan:
        # Any exception, not a list: one escaping here drops the whole batch, every other request's
        # spans with it, and a crafted request could do that on demand.
        try:
            return _redacted_span(span, self._redact, self._withhold_exception_text)
        except Exception as exc:
            return _withheld(span, type(exc).__qualname__)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([self._one(s) for s in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)
