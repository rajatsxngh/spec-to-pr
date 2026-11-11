"""SWE-bench Live importer and grading, with a small fake instance. No network, no Docker."""

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from conftest import ROOT
from spec_to_pr import swebench_grade as grader
from spec_to_pr import swebench_live as swl
from spec_to_pr.config import SandboxLimits
from spec_to_pr.evaluate import load_config, oracle_model, run_eval
from spec_to_pr.sandbox import LocalSandbox
from spec_to_pr.tasks import grade, load_task, load_taskset, validate_task

GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "core.autocrlf=false"]

CALC = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a + b  # bug\n"
CALC_FIXED = CALC.replace("return a + b  # bug", "return a * b")
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
TESTS_NEW = TESTS.replace("from calc import add", "from calc import add, mul") + \
    "\n\ndef test_mul():\n    assert mul(2, 3) == 6\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run([*GIT, *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def diff_for(repo: Path, path: str, new: str) -> str:
    """The git diff that turns `path` into `new` (left unapplied)."""
    old = (repo / path).read_text(encoding="utf-8") if (repo / path).exists() else None
    (repo / path).write_text(new, encoding="utf-8", newline="\n")
    git(repo, "add", "-N", path)
    out = git(repo, "diff", "--", path)
    if old is None:
        git(repo, "rm", "-q", "--cached", path)
        (repo / path).unlink()
    else:
        (repo / path).write_text(old, encoding="utf-8", newline="\n")
    return out


@pytest.fixture
def fake(tmp_path):
    """A git repo standing in for an instance image's /testbed, and its instance record."""
    repo = tmp_path / "testbed"
    (repo / "tests").mkdir(parents=True)
    (repo / "calc.py").write_text(CALC, encoding="utf-8", newline="\n")
    (repo / "tests" / "test_calc.py").write_text(TESTS, encoding="utf-8", newline="\n")
    (repo / "tests" / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1]))\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD").strip()
    inst = {
        "repo": "acme/calc", "pull_number": "7", "instance_id": "acme__calc-7", "issue_numbers": ["6"],
        "base_commit": base,
        "patch": diff_for(repo, "calc.py", CALC_FIXED),
        "test_patch": diff_for(repo, "tests/test_calc.py", TESTS_NEW),
        "problem_statement": "`mul(2, 3)` returns 5 instead of 6.\nIt adds instead of multiplying. Don't use ''' here.'",
        "hints_text": "", "created_at": "2025-08-30T10:00:00",
        "test_cmds": ["python -m pytest -rA -p no:cacheprovider tests"],
        "log_parser": "pytest", "rebuild_cmds": [],
        "FAIL_TO_PASS": ["tests/test_calc.py::test_mul"],
        "PASS_TO_PASS": ["tests/test_calc.py::test_add", "tests/test_calc.py::test_not_in_log"],
        "docker_image": "starryzhang/sweb.eval.x86_64.acme_1776_calc-7",
        "difficulty": {"files": 1, "hunks": 1, "lines": 2},
    }
    return repo, inst


def local_factory(repo: Path):
    return lambda task: LocalSandbox(repo, SandboxLimits(command_timeout_s=120))


# ---------------------------------------------------------------- grader (official parser and rule)


LOG = """============ short test summary info ============
PASSED tests/test_a.py::test_ok
PASSED tests/test_a.py::test_param[a b]
FAILED tests/test_a.py::test_bad - AssertionError: boom
XFAIL tests/test_a.py::test_expected - reason
SKIPPED [1] tests/test_a.py:9: no network
ERROR tests/test_b.py::test_setup
"""


def test_grader_parses_pytest_summary_like_the_official_harness():
    status = grader.parse_log(LOG)
    assert status["tests/test_a.py::test_ok"] == "pass"
    assert status["tests/test_a.py::test_bad"] == "fail"
    assert status["tests/test_a.py::test_expected"] == "pass"  # XFAIL counts as pass
    assert status["tests/test_b.py::test_setup"] == "fail"
    assert status["tests/test_a.py::test_param[a"] == "pass"  # ids are split on spaces, as upstream does


def test_grader_resolve_rule():
    status = grader.parse_log(LOG)
    ok = grader.evaluate(status, ["tests/test_a.py::test_ok"], ["tests/test_a.py::test_missing"])
    assert ok["resolved"]  # a PASS_TO_PASS test absent from the log is not a failure
    assert not grader.evaluate(status, ["tests/test_a.py::test_bad"], [])["resolved"]
    assert not grader.evaluate(status, ["tests/test_a.py::test_ok"], ["tests/test_b.py::test_setup"])["resolved"]
    assert not grader.evaluate(status, ["tests/test_a.py::test_never_ran"], [])["resolved"]


