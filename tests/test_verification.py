import pytest

from conftest import ROOT
from spec_to_pr.agent import _ensure_git_baseline, run_agent
from spec_to_pr.config import RunConfig
from spec_to_pr.evaluate import load_config
from spec_to_pr.models import ScriptedModel, ScriptedTurn
from spec_to_pr.tools import Toolbox
from spec_to_pr.verification import Verifier, detect_weakening, is_test_path

PYTEST = "python -m pytest -q -p no:cacheprovider"
REPRO = "from stats import median\n\n\ndef test_even():\n    assert median([4, 1, 3, 2]) == 2.5\n"
PASSES_ON_BASE = "from stats import mean\n\n\ndef test_mean_again():\n    assert mean([2, 4]) == 3\n"
FIX = {"path": "stats.py", "old_str": "ordered[mid + 1]", "new_str": "ordered[mid - 1]"}


@pytest.fixture
def box(planted_sandbox):
    _ensure_git_baseline(planted_sandbox)
    return planted_sandbox


def tb(box):
    return Toolbox(box, 60, verifier=Verifier(box, timeout_s=120))


@pytest.mark.parametrize("path,expected", [
    ("tests/test_x.py", True), ("test_stats.py", True), ("pkg/foo_test.py", True), ("src/a.spec.ts", True),
    ("conftest.py", True), ("stats.py", False), ("src/testing_utils.py", False), ("docs/test.md", False),
])
def test_is_test_path(path, expected):
    assert is_test_path(path) is expected


def test_source_edits_wait_for_a_test(box):
    t = tb(box)
    refused = t.call("edit_file", FIX)
    assert refused.is_error and "Test-first" in refused.output
    assert not t.call("edit_file", {"path": "test_even.py", "old_str": "", "new_str": REPRO}).is_error
    assert not t.call("edit_file", FIX).is_error


def test_finish_needs_test_command(box):
    r = tb(box).call("finish", {"summary": "done"})
    assert r.is_error and not r.finished and "test_command" in r.output


def test_finish_accepts_reproducing_test(box):
    t = tb(box)
    t.call("edit_file", {"path": "test_even.py", "old_str": "", "new_str": REPRO})
    t.call("edit_file", FIX)
    r = t.call("finish", {"summary": "fixed", "test_command": f"{PYTEST} test_even.py"})
    assert r.finished and not r.is_error, r.output
    ev = t.verifier.last_check.evidence
    assert ev["verified"] and ev["fails_without_change"] and ev["passes_with_change"]
    assert ev["tests_changed"] == ["test_even.py"] and ev["sources_changed"] == ["stats.py"]
    assert "ordered[mid - 1]" in box.read_file("stats.py")  # stash was popped back


def test_finish_refuses_test_that_does_not_reproduce(box):
    t = tb(box)
    t.call("edit_file", {"path": "test_mean2.py", "old_str": "", "new_str": PASSES_ON_BASE})
    t.call("edit_file", FIX)
    r = t.call("finish", {"summary": "fixed", "test_command": f"{PYTEST} test_mean2.py"})
    assert not r.finished and "don't reproduce" in r.output


def test_failing_tests_block_unless_explained(box):
    t = tb(box)
    t.call("edit_file", {"path": "test_even.py", "old_str": "", "new_str": REPRO})
    args = {"summary": "partial", "test_command": f"{PYTEST} test_even.py"}
    blocked = t.call("finish", args)
    assert not blocked.finished and "Tests fail" in blocked.output
    allowed = t.call("finish", {**args, "explanation": "the fix needs a library upgrade"})
    assert allowed.finished
    assert t.verifier.last_check.evidence["verified"] is False


# ---------------------------------------------------------------- detector


def test_detector_flags_removed_assert_and_skip(box):
    text = box.read_file("test_stats.py")
    text = text.replace("    assert median([3, 1, 2]) == 2\n", "    pass\n")
    text = text.replace("def test_median_even", "@pytest.mark.skip\ndef test_median_even")
    box.write_file("test_stats.py", text)
    flags = detect_weakening(box)
    assert any("removed 1 assertion" in f for f in flags)
    assert any("skip/xfail" in f for f in flags)


def test_detector_flags_deleted_file_and_removed_test(box):
    text = box.read_file("test_stats.py")
    start = text.index("def test_empty")
    box.write_file("test_stats.py", text[:start])
    flags = detect_weakening(box)
    assert any("removed 1 test function" in f for f in flags)
    box.exec("git rm -qf test_stats.py", timeout_s=30)
    assert any("deleted test file test_stats.py" in f for f in detect_weakening(box))


def test_detector_ignores_new_tests_and_source_edits(box):
    box.write_file("test_even.py", REPRO)
    box.write_file("stats.py", box.read_file("stats.py").replace("ordered[mid + 1]", "ordered[mid - 1]"))
    assert detect_weakening(box) == []


