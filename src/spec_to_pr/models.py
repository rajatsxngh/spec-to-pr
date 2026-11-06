"""Model adapter (F11): one interface, swappable models.

`AnthropicModel` calls Claude through the official Anthropic SDK. `ScriptedModel`
replays canned responses so the loop can be tested without an API key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import ModelSettings
from .pricing import Usage, cost_usd


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ModelResponse:
    # Content blocks exactly as returned; appended to history unchanged so thinking
    # blocks stay valid (history is append-only).
    content: list[Any]
    tool_calls: list[ToolCall]
    text: str
    stop_reason: str
    usage: Usage
    model: str  # the model that served the turn (differs after a refusal fallback)
    cost_usd: float
    request_id: str | None = None
    stop_details: dict[str, Any] | None = None


class ModelClient(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse: ...


def _block_type(block: Any) -> str:
    return block["type"] if isinstance(block, dict) else block.type


def _parse_content(content: list[Any]) -> tuple[list[ToolCall], str]:
    calls: list[ToolCall] = []
    texts: list[str] = []
    for block in content:
        kind = _block_type(block)
        if kind == "tool_use":
            if isinstance(block, dict):
                calls.append(ToolCall(block["id"], block["name"], dict(block["input"])))
            else:
                calls.append(ToolCall(block.id, block.name, dict(block.input)))
        elif kind == "text":
            texts.append(block["text"] if isinstance(block, dict) else block.text)
    return calls, "\n".join(texts)


class AnthropicModel:
    """Claude via the Anthropic SDK. Credentials come from the environment
    (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile)."""

    def __init__(self, settings: ModelSettings, client: Any | None = None):
        import anthropic

        self.settings = settings
        self.name = settings.model
        self._client = client or anthropic.Anthropic()

    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        s = self.settings
        kwargs: dict[str, Any] = dict(
            model=s.model,
            max_tokens=s.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
            output_config={"effort": s.effort},
        )
        if s.prompt_caching:
            kwargs["cache_control"] = {"type": "ephemeral"}
        if s.fallback:
            response = self._client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            )
        else:
            response = self._client.messages.create(**kwargs)

        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        served_by = response.model
        calls, text = _parse_content(list(response.content))
        details = None
        if response.stop_reason == "refusal" and getattr(response, "stop_details", None):
            details = {"category": response.stop_details.category, "explanation": response.stop_details.explanation}
        return ModelResponse(
            content=list(response.content),
            tool_calls=calls,
            text=text,
            stop_reason=response.stop_reason,
            usage=usage,
            model=served_by,
            cost_usd=cost_usd(served_by, usage),
            request_id=getattr(response, "_request_id", None),
            stop_details=details,
        )


@dataclass
class ScriptedTurn:
    """One canned model turn: optional text plus tool calls as (name, input) pairs."""

    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    text: str = ""
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=1_000, output_tokens=200))
    stop_reason: str | None = None


class ScriptedModel:
    """Replays a fixed list of turns. Used in tests and dry runs."""

    def __init__(self, turns: list[ScriptedTurn], model: str = "claude-opus-5-5"):
        self.name = model
        self._turns = list(turns)
        self._i = 0
        self.requests: list[list[dict[str, Any]]] = []

    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse:
        self.requests.append(list(messages))
        if self._i >= len(self._turns):
            turn = ScriptedTurn(text="(script exhausted)", stop_reason="end_turn")
        else:
            turn = self._turns[self._i]
        self._i += 1
        content: list[dict[str, Any]] = []
        if turn.text:
            content.append({"type": "text", "text": turn.text})
        for n, (name, args) in enumerate(turn.tool_calls):
            content.append({"type": "tool_use", "id": f"toolu_{self._i}_{n}", "name": name, "input": args})
        calls, text = _parse_content(content)
        stop = turn.stop_reason or ("tool_use" if calls else "end_turn")
        return ModelResponse(
            content=content,
            tool_calls=calls,
            text=text,
            stop_reason=stop,
            usage=turn.usage,
            model=self.name,
            cost_usd=cost_usd(self.name, turn.usage),
        )
