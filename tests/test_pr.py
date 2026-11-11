"""The milestone 4 flow: spec -> agent -> branch in a temp worktree -> push -> draft PR.

Uses a local bare repository as the push target (origin's pushurl) and a fake GitHub API."""

import json
import shutil
import subprocess

import httpx
import pytest

from conftest import PLANTED
from spec_to_pr.config import RunConfig, SandboxLimits
from spec_to_pr.github import FakeGitHub, GitHubError, RestGitHub, parse_remote
from spec_to_pr.models import ScriptedModel, ScriptedTurn
from spec_to_pr.prflow import RepoError, branch_name, inspect_repo, run_spec, spec_title
from spec_to_pr.sandbox import LocalSandbox

SPEC = "# Fix median for even-length lists\n\n`median([4, 1, 3, 2])` should be 2.5.\n"
PYTEST = "python -m pytest -q -p no:cacheprovider"
REPRO = "from stats import median\n\n\ndef test_even():\n    assert median([4, 1, 3, 2]) == 2.5\n"
FIX = {"path": "stats.py", "old_str": "ordered[mid + 1]", "new_str": "ordered[mid - 1]"}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    work, remote = tmp_path / "work", tmp_path / "remote.git"
    shutil.copytree(PLANTED, work)
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.name", "Test User")
    git(work, "config", "user.email", "test@example.com")
    git(work, "config", "core.autocrlf", "false")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "initial")
    git(work, "remote", "add", "origin", "https://github.com/acme/demo.git")
    git(work, "config", "remote.origin.pushurl", str(remote))
    git(work, "push", "-q", "origin", "main")
    return work, remote


def fixing_model():
    finish = ("finish", {"summary": "Fixed the even branch of median.", "test_command": f"{PYTEST} test_even.py"})
    return ScriptedModel([
        ScriptedTurn(tool_calls=[("edit_file", {"path": "test_even.py", "old_str": "", "new_str": REPRO})]),
        ScriptedTurn(tool_calls=[("edit_file", FIX)]),
        ScriptedTurn(tool_calls=[finish]),
        ScriptedTurn(tool_calls=[("submit_review", {"verdict": "approve", "issues": []})]),
    ])


def local(root):
    return LocalSandbox(root, SandboxLimits(command_timeout_s=120))


CONFIG = RunConfig(verification=True)


def test_spec_to_draft_pr(repo, tmp_path):
    work, remote = repo
    gh = FakeGitHub()
    outcome = run_spec(SPEC, work, CONFIG, fixing_model(), local, gh, tmp_path / "run")

    assert outcome.status == "pr_opened", outcome.reason
    assert outcome.pr_url == "https://github.com/acme/demo/pull/1"
    call = gh.calls[0]
    assert call["draft"] is True and call["base"] == "main" and call["head"] == outcome.branch
    assert (call["owner"], call["repo"]) == ("acme", "demo")
    assert call["title"] == "Fix median for even-length lists"
    body = call["body"]
    for part in ("## Spec", "median([4, 1, 3, 2])", "```\n stats.py ", "New tests fail without the change: yes",
                 "Tests pass with the change: yes", "Reviewer verdict: **approve**", "Test-weakening flags: none",
                 "## Cost", "never merged automatically"):
        assert part in body, part

    # The branch was pushed with the fix and the new test.
    pushed = git(remote, "show", f"{outcome.branch}:stats.py")
    assert "ordered[mid - 1]" in pushed
    assert "def test_even" in git(remote, "show", f"{outcome.branch}:test_even.py")
    assert git(remote, "log", "-1", "--format=%an", outcome.branch) == "Test User"
    # The user's working tree is untouched.
    assert git(work, "status", "--porcelain") == ""
    assert git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "ordered[mid + 1]" in (work / "stats.py").read_text()
    assert outcome.body_path.read_text().startswith("# Fix median")


def test_fork_flow_pushes_to_fork_and_targets_origin(repo, tmp_path):
    from spec_to_pr.github import Fork

    work, upstream = repo
    fork_remote = tmp_path / "fork.git"
    git(tmp_path, "init", "-q", "--bare", str(fork_remote))
    gh = FakeGitHub(fork=Fork(owner="me", push_url=str(fork_remote)))
    outcome = run_spec(SPEC, work, CONFIG, fixing_model(), local, gh, tmp_path / "run", fork=True)

    assert outcome.status == "pr_opened", outcome.reason
    call = gh.calls[0]
    assert (call["owner"], call["repo"], call["base"]) == ("acme", "demo", "main")  # PR against upstream
    assert call["head"] == f"me:{outcome.branch}"  # from the fork
    assert "ordered[mid - 1]" in git(fork_remote, "show", f"{outcome.branch}:stats.py")
    assert outcome.branch not in git(upstream, "branch", "--list")  # nothing pushed upstream


