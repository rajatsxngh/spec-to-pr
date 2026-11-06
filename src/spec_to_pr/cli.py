"""Command line entry point (Typer)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Optional

import typer

from .agent import run_agent
from .config import Budget, ModelSettings, RunConfig, SandboxLimits
from .models import AnthropicModel
from .pricing import price_for
from .sandbox import DEFAULT_IMAGE, DockerSandbox, LocalSandbox

app = typer.Typer(add_completion=False, help="Spec-to-PR coding-agent harness.")

DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "sandbox.Dockerfile"


@app.command()
def solve(
    repo: Path = typer.Argument(..., exists=True, file_okay=False, help="Repository to work on."),
    task: Optional[str] = typer.Option(None, "--task", help="Task text."),
    task_file: Optional[Path] = typer.Option(None, "--task-file", exists=True, dir_okay=False, help="Markdown task file."),
    commit: Optional[str] = typer.Option(None, help="Check out this commit inside the sandbox first."),
    sandbox: str = typer.Option("docker", help="docker (isolated) or local (unsafe, for development only)."),
    image: str = typer.Option(DEFAULT_IMAGE, help="Docker image for the sandbox."),
    model: str = typer.Option(ModelSettings.model, help="Claude model ID."),
    effort: str = typer.Option(ModelSettings.effort, help="low | medium | high | xhigh | max"),
    fallback: bool = typer.Option(True, help="Server-side refusal fallback to another model."),
    verification: bool = typer.Option(True, help="Test-first, finish gate and reviewer (F7)."),
    skills: bool = typer.Option(True, help="On-demand skills and AGENTS.md (F8)."),
    max_steps: int = typer.Option(Budget.max_steps),
    max_tokens: int = typer.Option(Budget.max_tokens),
    max_usd: float = typer.Option(Budget.max_usd),
    max_minutes: float = typer.Option(Budget.max_seconds / 60),
    out: Path = typer.Option(Path("runs"), help="Directory for run outputs."),
) -> None:
    """Run the agent on one task and write a trajectory, report and patch."""
    if bool(task) == bool(task_file):
        raise typer.BadParameter("Pass exactly one of --task or --task-file.")
    task_text = task or task_file.read_text(encoding="utf-8")  # type: ignore[union-attr]
    try:
        price_for(model)  # fail fast if dollar budgets can't be enforced for this model
    except KeyError as e:
        raise typer.BadParameter(str(e.args[0]), param_hint="--model") from None

    config = RunConfig(
        budget=Budget(max_steps, max_tokens, max_usd, max_minutes * 60),
        sandbox=SandboxLimits(),
        model=ModelSettings(model=model, effort=effort, fallback=fallback),
        verification=verification,
        skills=skills,
    )
    if sandbox == "docker":
        box = DockerSandbox(repo, config.sandbox, image=image, commit=commit)
    elif sandbox == "local":
        typer.secho("Warning: local sandbox runs agent commands directly on this machine.", fg="yellow", err=True)
        box = LocalSandbox(repo, config.sandbox, commit=commit)
    else:
        raise typer.BadParameter("--sandbox must be docker or local")

    run_dir = out / time.strftime("%Y%m%d-%H%M%S")
    try:
        result = run_agent(task_text, AnthropicModel(config.model), box, config, run_dir)
    finally:
        box.close()

    b = result.budget
    typer.echo(f"{result.status}: {result.detail}")
    typer.echo(f"steps={b['steps']} tokens={b['tokens']:,} cost=${b['cost_usd']:.4f} time={b['elapsed_s']}s")
    typer.echo(f"report: {run_dir / 'report.md'}")
    raise typer.Exit(0 if result.status == "finished" else 1)


def _sandbox_factory(kind: str, image: str, limits: SandboxLimits):
    if kind == "docker":
        return lambda task: DockerSandbox(task.repo, limits, image=task.image or image, commit=task.commit)
    if kind == "local":
        typer.secho("Warning: local sandbox runs agent commands directly on this machine.", fg="yellow", err=True)
        return lambda task: LocalSandbox(task.repo, limits, commit=task.commit)
    raise typer.BadParameter("--sandbox must be docker or local")


@app.command("eval")
def eval_cmd(
    tasks: Path = typer.Argument(Path("tasks/self"), exists=True, file_okay=False, help="Task set directory."),
    config: Path = typer.Option(Path("configs/bare.toml"), exists=True, dir_okay=False, help="Ablation config."),
    agent: str = typer.Option("claude", help="claude, or oracle/noop to check the pipeline without API calls."),
    runs: int = typer.Option(1, help="Runs per task (the PRD reports 3)."),
    workers: int = typer.Option(4, help="Tasks in parallel."),
    max_total_usd: float = typer.Option(20.0, help="Hard spend cap for the whole eval."),
    limit: Optional[int] = typer.Option(None, help="Only the first N tasks (5 for the CI smoke eval)."),
    sandbox: str = typer.Option("docker"),
    image: str = typer.Option(DEFAULT_IMAGE),
    out: Path = typer.Option(Path("results")),
) -> None:
    """Run an eval and print the scoreboard."""
    from .evaluate import ConfigError, load_config, noop_model, oracle_model, run_eval, write_scoreboard
    from .tasks import load_taskset

    try:
        cfg = load_config(config)
    except ConfigError as e:
        raise typer.BadParameter(str(e), param_hint="--config") from None
    taskset = load_taskset(tasks)
    if agent == "claude":
        try:
            price_for(cfg.run.model.model)
        except KeyError as e:
            raise typer.BadParameter(str(e.args[0]), param_hint="config [model]") from None
        make_model = lambda task: AnthropicModel(cfg.run.model)  # noqa: E731
    elif agent == "oracle":
        make_model = oracle_model
    elif agent == "noop":
        make_model = noop_model
    else:
        raise typer.BadParameter("--agent must be claude, oracle or noop")

    out_dir = out / f"{time.strftime('%Y%m%d-%H%M%S')}-{cfg.name}-{agent}"
    typer.echo(f"Task set {taskset.name} {taskset.version} ({len(taskset.tasks)} tasks), config {cfg.name}, "
               f"agent {agent}, cap ${max_total_usd:.2f}")

    def progress(tr):
        mark = "PASS" if tr.resolved else "fail"
        typer.echo(f"  {mark} {tr.task_id} run{tr.run} [{tr.status}] ${tr.cost_usd:.4f} {tr.elapsed_s}s")

    results = run_eval(taskset, cfg, make_model, _sandbox_factory(sandbox, image, cfg.run.sandbox), out_dir,
                       runs=runs, workers=workers, max_total_usd=max_total_usd, limit=limit, on_result=progress,
                       reserve_per_task=None if agent == "claude" else 0.0)  # scripted agents cost nothing
    write_scoreboard(results, taskset, cfg, out_dir)
    typer.echo((out_dir / "scoreboard.md").read_text(encoding="utf-8"))
    typer.echo(f"Results: {out_dir}")


@app.command("validate-tasks")
def validate_tasks(
    tasks: Path = typer.Argument(Path("tasks/self"), exists=True, file_okay=False),
    sandbox: str = typer.Option("docker"),
    image: str = typer.Option(DEFAULT_IMAGE),
) -> None:
    """Check every task: hidden tests fail on the original code and pass with the reference fix."""
    from .tasks import load_taskset, validate_task

    taskset = load_taskset(tasks)
    make = _sandbox_factory(sandbox, image, SandboxLimits())
    bad = 0
    for task in taskset.tasks:
        v = validate_task(task, make)
        bad += not v.ok
        typer.echo(f"{'ok  ' if v.ok else 'BAD '} {task.id}  fails_on_base={v.fails_on_base} "
                   f"passes_with_solution={v.passes_with_solution}")
        if not v.ok:
            typer.echo((v.solution_output or v.base_output)[-1500:])
    typer.echo(f"{len(taskset.tasks) - bad} of {len(taskset.tasks)} tasks valid ({taskset.version}, hash {taskset.content_hash})")
    raise typer.Exit(1 if bad else 0)


@app.command("build-image")
def build_image(tag: str = typer.Option(DEFAULT_IMAGE)) -> None:
    """Build the default sandbox image (Python, git, ripgrep, pytest)."""
    proc = subprocess.run(["docker", "build", "-t", tag, "-f", str(DOCKERFILE), str(DOCKERFILE.parent)])
    raise typer.Exit(proc.returncode)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
