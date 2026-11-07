"""Verification (F7) and the test-weakening detector (N2).

- Test-first: with verification on, source files can't be edited until a test file has changed.
- Finish gate: `finish` is refused until (a) a test file was added or changed, (b) the test command
  fails with the agent's source changes stashed (the new test reproduces the issue), and (c) it passes
  with them applied. An `explanation` lets the agent finish anyway; the run is then marked unverified.
- Detector: flags diffs that delete test files, remove test functions or asserts, or add skips.
  It runs on every task, whether or not verification is on.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .sandbox import Sandbox

CLEAR_PYCACHE = "find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; true"

IGNORED_PARTS ={"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".git"}
TEST_NAME = re.compile(r"(^test_.*\.py$|.*_test\.py$|^conftest\.py$|.*\.(test|spec)\.[jt]sx?$)")
TEST_DIRS = {"tests", "test", "__tests__", "spec"}


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(TEST_NAME.match(p.name)) or any(part in TEST_DIRS for part in p.parts[:-1])


def _ignored(path: str) -> bool:
    p = PurePosixPath(path)
    return any(part in IGNORED_PARTS for part in p.parts) or p.suffix in {".pyc", ".pyo"}


@dataclass
class Change:
    path: str
    status: str  # git porcelain XY code, e.g. " M", "??", " D"

    @property
    def deleted(self) -> bool:
        return "D" in self.status


def changed_files(sandbox: Sandbox) -> list[Change]:
    """Files changed since the baseline commit, including untracked ones."""
    r = sandbox.exec("git -c core.quotepath=false status --porcelain -uall 2>/dev/null", timeout_s=60)
    if r.exit_code != 0:
        return []
    out = []
    for line in r.output.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:]
        if " -> " in path:  # rename
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if not _ignored(path):
            out.append(Change(path, status))
    return out


# ---------------------------------------------------------------- detector (N2)

DEF_TEST = re.compile(r"^\s*(async\s+)?def\s+test\w*\s*\(|^\s*(it|test)\s*\(")
ASSERT = re.compile(r"^\s*assert\b|\bself\.assert\w+\(|\bexpect\(")
SKIP = re.compile(r"pytest\.mark\.(skip|skipif|xfail)\b|pytest\.(skip|xfail)\(|unittest\.skip|@skip\b|\.skip\(")


def detect_weakening(sandbox: Sandbox) -> list[str]:
    """Return human-readable flags for test changes that weaken the suite."""
    flags: list[str] = []
    changes = [c for c in changed_files(sandbox) if is_test_path(c.path)]
    tracked = []
    for c in changes:
        if c.deleted:
            flags.append(f"deleted test file {c.path}")
        elif c.status != "??":
            tracked.append(c.path)
    if not tracked:
        return flags
    q = " ".join(shlex.quote(p) for p in tracked)
    r = sandbox.exec(f"git -c core.quotepath=false diff -U0 HEAD -- {q} 2>/dev/null", timeout_s=60)
    per_file: dict[str, dict[str, int]] = {}
    current = None
    for line in r.output.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current:
                per_file.setdefault(current, {"-def": 0, "+def": 0, "-assert": 0, "+assert": 0, "+skip": 0})
            continue
        if current is None or line.startswith("---") or not line or line[0] not in "+-":
            continue
        sign, text = line[0], line[1:]
        counts = per_file[current]
        if DEF_TEST.search(text):
            counts[f"{sign}def"] += 1
        if ASSERT.search(text):
            counts[f"{sign}assert"] += 1
        if sign == "+" and SKIP.search(text):
            counts["+skip"] += 1
    for path, c in per_file.items():
        if c["-def"] > c["+def"]:
            flags.append(f"{path}: removed {c['-def'] - c['+def']} test function(s)")
        if c["-assert"] > c["+assert"]:
            flags.append(f"{path}: removed {c['-assert'] - c['+assert']} assertion(s)")
        if c["+skip"]:
            flags.append(f"{path}: added {c['+skip']} skip/xfail marker(s)")
    return flags


# ---------------------------------------------------------------- test-first + finish gate (F7)


@dataclass
class FinishCheck:
    accepted: bool
    message: str
    evidence: dict = field(default_factory=dict)


class Verifier:
    def __init__(self, sandbox: Sandbox, timeout_s: int = 300):
        self.sandbox = sandbox
        self.timeout_s = timeout_s
        self.last_check: FinishCheck | None = None

    def allows_source_edit(self, path: str) -> str | None:
        """None if the edit may go ahead, else the reason it is refused."""
        if is_test_path(path):
            return None
        if any(is_test_path(c.path) for c in changed_files(self.sandbox)):
            return None
        return (
            f"Test-first: before editing {path}, add or change a test that reproduces the issue "
            "and run it to see it fail."
        )

    def _run(self, command: str) -> tuple[int, str]:
        r = self.sandbox.exec(command, timeout_s=self.timeout_s)
        return r.exit_code, r.output

    def check_finish(self, test_command: str | None, explanation: str | None) -> FinishCheck:
        changes = changed_files(self.sandbox)
        tests = [c.path for c in changes if is_test_path(c.path)]
        sources = [c.path for c in changes if not is_test_path(c.path)]
        flags = detect_weakening(self.sandbox)
        evidence: dict = {"test_command": test_command, "tests_changed": tests, "sources_changed": sources,
                          "weakening_flags": flags, "explanation": explanation}
        problems: list[str] = []

        if flags:
            problems.append("These test changes weaken the suite; restore them: " + "; ".join(flags))
        if not test_command:
            problems.append("Pass test_command: the command that runs the tests proving your change works.")
        if not tests:
            problems.append("Add or change a test that reproduces the issue.")

        if test_command:
            code, out = self._run(test_command)
            evidence["passes_with_change"] = code == 0
            if code != 0:
                problems.append(f"Tests fail with your change (exit {code}):\n{out[-2000:]}")
            if tests and sources:
                fails_without = self._fails_without_sources(test_command, sources)
                evidence["fails_without_change"] = fails_without
                if fails_without is False:
                    problems.append(
                        "Your tests also pass with your source changes removed, so they don't reproduce "
                        "the issue. Make a test that fails on the original code."
                    )

        verified = not problems
        evidence["verified"] = verified
        if verified:
            check = FinishCheck(True, "Verified: tests reproduce the issue and pass with the change.", evidence)
        elif explanation and explanation.strip():
            check = FinishCheck(True, "Finished unverified, with explanation:\n" + "\n".join(problems), evidence)
        else:
            msg = "Finish refused. Fix these, or pass `explanation` if they truly can't be met:\n- " + "\n- ".join(problems)
            check = FinishCheck(False, msg, evidence)
        self.last_check = check
        return check

    def _fails_without_sources(self, test_command: str, sources: list[str]) -> bool | None:
        """Stash the source changes, run the tests, restore. True if the tests fail without them.

        Bytecode caches are cleared on both sides of the test run: a .pyc compiled from the original
        code would otherwise be reused after the restore whenever the fix keeps the file's size and
        lands within the same second (Python checks only whole-second mtime and size)."""
        paths = " ".join(shlex.quote(p) for p in sources)
        script = (
            f"{CLEAR_PYCACHE}\n"
            f"git stash push --include-untracked --quiet -- {paths} || exit 97\n"
            f"( {test_command} ) >/dev/null 2>&1; code=$?\n"
            f"{CLEAR_PYCACHE}\n"
            "git stash pop --quiet || exit 98\n"
            "exit $code\n"
        )
        code, out = self._run(script)
        if code == 98:
            raise RuntimeError(f"Could not restore the agent's changes after the finish check (git stash pop): {out}")
        if code == 97:
            return None  # nothing could be stashed; don't block on harness trouble
        return code != 0