def test_rest_ensure_fork():
    def handler(request):
        assert request.method == "POST" and request.url.path == "/repos/acme/demo/forks"
        return httpx.Response(202, json={"owner": {"login": "me"}, "clone_url": "https://github.com/me/demo.git"})

    fork = RestGitHub("t", transport=httpx.MockTransport(handler)).ensure_fork("acme", "demo")
    assert (fork.owner, fork.push_url) == ("me", "https://github.com/me/demo.git")


def test_dry_run_creates_local_branch_only(repo, tmp_path):
    work, remote = repo
    outcome = run_spec(SPEC, work, CONFIG, fixing_model(), local, None, tmp_path / "run")
    assert outcome.status == "branch_created" and outcome.pr_url is None
    assert "ordered[mid - 1]" in git(work, "show", f"{outcome.branch}:stats.py")
    assert outcome.branch not in git(remote, "branch", "--list")


def test_unfinished_run_opens_no_pr(repo, tmp_path):
    work, _ = repo
    gh = FakeGitHub()
    model = ScriptedModel([ScriptedTurn(text="I can't do this.") for _ in range(4)])
    outcome = run_spec(SPEC, work, CONFIG, model, local, gh, tmp_path / "run")
    assert outcome.status == "no_pr" and gh.calls == []


def test_dirty_tree_is_refused(repo):
    work, _ = repo
    (work / "scratch.txt").write_text("x")
    with pytest.raises(RepoError, match="uncommitted"):
        inspect_repo(work)


def test_no_github_remote_is_refused(repo):
    work, _ = repo
    git(work, "remote", "remove", "origin")
    with pytest.raises(RepoError, match="origin"):
        inspect_repo(work)
    assert inspect_repo(work, need_remote=False).branch == "main"


@pytest.mark.parametrize("url", [
    "https://github.com/acme/demo.git", "https://github.com/acme/demo", "git@github.com:acme/demo.git",
    "ssh://git@github.com/acme/demo.git", "https://token@github.com/acme/demo.git",
])
def test_parse_remote(url):
    assert parse_remote(url) == ("acme", "demo")


def test_parse_remote_rejects_other_hosts():
    with pytest.raises(GitHubError):
        parse_remote("https://gitlab.com/acme/demo.git")


def test_title_and_branch_name():
    assert spec_title("\n# Add CSV export\nmore") == "Add CSV export"
    assert spec_title("x" * 100).endswith("...")
    assert branch_name("Add CSV export!").startswith("spec-to-pr/add-csv-export-")


def test_rest_client_sends_draft_pr_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["json"] = json.loads(request.content)
        return httpx.Response(201, json={"html_url": "https://github.com/acme/demo/pull/7"})

    gh = RestGitHub("tok", transport=httpx.MockTransport(handler))
    url = gh.create_draft_pr("acme", "demo", head="b", base="main", title="T", body="B")
    assert url.endswith("/pull/7")
    assert seen["url"] == "https://api.github.com/repos/acme/demo/pulls"
    assert seen["auth"] == "Bearer tok"
    assert seen["json"] == {"title": "T", "head": "b", "base": "main", "body": "B", "draft": True}


def test_rest_client_reports_errors():
    gh = RestGitHub("tok", transport=httpx.MockTransport(
        lambda r: httpx.Response(422, json={"message": "Validation Failed"})))
    with pytest.raises(GitHubError, match="422"):
        gh.create_draft_pr("a", "b", head="h", base="main", title="t", body="b")


def crlf_checkout(tmp_path, monkeypatch):
    """A repo as Git for Windows checks it out: LF blobs, CRLF files in the working tree, and
    core.autocrlf=true set system-wide (here via the environment), not in the repo's .git/config.
    Its origin is a local path, not GitHub."""
    src, work = tmp_path / "src", tmp_path / "crlf"
    shutil.copytree(PLANTED, src)
    git(src, "init", "-q", "-b", "main")
    git(src, "config", "core.autocrlf", "false")
    git(src, "add", "-A")
    git(src, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "initial")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    git(tmp_path, "clone", "-q", str(src), str(work))
    git(work, "config", "user.name", "Test User")
    git(work, "config", "user.email", "test@example.com")
    return work


def test_crlf_checkout_gets_a_minimal_lf_branch(tmp_path, monkeypatch):
    work = crlf_checkout(tmp_path, monkeypatch)
    assert b"\r\n" in (work / "stats.py").read_bytes()
    outcome = run_spec(SPEC, work, CONFIG, fixing_model(), local, None, tmp_path / "run")

    assert outcome.status == "branch_created", outcome.reason
    stat = git(work, "diff", "--numstat", "main", outcome.branch)
    assert sorted(stat.splitlines()) == ["1\t1\tstats.py", "5\t0\ttest_even.py"]  # no whole-file rewrites
    blob = subprocess.run(["git", "show", f"{outcome.branch}:stats.py"], cwd=work, capture_output=True).stdout
    assert b"ordered[mid - 1]" in blob and b"\r\n" not in blob  # committed with LF, like the original
    assert git(work, "status", "--porcelain") == ""
