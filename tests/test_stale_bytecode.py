"""Regression: a same-size fix must never be graded against stale bytecode.

The original bug: the finish gate stashes the fix and runs the tests (compiling the original code to
.pyc), then restores the fix. When the fix keeps the file's size and the restore lands in the same
second, Python's .pyc check (whole-second mtime + size) passes and the old code runs. Found by the
experiment smoke check in Docker on inj-001-readme (`5 / 9` -> `9 / 5`).
"""

import pytest

from conftest import ROOT
from spec_to_pr.agent import _ensure_git_baseline
from spec_to_pr.config import SandboxLimits
from spec_to_pr.sandbox import DockerSandbox, LocalSandbox, docker_available
from spec_to_pr.tasks import grade, load_taskset, overlay
from spec_to_pr.tools import Toolbox
from spec_to_pr.verification import Verifier

TASK = load_taskset(ROOT / "tasks" / "injection").tasks[0]
assert TASK.id == "inj-001-readme"


def sandboxes():
    yield pytest.param(lambda: LocalSandbox(TASK.repo, SandboxLimits()), id="local")
    yield pytest.param(lambda: DockerSandbox(TASK.repo, SandboxLimits()), id="docker",
                       marks=pytest.mark.skipif(not docker_available(), reason="Docker not available"))


@pytest.mark.parametrize("make", list(sandboxes()))
def test_same_size_fix_survives_finish_gate_and_grading(make):
    for _ in range(4):  # the race is timing-dependent; repeat to make a regression likely to show
        box = make()
        try:
            _ensure_git_baseline(box)
            tb = Toolbox(box, 60, verifier=Verifier(box, timeout_s=120))
            hidden = (TASK.hidden / "tests" / "test_temperature_hidden.py").read_text(encoding="utf-8")
            assert not tb.call("edit_file", {"path": "tests/test_c_to_f.py", "old_str": "", "new_str": hidden}).is_error
            fix = {"path": "temperature.py", "old_str": "celsius * 5 / 9 + 32", "new_str": "celsius * 9 / 5 + 32"}
            assert len(fix["old_str"]) == len(fix["new_str"])  # the size-preserving edit that triggers the bug
            assert not tb.call("edit_file", fix).is_error
            r = tb.call("finish", {"summary": "x", "test_command": TASK.test_cmd})
            assert r.finished, r.output
            assert tb.verifier.last_check.evidence["fails_without_change"] is True
            g = grade(TASK, box)
            assert g.resolved, g.output[-1500:]
        finally:
            box.close()


def test_sandboxes_disable_bytecode():
    box = LocalSandbox(TASK.repo, SandboxLimits())
    try:
        assert box.exec("echo $PYTHONDONTWRITEBYTECODE", 30).output.strip() == "1"
    finally:
        box.close()
