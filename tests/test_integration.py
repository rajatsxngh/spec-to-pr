"""Integration: every component working together.

- The spec-to-PR flow with all components on (planning, verification and review, skills and AGENTS.md,
  context engineering, denylist, detector) produces one coherent draft PR.
- Every ablation config runs through the eval pipeline (oracle agent) and resolves its tasks.
- The injection task set passes with the full harness, and following a planted instruction is caught.
"""

import subprocess

import pytest

from conftest import ROOT
from spec_to_pr.config import RunConfig, SandboxLimits
from spec_to_pr.evaluate import load_config, oracle_for, run_eval, scoreboard
from spec_to_pr.github import FakeGitHub
from spec_to_pr.models import ScriptedModel, ScriptedTurn
from spec_to_pr.planning import Decision
from spec_to_pr.prflow import run_spec
from spec_to_pr.sandbox import LocalSandbox
from spec_to_pr.tasks import load_taskset
from spec_to_pr.trajectory import Trajectory
from test_pr import PYTEST, REPRO, SPEC, git, local, repo  # noqa: F401  (repo is a fixture)

CONFIGS = ["baseline", "bare", "verification", "skills", "planning", "context"]
PLAN = ("## Files to change\n- stats.py\n## Steps\n1. Reproduce\n2. Fix\n## Tests to add\n- test_even.py\n"
        "## Acceptance criteria\n- median([4, 1, 3, 2]) == 2.5\n")


def box(task):
    return LocalSandbox(task.repo, SandboxLimits(command_timeout_s=120))


def test_full_harness_spec_to_pr(repo, tmp_path):  # noqa: F811
    work, remote = repo
    (work / "AGENTS.md").write_text("Run tests with: python -m pytest -q\n")
    git(work, "add", "AGENTS.md")
    git(work, "commit", "-qm", "conventions")

    finish = ("finish", {"summary": "Fixed median.", "test_command": f"{PYTEST} test_even.py"})
    model = ScriptedModel([
        ScriptedTurn(tool_calls=[("load_skill", {"name": "python-testing"}), ("run", {"command": "git push"})]),
        ScriptedTurn(tool_calls=[("edit_file", {"path": "stats.py", "old_str": "ordered[mid + 1]",
                                                "new_str": "ordered[mid - 1]"})]),  # refused: plan first
        ScriptedTurn(tool_calls=[("submit_plan", {"plan": PLAN})]),
        ScriptedTurn(tool_calls=[("edit_file", {"path": "stats.py", "old_str": "ordered[mid + 1]",
                                                "new_str": "ordered[mid - 1]"})]),  # refused: test first
        ScriptedTurn(tool_calls=[("edit_file", {"path": "test_even.py", "old_str": "", "new_str": REPRO})]),
        ScriptedTurn(tool_calls=[("edit_file", {"path": "stats.py", "old_str": "ordered[mid + 1]",
                                                "new_str": "ordered[mid - 1]"})]),
        ScriptedTurn(tool_calls=[finish]),
        ScriptedTurn(tool_calls=[("submit_review", {"verdict": "approve", "issues": []})]),
    ])
    config = RunConfig(verification=True, skills=True, planning=True, context=True)
    gh = FakeGitHub()
    outcome = run_spec(SPEC, work, config, model, local, gh, tmp_path / "run",
                       approver=lambda p: Decision("approve", p))

    assert outcome.status == "pr_opened", outcome.reason
    # Every component left its mark on the run.
    first = model.requests[0][0]["content"]
    assert "<repo_map>" in first and "def median" in first  # context: repo map
    assert "Run tests with" in first  # skills: AGENTS.md
    results = [m["content"] for m in (r[-1] for r in model.requests[1:]) if isinstance(m["content"], list)]
    texts = [b["content"] for r in results for b in r if b.get("type") == "tool_result"]
    assert any(t.startswith("# Skill: python-testing") for t in texts)  # skills: load_skill
    assert any("denylist" in t for t in texts)  # safety: git push blocked
    assert any("Plan first" in t for t in texts)  # planning gate
    assert any("Test-first" in t for t in texts)  # verification gate
    r = outcome.result
    assert r.verification["verified"] and r.verification["fails_without_change"]
    assert [x["verdict"] for x in r.reviews] == ["approve"]
    assert r.plan["by"] == "human" and r.weakening_flags == []
    # One coherent draft PR.
    body = gh.calls[0]["body"]
    for part in ("## Spec", "## Plan", "## Changes", "Verified: yes", "Reviewer verdict: **approve**",
                 "Test-weakening flags: none", "## Cost"):
        assert part in body, part
    assert gh.calls[0]["draft"] is True
    assert "ordered[mid - 1]" in git(remote, "show", f"{outcome.branch}:stats.py")
    events = {e["event"] for e in Trajectory.read(tmp_path / "run" / "trajectory.jsonl")}
    assert {"run_start", "repo_map", "agents_md", "plan", "model_call", "tool_call", "review", "run_end"} <= events


@pytest.mark.parametrize("name", CONFIGS)
def test_every_config_runs_the_eval_pipeline(name, tmp_path):
    cfg = load_config(ROOT / "configs" / f"{name}.toml")
    taskset = load_taskset(ROOT / "tasks" / "self")
    rows = run_eval(taskset, cfg, oracle_for(cfg), box, tmp_path, limit=2, reserve_per_task=0)
    assert [r.resolved for r in rows] == [True, True], [r.detail for r in rows]
    if cfg.run.verification:
        assert all(r.verified for r in rows) and all(r.review == "approve" for r in rows)


def test_injection_set_with_full_harness(tmp_path):
    cfg = load_config(ROOT / "configs" / "context.toml")
    taskset = load_taskset(ROOT / "tasks" / "injection")
    rows = run_eval(taskset, cfg, oracle_for(cfg), box, tmp_path, reserve_per_task=0)
    sb = scoreboard(rows, taskset, cfg)
    assert sb["resolved"] == 3 and sb["integrity_failures"] == 0 and sb["test_weakening_flagged"] == 0


def test_cli_commands_load():
    out = subprocess.run(["uv", "run", "spec-to-pr", "--help"], capture_output=True, text=True, cwd=ROOT).stdout
    for cmd in ("solve", "run", "eval", "validate-tasks", "build-image"):
        assert cmd in out
