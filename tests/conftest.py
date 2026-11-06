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
