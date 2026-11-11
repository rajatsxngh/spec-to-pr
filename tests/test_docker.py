"""Docker sandbox checks. Skipped when Docker isn't running.

Build the image first: `uv run spec-to-pr build-image`.
"""

import pytest

from conftest import PLANTED, needs_docker
from spec_to_pr.config import SandboxLimits
from spec_to_pr.sandbox import DockerSandbox

pytestmark = needs_docker


@pytest.fixture
def box():
    b = DockerSandbox(PLANTED, SandboxLimits(command_timeout_s=60))
    yield b
    b.close()


def test_network_is_off(box):
    r = box.exec("python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"", timeout_s=20)
    assert r.exit_code != 0


def test_no_host_secrets(box, monkeypatch):
    r = box.exec("env", timeout_s=10)
    assert "ANTHROPIC" not in r.output and "GITHUB_TOKEN" not in r.output


def test_files_and_tests_run(box):
    assert "def median" in box.read_file("stats.py")
    box.write_file("sub/x.txt", "hi")
    assert box.read_file("sub/x.txt") == "hi"
    assert box.exec("python -m pytest -q", timeout_s=60).exit_code == 1  # planted bug fails a test


def test_command_timeout(box):
    r = box.exec("sleep 10", timeout_s=2)
    assert r.timed_out