def test_grader_main_exit_codes(tmp_path):
    (tmp_path / "log").write_text(LOG, encoding="utf-8")
    (tmp_path / "t.json").write_text(json.dumps({"FAIL_TO_PASS": ["tests/test_a.py::test_ok"],
                                                 "PASS_TO_PASS": []}), encoding="utf-8")
    assert grader.main(["g", str(tmp_path / "log"), str(tmp_path / "t.json")]) == 0
    (tmp_path / "t.json").write_text(json.dumps({"FAIL_TO_PASS": ["tests/test_a.py::test_bad"],
                                                 "PASS_TO_PASS": []}), encoding="utf-8")
    assert grader.main(["g", str(tmp_path / "log"), str(tmp_path / "t.json")]) == 1


# ---------------------------------------------------------------- selection


def test_static_rules(fake):
    _, inst = fake
    rules = swl.Rules(since="2025-06-01")
    assert swl.static_reject(inst, rules) is None
    assert "before" in swl.static_reject({**inst, "created_at": "2025-01-01"}, rules)
    assert "parser" in swl.static_reject({**inst, "log_parser": "jest"}, rules)
    assert "binary" in swl.static_reject({**inst, "patch": "GIT binary patch\n"}, rules)
    assert "carriage" in swl.static_reject({**inst, "test_patch": inst["test_patch"].replace("\n", "\r\n")}, rules)
    assert "FAIL_TO_PASS" in swl.static_reject({**inst, "FAIL_TO_PASS": []}, rules)
    assert "PASS_TO_PASS" in swl.static_reject({**inst, "PASS_TO_PASS": ["x"] * 6000}, rules)


def test_candidates_cap_per_repo_and_image_size(fake):
    _, inst = fake
    insts = [{**inst, "instance_id": f"acme__calc-{i}", "docker_image": f"img{i}"} for i in range(4)]
    insts.append({**inst, "instance_id": "other__x-1", "repo": "other/x", "docker_image": "big"})
    sizes = {"img0": 0.5, "img1": 0.6, "img2": 0.7, "img3": 0.4, "big": 9.0}
    rejected = {}
    got = list(swl.candidates(insts, swl.Rules(per_repo=2, max_image_gb=1.5), sizes.get,
                              on_reject=lambda i, r: rejected.setdefault(i["instance_id"], r)))
    assert [i["instance_id"] for i in got] == ["acme__calc-0", "acme__calc-1"]
    assert "already 2" in rejected["acme__calc-2"]
    assert "9.00 GB" in rejected["other__x-1"]


def test_patch_files():
    patch = ("diff --git a/src/x.py b/src/x.py\n--- a/src/x.py\n+++ b/src/x.py\n"
             "diff --git a/old.py b/new.py\nrename from old.py\nrename to new.py\n")
    assert swl.patch_files(patch) == ["src/x.py", "old.py", "new.py"]


# ---------------------------------------------------------------- task files and grading


def test_written_task_loads(fake, tmp_path):
    _, inst = fake
    d = swl.write_task(inst, tmp_path / "set")
    task = load_task(d)
    assert task.workdir == "/testbed" and task.image == inst["docker_image"]
    assert task.statement == inst["problem_statement"].strip()
    assert task.test_cmd == "bash .swebench_live/run.sh"
    assert task.has_solution and not task.repo.exists()
    meta = tomllib.loads((d / "task.toml").read_text(encoding="utf-8"))["swebench_live"]
    assert meta["base_commit"] == inst["base_commit"] and meta["fail_to_pass"] == 1
    kit = d / "hidden" / ".swebench_live"
    assert (kit / "test_files.txt").read_text(encoding="utf-8").split() == ["tests/test_calc.py"]
    assert json.loads((kit / "tests.json").read_text(encoding="utf-8"))["FAIL_TO_PASS"] == inst["FAIL_TO_PASS"]
    # The agent-facing task never contains the tests or the fix.
    assert "test_mul" not in (d / "task.toml").read_text(encoding="utf-8")


def test_fake_instance_validates_end_to_end(fake, tmp_path):
    repo, inst = fake
    task = load_task(swl.write_task(inst, tmp_path / "set"))
    v = validate_task(task, local_factory(repo))
    assert v.fails_on_base, v.base_output[-1500:]
    assert v.passes_with_solution, v.solution_output[-1500:]
    assert "NOT RESOLVED" in v.base_output and "FAIL_TO_PASS passing: 1/1" in v.solution_output


def test_agent_edits_to_patched_tests_do_not_count(fake, tmp_path):
    repo, inst = fake
    task = load_task(swl.write_task(inst, tmp_path / "set"))
    box = local_factory(repo)(task)
    try:
        # An agent that "fixes" the bug by rewriting the test file the test patch touches.
        box.write_file("tests/test_calc.py", "def test_mul():\n    pass\n")
        g = grade(task, box)
        assert not g.resolved, g.output[-1500:]
    finally:
        box.close()


