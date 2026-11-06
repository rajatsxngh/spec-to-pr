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


@app.command("run")
def run_cmd(
    spec: str = typer.Argument(..., help="Markdown spec file, or a GitHub issue URL."),
    repo: Path = typer.Option(Path("."), exists=True, file_okay=False, help="Git repository to change."),
    dry_run: bool = typer.Option(False, help="Stop at a local branch: no push, no PR."),
    planning: bool = typer.Option(True, help="Draft plan.md and wait for approval before editing (F4)."),
    auto_approve: bool = typer.Option(False, help="Approve the plan without asking (eval mode)."),
    sandbox: str = typer.Option("docker", help="docker (isolated) or local (unsafe, for development only)."),
    image: str = typer.Option(DEFAULT_IMAGE),
    model: str = typer.Option(ModelSettings.model),
    effort: str = typer.Option(ModelSettings.effort),
    max_steps: int = typer.Option(Budget.max_steps),
    max_usd: float = typer.Option(Budget.max_usd),
    max_minutes: float = typer.Option(Budget.max_seconds / 60),
    out: Path = typer.Option(Path("runs")),
) -> None:
    """Spec in, draft PR out: run the agent with verification, then open a draft PR."""
    import sys

    from .github import GitHubError, RestGitHub, github_token, parse_issue_url
    from .planning import auto_approve as auto_approver
    from .prflow import RepoError, inspect_repo, run_spec, spec_from_issue
    from .tracing import setup_tracing

    try:
        price_for(model)
    except KeyError as e:
        raise typer.BadParameter(str(e.args[0]), param_hint="--model") from None
    if planning and not auto_approve and not sys.stdin.isatty():
        raise typer.BadParameter("Plan approval needs a terminal; pass --auto-approve to skip it.")

    # Fail before spending anything: repo state, token, and the spec or issue.
    github, issue = None, None
    try:
        state = inspect_repo(repo, need_remote=not dry_run)
        token = github_token()
        if not dry_run:
            if not token:
                raise typer.BadParameter("No GitHub token: set GH_TOKEN or log in with `gh auth login`, or use --dry-run.")
            github = RestGitHub(token)
        parsed = parse_issue_url(spec)
        if parsed:
            owner, name, number = parsed
            if state.owner and (owner.lower(), name.lower()) != (state.owner.lower(), state.name.lower()):
                raise GitHubError(f"The issue is in {owner}/{name} but the repo's origin is {state.owner}/{state.name}")
            issue = (github or RestGitHub(token)).get_issue(owner, name, number)
            spec_text = spec_from_issue(issue)
        else:
            path = Path(spec)
            if not path.is_file():
                raise typer.BadParameter(f"{spec} is neither a file nor a GitHub issue URL", param_hint="SPEC")
            spec_text = path.read_text(encoding="utf-8")
    except (RepoError, GitHubError) as e:
        raise typer.BadParameter(str(e), param_hint="--repo") from None

    config = RunConfig(
        budget=Budget(max_steps=max_steps, max_usd=max_usd, max_seconds=max_minutes * 60),
        model=ModelSettings(model=model, effort=effort),
        verification=True,
        skills=True,
        planning=planning,
    )
    setup_tracing()
    factory = _sandbox_factory(sandbox, image, config.sandbox)
    make = lambda root: factory(_RepoTask(root))  # noqa: E731
    run_dir = out / time.strftime("%Y%m%d-%H%M%S")
    approver = auto_approver if auto_approve else _terminal_approver
    outcome = run_spec(spec_text, repo, config, AnthropicModel(config.model), make, github, run_dir,
                       approver=approver, issue=issue)
    if outcome.result:
        b = outcome.result.budget
        typer.echo(f"agent: {outcome.result.status}; cost ${b['cost_usd']:.4f}, {b['steps']} steps, {b['elapsed_s']}s")
    typer.echo(outcome.reason)
    if outcome.body_path:
        typer.echo(f"PR description: {outcome.body_path}")
    typer.echo(f"report: {run_dir / 'report.md'}")
    raise typer.Exit(0 if outcome.status != "no_pr" else 1)


