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


@app.command("build-image")
def build_image(tag: str = typer.Option(DEFAULT_IMAGE)) -> None:
    """Build the default sandbox image (Python, git, ripgrep, pytest)."""
    proc = subprocess.run(["docker", "build", "-t", tag, "-f", str(DOCKERFILE), str(DOCKERFILE.parent)])
    raise typer.Exit(proc.returncode)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
