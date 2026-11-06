import json
import subprocess

import pytest

from conftest import ROOT
from spec_to_pr.config import SandboxLimits
from spec_to_pr.evaluate import oracle_for
from spec_to_pr.experiment import SpecError, cell_config, estimate, load_spec, run_experiment
from spec_to_pr.models import ScriptedModel, ScriptedTurn
from spec_to_pr.pricing import Usage
from spec_to_pr.report import report_markdown, write_report
from spec_to_pr.sandbox import LocalSandbox

CONFIGS = ["bare", "verification", "skills", "planning", "context"]


def box(task):
    return LocalSandbox(task.repo, SandboxLimits(command_timeout_s=120))


def small_spec(tmp_path, configs=("bare", "context"), models=("claude-haiku-5-5", "claude-opus-5-5"), cap=5.0):
    p = tmp_path / "spec.toml"
    p.write_text(
        f'name = "t"\ntasks = "tasks/injection"\nruns = 1\nworkers = 3\nmax_total_usd = {cap}\n'
        f"configs = {[f'configs/{c}.toml' for c in configs]}\nmodels = {list(models)}\n".replace("'", '"'))
    return load_spec(p, root=ROOT)


def test_full_spec_and_estimate():
    spec = load_spec(ROOT / "experiments" / "full.toml", root=ROOT)
    assert [c.stem for c in spec.configs] == CONFIGS
    assert spec.models == ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    est = estimate(spec)
    assert est == {"cells": 15, "tasks": 10, "task_runs": 450, "worst_case_usd": 900.0, "cap_usd": 150.0}


def test_spec_errors(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('name = "x"\ntasks = "tasks/self"\nconfigs = ["configs/bare.toml"]\nmodels = ["a", "a"]\n')
    with pytest.raises(SpecError, match="duplicate"):
        load_spec(p, root=ROOT)
    p.write_text('name = "x"\ntasks = "tasks/self"\n')
    with pytest.raises(SpecError, match="configs"):
        load_spec(p, root=ROOT)


def test_cell_config_overrides_model_only():
    cfg = cell_config(ROOT / "configs" / "context.toml", "claude-haiku-5-5")
    assert cfg.name == "context" and cfg.run.model.model == "claude-haiku-5-5"
    assert cfg.run.context and cfg.run.model.effort == "high" and cfg.run.model.fallback is False


def test_experiment_runs_resumes_and_reports(tmp_path):
    spec = small_spec(tmp_path)
    out = tmp_path / "out"
    seen = []
    boards = run_experiment(spec, oracle_for, box, out, reserve_per_task=0,
                            on_cell=lambda cfg, sb, cached: seen.append((cfg.name, cfg.run.model.model, cached)))
    assert [(b["config"], b["model"]) for b in boards] == [
        ("bare", "claude-haiku-5-5"), ("bare", "claude-opus-5-5"),
        ("context", "claude-haiku-5-5"), ("context", "claude-opus-5-5")]
    assert all(b["resolve_rate_mean"] == 1.0 and b["integrity_failures"] == 0 for b in boards)
    assert all(not cached for *_, cached in seen)
    assert json.loads((out / "results.json").read_text())["spec"]["name"] == "t"

    # Rerun: every cell is cached. Delete one cell's scoreboard: only that cell runs again.
    seen.clear()
    run_experiment(spec, oracle_for, box, out, reserve_per_task=0, on_cell=lambda c, s, k: seen.append(k))
    assert seen == [True, True, True, True]
    (out / "cells" / "context__claude-opus-5-5" / "scoreboard.json").unlink()
    seen.clear()
    run_experiment(spec, oracle_for, box, out, reserve_per_task=0, on_cell=lambda c, s, k: seen.append(k))
    assert seen == [True, True, True, False]

    files = write_report(boards, ["bare", "context"], spec.models, out, "t")
    assert {f.name for f in files} == {"report.md", "ablation.png", "cost_quality.png"}
    assert all(f.stat().st_size > 0 for f in files)


def test_global_cap_spans_cells(tmp_path):
    spec = small_spec(tmp_path, configs=("bare",), models=("claude-haiku-5-5", "claude-opus-5-5"), cap=4.0)
    pricey = Usage(input_tokens=200_000, output_tokens=10_000)  # $1.00 per run on Opus pricing

    def spender(cfg):
        return lambda task: ScriptedModel([ScriptedTurn(tool_calls=[("finish", {"summary": "x"})], usage=pricey)])

    boards = run_experiment(spec, spender, box, tmp_path / "out", reserve_per_task=1.0)
    total = sum(b["total_cost_usd"] for b in boards)
    assert total <= 4.0
    assert boards[0]["task_runs"] == 3 and boards[0]["skipped"] == 0
    assert boards[1]["task_runs"] == 1 and boards[1]["skipped"] == 2
    md = report_markdown(boards, ["bare"], spec.models, "t")
    assert "skipped by the spend cap" in md


def test_report_table_deltas():
    def b(cfg, rate, cpr):
        return dict(config=cfg, model="m", resolve_rate_mean=rate, resolve_rate_min=rate, resolve_rate_max=rate,
                    cost_per_resolved_usd=cpr, mean_tokens=1000, test_weakening_flagged=0, skipped=0,
                    total_cost_usd=1.0, task_runs=10, taskset=dict(name="self", version="v", hash="h"))

    md = report_markdown([b("bare", 0.4, 0.05), b("verification", 0.6, 0.08)], ["bare", "verification", "skills"],
                         ["m"], "t")
    assert "| + Verification | m | 60% | 60% to 60% | +20 pts | $0.080 | +$0.030 |" in md
    assert "| + Skills | m | not run |" in md


def test_cli_estimate_only():
    out = subprocess.run(["uv", "run", "spec-to-pr", "experiment", "experiments/full.toml", "--estimate-only"],
                         capture_output=True, text=True, cwd=ROOT).stdout
    assert "450 task runs" in out and "$900.00" in out and "$150.00" in out


def test_cli_claude_requires_yes():
    proc = subprocess.run(["uv", "run", "spec-to-pr", "experiment", "experiments/full.toml"],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode != 0 and "--yes" in (proc.stdout + proc.stderr)
