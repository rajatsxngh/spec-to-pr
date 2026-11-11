"""Sandboxes (F2): where every agent command runs.

`DockerSandbox` is the real one: one container per task, repo copied in at the
target commit (or, with `repo=None`, the checkout the image already holds at
`workdir`, as SWE-bench images do), network off, CPU/memory/pid limits, and no host environment
variables passed in (so no secrets). `LocalSandbox` runs on the host in a temp
copy of the repo; it exists for tests and quick development only and is never
safe for untrusted tasks.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from .config import SandboxLimits
from .safety import scrub_env


@dataclass
class ExecResult:
    exit_code: int
    output: str  # stdout and stderr, interleaved
    timed_out: bool = False


class SandboxError(RuntimeError):
    pass


class Sandbox(Protocol):
    def exec(self, command: str, timeout_s: int) -> ExecResult: ...
    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def close(self) -> None: ...


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- Docker


DEFAULT_IMAGE = "spec-to-pr-sandbox:latest"
WORKDIR = "/repo"


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def host_converts_crlf(repo: Path) -> bool:
    """True when the host's git converts line endings for this repo (core.autocrlf true/input, the
    Git for Windows default). Its working tree then holds CRLF files over LF blobs, which a
    container's git, without that setting, would report as modified on every line."""
    try:
        proc = subprocess.run(["git", "-C", str(repo), "config", "--get", "core.autocrlf"],
                              capture_output=True, text=True, encoding="utf-8", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.stdout.strip().lower() in ("true", "input")


class DockerSandbox:
    def __init__(
        self,
        repo: Path | None,
        limits: SandboxLimits,
        image: str = DEFAULT_IMAGE,
        commit: str | None = None,
        network: bool = False,
        workdir: str = WORKDIR,
    ):
        if not shutil.which("docker"):
            raise SandboxError("Docker is not installed or not on PATH.")
        self.source = repo  # host directory the sandbox was copied from (used for the repo map)
        self.workdir = workdir
        self.name = f"spec-to-pr-{uuid.uuid4().hex[:12]}"
        cmd = [
            "docker", "run", "-d", "--rm",
            "--name", self.name,
            "--cpus", str(limits.cpus),
            "--memory", limits.memory,
            "--pids-limit", str(limits.pids),
            "--workdir", workdir,
            # A clean environment: no -e/--env-file, so host secrets never enter.
            "--env", "HOME=/root",
            # No .pyc files: a cache compiled from the original code can outlive a same-size edit
            # made within the same second, and Python would run the stale bytecode.
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            # The network is off, so `uv run` must use the environment as installed instead of
            # trying to re-sync (SWE-bench images built with uv would otherwise fail every test run).
            "--env", "UV_NO_SYNC=1",
            "--env", "UV_OFFLINE=1",
        ]
        if not network:
            cmd += ["--network", "none"]
        cmd += [image, "sleep", "infinity"]
        self._docker(cmd, "start container")
        try:
            if repo is not None:
                self._docker(["docker", "exec", self.name, "mkdir", "-p", workdir], "create workdir")
                self._docker(["docker", "cp", f"{repo.resolve()}{os.sep}.", f"{self.name}:{workdir}"], "copy repo")
                if host_converts_crlf(repo):
                    # `input` compares CRLF files to their LF blobs as unchanged and never writes CRLF,
                    # so diffs show only the agent's edits, with LF endings like the committed blobs.
                    # safecrlf=false drops the per-file "CRLF will be replaced" warnings from git output.
                    self.exec("git rev-parse --is-inside-work-tree >/dev/null 2>&1 && "
                              "git config core.autocrlf input && git config core.safecrlf false", timeout_s=30)
            if commit:
                result = self.exec(f"git checkout -q {commit}", timeout_s=60)
                if result.exit_code != 0:
                    raise SandboxError(f"git checkout {commit} failed: {result.output}")
        except Exception:
            self.close()
            raise

    @staticmethod
    def _docker(cmd: list[str], what: str) -> None:
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            raise SandboxError(f"docker failed to {what}: {_decode(proc.stderr).strip()}")

    def exec(self, command: str, timeout_s: int) -> ExecResult:
        # `timeout` inside the container kills the command; the host timeout is a backstop.
        cmd = ["docker", "exec", "-w", self.workdir, self.name, "timeout", "-k", "5", str(timeout_s), "bash", "-lc", command]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_s + 15)
        except subprocess.TimeoutExpired as e:
            return ExecResult(124, _decode(e.stdout or b""), timed_out=True)
        return ExecResult(proc.returncode, _decode(proc.stdout), timed_out=proc.returncode == 124)

    def _path(self, path: str) -> str:
        p = PurePosixPath(path)
        return str(p if p.is_absolute() else PurePosixPath(self.workdir) / p)

    def read_file(self, path: str) -> str:
        proc = subprocess.run(["docker", "exec", self.name, "cat", "--", self._path(path)], capture_output=True)
        if proc.returncode != 0:
            raise FileNotFoundError(_decode(proc.stderr).strip() or path)
        return _decode(proc.stdout)

    def write_file(self, path: str, content: str) -> None:
        target = self._path(path)
        script = 'mkdir -p "$(dirname "$1")" && cat > "$1"'
        proc = subprocess.run(
            ["docker", "exec", "-i", self.name, "sh", "-c", script, "sh", target],
            input=content.encode("utf-8"),
            capture_output=True,
        )
        if proc.returncode != 0:
            raise OSError(_decode(proc.stderr).strip())

    def close(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


# ---------------------------------------------------------------- Local (dev only)


class LocalSandbox:
    """Runs commands on the host in a temp copy of the repo. NOT isolated."""

    def __init__(self, repo: Path, limits: SandboxLimits, commit: str | None = None):
        bash = shutil.which("bash")
        if not bash:
            raise SandboxError("LocalSandbox needs bash on PATH (Git Bash on Windows).")
        self._bash = bash
        self.source = repo
        self._tmp = tempfile.mkdtemp(prefix="spec-to-pr-")
        self.root = Path(self._tmp) / "repo"
        shutil.copytree(repo, self.root, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"))
        if commit:
            result = self.exec(f"git checkout -q {commit}", timeout_s=60)
            if result.exit_code != 0:
                raise SandboxError(f"git checkout {commit} failed: {result.output}")

    def exec(self, command: str, timeout_s: int) -> ExecResult:
        # Put this interpreter's directory first so `python` in task commands is the
        # project venv (which has pytest), as it would be inside the sandbox image.
        env = scrub_env(dict(os.environ))  # no tokens or keys, even in the dev sandbox
        env["PYTHONDONTWRITEBYTECODE"] = "1"  # no stale .pyc caches (see DockerSandbox)
        env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
        try:
            proc = subprocess.run(
                [self._bash, "-c", command],
                cwd=self.root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            return ExecResult(124, _decode(e.stdout or b""), timed_out=True)
        return ExecResult(proc.returncode, _decode(proc.stdout))

    def _path(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if p != self.root.resolve() and self.root.resolve() not in p.parents:
            raise PermissionError(f"{path} is outside the repo")
        return p

    def read_file(self, path: str) -> str:
        return self._path(path).read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        p = self._path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="")

    def close(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)
