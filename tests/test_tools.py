import shutil

import pytest

from spec_to_pr.tools import Toolbox, truncate


def test_edit_file_replaces_unique_match(planted_sandbox):
    tb = Toolbox(planted_sandbox)
    r = tb.call("edit_file", {"path": "stats.py", "old_str": "ordered[mid + 1]", "new_str": "ordered[mid - 1]"})
    assert not r.is_error, r.output
    assert "ordered[mid - 1]" in planted_sandbox.read_file("stats.py")


def test_edit_file_rejects_missing_text(planted_sandbox):
    r = Toolbox(planted_sandbox).call("edit_file", {"path": "stats.py", "old_str": "nope", "new_str": "x"})
    assert r.is_error and "not found" in r.output


def test_edit_file_rejects_ambiguous_text(planted_sandbox):
    r = Toolbox(planted_sandbox).call("edit_file", {"path": "stats.py", "old_str": "if not values:", "new_str": "x"})
    assert r.is_error and "matches 2 places" in r.output


def test_edit_file_creates_new_file(planted_sandbox):
    tb = Toolbox(planted_sandbox)
    r = tb.call("edit_file", {"path": "notes/new.txt", "old_str": "", "new_str": "hello"})
    assert not r.is_error
    assert planted_sandbox.read_file("notes/new.txt") == "hello"
    again = tb.call("edit_file", {"path": "notes/new.txt", "old_str": "", "new_str": "x"})
    assert again.is_error


def test_read_file_numbers_lines(planted_sandbox):
    r = Toolbox(planted_sandbox).call("read_file", {"path": "stats.py", "start_line": 4, "end_line": 5})
    assert r.output.splitlines()[0].strip().startswith("4")
    assert "def mean" in r.output


def test_read_file_outside_repo_is_refused(planted_sandbox):
    r = Toolbox(planted_sandbox).call("read_file", {"path": "../../etc/passwd"})
    assert r.is_error


def test_run_reports_exit_code(planted_sandbox):
    r = Toolbox(planted_sandbox).call("run", {"command": "echo hi; exit 3"})
    assert "[exit code 3]" in r.output and "hi" in r.output
    assert not r.is_error


def test_run_times_out(planted_sandbox):
    r = Toolbox(planted_sandbox, default_timeout_s=1).call("run", {"command": "sleep 5"})
    assert r.is_error and "timed out" in r.output


@pytest.mark.skipif(not shutil.which("rg") and not shutil.which("grep"), reason="needs rg or grep")
def test_search_finds_matches(planted_sandbox):
    tb = Toolbox(planted_sandbox)
    assert "stats.py" in tb.call("search", {"pattern": "def median"}).output
    assert tb.call("search", {"pattern": "zzz_not_here"}).output == "No matches."


def test_unknown_tool_and_bad_args(planted_sandbox):
    tb = Toolbox(planted_sandbox)
    assert tb.call("delete_everything", {}).is_error
    assert tb.call("run", {"cmd": "ls"}).is_error


def test_truncate_keeps_head_and_tail():
    text = "A" * 50 + "B" * 50
    out = truncate(text, 20)
    assert out.startswith("A" * 10) and out.endswith("B" * 10) and "omitted" in out
