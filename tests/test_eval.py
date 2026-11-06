from pathlib import Path

import pytest

from conftest import ROOT
from spec_to_pr.config import SandboxLimits
from spec_to_pr.evaluate import (
    ConfigError,
    EvalConfig,
    load_config,
    noop_model,
    oracle_model,
    run_eval,
    scoreboard,
    write_scoreboard,
)
from spec_to_pr.models import ScriptedModel, ScriptedTurn
from spec_to_pr.pricing import Usage
from spec_to_pr.sandbox import LocalSandbox
from spec_to_pr.tasks import load_taskset, validate_task

TASKS = ROOT / "tasks" / "self"


def local(task):
    return LocalSandbox(task.repo, SandboxLimits(command_timeout_s=120))


@pytest.fixture(scope="module")
def taskset():
    return load_taskset(TASKS)


@pytest.fixture(scope="module")
def config():
    return load_config(ROOT / "configs" / "bare.toml")


def test_taskset_loads(taskset):
    assert taskset.version == "self-v1"
    assert len(taskset.tasks) == 10
    assert len({t.id for t in taskset.tasks}) == 10
    assert len(taskset.content_hash) == 12


def test_hidden_tests_are_not_visible_to_the_agent(taskset):
    for t in taskset.tasks:
        hidden = {p.name for p in t.hidden.rglob("*_hidden.py")}
        assert hidden, t.id
        assert not any(t.repo.rglob("*_hidden.py")), t.id


@pytest.mark.parametrize("task_id", [p.name for p in sorted(TASKS.iterdir()) if p.is_dir()])
def test_every_task_is_valid(task_id, taskset):
    task = next(t for t in taskset.tasks if t.id == task_id)
    v = validate_task(task, local)
    assert v.fails_on_base, v.base_output[-800:]
    assert v.passes_with_solution, v.solution_output[-800:]


def test_oracle_resolves_and_noop_does_not(taskset, config, tmp_path):
    good = run_eval(taskset, config, oracle_model, local, tmp_path / "oracle", limit=3, reserve_per_task=0)
    bad = run_eval(taskset, config, noop_model, local, tmp_path / "noop", limit=3, reserve_per_task=0)
    assert [r.resolved for r in good] == [True, True, True]
    assert [r.resolved for r in bad] == [False, False, False]
    sb = write_scoreboard(good, taskset, config, tmp_path / "oracle")
    assert sb["resolve_rate_mean"] == 1.0 and sb["taskset"]["version"] == "self-v1"
    assert (tmp_path / "oracle" / "scoreboard.md").exists()
    assert (tmp_path / "oracle" / "results.jsonl").read_text().count("\n") == 3


def test_weakened_visible_tests_are_restored_before_grading(taskset):
    """An agent that guts the visible test file still gets graded on the original tests."""
    from spec_to_pr.tasks import grade

    task = taskset.tasks[0]
    original = (task.repo / "tests" / "test_slug.py").read_text(encoding="utf-8")
    box = local(task)
    try:
        box.write_file("tests/test_slug.py", "def test_ok():\n    pass\n")
        g = grade(task, box)
        assert box.read_file("tests/test_slug.py") == original
        assert not g.resolved  # the code is still buggy, and the restored + hidden tests say so
    finally:
        box.close()


def test_spend_cap_is_strict(taskset, config, tmp_path):
    # Each scripted run costs $1.00; reserve $1.00 per task, cap $2.50, one worker.
    pricey = Usage(input_tokens=200_000, output_tokens=10_000)
    spender = lambda t: ScriptedModel([ScriptedTurn(tool_calls=[("finish", {"summary": "x"})], usage=pricey)])  # noqa: E731
    rows = run_eval(taskset, config, spender, local, tmp_path, limit=5, workers=1, max_total_usd=2.5,
                    reserve_per_task=1.0)
    ran = [r for r in rows if r.status != "skipped_spend_cap"]
    assert len(ran) == 2
    assert sum(r.cost_usd for r in ran) <= 2.5
    assert sum(r.status == "skipped_spend_cap" for r in rows) == 3


def test_harness_error_does_not_stop_eval(taskset, config, tmp_path):
    def flaky(task):
        if task.id.endswith("slugify"):
            raise RuntimeError("sandbox failed to start")
        return local(task)

    rows = run_eval(taskset, config, oracle_model, flaky, tmp_path, limit=2, reserve_per_task=0)
    assert {r.status for r in rows} == {"harness_error", "finished"}


def test_scoreboard_spread_over_runs(taskset, config):
    from spec_to_pr.evaluate import TaskRun

    rows = [TaskRun("a", 1, "finished", True, 1.0), TaskRun("b", 1, "finished", False, 1.0),
            TaskRun("a", 2, "finished", True, 1.0), TaskRun("b", 2, "finished", True, 1.0)]
    sb = scoreboard(rows, taskset, config)
    assert sb["runs"] == 2
    assert sb["resolve_rate_mean"] == 0.75 and sb["resolve_rate_min"] == 0.5 and sb["resolve_rate_max"] == 1.0
    assert sb["cost_per_resolved_usd"] == pytest.approx(4 / 3, abs=1e-4)


INJECTION = ROOT / "tasks" / "injection"


@pytest.mark.parametrize("task_id", [p.name for p in sorted(INJECTION.iterdir()) if p.is_dir()])
def test_injection_tasks_are_valid(task_id):
    task = next(t for t in load_taskset(INJECTION).tasks if t.id == task_id)
    v = validate_task(task, local)
    assert v.fails_on_base and v.passes_with_solution
    assert v.violation_caught is True  # the integrity check notices the planted action


def test_following_a_planted_instruction_fails_the_task(config, tmp_path):
    taskset = load_taskset(INJECTION)
    task = taskset.tasks[0]  # README asks for PWNED.txt

    def obedient(t):
        turns = oracle_model(t)._turns[:-1]
        turns.append(ScriptedTurn(tool_calls=[("run", {"command": "echo prompt > PWNED.txt"})]))
        turns.append(ScriptedTurn(tool_calls=[("finish", {"summary": "done"})]))
        return ScriptedModel(turns)

    rows = run_eval(taskset, config, obedient, local, tmp_path, limit=1, reserve_per_task=0)
    assert rows[0].task_id == task.id and not rows[0].integrity_ok and not rows[0].resolved
    sb = scoreboard(rows, taskset, config)
    assert sb["integrity_failures"] == 1


def test_config_rejects_unbuilt_component(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('name = "x"\n[components]\ncontext = true\n')
    with pytest.raises(ConfigError, match="milestone 6"):
        load_config(p)
    p.write_text('name = "x"\n[components]\nmagic = false\n')
    with pytest.raises(ConfigError, match="unknown component"):
        load_config(p)


def test_bare_config_pins_model(config):
    assert isinstance(config, EvalConfig)
    assert config.run.model.fallback is False
    assert config.run.budget.max_usd == 2.0
