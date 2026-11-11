"""The mini-swe-agent baseline adapter, with scripted models (no API calls)."""

import dataclasses
import json

import pytest

from conftest import ROOT
from spec_to_pr.baseline import (ANTHROPIC_BASH_TOOL, MINI_VERSION, load_mini_config, run_baseline,
                                 to_anthropic)
from spec_to_pr.config import Budget, RunConfig, SandboxLimits
from spec_to_pr.evaluate import ConfigError, baseline_oracle, load_config, noop_model, oracle_for, run_eval, scoreboard
from spec_to_pr.experiment import load_spec, run_experiment
from spec_to_pr.models import ScriptedModel, ScriptedTurn
from spec_to_pr.report import headline, report_markdown, write_report
from spec_to_pr.sandbox import LocalSandbox
from spec_to_pr.tasks import load_taskset
from spec_to_pr.trajectory import Trajectory

SUBMIT = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git diff"


def bash(*commands, text="Thinking."):
    return ScriptedTurn(text=text, tool_calls=[("bash", {"command": c}) for c in commands])


def box(task):
    return LocalSandbox(task.repo, SandboxLimits(command_timeout_s=120))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    sb = LocalSandbox(r, SandboxLimits(command_timeout_s=30))
    yield sb
    sb.close()


def config(**budget):
    return RunConfig(budget=Budget(**budget), sandbox=SandboxLimits(command_timeout_s=30))


FIX = "sed -i 's/a - b/a + b/' calc.py"


def test_upstream_loop_prompts_and_submission(repo, tmp_path):
    model = ScriptedModel([bash("cat calc.py", "ls"), bash(FIX), bash(SUBMIT)])
    r = run_baseline("add() subtracts.", model, repo, config(), tmp_path / "run")
    assert r.status == "finished" and "Submitted" in r.detail
    assert repo.read_file("calc.py") == "def add(a, b):\n    return a + b\n"
    assert "+    return a + b" in r.diff
    assert r.budget["steps"] == 3 and r.budget["cost_usd"] > 0  # scripted turns are priced like real ones

    # Upstream's SWE-bench prompts, with its /testbed working directory replaced by the sandbox's.
    upstream = load_mini_config()["agent"]
    assert model.systems[0] == upstream["system_template"].strip()
    first = model.requests[0][0]["content"][0]["text"]
    assert "<pr_description>" in first and "add() subtracts." in first and "/testbed" not in first
    assert model.tool_names[0] == ["bash"]

    # Valid Anthropic history: parallel tool calls answered by tool_result blocks in one user turn,
    # with upstream's observation template.
    second = model.requests[1]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    ids = [b["id"] for b in second[1]["content"] if b["type"] == "tool_use"]
    results = second[2]["content"]
    assert [b["tool_use_id"] for b in results] == ids and len(ids) == 2
    assert "<returncode>0</returncode>" in results[0]["content"] and "return a - b" in results[0]["content"]

    run = tmp_path / "run"
    events = [e["event"] for e in Trajectory.read(run / "trajectory.jsonl")]
    assert events[0] == "run_start" and events[-1] == "run_end"
    assert events.count("model_call") == 3 and events.count("tool_call") == 4
    mini = json.loads((run / "mini_trajectory.json").read_text(encoding="utf-8"))
    assert mini["info"]["exit_status"] == "Submitted" and mini["info"]["mini_version"] == MINI_VERSION
    assert mini["trajectory_format"] == "mini-swe-agent-1.1" and mini["messages"][-1]["role"] == "exit"
    assert "a + b" in (run / "submission.patch").read_text(encoding="utf-8")
    assert {"report.md", "report.json", "patch.diff"} <= {p.name for p in run.iterdir()}


def test_format_errors_end_the_run_as_upstream(repo, tmp_path):
    model = ScriptedModel([ScriptedTurn(text="I think it's done."),
                           ScriptedTurn(tool_calls=[("finish", {"summary": "x"})]),  # not its tool
                           ScriptedTurn(text="Still no tool call.")])
    r = run_baseline("t", model, repo, config(), tmp_path / "run")
    assert r.status == "stopped_without_finish" and "RepeatedFormatError" in r.detail
    assert r.budget["steps"] == 3 and r.budget["cost_usd"] > 0  # format-error turns are still billed
    # Upstream drops the bad turn and sends its format-error template back as a user message.
    assert model.requests[1][-1]["role"] == "user"
    assert "No tool calls found" in model.requests[1][-1]["content"][-1]["text"]
    assert "Unknown tool 'finish'" in model.requests[2][-1]["content"][-1]["text"]


@pytest.mark.parametrize("cap,expected", [({"max_steps": 2}, "max_steps (2)"),
                                          ({"max_usd": 0.001}, "max_usd ($0.00)"),
                                          ({"max_tokens": 1500}, "max_tokens (1,500)")])
def test_budget_caps(repo, tmp_path, cap, expected):
    model = ScriptedModel([bash("true")] * 10)
    r = run_baseline("t", model, repo, config(**cap), tmp_path / "run")
    assert r.status == "budget_exceeded" and expected in r.detail


def test_model_failure_is_reported(repo, tmp_path):
    class Broken:
        name = "claude-opus-5-5"

        def complete(self, *a):
            raise ConnectionError("network down")

    r = run_baseline("t", Broken(), repo, config(), tmp_path / "run")
    assert r.status == "error" and "network down" in r.detail
    assert (tmp_path / "run" / "report.md").exists()


