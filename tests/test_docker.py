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


def test_crlf_checkout_is_clean_in_the_container(tmp_path, monkeypatch):
    """Git for Windows (core.autocrlf=true) checks files out with CRLF over LF blobs; the
    container's git must not see every line as modified."""
    from test_pr import crlf_checkout

    work = crlf_checkout(tmp_path, monkeypatch)
    b = DockerSandbox(work, SandboxLimits(command_timeout_s=60))
    try:
        assert b.exec("git status --porcelain", timeout_s=30).output.strip() == ""
        b.exec("sed -i 's/mid + 1]/mid - 1]/' stats.py", timeout_s=30)
        diff = b.exec("git diff --numstat", timeout_s=30).output.strip()
        assert diff == "1\t1\tstats.py"
    finally:
        b.close()
