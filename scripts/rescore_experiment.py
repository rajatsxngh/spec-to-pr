"""Rebuild an experiment's scoreboards, results.json and report from its saved per-run rows, with no API calls.

Runs that failed on an account error (no credit, a bad key) are relabeled `skipped_account_error`, so
they drop out of the rates and the cell is rerun next time the experiment runs. Use it on results
recorded before the harness learned to skip those runs itself.

    uv run --extra report python scripts/rescore_experiment.py experiments/swebench-pilot.toml
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from spec_to_pr.evaluate import SKIPPED_ACCOUNT, TaskRun, is_account_error, write_scoreboard  # noqa: E402
from spec_to_pr.experiment import cell_config, cell_dir, load_spec  # noqa: E402
from spec_to_pr.report import write_report  # noqa: E402
from spec_to_pr.tasks import load_taskset  # noqa: E402


def main(spec_path: str, agent: str = "claude") -> None:
    spec = load_spec(Path(spec_path), root=ROOT)
    out = ROOT / "results" / f"experiment-{spec.name}-{agent}"
    taskset = load_taskset(spec.tasks)
    names = {f.name for f in fields(TaskRun)}
    boards, relabeled = [], 0
    for c in spec.configs:
        for m in spec.models:
            cfg = cell_config(c, m)
            d = cell_dir(out, cfg.name, m)
            rows_file = d / "results.jsonl"
            if not rows_file.exists():
                continue
            rows = [TaskRun(**{k: v for k, v in json.loads(line).items() if k in names})
                    for line in rows_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            for r in rows:
                if r.status == "error" and is_account_error(r.detail):
                    r.status, r.resolved = SKIPPED_ACCOUNT, False
                    relabeled += 1
            rows_file.write_text("".join(json.dumps(asdict(r)) + "\n" for r in rows), encoding="utf-8")
            boards.append(write_scoreboard(rows, taskset, cfg, d))
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    results["cells"] = boards
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    files = write_report(boards, [cell_config(c, spec.models[0]).name for c in spec.configs], spec.models, out, spec.name)
    print(f"Relabeled {relabeled} account-error runs; rebuilt {len(boards)} cells: " + ", ".join(str(f) for f in files))


if __name__ == "__main__":
    main(*sys.argv[1:])