def test_timeout_matches_upstream_observation(repo, tmp_path):
    model = ScriptedModel([bash("sleep 4"), bash(SUBMIT)])
    cfg = dataclasses.replace(config(), sandbox=SandboxLimits(command_timeout_s=1))
    run_baseline("t", model, repo, cfg, tmp_path / "run")
    obs = model.requests[1][-1]["content"][0]["content"]
    assert "<returncode>-1</returncode>" in obs and "timed out after 1 seconds" in obs


def test_grade_on_submission_applies_only_the_patch(repo, tmp_path):
    model = ScriptedModel([bash(FIX, "echo junk > scratch.py"),
                           bash("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git diff -- calc.py")])
    r = run_baseline("t", model, repo, config(), tmp_path / "run", grade_on="submission")
    assert r.status == "finished" and "a + b" in repo.read_file("calc.py")
    assert repo.exec("test -e scratch.py", 10).exit_code != 0  # not in the submitted patch

    model = ScriptedModel([bash(FIX)] * 5)  # never submits
    r = run_baseline("t", model, repo, config(max_steps=2), tmp_path / "run2", grade_on="submission")
    assert r.status == "budget_exceeded" and r.diff == ""


def test_to_anthropic_merges_turns():
    system, msgs = to_anthropic([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "user", "content": "format error"},
        {"role": "assistant", "content": "hi", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "bash", "arguments": '{"command": "ls"}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "out"},
        {"role": "exit", "content": "", "extra": {}},
    ])
    assert system == "sys"
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [b["text"] for b in msgs[0]["content"]] == ["task", "format error"]
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}}
    assert msgs[2]["content"] == [{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]
    assert ANTHROPIC_BASH_TOOL["input_schema"]["required"] == ["command"]


def test_baseline_config(tmp_path):
    cfg = load_config(ROOT / "configs" / "baseline.toml")
    bare = load_config(ROOT / "configs" / "bare.toml")
    assert cfg.agent == "mini-swe-agent" and cfg.grade_on == "worktree" and not any(cfg.components.values())
    assert cfg.run.budget == bare.run.budget and cfg.run.model == bare.run.model  # same caps, same model
    assert oracle_for(cfg) is baseline_oracle
    p = tmp_path / "c.toml"
    p.write_text('agent = "swe-agent"\n')
    with pytest.raises(ConfigError, match="unknown agent"):
        load_config(p)
    p.write_text('agent = "mini-swe-agent"\n[components]\nverification = true\n')
    with pytest.raises(ConfigError, match="don't apply"):
        load_config(p)
    p.write_text('agent = "mini-swe-agent"\n[baseline]\ngrade_on = "vibes"\n')
    with pytest.raises(ConfigError, match="grade_on"):
        load_config(p)


def test_baseline_through_the_eval_pipeline(tmp_path):
    cfg = load_config(ROOT / "configs" / "baseline.toml")
    taskset = load_taskset(ROOT / "tasks" / "self")
    rows = run_eval(taskset, cfg, oracle_for(cfg), box, tmp_path / "oracle", limit=3, reserve_per_task=0)
    assert [r.resolved for r in rows] == [True] * 3, [r.detail for r in rows]
    assert all(r.status == "finished" and r.verified is None for r in rows)
    sb = scoreboard(rows, taskset, cfg)
    assert sb["agent"] == "mini-swe-agent" and sb["resolve_rate_mean"] == 1.0
    assert (tmp_path / "oracle" / "runs" / rows[0].task_id / "run1" / "grade.txt").exists()

    # Graded on the submitted patch too, as SWE-bench does.
    sub = dataclasses.replace(cfg, grade_on="submission")
    rows = run_eval(taskset, sub, oracle_for(sub), box, tmp_path / "sub", limit=2, reserve_per_task=0)
    assert [r.resolved for r in rows] == [True, True], [r.detail for r in rows]

    # The no-op agent calls our finish tool, which mini-swe-agent doesn't have: nothing is resolved.
    rows = run_eval(taskset, cfg, noop_model, box, tmp_path / "noop", limit=2, reserve_per_task=0)
    assert not any(r.resolved for r in rows) and {r.status for r in rows} == {"stopped_without_finish"}


def test_experiment_report_shows_baseline(tmp_path):
    p = tmp_path / "spec.toml"
    p.write_text('name = "t"\ntasks = "tasks/injection"\nruns = 1\nworkers = 3\nmax_total_usd = 5\n'
                 'configs = ["configs/baseline.toml", "configs/bare.toml", "configs/context.toml"]\n'
                 'models = ["claude-haiku-5-5"]\n')
    spec = load_spec(p, root=ROOT)
    boards = run_experiment(spec, oracle_for, box, tmp_path / "out", reserve_per_task=0)
    assert [b["config"] for b in boards] == ["baseline", "bare", "context"]
    assert boards[0]["agent"] == "mini-swe-agent" and boards[0]["integrity_failures"] == 0
    assert all(b["resolve_rate_mean"] == 1.0 for b in boards)

    configs = ["baseline", "bare", "context"]
    md = report_markdown(boards, configs, spec.models, "t")
    assert "| mini-swe-agent | claude-haiku-5-5 | 100% |" in md and "Full harness against the baseline" in md
    bare_row = next(line for line in md.splitlines() if line.startswith("| Bare loop"))
    assert "pts" not in bare_row  # the first rung isn't compared with the baseline
    h = headline(boards, configs, spec.models)
    assert h["best_component"] == "context" and h["per_model"]["claude-haiku-5-5"]["baseline"] == 1.0
    files = write_report(boards, configs, spec.models, tmp_path / "out", "t")
    assert {f.name for f in files} == {"report.md", "ablation.png", "cost_quality.png"}
