import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from spec_to_pr.agent import run_agent  # noqa: E402
from spec_to_pr.config import RunConfig  # noqa: E402
from spec_to_pr.models import ScriptedModel, ScriptedTurn  # noqa: E402
from spec_to_pr.tracing import setup_tracing  # noqa: E402

EXPORTER = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def provider():
    p = TracerProvider()
    p.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace.set_tracer_provider(p)
    yield


def test_spans_per_run_model_call_and_tool_call(planted_sandbox, tmp_path):
    EXPORTER.clear()
    model = ScriptedModel([
        ScriptedTurn(tool_calls=[("run", {"command": "echo hi"})]),
        ScriptedTurn(tool_calls=[("finish", {"summary": "done"})]),
    ])
    run_agent("task", model, planted_sandbox, RunConfig(), tmp_path)
    spans = EXPORTER.get_finished_spans()
    names = [s.name for s in spans]
    assert names.count("chat") == 2 and names.count("execute_tool") == 2 and names.count("spec_to_pr.run") == 1

    run = next(s for s in spans if s.name == "spec_to_pr.run")
    assert run.attributes["spec_to_pr.status"] == "finished"
    assert run.attributes["spec_to_pr.steps"] == 2
    chat = next(s for s in spans if s.name == "chat")
    assert chat.attributes["gen_ai.request.model"] == "claude-opus-5-5"
    assert chat.attributes["gen_ai.usage.input_tokens"] == 1000
    assert chat.attributes["spec_to_pr.cost_usd"] > 0
    assert chat.parent.span_id == run.context.span_id  # nested under the run
    tools = {s.attributes["gen_ai.tool.name"] for s in spans if s.name == "execute_tool"}
    assert tools == {"run", "finish"}


def test_setup_is_off_without_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    assert setup_tracing() is False