def _terminal_approver(plan: str):
    """Show plan.md and ask: approve, edit (opens $EDITOR), or reject."""
    from .planning import Decision, missing_sections

    typer.echo("\n" + "=" * 72 + "\nPLAN\n" + "=" * 72 + f"\n{plan}\n" + "=" * 72)
    while True:
        choice = typer.prompt("Approve, edit or reject the plan? [a/e/r]", default="a").strip().lower()[:1]
        if choice == "a":
            return Decision("approve", plan)
        if choice == "r":
            note = typer.prompt("Reason (optional)", default="", show_default=False)
            return Decision("reject", plan, note=note)
        if choice == "e":
            edited = typer.edit(plan, extension=".md")
            if edited is None or not edited.strip():
                typer.echo("No changes saved; asking again.")
                continue
            missing = missing_sections(edited)
            if missing:
                typer.echo(f"The edited plan is missing: {', '.join(missing)}. Try again.")
                continue
            return Decision("edit", edited.strip())


class _RepoTask:
    """Adapts a repo root to the task shape the sandbox factory expects."""

    def __init__(self, root: Path):
        self.repo, self.image, self.commit = root, None, None


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
    from .evaluate import ConfigError, load_config, noop_model, oracle_for, run_eval, write_scoreboard
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
        make_model = oracle_for(cfg)  # satisfies the config's components (plan, repro test, review)
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


@app.command("experiment")
def experiment_cmd(
    spec_file: Path = typer.Argument(Path("experiments/full.toml"), exists=True, dir_okay=False),
    agent: str = typer.Option("claude", help="claude, or oracle to check the pipeline without API calls."),
    estimate_only: bool = typer.Option(False, "--estimate-only", help="Print the size and worst-case cost, then stop."),
    yes: bool = typer.Option(False, "--yes", help="Confirm spending up to the spec's cap (required for claude)."),
    sandbox: str = typer.Option("docker"),
    image: str = typer.Option(DEFAULT_IMAGE),
    out: Path = typer.Option(Path("results")),
) -> None:
    """Run every config × model × run in a spec, resuming finished cells, then write the report and charts."""
    from .evaluate import oracle_for
    from .experiment import SpecError, estimate, load_spec, run_experiment
    from .report import write_report

    try:
        spec = load_spec(spec_file)
    except SpecError as e:
        raise typer.BadParameter(str(e)) from None
    est = estimate(spec)
    typer.echo(f"Experiment {spec.name}: {est['cells']} cells ({len(spec.configs)} configs × {len(spec.models)} models), "
               f"{est['tasks']} tasks × {spec.runs} runs = {est['task_runs']} task runs.")
    typer.echo(f"Worst case ${est['worst_case_usd']:.2f} if every run spends its full budget; "
               f"the spend cap stops it at ${spec.max_total_usd:.2f}.")
    if estimate_only:
        return
    if agent == "claude":
        for m in spec.models:
            try:
                price_for(m)
            except KeyError as e:
                raise typer.BadParameter(str(e.args[0]), param_hint="models") from None
        if not yes:
            raise typer.BadParameter(f"This spends real money (up to ${spec.max_total_usd:.2f}); pass --yes to confirm.")
        make_model = lambda cfg: (lambda task: AnthropicModel(cfg.run.model))  # noqa: E731
        reserve = None
    elif agent == "oracle":
        make_model, reserve = oracle_for, 0.0
    else:
        raise typer.BadParameter("--agent must be claude or oracle")

    out_dir = out / f"experiment-{spec.name}-{agent}"

    def progress(cfg, sb, cached):
        state = "cached" if cached else "done"
        typer.echo(f"  [{state}] {cfg.name} × {cfg.run.model.model}: {sb['resolve_rate_mean']:.0%} resolved, "
                   f"${sb['total_cost_usd']:.2f}" + (f", {sb['skipped']} skipped by the cap" if sb["skipped"] else ""))

    boards = run_experiment(spec, make_model, _sandbox_factory(sandbox, image, SandboxLimits()), out_dir,
                            reserve_per_task=reserve, on_cell=progress)
    files = write_report(boards, [load_config_name(c) for c in spec.configs], spec.models, out_dir, spec.name)
    typer.echo(f"Total ${sum(b['total_cost_usd'] for b in boards):.2f}. Report: " + ", ".join(str(f) for f in files))


def load_config_name(path: Path) -> str:
    from .evaluate import load_config

    return load_config(path).name


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
                   f"violation_caught={v.violation_caught} "
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
