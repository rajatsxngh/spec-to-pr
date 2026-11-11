import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from spec_to_pr.config import SandboxLimits
from spec_to_pr.sandbox import LocalSandbox

ROOT = Path(__file__).resolve().parents[1]
PLANTED = ROOT / "examples" / "planted_bug"
# Forward slashes so the path survives bash quoting on Windows.
PYTHON = Path(sys.executable).as_posix()


@pytest.fixture
def planted_sandbox():
    box = LocalSandbox(PLANTED, SandboxLimits(command_timeout_s=60))
    yield box
    box.close()


@functools.cache
def docker_ready() -> bool:
    """Docker is running and the sandbox image is built (`uv run spec-to-pr build-image`)."""
    from spec_to_pr.sandbox import DEFAULT_IMAGE, docker_available

    if not docker_available():
        return False
    return subprocess.run(["docker", "image", "inspect", DEFAULT_IMAGE], capture_output=True).returncode == 0


needs_docker = pytest.mark.skipif(not docker_ready(), reason="Docker or the sandbox image is not available")

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    """Run the spec-to-pr CLI with plain, unwrapped output.

    CI terminals make Typer/Rich print colored, line-wrapped boxes; turn that off and strip any ANSI
    codes that slip through, so tests can match on plain text in `.stdout`/`.stderr`.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("FORCE_COLOR", "PY_COLORS", "CLICOLOR_FORCE")}
    env.update(NO_COLOR="1", TERM="dumb", COLUMNS="250", _TYPER_FORCE_DISABLE_TERMINAL="1")
    proc = subprocess.run(["uv", "run", "spec-to-pr", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=ROOT, env=env)
    proc.stdout = _ANSI.sub("", proc.stdout or "")
    proc.stderr = _ANSI.sub("", proc.stderr or "")
    return proc
