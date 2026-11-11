"""Integration checks, run after every milestone: `uv run python scripts/check.py`.

Runs the test suite, validates every task set, runs the oracle agent through the real CLI with every
ablation config, runs the injection set with the full harness, runs a no-op eval, and checks the CLI.
Scoreboards are read back so the numbers are checked, not just exit codes. No API calls; Docker is
used when available, else the local sandbox.

Options: --fast skips the test suite (it runs separately in CI).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ["bare", "verification", "skills", "planning", "context"]
COMMANDS = ["solve", "run", "eval", "experiment", "validate-tasks", "import-swebench-live", "build-image"]


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return False
    return subprocess.run(["docker", "image", "inspect", "spec-to-pr-sandbox:latest"], capture_output=True).returncode == 0


SANDBOX = "docker" if docker_ready() else "local"
results: list[tuple[str, str, str]] = []  # (check, status, detail)


def run(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def record(name: str, ok: bool, detail: str, t0: float) -> None:
    results.append((name, "PASS" if ok else "FAIL", f"{detail} ({time.monotonic() - t0:.0f}s)"))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def eval_scoreboard(tasks: str, config: str, agent: str, out: Path) -> dict | None:
    proc = run(["uv", "run", "spec-to-pr", "eval", tasks, "--config", f"configs/{config}.toml", "--agent", agent,
                "--max-total-usd", "0", "--sandbox", SANDBOX, "--out", str(out)])
    boards = sorted(out.glob("*/scoreboard.json"))
    if proc.returncode != 0 or not boards:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
        return None
    board = json.loads(boards[-1].read_text(encoding="utf-8"))
    shutil.rmtree(out, ignore_errors=True)
    return board


def main() -> int:
    fast = "--fast" in sys.argv
    print(f"Sandbox: {SANDBOX}" + ("" if SANDBOX == "docker" else " (Docker or the sandbox image is unavailable)"))
    tmp = Path(tempfile.mkdtemp(prefix="spec-to-pr-check-"))

    if fast:
        results.append(("test suite", "SKIP", "--fast"))
    else:
        t0 = time.monotonic()
        proc = run(["uv", "run", "--extra", "tracing", "--extra", "report", "pytest", "-q"])
        summary = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
        record("test suite", proc.returncode == 0, summary, t0)

    for tasks in ("tasks/self", "tasks/injection"):
        t0 = time.monotonic()
        proc = run(["uv", "run", "spec-to-pr", "validate-tasks", tasks, "--sandbox", SANDBOX])
        last = (proc.stdout.strip().splitlines() or ["(no output)"])[-1]
        record(f"validate {tasks}", proc.returncode == 0, last, t0)

    for config in CONFIGS:
        t0 = time.monotonic()
        sb = eval_scoreboard("tasks/self", config, "oracle", tmp / config)
        ok = bool(sb) and sb["resolve_rate_mean"] == 1.0 and sb["harness_errors"] == 0 \
            and sb["test_weakening_flagged"] == 0
        detail = f"{sb['resolved']}/{sb['task_runs']} resolved, {sb['harness_errors']} harness errors" if sb else "eval failed"
        record(f"oracle eval, config {config}", ok, detail, t0)

    t0 = time.monotonic()
    sb = eval_scoreboard("tasks/injection", "context", "oracle", tmp / "injection")
    ok = bool(sb) and sb["resolved"] == sb["task_runs"] == 3 and sb["integrity_failures"] == 0
    record("injection set, full harness", ok,
           f"{sb['resolved']}/3 resolved, {sb['integrity_failures']} planted instructions followed" if sb else "eval failed", t0)

    t0 = time.monotonic()
    sb = eval_scoreboard("tasks/self", "bare", "noop", tmp / "noop")
    ok = bool(sb) and sb["resolved"] == 0 and sb["task_runs"] == 10
    record("noop eval resolves nothing", ok, f"{sb['resolved']}/{sb['task_runs']} resolved" if sb else "eval failed", t0)

    t0 = time.monotonic()
    out = tmp / "experiment"
    proc = run(["uv", "run", "spec-to-pr", "experiment", "experiments/smoke.toml", "--agent", "oracle",
                "--sandbox", SANDBOX, "--out", str(out)])
    results_file = out / "experiment-smoke-oracle" / "results.json"
    ok, detail = False, "experiment failed"
    if proc.returncode == 0 and results_file.exists():
        cells = json.loads(results_file.read_text(encoding="utf-8"))["cells"]
        report_dir = results_file.parent
        charts = all((report_dir / f).exists() for f in ("report.md", "ablation.png", "cost_quality.png"))
        ok = len(cells) == 10 and all(c["resolve_rate_mean"] == 1.0 for c in cells) and charts
        detail = f"{sum(c['resolve_rate_mean'] == 1.0 for c in cells)}/{len(cells)} cells at 100%, " \
                 f"report and charts {'written' if charts else 'MISSING'}"
    else:
        print(proc.stdout[-2000:], proc.stderr[-2000:])
    record("experiment smoke (5 configs x 2 models)", ok, detail, t0)

    # The experiment's output feeds the README updater (run on a copy of the README).
    t0 = time.monotonic()
    ok, detail = False, "no experiment output"
    if results_file.exists():
        import importlib.util

        spec = importlib.util.spec_from_file_location("update_readme", ROOT / "scripts" / "update_readme.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        readme = tmp / "README.md"
        readme.write_text((ROOT / "README.md").read_text(encoding="utf-8"), encoding="utf-8")
        try:
            mod.update(results_file.parent, readme, tmp / "docs" / "img", allow_oracle=True)
            text = readme.read_text(encoding="utf-8")
            ok = "| + Context |" in text and (tmp / "docs" / "img" / "ablation.png").exists()
            detail = "results section and charts written" if ok else "results section incomplete"
        except SystemExit as e:
            detail = f"updater failed: {e}"
    record("README results from experiment", ok, detail, t0)

    t0 = time.monotonic()
    missing = [c for c in COMMANDS if run(["uv", "run", "spec-to-pr", c, "--help"]).returncode != 0]
    record("CLI commands", not missing, f"missing or broken: {missing}" if missing else f"{len(COMMANDS)} commands load", t0)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n| Check | Result | Detail |\n| --- | --- | --- |")
    for name, status, detail in results:
        print(f"| {name} | {status} | {detail} |")
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed ({SANDBOX} sandbox).")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
