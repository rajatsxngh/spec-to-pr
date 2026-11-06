import json

import httpx
import pytest

from conftest import ROOT
from spec_to_pr.agent import run_agent
from spec_to_pr.config import RunConfig
from spec_to_pr.evaluate import load_config
from spec_to_pr.github import FakeGitHub, GitHubError, Issue, RestGitHub, parse_issue_url
from spec_to_pr.models import ScriptedModel, ScriptedTurn
from spec_to_pr.planning import Decision, missing_sections
from spec_to_pr.prflow import run_spec, spec_from_issue
from test_pr import CONFIG, PYTEST, REPRO, SPEC, git, local, repo  # noqa: F401  (repo is a fixture)

PLAN = """# Plan
## Files to change
- stats.py: median() even branch
## Steps
1. Add a failing test. 2. Fix the index.
## Tests to add
- test_even in test_even.py
## Acceptance criteria
- median([4, 1, 3, 2]) == 2.5
"""
FIX = {"path": "stats.py", "old_str": "ordered[mid + 1]", "new_str": "ordered[mid - 1]"}


def planned_turns(finish=True):
    turns = [
        ScriptedTurn(tool_calls=[("edit_file", FIX)]),  # refused: no plan yet
        ScriptedTurn(tool_calls=[("submit_plan", {"plan": "## Steps\njust fix it"})]),  # refused: sections missing
        ScriptedTurn(tool_calls=[("run", {"command": "echo junk > scratch.py"}), ("submit_plan", {"plan": PLAN})]),
        ScriptedTurn(tool_calls=[("edit_file", FIX)]),
    ]
    if finish:
        turns.append(ScriptedTurn(tool_calls=[("finish", {"summary": "done"})]))
    return turns


def test_missing_sections():
    assert missing_sections(PLAN) == []
    assert missing_sections("## Steps\nx") == ["Files to change", "Tests to add", "Acceptance criteria"]


def test_plan_gate_then_auto_approve(planted_sandbox, tmp_path):
    model = ScriptedModel(planned_turns())
    result = run_agent("fix median", model, planted_sandbox, RunConfig(planning=True), tmp_path)

    assert result.status == "finished"
    first = model.requests[1][-1]["content"][0]
    assert first["is_error"] and "Plan first" in first["content"]
    second = model.requests[2][-1]["content"][0]
    assert second["is_error"] and "missing these sections" in second["content"]
    approved = model.requests[3][-1]["content"][1]["content"]
    assert approved == "The plan was approved. Implement it."
    assert result.plan == {"text": PLAN, "action": "approve", "by": "auto", "note": ""}
    assert (tmp_path / "plan.md").read_text() == PLAN
    # The file the agent created while planning was reverted before implementation.
    assert "scratch.py" not in result.diff and "ordered[mid - 1]" in result.diff
    assert "submit_plan" in model.tool_names[0]


def test_human_edits_the_plan(planted_sandbox, tmp_path):
    edited = PLAN + "- also handle a single element\n"
    model = ScriptedModel(planned_turns())
    result = run_agent("fix median", model, planted_sandbox, RunConfig(planning=True), tmp_path,
                       approver=lambda plan: Decision("edit", edited))
    assert result.plan["action"] == "edit" and result.plan["text"] == edited
    told = model.requests[3][-1]["content"][1]["content"]
    assert "approved with edits" in told and "single element" in told
    assert (tmp_path / "plan.md").read_text() == edited


def test_rejected_plan_ends_run(planted_sandbox, tmp_path):
    model = ScriptedModel(planned_turns())
    result = run_agent("fix median", model, planted_sandbox, RunConfig(planning=True), tmp_path,
                       approver=lambda plan: Decision("reject", plan, note="wrong approach"))
    assert result.status == "plan_rejected"
    assert result.diff.strip() == ""  # nothing implemented, and planning-phase changes reverted
    assert result.budget["steps"] == 3
    assert not (tmp_path / "plan.md").exists()


