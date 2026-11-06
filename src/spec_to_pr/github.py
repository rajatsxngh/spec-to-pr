"""GitHub integration (F10): parse the remote, find a token, open draft PRs over the REST API.

Runs on the host only; the token never enters the sandbox. The token comes from GH_TOKEN or
GITHUB_TOKEN, or from `gh auth token` when the GitHub CLI is logged in. A fine-grained token
scoped to the one repository with Contents and Pull requests write access is enough.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

import httpx

API = "https://api.github.com"
REMOTE = re.compile(r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
                    r"(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?/?$")


class GitHubError(RuntimeError):
    pass


def parse_remote(url: str) -> tuple[str, str]:
    m = REMOTE.match(url.strip())
    if not m:
        raise GitHubError(f"Not a GitHub remote: {url!r}")
    return m["owner"], m["name"]


def github_token() -> str | None:
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    if shutil.which("gh"):
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return None


class GitHubClient(Protocol):
    def create_draft_pr(self, owner: str, repo: str, head: str, base: str, title: str, body: str) -> str: ...


class RestGitHub:
    def __init__(self, token: str, api: str = API, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=api,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "spec-to-pr",
            },
            timeout=30,
            transport=transport,
        )

    def create_draft_pr(self, owner: str, repo: str, head: str, base: str, title: str, body: str) -> str:
        resp = self._client.post(
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body, "draft": True},
        )
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise GitHubError(f"GitHub refused the pull request ({resp.status_code}): {detail}")
        return resp.json()["html_url"]


@dataclass
class FakeGitHub:
    """Records PR requests instead of calling GitHub. For tests and dry runs."""

    calls: list[dict] = field(default_factory=list)

    def create_draft_pr(self, owner: str, repo: str, head: str, base: str, title: str, body: str) -> str:
        self.calls.append(dict(owner=owner, repo=repo, head=head, base=base, title=title, body=body, draft=True))
        return f"https://github.com/{owner}/{repo}/pull/{len(self.calls)}"
