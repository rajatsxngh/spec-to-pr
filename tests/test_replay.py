"""The replay command: plays a recorded run back with no model, sandbox or network."""

import json

import pytest

from conftest import ROOT, run_cli
from spec_to_pr.replay import ReplayError, frames

SAMPLE = ROOT / "examples" / "replay" / "osmnx-1334"


def test_sample_run_replays_every_stage():
    out = [text for _, text in frames(SAMPLE)]
    joined = "\n".join(out)
    assert out[0].startswith("Replay: spec-to-pr") and "planning" in out[0]
    for marker in ("Task: ", "[plan] approve", "[review] fresh-context reviewer: approve",
                   "[verify] verified", "[done] finished", "[patch]", "osmnx/features.py"):
        assert marker in joined
    assert "\033[" not in joined  # no color unless asked
    assert all(0 <= pause <= 2.5 for pause, _ in frames(SAMPLE))


def test_output_is_clipped_and_color_is_optional(tmp_path):
    rows = [
        {"event": "run_start", "ts": 0, "task": "fix it", "model": "m", "config": {}},
        {"event": "tool_call", "ts": 100, "step": 1, "tool": "run", "input": {"command": "ls"},
         "output": "\n".join(f"line{i}" for i in range(50))},
        {"event": "run_end", "ts": 101, "status": "finished", "budget": {"steps": 1, "tokens": 5, "cost_usd": 0.01}},
    ]
    (tmp_path / "trajectory.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    got = list(frames(tmp_path, max_lines=3))
    assert "bare loop" in got[0][1]
    tool = next(t for _, t in got if "$ ls" in t)
    assert "line2" in tool and "line3" not in tool and "(47 more lines)" in tool
    assert next(p for p, t in got if "$ ls" in t) == 2.5  # a long gap is capped
    assert any("\033[" in t for _, t in frames(tmp_path, color=True))


def test_bad_inputs(tmp_path):
    with pytest.raises(ReplayError, match="no trajectory"):
        list(frames(tmp_path))
    (tmp_path / "trajectory.jsonl").write_text('{"event": "tool_call"}\n', encoding="utf-8")
    with pytest.raises(ReplayError, match="not a spec-to-pr trajectory"):
        list(frames(tmp_path))


def test_cli_replay():
    r = run_cli("replay", str(SAMPLE), "--no-delay")
    assert r.returncode == 0 and "[verify] verified" in r.stdout