def test_oracle_resolves_patch_task(fake, tmp_path):
    repo, inst = fake
    swl.write_taskset(tmp_path / "set", [], "t")  # ensure dir and manifest exist
    swl.write_task(inst, tmp_path / "set")
    swl.write_taskset(tmp_path / "set", [inst["instance_id"]], "t")
    ts = load_taskset(tmp_path / "set")
    results = run_eval(ts, load_config(ROOT / "configs" / "bare.toml"), oracle_model, local_factory(repo),
                       tmp_path / "out", workers=1, max_total_usd=0, reserve_per_task=0.0)
    assert results[0].resolved, results[0].detail


# ---------------------------------------------------------------- import pipeline


def test_import_keeps_valid_tasks_and_appends(fake, tmp_path):
    _, inst = fake
    insts = [{**inst, "instance_id": f"acme__calc-{i}", "repo": f"acme/r{i}", "docker_image": f"img{i}"}
             for i in range(5)]
    out = tmp_path / "set"
    bad = {"acme__calc-1"}
    removed = []
    outcomes = swl.import_tasks(
        insts, out, swl.Rules(), limit=2, version="v1", size_of=lambda img: 0.5,
        validate=lambda d: (d.name not in bad, "gold patch failed"),
        prepare=lambda img: (1.0, 2.0), cleanup=removed.append, log=lambda s: None)
    assert [o.instance_id for o in outcomes if o.accepted] == ["acme__calc-0", "acme__calc-2"]
    assert removed == ["img1"] and not (out / "acme__calc-1").exists()
    ts = load_taskset(out)
    assert [t.id for t in ts.tasks] == ["acme__calc-0", "acme__calc-2"] and ts.version == "v1"
    stats = json.loads((out / "import-stats.json").read_text(encoding="utf-8"))
    assert stats["acme__calc-0"]["image_disk_gb"] == 2.0
    # A second run appends and skips tasks already in the set.
    swl.import_tasks(insts, out, swl.Rules(), limit=3, version="v1", size_of=lambda img: 0.5,
                     validate=lambda d: (True, ""), log=lambda s: None)
    assert [t.id for t in load_taskset(out).tasks] == ["acme__calc-0", "acme__calc-2", "acme__calc-1"]


def test_rejected_task_frees_its_repo_quota(fake, tmp_path):
    _, inst = fake
    insts = [{**inst, "instance_id": f"acme__calc-{i}", "docker_image": f"img{i}"} for i in range(3)]
    outcomes = swl.import_tasks(insts, tmp_path / "set", swl.Rules(per_repo=1), limit=5, version="v1",
                                size_of=lambda img: 0.5, validate=lambda d: (d.name != "acme__calc-0", "no"),
                                log=lambda s: None)
    assert [o.instance_id for o in outcomes if o.accepted] == ["acme__calc-1"]


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def test_fetch_paginates_and_retries(monkeypatch):
    monkeypatch.setattr(swl.time, "sleep", lambda s: None)
    rows = [{"instance_id": f"i{n}"} for n in range(5)]
    calls = []

    def get(url, params=None):
        calls.append(params)
        if len(calls) == 1:
            return FakeResp(500, {"error": "Authentication check on the Hugging Face Hub failed"})
        off, n = params["offset"], params["length"]
        return FakeResp(200, {"rows": [{"row": r} for r in rows[off:off + n]], "num_rows_total": len(rows)})

    got = list(swl.fetch_instances("2025-06-01", page=2, get=get))
    assert [r["instance_id"] for r in got] == [f"i{n}" for n in range(5)]
    assert calls[1]["where"] == "\"created_at\">='2025-06-01'" and calls[1]["orderby"] == "\"created_at\" DESC"
    with pytest.raises(ValueError):
        list(swl.fetch_instances("June", get=get))


def test_image_size_from_docker_hub():
    def get(url, params=None):
        assert url.endswith("/starryzhang/sweb.eval.x86_64.acme_1776_calc-7/tags/latest")
        return FakeResp(200, {"full_size": 450_000_000})

    assert swl.image_size_gb("starryzhang/sweb.eval.x86_64.acme_1776_calc-7", get=get) == 0.45
    assert swl.image_size_gb("x/y", get=lambda url, params=None: FakeResp(404, {})) is None


def test_fetch_falls_back_to_rows_when_filter_fails(monkeypatch):
    monkeypatch.setattr(swl.time, "sleep", lambda s: None)
    rows = [{"instance_id": f"i{n}", "created_at": f"2025-0{n + 1}-15T00:00:00"} for n in range(6)]

    def get(url, params=None):
        if url.endswith("/filter"):
            return FakeResp(500, {"error": "Authentication check on the Hugging Face Hub failed"})
        off, n = params["offset"], params["length"]
        return FakeResp(200, {"rows": [{"row": r} for r in rows[off:off + n]], "num_rows_total": len(rows)})

    got = [r["instance_id"] for r in swl.fetch_instances("2025-03-01", page=2, get=get)]
    assert got == ["i5", "i4", "i3", "i2"]  # newest first, stops at the first page older than since
