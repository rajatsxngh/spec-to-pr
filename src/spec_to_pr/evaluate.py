"""Eval runner (F12): parallel task runs, hidden-test grading, a hard spend cap and a scoreboard.

`make eval` calls this through `spec-to-pr eval`. Results land in results/<stamp>-<config>/:
results.jsonl (one row per task run), scoreboard.md, scoreboard.json, and runs/ with every
trajectory.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
import tomllib
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agent import run_agent
from .config import Budget, ModelSettings, RunConfig
from .models import ModelClient, ScriptedModel, ScriptedTurn
from .sandbox import Sandbox
from .tasks import Task, TaskSet, grade

# Harness components that ablation configs switch on and off, and the milestone that builds each.
COMPONENTS = {"verification": 3, "skills": 3, "planning": 5, "context": 6}
BUILT: set[str] = {"verification", "skills", "planning"}  # grows as milestones land


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EvalConfig:
    name: str
    run: RunConfig
    components: dict[str, bool] = field(default_factory=dict)


def load_config(path: Path) -> EvalConfig:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    components = {k: bool(v) for k, v in raw.get("components", {}).items()}
    for name, on in components.items():
        if name not in COMPONENTS:
            raise ConfigError(f"{path}: unknown component {name!r}; known: {', '.join(COMPONENTS)}")
        if on and name not in BUILT:
            raise ConfigError(f"{path}: component {name!r} is not built yet (milestone {COMPONENTS[name]})")
    run = RunConfig(
        budget=Budget(**raw.get("budget", {})),
        model=ModelSettings(**raw.get("model", {})),
        max_tool_output_chars=raw.get("max_tool_output_chars", RunConfig.max_tool_output_chars),
        verification=components.get("verification", False),
        skills=components.get("skills", False),
        planning=components.get("planning", False),  # plans are auto-approved in evals
        max_review_rounds=raw.get("max_review_rounds", RunConfig.max_review_rounds),
    )
    return EvalConfig(name=raw.get("name", path.stem), run=run, components=components)


@dataclass
class TaskRun:
    task_id: str
    run: int
    status: str  # agent status, or "skipped_spend_cap" / "harness_error"
    resolved: bool
    cost_usd: float = 0.0
    steps: int = 0
    tokens: int = 0
    elapsed_s: float = 0.0
    models_used: list[str] = field(default_factory=list)
    detail: str = ""
    weakening_flags: list[str] = field(default_factory=list)
    verified: bool | None = None  # None when verification is off
    review: str | None = None  # last reviewer verdict
    integrity_ok: bool = True  # False when a planted instruction was followed (N3)


ModelFactory = Callable[[Task], ModelClient]
SandboxFactory = Callable[[Task], Sandbox]


def run_one(task: Task, run_idx: int, config: EvalConfig, make_model: ModelFactory,
            make_sandbox: SandboxFactory, out_dir: Path) -> TaskRun:
    sandbox = None
    try:
        sandbox = make_sandbox(task)
        run_dir = out_dir / "runs" / f"{task.id}" / f"run{run_idx}"
        result = run_agent(task.statement, make_model(task), sandbox, config.run, run_dir)
        g = grade(task, sandbox)
        (run_dir / "grade.txt").write_text(g.output, encoding="utf-8")
        b = result.budget
        return TaskRun(task.id, run_idx, result.status, g.resolved, b["cost_usd"], b["steps"], b["tokens"],
                       b["elapsed_s"], result.models_used, result.detail, result.weakening_flags,
                       (result.verification or {}).get("verified") if config.run.verification else None,
                       result.reviews[-1]["verdict"] if result.reviews else None, g.integrity_ok)
    except Exception as e:  # a broken task or sandbox must not stop the whole eval
        return TaskRun(task.id, run_idx, "harness_error", False, detail=f"{type(e).__name__}: {e}")
    finally:
        if sandbox is not None:
            sandbox.close()


def run_eval(
    taskset: TaskSet,
    config: EvalConfig,
    make_model: ModelFactory,
    make_sandbox: SandboxFactory,
    out_dir: Path,
    runs: int = 1,
    workers: int = 4,
    max_total_usd: float = 20.0,
    limit: int | None = None,
    on_result: Callable[[TaskRun], None] | None = None,
    reserve_per_task: float | None = None,
) -> list[TaskRun]:
    """Run every task `runs` times. The spend cap is strict: a task starts only if the money already
    spent plus the worst case of every running task (each capped at the per-task max_usd) still fits.
    A run can overshoot its own max_usd by at most one model call, since budgets are checked between calls."""
    tasks = taskset.tasks[:limit] if limit else taskset.tasks
    jobs = [(t, r) for r in range(1, runs + 1) for t in tasks]
    per_task = config.run.budget.max_usd if reserve_per_task is None else reserve_per_task
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[TaskRun] = []
    spent = 0.0
    lock = threading.Lock()
    log = (out_dir / "results.jsonl").open("a", encoding="utf-8")

    def record(tr: TaskRun) -> None:
        results.append(tr)
        log.write(json.dumps(asdict(tr)) + "\n")
        log.flush()
        if on_result:
            on_result(tr)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            running: dict[Future, tuple[Task, int]] = {}
            queue = list(jobs)
            while queue or running:
                while queue and len(running) < workers and spent + (len(running) + 1) * per_task <= max_total_usd:
                    task, r = queue.pop(0)
                    fut = pool.submit(run_one, task, r, config, make_model, make_sandbox, out_dir)
                    running[fut] = (task, r)
                if not running:  # nothing in flight and the next task can't fit under the cap
                    for task, r in queue:
                        record(TaskRun(task.id, r, "skipped_spend_cap", False,
                                       detail=f"spent ${spent:.2f} of ${max_total_usd:.2f}"))
                    queue.clear()
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for fut in done:
                    running.pop(fut)
                    tr = fut.result()
                    with lock:
                        spent += tr.cost_usd
                    record(tr)
    finally:
        log.close()
    results.sort(key=lambda t: (t.run, t.task_id))
    return results


# ---------------------------------------------------------------- scoreboard


def scoreboard(results: list[TaskRun], taskset: TaskSet, config: EvalConfig) -> dict[str, Any]:
    attempted = [r for r in results if r.status != "skipped_spend_cap"]
    by_run: dict[int, list[TaskRun]] = {}
    for r in attempted:
        by_run.setdefault(r.run, []).append(r)
    rates = [sum(x.resolved for x in rs) / len(rs) for _, rs in sorted(by_run.items())]
    resolved = sum(r.resolved for r in attempted)
    cost = sum(r.cost_usd for r in attempted)

    def mean(values: list[float]) -> float:
        return round(statistics.fmean(values), 3) if values else 0.0

    return {
        "config": config.name,
        "model": config.run.model.model,
        "components": config.components,
        "taskset": {"name": taskset.name, "version": taskset.version, "hash": taskset.content_hash},
        "runs": len(by_run),
        "task_runs": len(attempted),
        "skipped": len(results) - len(attempted),
        "harness_errors": sum(r.status == "harness_error" for r in attempted),
        "resolved": resolved,
        "resolve_rate_mean": mean(rates),
        "resolve_rate_min": round(min(rates), 3) if rates else 0.0,
        "resolve_rate_max": round(max(rates), 3) if rates else 0.0,
        "resolve_rate_stdev": round(statistics.stdev(rates), 3) if len(rates) > 1 else 0.0,
        "total_cost_usd": round(cost, 4),
        "cost_per_resolved_usd": round(cost / resolved, 4) if resolved else None,
        "mean_time_s": mean([r.elapsed_s for r in attempted]),
        "mean_steps": mean([r.steps for r in attempted]),
        "mean_tokens": mean([r.tokens for r in attempted]),
        "test_weakening_flagged": sum(bool(r.weakening_flags) for r in attempted),
        "flagged_but_resolved": sum(bool(r.weakening_flags) and r.resolved for r in attempted),
        "integrity_failures": sum(not r.integrity_ok for r in attempted),
        "per_task": {
            tid: f"{sum(r.resolved for r in attempted if r.task_id == tid)}/{sum(r.task_id == tid for r in attempted)}"
            for tid in dict.fromkeys(r.task_id for r in results)
        },
    }


def scoreboard_markdown(sb: dict[str, Any]) -> str:
    rate = f"{sb['resolve_rate_mean']:.1%}"
    if sb["runs"] > 1:
        rate += f" (min {sb['resolve_rate_min']:.1%}, max {sb['resolve_rate_max']:.1%}, sd {sb['resolve_rate_stdev']:.1%})"
    cpr = f"${sb['cost_per_resolved_usd']:.4f}" if sb["cost_per_resolved_usd"] is not None else "n/a (none resolved)"
    lines = [
        f"# Scoreboard: {sb['config']}",
        "",
        f"Task set `{sb['taskset']['name']}` {sb['taskset']['version']} (hash {sb['taskset']['hash']}), "
        f"model `{sb['model']}`, {sb['runs']} run(s).",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Resolve rate | {rate} |",
        f"| Resolved | {sb['resolved']} of {sb['task_runs']} task runs |",
        f"| Cost per resolved task | {cpr} |",
        f"| Total cost | ${sb['total_cost_usd']:.4f} |",
        f"| Mean time | {sb['mean_time_s']}s |",
        f"| Mean steps | {sb['mean_steps']} |",
        f"| Mean tokens | {sb['mean_tokens']:,.0f} |",
        f"| Test-weakening flagged | {sb['test_weakening_flagged']} (resolved anyway: {sb['flagged_but_resolved']}) |",
        f"| Planted instructions followed | {sb['integrity_failures']} |",
        f"| Skipped (spend cap) | {sb['skipped']} |",
        f"| Harness errors | {sb['harness_errors']} |",
        "",
        "| Task | Resolved |",
        "| --- | --- |",
        *[f"| {tid} | {v} |" for tid, v in sb["per_task"].items()],
        "",
    ]
    return "\n".join(lines)


def write_scoreboard(results: list[TaskRun], taskset: TaskSet, config: EvalConfig, out_dir: Path) -> dict[str, Any]:
    sb = scoreboard(results, taskset, config)
    (out_dir / "scoreboard.json").write_text(json.dumps(sb, indent=2), encoding="utf-8")
    (out_dir / "scoreboard.md").write_text(scoreboard_markdown(sb), encoding="utf-8")
    return sb


# ---------------------------------------------------------------- reference agents (no API calls)


def oracle_model(task: Task) -> ModelClient:
    """Applies the task's reference solution, then finishes. Checks the pipeline end to end for free."""
    turns = []
    for f in sorted(p for p in task.solution.rglob("*") if p.is_file()):
        rel = f.relative_to(task.solution).as_posix()
        new = f.read_text(encoding="utf-8")
        original = task.repo / rel
        old = original.read_text(encoding="utf-8") if original.exists() else ""
        turns.append(ScriptedTurn(tool_calls=[("edit_file", {"path": rel, "old_str": old, "new_str": new})],
                                  usage=_zero()))
    turns.append(ScriptedTurn(tool_calls=[("finish", {"summary": "Applied the reference solution."})], usage=_zero()))
    return ScriptedModel(turns)


def noop_model(task: Task) -> ModelClient:
    """Finishes immediately without changing anything. Every task should come out unresolved."""
    return ScriptedModel([ScriptedTurn(tool_calls=[("finish", {"summary": "No changes."})], usage=_zero())])


def _zero():
    from .pricing import Usage

    return Usage()
