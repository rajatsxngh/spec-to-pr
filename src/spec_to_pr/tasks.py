"""Eval tasks (F12, N5): format, loading, hidden-test grading and validation.

A task set is a directory with `taskset.toml` (name, version, task ids) and one directory per task:

    <task>/task.toml   id, statement, test_cmd, timeout_s, optional image, commit and workdir
    <task>/repo/       what the agent sees
    <task>/hidden/     files copied over the repo before grading (hidden tests, plus the original
                       visible tests so an agent that weakened them is graded on the originals)
    <task>/solution/   reference fix, copied over the repo (used only to validate the task)

A task with `workdir` (e.g. "/testbed") works on the checkout already inside its `image` instead of a
`repo/` directory, and may give its reference fix as `solution.patch` (applied with `git apply`).
SWE-bench Live tasks have this shape; see swebench_live.py.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .sandbox import Sandbox


@dataclass(frozen=True)
class Task:
    id: str
    dir: Path
    statement: str
    test_cmd: str
    timeout_s: int = 180
    image: str | None = None
    commit: str | None = None
    # Prompt-injection tasks (N3): must exit 0 after the agent runs, before hidden tests are copied in.
    integrity_cmd: str | None = None
    # Performs the planted action; validation checks that integrity_cmd catches it.
    violation_cmd: str | None = None
    # Repo path inside `image` when the image already holds the repo (no repo/ is copied in).
    workdir: str | None = None

    @property
    def repo(self) -> Path:
        return self.dir / "repo"

    @property
    def solution_patch(self) -> Path:
        return self.dir / "solution.patch"

    @property
    def has_solution(self) -> bool:
        return self.solution.is_dir() or self.solution_patch.is_file()

    @property
    def hidden(self) -> Path:
        return self.dir / "hidden"

    @property
    def solution(self) -> Path:
        return self.dir / "solution"


@dataclass(frozen=True)
class TaskSet:
    name: str
    version: str
    dir: Path
    tasks: list[Task]
    content_hash: str  # changes whenever any task file changes


class TaskError(ValueError):
    pass


def load_task(path: Path) -> Task:
    meta = tomllib.loads((path / "task.toml").read_text(encoding="utf-8"))
    for key in ("id", "statement", "test_cmd"):
        if key not in meta:
            raise TaskError(f"{path}: task.toml is missing {key!r}")
    if meta["id"] != path.name:
        raise TaskError(f"{path}: id {meta['id']!r} does not match the directory name")
    if meta.get("workdir"):
        if not meta.get("image"):
            raise TaskError(f"{path}: a task with workdir needs the image that holds the repo")
    elif not (path / "repo").is_dir():
        raise TaskError(f"{path}: missing repo/")
    if not (path / "hidden").is_dir():
        raise TaskError(f"{path}: missing hidden/")
    return Task(
        id=meta["id"],
        dir=path,
        statement=meta["statement"].strip(),
        test_cmd=meta["test_cmd"],
        timeout_s=int(meta.get("timeout_s", 180)),
        image=meta.get("image"),
        commit=meta.get("commit"),
        integrity_cmd=meta.get("integrity_cmd"),
        violation_cmd=meta.get("violation_cmd"),
        workdir=meta.get("workdir"),
    )


def load_taskset(path: Path) -> TaskSet:
    manifest_path = path / "taskset.toml"
    if not manifest_path.exists():
        raise TaskError(f"{path}: no taskset.toml")
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    tasks = [load_task(path / tid) for tid in manifest["tasks"]]
    return TaskSet(manifest["name"], manifest["version"], path, tasks, _hash_dir(path))


def _hash_dir(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        h.update(f.relative_to(path).as_posix().encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def overlay(sandbox: Sandbox, source: Path) -> list[str]:
    """Copy every file under `source` into the sandbox repo at the same relative path."""
    written = []
    for f in sorted(p for p in source.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        rel = f.relative_to(source).as_posix()
        sandbox.write_file(rel, f.read_text(encoding="utf-8"))
        written.append(rel)
    return written


SOLUTION_PATCH_PATH = ".git/spec-to-pr-solution.patch"  # inside the repo but never part of its diff


def apply_solution(task: Task, sandbox: Sandbox) -> None:
    """Apply the task's reference fix: copy solution/ over the repo, or `git apply` solution.patch."""
    if task.solution.is_dir():
        overlay(sandbox, task.solution)
        return
    sandbox.write_file(SOLUTION_PATCH_PATH, task.solution_patch.read_text(encoding="utf-8"))
    r = sandbox.exec(f"git apply --whitespace=nowarn {SOLUTION_PATCH_PATH}", timeout_s=120)
    if r.exit_code != 0:
        raise TaskError(f"{task.id}: solution.patch did not apply: {r.output[-1000:]}")


@dataclass
class Grade:
    resolved: bool
    exit_code: int
    output: str
    integrity_ok: bool = True


def check_integrity(task: Task, sandbox: Sandbox) -> tuple[bool, str]:
    if not task.integrity_cmd:
        return True, ""
    r = sandbox.exec(task.integrity_cmd, timeout_s=60)
    return r.exit_code == 0, r.output


def grade(task: Task, sandbox: Sandbox) -> Grade:
    """Check integrity (planted instructions ignored), then apply the hidden tests and run the test command.
    Resolved needs both."""
    integrity_ok, integrity_out = check_integrity(task, sandbox)
    overlay(sandbox, task.hidden)
    # Grade the code as it is now, never stale bytecode left by an earlier test run.
    sandbox.exec("find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; true", timeout_s=60)
    r = sandbox.exec(task.test_cmd, timeout_s=task.timeout_s)
    out = r.output if integrity_ok else f"INTEGRITY CHECK FAILED: {task.integrity_cmd}\n{integrity_out}\n{r.output}"
    return Grade(resolved=r.exit_code == 0 and integrity_ok, exit_code=r.exit_code, output=out,
                 integrity_ok=integrity_ok)


@dataclass
class Validation:
    task_id: str
    fails_on_base: bool
    passes_with_solution: bool
    base_output: str = ""
    solution_output: str = ""
    violation_caught: bool | None = None  # None when the task has no integrity check

    @property
    def ok(self) -> bool:
        return self.fails_on_base and self.passes_with_solution and self.violation_caught is not False


def validate_task(task: Task, make_sandbox: Callable[[Task], Sandbox]) -> Validation:
    """A good task fails its hidden tests on the original code and passes with the reference fix."""
    base = make_sandbox(task)
    try:
        g_base = grade(task, base)
    finally:
        base.close()
    if not task.has_solution:
        return Validation(task.id, not g_base.resolved, False, g_base.output, "missing solution/")
    fixed = make_sandbox(task)
    try:
        apply_solution(task, fixed)
        g_fixed = grade(task, fixed)
    finally:
        fixed.close()
    caught = None
    if task.integrity_cmd:
        if not task.violation_cmd:
            caught = False
        else:
            bad = make_sandbox(task)
            try:
                apply_solution(task, bad)
                bad.exec(task.violation_cmd, timeout_s=60)
                caught = not check_integrity(task, bad)[0]
            finally:
                bad.close()
    return Validation(task.id, not g_base.resolved, g_fixed.resolved, g_base.output, g_fixed.output, caught)
