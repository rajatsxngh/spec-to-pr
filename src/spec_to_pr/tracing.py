"""Tracing (N4): OpenTelemetry spans per run, model call and tool call.

Off unless OTEL_EXPORTER_OTLP_ENDPOINT (or ..._TRACES_ENDPOINT) is set and the `tracing` extra is
installed (`uv sync --extra tracing`). Any OTLP backend works, including Langfuse and Phoenix:

    # Langfuse
    OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
    OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64 of public_key:secret_key>"
    # Phoenix (local)
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006

Span attributes follow the OpenTelemetry GenAI conventions (gen_ai.*) plus spec_to_pr.* extras.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import Any, Iterator

try:
    from opentelemetry import trace as _trace
except ImportError:  # the tracing extra isn't installed
    _trace = None


def setup_tracing(service_name: str = "spec-to-pr") -> bool:
    """Install an OTLP exporter if configured. Returns True when spans will be exported."""
    if not (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")):
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        print("Tracing endpoint set but the tracing extra isn't installed: uv sync --extra tracing", file=sys.stderr)
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))  # endpoint and headers from env
    _trace.set_tracer_provider(provider)
    return True


class _Span:
    def __init__(self, span: Any = None):
        self._span = span

    def set(self, **attrs: Any) -> None:
        if self._span is None:
            return
        for k, v in attrs.items():
            if v is None:
                continue
            if not isinstance(v, (str, bool, int, float)):
                v = str(v)
            self._span.set_attribute(k, v)


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[_Span]:
    """A span when OpenTelemetry is available (a no-op until a provider is set), else nothing."""
    if _trace is None:
        yield _Span()
        return
    with _trace.get_tracer("spec_to_pr").start_as_current_span(name) as s:
        wrapped = _Span(s)
        wrapped.set(**attrs)
        yield wrapped
