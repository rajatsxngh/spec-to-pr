from conftest import PLANTED, ROOT
from spec_to_pr.agent import run_agent
from spec_to_pr.config import RunConfig
from spec_to_pr.context import (SUMMARY_PROMPT, build_repo_map, continuation_message, extract_summary,
                                python_outline)
from spec_to_pr.evaluate import load_config
from spec_to_pr.models import ScriptedModel, ScriptedTurn
from spec_to_pr.pricing import Usage
from spec_to_pr.trajectory import Trajectory

SOURCE = b'''
import x

class Base(object):
    """Doc."""

    def __init__(self, name: str):
        self.name = name

    @property
    def upper(self) -> str:
        return self.name.upper()

    class Inner:
        def go(self): ...

@decorator
def top(a, *args, b=1, **kw) -> int:
    def nested():
        pass
    return 1
'''

BIG = Usage(input_tokens=90_000, output_tokens=500)


def test_python_outline():
    assert python_outline(SOURCE) == [
        "class Base(object)",
        "  def __init__(self, name: str)",
        "  def upper(self) -> str",
        "  class Inner",
        "    def go(self)",
        "def top(a, *args, b=1, **kw) -> int",
    ]


def test_repo_map_of_planted_example():
    m = build_repo_map(PLANTED)
    assert "stats.py\n  def mean(values: list[float]) -> float\n  def median(values: list[float]) -> float" in m
    assert "TASK.md" in m and "test_stats.py" in m


def test_repo_map_truncates():
    m = build_repo_map(ROOT / "src", max_chars=300)
    assert "more files omitted" in m and len(m) < 450


def test_context_puts_repo_map_first_and_logs_size(planted_sandbox, tmp_path):
    model = ScriptedModel([ScriptedTurn(tool_calls=[("finish", {"summary": "x"})])])
    run_agent("fix median", model, planted_sandbox, RunConfig(context=True), tmp_path)
    first = model.requests[0][0]["content"]
    assert first.startswith("fix median") and "<repo_map>" in first and "def median" in first
    event = next(e for e in Trajectory.read(tmp_path / "trajectory.jsonl") if e["event"] == "repo_map")
    assert event["approx_tokens"] == event["chars"] // 4 > 0


def test_no_repo_map_without_context(planted_sandbox, tmp_path):
    model = ScriptedModel([ScriptedTurn(tool_calls=[("finish", {"summary": "x"})])])
    run_agent("fix", model, planted_sandbox, RunConfig(), tmp_path)
    assert "<repo_map>" not in model.requests[0][0]["content"]


def test_context_trims_tool_output_tighter(planted_sandbox, tmp_path):
    cmd = "python -c \"print('a' * 30000)\""
    for config, cap in ((RunConfig(), 20_000), (RunConfig(context=True), 8_000)):
        model = ScriptedModel([ScriptedTurn(tool_calls=[("run", {"command": cmd})]),
                               ScriptedTurn(tool_calls=[("finish", {"summary": "x"})])])
        run_agent("t", model, planted_sandbox, config, tmp_path / str(cap))
        out = model.requests[1][-1]["content"][0]["content"]
        assert "omitted" in out and cap <= len(out) <= cap + 100


def test_compaction_starts_fresh_conversation(planted_sandbox, tmp_path):
    model = ScriptedModel([
        ScriptedTurn(tool_calls=[("edit_file", {"path": "stats.py", "old_str": "ordered[mid + 1]",
                                                "new_str": "ordered[mid - 1]"})], usage=BIG),
        ScriptedTurn(text="<summary>Fixed the median index in stats.py; tests not run yet.</summary>"),
        ScriptedTurn(tool_calls=[("finish", {"summary": "done"})]),
    ])
    config = RunConfig(context=True, compact_at_tokens=80_000)
    result = run_agent("fix median", model, planted_sandbox, config, tmp_path)

    assert result.status == "finished" and result.budget["steps"] == 3
    summary_request = model.requests[1]
    assert summary_request[-1]["content"][-1] == {"type": "text", "text": SUMMARY_PROMPT}
    assert summary_request[-1]["content"][0]["type"] == "tool_result"  # same message as the round's results
    fresh = model.requests[2]
    assert len(fresh) == 1
    body = fresh[0]["content"]
    assert body.startswith("fix median") and "<repo_map>" in body
    assert "Fixed the median index" in body and "ordered[mid - 1]" in body  # summary + current diff
    event = next(e for e in Trajectory.read(tmp_path / "trajectory.jsonl") if e["event"] == "compaction")
    assert event["context_tokens_before"] == 90_500


def test_compaction_keeps_plan_and_conventions(planted_sandbox, tmp_path):
    planted_sandbox.write_file("AGENTS.md", "Use type hints everywhere.")
    plan = "## Files to change\n- stats.py\n## Steps\n1. fix\n## Tests to add\n- none\n## Acceptance criteria\n- ok\n"
    model = ScriptedModel([
        ScriptedTurn(tool_calls=[("submit_plan", {"plan": plan})], usage=BIG),
        ScriptedTurn(text="<summary>Plan approved.</summary>"),
        ScriptedTurn(tool_calls=[("finish", {"summary": "done"})]),
    ])
    config = RunConfig(context=True, planning=True, skills=True)
    run_agent("fix", model, planted_sandbox, config, tmp_path)
    first = model.requests[0][0]["content"]
    assert "<repo_map>" in first and "Use type hints" in first  # both, not one replacing the other
    body = model.requests[2][0]["content"]
    assert "<approved_plan>" in body and "## Files to change" in body
    assert "<repo_map>" in body and "Use type hints everywhere." in body


def test_no_compaction_without_context_or_past_the_cap(planted_sandbox, tmp_path):
    turns = [ScriptedTurn(tool_calls=[("run", {"command": "true"})], usage=BIG) for _ in range(3)]
    model = ScriptedModel(turns + [ScriptedTurn(tool_calls=[("finish", {"summary": "x"})])])
    run_agent("t", model, planted_sandbox, RunConfig(), tmp_path / "off")
    assert all(len(r) > 1 for r in model.requests[1:])  # never restarted

    turns = []
    for _ in range(3):
        turns += [ScriptedTurn(tool_calls=[("run", {"command": "true"})], usage=BIG), ScriptedTurn(text="<summary>s</summary>")]
    model = ScriptedModel(turns + [ScriptedTurn(tool_calls=[("finish", {"summary": "x"})])])
    config = RunConfig(context=True, max_compactions=2)
    run_agent("t", model, planted_sandbox, config, tmp_path / "cap")
    events = Trajectory.read(tmp_path / "cap" / "trajectory.jsonl")
    assert sum(e["event"] == "compaction" for e in events) == 2


def test_summary_helpers():
    assert extract_summary("blah <summary> kept </summary> tail") == "kept"
    assert extract_summary("no tags") == "no tags"
    msg = continuation_message("task", None, None, "", "")
    assert "(no changes yet)" in msg and "No summary was produced" in msg and "<approved_plan>" not in msg


def test_context_config_is_full_harness():
    cfg = load_config(ROOT / "configs" / "context.toml")
    r = cfg.run
    assert r.context and r.planning and r.skills and r.verification and r.compact_at_tokens == 80_000