def test_weakening_flagged_in_every_run_report(box, tmp_path):
    cheat = ScriptedModel([
        ScriptedTurn(tool_calls=[("run", {"command": "rm test_stats.py"})]),
        ScriptedTurn(tool_calls=[("finish", {"summary": "tests pass now"})]),
    ])
    result = run_agent("fix median", cheat, box, RunConfig(), tmp_path)  # verification off
    assert result.weakening_flags == ["deleted test file test_stats.py"]
    assert "deleted test file" in (tmp_path / "report.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- reviewer + full loop


def test_reviewer_sends_agent_back_once_then_approves(box, tmp_path):
    finish = ("finish", {"summary": "Fixed median.", "test_command": f"{PYTEST} test_even.py"})
    model = ScriptedModel([
        ScriptedTurn(tool_calls=[("edit_file", {"path": "test_even.py", "old_str": "", "new_str": REPRO})]),
        ScriptedTurn(tool_calls=[("edit_file", FIX)]),
        ScriptedTurn(tool_calls=[finish]),
        ScriptedTurn(tool_calls=[("submit_review", {"verdict": "request_changes", "issues": ["Explain the fix"]})]),
        ScriptedTurn(tool_calls=[finish]),
        ScriptedTurn(tool_calls=[("submit_review", {"verdict": "approve", "issues": []})]),
    ])
    config = RunConfig(verification=True)
    result = run_agent("median is wrong for even-length lists", model, box, config, tmp_path)

    assert result.status == "finished", result.detail
    assert [r["verdict"] for r in result.reviews] == ["request_changes", "approve"]
    assert result.verification["verified"] is True
    assert result.budget["steps"] == 6  # reviews count against the budget
    review_request = model.requests[3]
    assert len(review_request) == 1 and "<diff>" in review_request[0]["content"]  # fresh context
    assert "ordered[mid - 1]" in review_request[0]["content"]
    feedback = model.requests[4][-1]["content"][0]["content"]
    assert "Explain the fix" in feedback
    assert "finish" in model.tool_names[0] and "submit_review" in model.tool_names[3]


def test_reviewer_rounds_are_capped(box, tmp_path):
    finish = ("finish", {"summary": "x", "test_command": f"{PYTEST} test_even.py"})
    nope = ("submit_review", {"verdict": "request_changes", "issues": ["no"]})
    model = ScriptedModel([
        ScriptedTurn(tool_calls=[("edit_file", {"path": "test_even.py", "old_str": "", "new_str": REPRO})]),
        ScriptedTurn(tool_calls=[("edit_file", FIX)]),
        ScriptedTurn(tool_calls=[finish]), ScriptedTurn(tool_calls=[nope]),
        ScriptedTurn(tool_calls=[finish]), ScriptedTurn(tool_calls=[nope]),
    ])
    result = run_agent("fix", model, box, RunConfig(verification=True, max_review_rounds=1), tmp_path)
    assert result.status == "finished"
    assert [r["verdict"] for r in result.reviews] == ["request_changes", "request_changes"]


# ---------------------------------------------------------------- skills + AGENTS.md


def test_skills_listed_loaded_and_agents_md_included(box, tmp_path):
    box.write_file("AGENTS.md", "Run tests with: python -m pytest -q\nUse type hints.")
    model = ScriptedModel([
        ScriptedTurn(tool_calls=[("load_skill", {"name": "python-testing"})]),
        ScriptedTurn(tool_calls=[("load_skill", {"name": "nope"})]),
        ScriptedTurn(tool_calls=[("finish", {"summary": "done"})]),
    ])
    run_agent("fix median", model, box, RunConfig(skills=True), tmp_path / "run")

    assert "python-testing:" in model.systems[0] and "debugging:" in model.systems[0]
    assert "load_skill" in model.tool_names[0]
    first = model.requests[0][0]["content"]
    assert "Use type hints." in first and "repository_conventions" in first
    loaded = model.requests[1][-1]["content"][0]["content"]
    assert loaded.startswith("# Skill: python-testing") and "Reproduce first" in loaded
    missing = model.requests[2][-1]["content"][0]
    assert missing["is_error"] and "No skill named" in missing["content"]


def test_skills_off_hides_everything(box, tmp_path):
    box.write_file("AGENTS.md", "secret convention")
    model = ScriptedModel([ScriptedTurn(tool_calls=[("finish", {"summary": "done"})])])
    run_agent("fix", model, box, RunConfig(), tmp_path)
    assert "load_skill" not in model.tool_names[0]
    assert "secret convention" not in model.requests[0][0]["content"]
    assert "Skills you can load" not in model.systems[0]


def test_ablation_configs_load():
    v = load_config(ROOT / "configs" / "verification.toml")
    s = load_config(ROOT / "configs" / "skills.toml")
    assert v.run.verification and not v.run.skills
    assert s.run.verification and s.run.skills
