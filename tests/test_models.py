from types import SimpleNamespace

import pytest

from spec_to_pr.config import ModelSettings
from spec_to_pr.models import AnthropicModel
from spec_to_pr.pricing import UnknownModelPrice, Usage, cost_usd


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_response(model="claude-opus-5-5"):
    return SimpleNamespace(
        model=model,
        stop_reason="tool_use",
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="Looking."),
            SimpleNamespace(type="tool_use", id="toolu_1", name="run", input={"command": "ls"}),
        ],
        usage=SimpleNamespace(
            input_tokens=1000, output_tokens=500, cache_read_input_tokens=4000, cache_creation_input_tokens=0
        ),
        _request_id="req_123",
    )


def client_with(response):
    plain, beta = FakeMessages(response), FakeMessages(response)
    return SimpleNamespace(messages=plain, beta=SimpleNamespace(messages=beta)), plain, beta


def test_request_shape_with_fallback():
    client, plain, beta = client_with(fake_response())
    resp = AnthropicModel(ModelSettings(), client=client).complete("sys", [{"role": "user", "content": "x"}], [])
    k = beta.kwargs
    assert plain.kwargs is None
    assert k["model"] == "claude-opus-5-5"
    assert k["output_config"] == {"effort": "high"}
    assert k["fallbacks"] == "default" and k["betas"] == ["server-side-fallback-2026-07-01"]
    assert k["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in k and "tool_choice" not in k  # adaptive by default; forced tool use is a 400
    assert resp.tool_calls[0].name == "run" and resp.text == "Looking."
    assert len(resp.content) == 3  # thinking block kept for the history
    assert resp.cost_usd == pytest.approx((1000 * 4 + 500 * 20 + 4000 * 0.2) / 1e6)


def test_no_fallback_uses_plain_endpoint():
    client, plain, beta = client_with(fake_response())
    AnthropicModel(ModelSettings(fallback=False), client=client).complete("s", [], [])
    assert beta.kwargs is None and "fallbacks" not in plain.kwargs


def test_cost_uses_model_that_served_the_turn():
    client, _, _ = client_with(fake_response(model="claude-sonnet-5-5"))
    resp = AnthropicModel(ModelSettings(), client=client).complete("s", [], [])
    assert resp.model == "claude-sonnet-5-5"
    assert resp.cost_usd == pytest.approx((1000 * 2 + 500 * 10 + 4000 * 0.2) / 1e6)


def test_unknown_model_price_raises():
    with pytest.raises(UnknownModelPrice):
        cost_usd("not-a-model", Usage())
