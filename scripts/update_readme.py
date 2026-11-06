"""Fill the README's results section from an experiment run.

    uv run --extra report python scripts/update_readme.py results/experiment-full-claude

Copies the charts to docs/img/ and replaces everything between the results markers in README.md.
Refuses results from the oracle agent (those are pipeline checks, not results) unless --allow-oracle.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spec_to_pr.report import RUNG_LABELS, readme_results  # noqa: E402

START, END = "<!-- results:start -->", "<!-- results:end -->"


def update(results_dir: Path, readme: Path, img_dir: Path, allow_oracle: bool = False) -> str:
    if results_dir.name.endswith("-oracle") and not allow_oracle:
        raise SystemExit(f"{results_dir} is an oracle run (a pipeline check), not real results.")
    data = json.loads((results_dir / "results.json").read_text(encoding="utf-8"))
    boards = data["cells"]
    configs = list(dict.fromkeys(b["config"] for b in boards))
    configs.sort(key=lambda c: list(RUNG_LABELS).index(c) if c in RUNG_LABELS else 99)
    models = list(dict.fromkeys(data["spec"]["models"]))
    img_dir.mkdir(parents=True, exist_ok=True)
    for name in ("ablation.png", "cost_quality.png"):
        if (results_dir / name).exists():
            shutil.copy(results_dir / name, img_dir / name)
    section = readme_results(boards, configs, models, img_dir.relative_to(readme.parent).as_posix())
    text = readme.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"{readme} has no {START} ... {END} markers")
    new = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: f"{START}\n{section}\n{END}", text, flags=re.S)
    readme.write_text(new, encoding="utf-8", newline="\n")
    return section


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit(__doc__)
    print(update(Path(args[0]), ROOT / "README.md", ROOT / "docs" / "img", allow_oracle="--allow-oracle" in sys.argv))