def test_planning_off_has_no_gate(planted_sandbox, tmp_path):
    model = ScriptedModel([ScriptedTurn(tool_calls=[("edit_file", FIX)]),
                           ScriptedTurn(tool_calls=[("finish", {"summary": "x"})])])
    result = run_agent("fix", model, planted_sandbox, RunConfig(), tmp_path)
    assert result.status == "finished" and "submit_plan" not in model.tool_names[0]


# ---------------------------------------------------------------- PR flow with a plan and an issue


def full_model():
    finish = ("finish", {"summary": "Fixed median.", "test_command": f"{PYTEST} test_even.py"})
    return ScriptedModel([
        ScriptedTurn(tool_calls=[("submit_plan", {"plan": PLAN})]),
        ScriptedTurn(tool_calls=[("edit_file", {"path": "test_even.py", "old_str": "", "new_str": REPRO})]),
        ScriptedTurn(tool_calls=[("edit_file", FIX)]),
        ScriptedTurn(tool_calls=[finish]),
        ScriptedTurn(tool_calls=[("submit_review", {"verdict": "approve", "issues": []})]),
    ])


def test_pr_from_issue_includes_plan_and_closes_issue(repo, tmp_path):  # noqa: F811
    work, remote = repo
    issue = Issue(12, "median is wrong for even lists", "median([4, 1, 3, 2]) gives 3.5", "https://github.com/acme/demo/issues/12", "alice")
    gh = FakeGitHub(issues={12: issue})
    spec = spec_from_issue(gh.get_issue("acme", "demo", 12))
    config = RunConfig(verification=True, planning=True)
    outcome = run_spec(spec, work, config, full_model(), local, gh, tmp_path / "run",
                       approver=lambda p: Decision("approve", p), issue=issue)

    assert outcome.status == "pr_opened", outcome.reason
    body = gh.calls[0]["body"]
    assert body.startswith("Closes #12")
    assert "## Plan" in body and "_approved by a human_" in body and "median([4, 1, 3, 2]) == 2.5" in body
    assert "GitHub issue #12 by @alice" in body
    assert gh.calls[0]["title"] == "median is wrong for even lists"


def test_rejected_plan_opens_no_pr(repo, tmp_path):  # noqa: F811
    work, remote = repo
    gh = FakeGitHub()
    outcome = run_spec(SPEC, work, RunConfig(verification=True, planning=True), full_model(), local, gh,
                       tmp_path / "run", approver=lambda p: Decision("reject", p))
    assert outcome.status == "no_pr" and "rejected" in outcome.reason and gh.calls == []


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/acme/demo/issues/12", ("acme", "demo", 12)),
    ("https://github.com/acme/demo/issues/12/", ("acme", "demo", 12)),
    ("https://github.com/acme/demo/pull/12", None),
    ("spec.md", None),
])
def test_parse_issue_url(url, expected):
    assert parse_issue_url(url) == expected


def test_rest_get_issue():
    def handler(request):
        assert request.url.path == "/repos/acme/demo/issues/5"
        assert "authorization" not in request.headers  # anonymous read
        return httpx.Response(200, json={"title": "T", "body": None, "html_url": "u", "user": {"login": "bob"}})

    issue = RestGitHub(None, transport=httpx.MockTransport(handler)).get_issue("acme", "demo", 5)
    assert (issue.title, issue.body, issue.author) == ("T", "", "bob")


def test_rest_get_issue_rejects_pull_requests():
    gh = RestGitHub("t", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "title": "T", "body": "", "html_url": "u", "user": {"login": "b"}, "pull_request": {}})))
    with pytest.raises(GitHubError, match="pull request"):
        gh.get_issue("a", "b", 1)


def test_planning_config_loads():
    cfg = load_config(ROOT / "configs" / "planning.toml")
    assert cfg.run.planning and cfg.run.verification and cfg.run.skills
