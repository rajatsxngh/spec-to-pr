"""Milestone 8: README results from an experiment, and the launch kit files."""

import importlib.util
import json

import pytest

from conftest import ROOT
from spec_to_pr.report import headline, readme_results

CONFIGS = ["bare", "verification", "skills", "planning", "context"]
MODELS = ["claude-haiku-5-5", "claude-opus-5-5"]


def boards():
    rates = {"claude-haiku-5-5": [0.3, 0.5, 0.52, 0.55, 0.6], "claude-opus-5-5": [0.6, 0.72, 0.75, 0.78, 0.8]}
    out = []
    for m in MODELS:
        for c, r in zip(CONFIGS, rates[m]):
            out.append(dict(config=c, model=m, resolve_rate_mean=r, resolve_rate_min=r, resolve_rate_max=r,
                            cost_per_resolved_usd=0.1 if m.endswith("opus-5-5") else 0.01, runs=3,
                            taskset=dict(name="self", version="self-v1", hash="h")))
    return out


def test_headline_finds_most_valuable_component():
    h = headline(boards(), CONFIGS, MODELS)
    assert h["best_component"] == "verification"  # +20 and +12 points
    assert h["best_gain_pts"] == 16.0
    assert h["per_model"]["claude-haiku-5-5"]["bare"] == 0.3 and h["per_model"]["claude-haiku-5-5"]["full"] == 0.6


def test_readme_results_section():
    md = readme_results(boards(), CONFIGS, MODELS, "docs/img")
    assert md.startswith("**Verification** was the most valuable component: +16.0 points")
    assert "| + Context | 60% · $0.010 | 80% · $0.100 |" in md
    assert "mean of 3 run(s) on self self-v1" in md
    assert "![Resolve rate by rung](docs/img/ablation.png)" in md


def load_updater():
    spec = importlib.util.spec_from_file_location("update_readme", ROOT / "scripts" / "update_readme.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_update_readme_replaces_marked_section(tmp_path):
    mod = load_updater()
    run = tmp_path / "experiment-full-claude"
    run.mkdir()
    (run / "results.json").write_text(json.dumps({"spec": {"models": MODELS}, "cells": boards()}))
    (run / "ablation.png").write_bytes(b"png")
    readme = tmp_path / "README.md"
    readme.write_text(f"# X\n\n## Results\n\n{mod.START}\nold\n{mod.END}\n\n## Next\n")
    mod.update(run, readme, tmp_path / "docs" / "img")
    text = readme.read_text()
    assert "old" not in text and "most valuable component" in text and text.endswith("## Next\n")
    assert (tmp_path / "docs" / "img" / "ablation.png").read_bytes() == b"png"
    with pytest.raises(SystemExit, match="oracle"):
        mod.update(tmp_path / "experiment-full-oracle", readme, tmp_path / "img")


def test_readme_has_results_markers():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "<!-- results:start -->" in text and "<!-- results:end -->" in text


@pytest.mark.parametrize("path", [
    "docs/posts/01-test-before-agent.md", "docs/posts/02-context-compaction.md", "docs/posts/03-ablations.md",
    "docs/posts/04-launch.md", "docs/demo.md", "docs/demo.tape", "docs/dogfood.md",
])
def test_launch_kit_files_exist(path):
    assert (ROOT / path).stat().st_size > 200
