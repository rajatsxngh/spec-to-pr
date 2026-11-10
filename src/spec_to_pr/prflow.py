"""One command from spec to draft PR (milestone 4).

    spec -> sandboxed agent (test-first, finish gate, reviewer) -> patch -> branch in a temporary
    worktree on the host -> push -> draft PR with spec, diff summary, tests, review and cost

The user's working tree is never modified: the patch is committed in a separate `git worktree`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .agent import RunResult, run_agent
from .config import RunConfig
from .github import GitHubClient, GitHubError, Issue, parse_remote
from .models import ModelClient
from .planning import Approver
from .sandbox import Sandbox

BOT_NAME, BOT_EMAIL = "spec-to-pr", "spec-to-pr@users.noreply.github.com"


class RepoError(RuntimeError):
    pass


def git(cwd: Path, *args: str, check: bool = True, strip: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if check and proc.returncode != 0:
        raise RepoError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip() if strip else proc.stdout.rstrip()


@dataclass(frozen=True)
class RepoState:
    root: Path
    branch: str
    head: str
    owner: str
    name: str


def inspect_repo(path: Path, need_remote: bool = True) -> RepoState:
    root = Path(git(path, "rev-parse", "--show-toplevel"))
    if git(root, "status", "--porcelain"):
        raise RepoError("The working tree has uncommitted or untracked changes; commit or stash them first.")
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise RepoError("HEAD is detached; check out the branch the PR should target.")
    head = git(root, "rev-parse", "HEAD")
    owner = name = ""
    url = git(root, "remote", "get-url", "origin", check=False)
    if url:
        try:
            owner, name = parse_remote(url)
        except GitHubError:
            if need_remote:
                raise
            # A dry run never talks to GitHub, so a GitLab, SSH-alias or local-path origin is fine.
    elif need_remote:
        raise RepoError("The repository has no 'origin' remote on GitHub.")
    return RepoState(root, branch, head, owner, name)


def spec_title(spec: str) -> str:
    for line in spec.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line if len(line) <= 72 else line[:69].rstrip() + "..."
    return "Spec-to-PR change"


def branch_name(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-") or "change"
    return f"spec-to-pr/{slug}-{time.strftime('%Y%m%d%H%M%S')}"


def spec_from_issue(issue: Issue) -> str:
    return (f"# {issue.title}\n\n{issue.body.strip()}\n\n"
            f"(GitHub issue #{issue.number} by @{issue.author}: {issue.url})")


def pr_body(spec: str, result: RunResult, diffstat: str, issue: Issue | None = None) -> str:
    b = result.budget
    v = result.verification
    lines = [f"Closes #{issue.number}", ""] if issue else []
    lines += ["## Spec", "", spec.strip(), ""]
    if result.plan:
        who = {"auto": "auto-approved (eval mode)", "human": "approved by a human"}.get(result.plan["by"], result.plan["by"])
        edited = ", with edits" if result.plan["action"] == "edit" else ""
        lines += ["## Plan", "", f"_{who}{edited}_", "", result.plan["text"].strip(), ""]
    lines += ["## Changes", "", "```", diffstat.rstrip() or "(none)", "```", ""]
    lines += ["## Tests", ""]
    if v is None:
        lines.append("Verification was off for this run.")
    else:
        yes = lambda x: {True: "yes", False: "no", None: "not checked"}[x]  # noqa: E731
        lines += [
            f"- Command: `{v.get('test_command') or '(none)'}`",
            f"- New tests fail without the change: {yes(v.get('fails_without_change'))}",
            f"- Tests pass with the change: {yes(v.get('passes_with_change'))}",
            f"- Verified: {yes(v.get('verified'))}",
        ]
        if v.get("explanation"):
            lines.append(f"- Agent's explanation for unmet checks: {v['explanation']}")
    lines += ["", "## Review", ""]
    if result.reviews:
        last = result.reviews[-1]
        lines.append(f"Reviewer verdict: **{last['verdict']}** after {len(result.reviews)} review(s).")
        lines += [f"- {issue}" for issue in last["issues"]]
    else:
        lines.append("No review ran.")
    lines += [
        "",
        "## Integrity",
        "",
        f"Test-weakening flags: {'; '.join(result.weakening_flags) or 'none'}",
        "",
        "## Cost",
        "",
        f"${b['cost_usd']:.4f} · {b['steps']} model calls · {b['tokens']:,} tokens · {b['elapsed_s']}s · "
        f"{', '.join(result.models_used) or 'no model'}",
        "",
        "## Agent summary",
        "",
        result.summary or "(none)",
        "",
        "---",
        "Generated by Spec-to-PR, an AI coding agent. This is a draft for human review; "
        "it is never merged automatically.",
    ]
    return "\n".join(lines)


@dataclass
class Outcome:
    status: str  # pr_opened | branch_created | no_pr
    reason: str
    result: RunResult | None = None
    branch: str | None = None
    pr_url: str | None = None
    body_path: Path | None = None


def commit_patch(state: RepoState, patch: str, branch: str, title: str, push: bool,
                 push_to: str = "origin") -> str:
    """Commit the patch on a new branch in a temporary worktree; push it (to `push_to`, a remote name or
    URL) if asked. Returns the diffstat."""
    tmp = Path(tempfile.mkdtemp(prefix="spec-to-pr-wt-"))
    worktree = tmp / "wt"
    git(state.root, "worktree", "add", "-q", "-b", branch, str(worktree), state.head)
    try:
        patch_file = tmp / "change.patch"
        patch_file.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8", newline="\n")
        # Apply to the index, not the working tree: the patch is against committed (LF) blobs, while
        # with core.autocrlf the worktree holds CRLF files that an LF patch would not apply to.
        try:
            git(worktree, "apply", "--cached", "--whitespace=nowarn", str(patch_file))
        except RepoError:
            git(worktree, "apply", "--cached", "--whitespace=nowarn", "--ignore-whitespace", str(patch_file))
        name = git(state.root, "config", "user.name", check=False) or BOT_NAME
        email = git(state.root, "config", "user.email", check=False) or BOT_EMAIL
        git(worktree, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", title,
            "-m", "Generated by Spec-to-PR.")
        diffstat = git(worktree, "diff", "--stat", "HEAD~1", "HEAD", strip=False)  # keep column alignment
        if push:
            _push(worktree, push_to, branch)
        return diffstat
    finally:
        git(state.root, "worktree", "remove", "--force", str(worktree), check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def _push(worktree: Path, target: str, branch: str, attempts: int = 4) -> None:
    """Push the branch. A just-created fork can take a few seconds to accept pushes, so retry briefly."""
    for i in range(attempts):
        try:
            git(worktree, "push", "-q", target, f"{branch}:{branch}")
            return
        except RepoError:
            if i == attempts - 1:
                raise
            time.sleep(3 * (i + 1))


def run_spec(
    spec: str,
    repo: Path,
    config: RunConfig,
    model: ModelClient,
    make_sandbox: Callable[[Path], Sandbox],
    github: GitHubClient | None,
    out_dir: Path,
    push: bool = True,
    approver: Approver | None = None,
    issue: Issue | None = None,
    fork: bool = False,
) -> Outcome:
    """Run the agent on a spec and open a draft PR. With github=None it stops at a local branch.
    With fork=True the branch goes to your fork and the PR targets origin (for repos you can't push to)."""
    state = inspect_repo(repo, need_remote=github is not None)
    fork_info = github.ensure_fork(state.owner, state.name) if (fork and github is not None) else None
    sandbox = make_sandbox(state.root)
    try:
        result = run_agent(spec, model, sandbox, config, out_dir, approver=approver)
    finally:
        sandbox.close()

    if result.status == "plan_rejected":
        return Outcome("no_pr", "The plan was rejected; no changes were made", result)
    if result.status != "finished":
        return Outcome("no_pr", f"Agent did not finish: {result.status} ({result.detail})", result)
    if not result.diff.strip():
        return Outcome("no_pr", "Agent finished without changing any files", result)

    title = spec_title(spec)
    branch = branch_name(title)
    diffstat = commit_patch(state, result.diff, branch, title, push=push and github is not None,
                            push_to=fork_info.push_url if fork_info else "origin")
    body = pr_body(spec, result, diffstat, issue)
    body_path = out_dir / "pr.md"
    body_path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    if github is None:
        return Outcome("branch_created", f"Created local branch {branch}; no PR opened", result, branch,
                       body_path=body_path)
    head = f"{fork_info.owner}:{branch}" if fork_info else branch
    url = github.create_draft_pr(state.owner, state.name, head=head, base=state.branch, title=title, body=body)
    return Outcome("pr_opened", f"Opened draft PR {url}", result, branch, url, body_path)
