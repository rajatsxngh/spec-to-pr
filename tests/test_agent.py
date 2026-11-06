import pytest

from conftest import PYTHON
from spec_to_pr.agent import run_agent
from spec_to_pr.config import Budget, RunConfig
from spec_to_pr.models import ScriptedModel, ScriptedTurn
from spec_to_pr.pricing import Usage, cost_usd
from spec_to_pr.trajectory import Trajectory

TASK = "median() is wrong for even-length lists. Fix it and make pytest pass."
PYTEST = f'"{PYTHON}" -m pytest -q -p no:cacheprovider'


def fixing_script() -> list[ScriptedTurn]:
    return [
        ScriptedTurn(tool_calls=[("search", {"pattern": "def median"})]),
        ScriptedTurn(tool_calls=[("read_file", {"path": "stats.py"}), ("run", {"command": PYTEST})]),
        ScriptedTurn(
            text="The even branch uses the wrong pair of indices.",
            tool_calls=[("edit_file", {"path": "stats.py", "old_str": "ordered[mid + 1]", "new_str": "ordered[mid - 1]"})],
        ),
        ScriptedTurn(tool_calls=[("run", {"command": PYTEST})]),
        ScriptedTurn(tool_calls=[("finish", {"summary": "Fixed median for even-length lists; pytest passes."})]),
    ]


def test_agent_fixes_planted_bug(planted_sandbox, tmp_path):
    result = run_agent(TASK, ScriptedModel(fixing_script()), planted_sandbox, RunConfig(), tmp_path)

    assert result.status == "finished"
    assert "ordered[mid - 1]" in result.diff
    check = planted_sandbox.exec(PYTEST, timeout_s=120)
    assert check.exit_code == 0, check.output

    events = Trajectory.read(tmp_path / "trajectory.jsonl")
    calls = [e for e in events if e["event"] == "model_call"]
    end = events[-1]
    assert end["event"] == "run_end"
    assert len(calls) == 5 == end["budget"]["steps"]
    # A task's total cost equals the sum of its steps (F3 acceptance).
    assert end["budget"]["cost_usd"] == pytest.approx(sum(c["cost_usd"] for c in calls), abs=1e-6)
    tool_events = [e for e in events if e["event"] == "tool_call"]
    assert {e["tool"] for e in tool_events} == {"search", "read_file", "run", "edit_file", "finish"}
    assert all("usage" in c and "cost_usd" in c for c in calls)
    for name in ("report.md", "report.json", "patch.diff"):
        assert (tmp_path / name).exists()


def test_history_is_append_only(planted_sandbox, tmp_path):
    model = ScriptedModel(fixing_script())
    run_agent(TASK, model, planted_sandbox, RunConfig(), tmp_path)
    # Each request starts with the previous request unchanged.
    for prev, cur in zip(model.requests, model.requests[1:]):
        assert cur[: len(prev)] == prev


def test_step_budget_stops_with_partial_report(planted_sandbox, tmp_path):
    looping = [ScriptedTurn(tool_calls=[("run", {"command": "echo working"})]) for _ in range(10)]
    config = RunConfig(budget=Budget(max_steps=3))
    result = run_agent(TASK, ScriptedModel(looping), planted_sandbox, config, tmp_path)
    assert result.status == "budget_exceeded"
    assert "max_steps" in result.detail
    assert result.budget["steps"] == 3
    assert "budget_exceeded" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_dollar_budget_stops_after_current_step(planted_sandbox, tmp_path):
    pricey = Usage(input_tokens=200_000, output_tokens=10_000)  # $1.00 per call on Opus 5.5
    assert cost_usd("claude-opus-5-5", pricey) == pytest.approx(1.0)
    turns = [ScriptedTurn(tool_calls=[("run", {"command": "true"})], usage=pricey) for _ in range(10)]
    config = RunConfig(budget=Budget(max_usd=2.5))
    result = run_agent(TASK, ScriptedModel(turns), planted_sandbox, config, tmp_path)
    assert result.status == "budget_exceeded" and "max_usd" in result.detail
    assert result.budget["steps"] == 3  # $3.00 spent; the cap is checked before each call


def test_stops_when_model_never_finishes(planted_sandbox, tmp_path):
    chatty = [ScriptedTurn(text="I think it's fine.") for _ in range(5)]
    result = run_agent(TASK, ScriptedModel(chatty), planted_sandbox, RunConfig(), tmp_path)
    assert result.status == "stopped_without_finish"
    assert result.budget["steps"] == 3  # first reply + 2 nudges


def test_refusal_stops_run(planted_sandbox, tmp_path):
    turns = [ScriptedTurn(text="", stop_reason="refusal")]
    result = run_agent(TASK, ScriptedModel(turns), planted_sandbox, RunConfig(), tmp_path)
    assert result.status == "refused"


def test_model_error_writes_partial_report(planted_sandbox, tmp_path):
    class Broken:
        name = "claude-opus-5-5"

        def complete(self, *a):
            raise ConnectionError("network down")

    result = run_agent(TASK, Broken(), planted_sandbox, RunConfig(), tmp_path)
    assert result.status == "error" and "network down" in result.detail
    assert (tmp_path / "report.md").exists()
