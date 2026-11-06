"""The full experiment (milestone 7): every ablation config × every model × N runs.

A spec file (experiments/*.toml) names the task set, configs (in ladder order), models, runs and a
global spend cap. Each config × model pair is a "cell" with its own eval and scoreboard under
results/experiment-<name>/cells/. Finished cells are skipped on rerun, so an interrupted experiment
resumes. The global cap is strict across cells: each cell gets only what is left.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .evaluate import EvalConfig, ModelFactory, SandboxFactory, load_config, run_eval, write_scoreboard
from .tasks import load_taskset


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    tasks: Path
    configs: list[Path]  # ladder order: each rung adds a component to the one before
    models: list[str]
    runs: int = 3
    workers: int = 4
    max_total_usd: float = 100.0


def load_spec(path: Path, root: Path | None = None) -> ExperimentSpec:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    base = root or Path.cwd()
    for key in ("tasks", "configs", "models"):
        if not raw.get(key):
            raise SpecError(f"{path}: missing {key!r}")
    spec = ExperimentSpec(
        name=raw.get("name", path.stem),
        tasks=base / raw["tasks"],
        configs=[base / c for c in raw["configs"]],
        models=list(raw["models"]),
        runs=int(raw.get("runs", 3)),
        workers=int(raw.get("workers", 4)),
        max_total_usd=float(raw.get("max_total_usd", 100.0)),
    )
    if len(set(spec.models)) != len(spec.models):
        raise SpecError(f"{path}: duplicate models")
    return spec


def cell_config(config_path: Path, model: str) -> EvalConfig:
    cfg = load_config(config_path)
    run = dataclasses.replace(cfg.run, model=dataclasses.replace(cfg.run.model, model=model))
    return dataclasses.replace(cfg, run=run)


def cell_dir(out: Path, config: str, model: str) -> Path:
    return out / "cells" / f"{config}__{model}"


def estimate(spec: ExperimentSpec) -> dict[str, Any]:
    """Size of the experiment and its worst case: every task run spends its full per-task budget."""
    tasks = len(load_taskset(spec.tasks).tasks)
    per_cell = []
    for c in spec.configs:
        cfg = load_config(c)
        per_cell.append(tasks * spec.runs * cfg.run.budget.max_usd * len(spec.models))
    task_runs = tasks * spec.runs * len(spec.configs) * len(spec.models)
    return {
        "cells": len(spec.configs) * len(spec.models),
        "tasks": tasks,
        "task_runs": task_runs,
        "worst_case_usd": round(sum(per_cell), 2),
        "cap_usd": spec.max_total_usd,
    }


def _complete(path: Path) -> dict | None:
    f = path / "scoreboard.json"
    if not f.exists():
        return None
    sb = json.loads(f.read_text(encoding="utf-8"))
    return sb if sb.get("skipped", 0) == 0 else None  # a cell cut short by the cap is rerun


def run_experiment(
    spec: ExperimentSpec,
    make_model: Callable[[EvalConfig], ModelFactory],
    make_sandbox: SandboxFactory,
    out: Path,
    reserve_per_task: float | None = None,
    on_cell: Callable[[EvalConfig, dict, bool], None] | None = None,
) -> list[dict]:
    """Run every cell not already complete. Returns the scoreboards in ladder order."""
    taskset = load_taskset(spec.tasks)
    out.mkdir(parents=True, exist_ok=True)
    boards: list[dict] = []
    spent = 0.0
    cells = [(c, m) for c in spec.configs for m in spec.models]
    done = {}
    for c, m in cells:
        cfg = cell_config(c, m)
        sb = _complete(cell_dir(out, cfg.name, m))
        if sb is not None:
            done[(cfg.name, m)] = sb
            spent += sb["total_cost_usd"]

    for c, m in cells:
        cfg = cell_config(c, m)
        d = cell_dir(out, cfg.name, m)
        if (cfg.name, m) in done:
            sb = done[(cfg.name, m)]
            if on_cell:
                on_cell(cfg, sb, True)
        else:
            if d.exists():  # a partial cell from an interrupted run: start it over
                for f in d.glob("results.jsonl"):
                    f.unlink()
            remaining = max(0.0, spec.max_total_usd - spent)
            rows = run_eval(taskset, cfg, make_model(cfg), make_sandbox, d, runs=spec.runs, workers=spec.workers,
                            max_total_usd=remaining, reserve_per_task=reserve_per_task)
            sb = write_scoreboard(rows, taskset, cfg, d)
            spent += sb["total_cost_usd"]
            if on_cell:
                on_cell(cfg, sb, False)
        boards.append(sb)

    (out / "results.json").write_text(json.dumps({"spec": _spec_dict(spec), "cells": boards}, indent=2),
                                      encoding="utf-8")
    return boards


def _spec_dict(spec: ExperimentSpec) -> dict:
    d = dataclasses.asdict(spec)
    d["tasks"] = str(spec.tasks)
    d["configs"] = [str(c) for c in spec.configs]
    return d
