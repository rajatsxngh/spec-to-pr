"""Experiment report (milestone 7): the ablation table and two charts.

- ablation.png: resolve rate at each rung of the ladder, one line per model, whiskers for the
  spread across runs. Answers "which component moved resolve rate, and for which model".
- cost_quality.png: cost per resolved task against resolve rate, one path per model from the bare
  loop to the full harness. Answers "what each step costs for what it buys".
- report.md: the same numbers as a table (the charts' table view), with the change from the
  previous rung.

Colors follow each model (fixed order, never by rank) and every series is also labeled directly,
so nothing depends on color alone. Charts need the `report` extra (matplotlib).
"""

from __future__ import annotations

from pathlib import Path

# Reference categorical palette, light mode, fixed order (validated for color-vision deficiency).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3de"
RUNG_LABELS = {"bare": "Bare loop", "verification": "+ Verification", "skills": "+ Skills",
               "planning": "+ Planning", "context": "+ Context"}


def _rung(name: str) -> str:
    return RUNG_LABELS.get(name, name)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def _usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.3f}"


def _delta(cur: float | None, prev: float | None, money: bool = False) -> str:
    if cur is None or prev is None:
        return ""
    d = cur - prev
    if money:
        return f"{'+' if d >= 0 else '-'}${abs(d):.3f}"
    return f"{d * 100:+.0f} pts"


def report_markdown(boards: list[dict], configs: list[str], models: list[str], title: str) -> str:
    by = {(b["config"], b["model"]): b for b in boards}
    lines = [
        f"# Experiment: {title}",
        "",
        "Resolve rate is the mean over runs, with the min to max across runs. Changes compare each rung with "
        "the rung before it for the same model.",
        "",
        "| Config | Model | Resolve rate | Spread | Change | Cost per resolved | Change | Mean tokens | Flags | Skipped |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for m in models:
        prev = None
        for c in configs:
            b = by.get((c, m))
            if b is None:
                lines.append(f"| {_rung(c)} | {m} | not run | | | | | | | |")
                prev = None
                continue
            rate, cpr = b["resolve_rate_mean"], b["cost_per_resolved_usd"]
            lines.append(
                f"| {_rung(c)} | {m} | {_pct(rate)} | {_pct(b['resolve_rate_min'])} to {_pct(b['resolve_rate_max'])} "
                f"| {_delta(rate, prev and prev['resolve_rate_mean'])} | {_usd(cpr)} "
                f"| {_delta(cpr, prev and prev['cost_per_resolved_usd'], money=True)} | {b['mean_tokens']:,.0f} "
                f"| {b['test_weakening_flagged']} | {b['skipped']} |")
            prev = b
    total = sum(b["total_cost_usd"] for b in boards)
    task_runs = sum(b["task_runs"] for b in boards)
    tsets = {f"{b['taskset']['name']} {b['taskset']['version']} ({b['taskset']['hash']})" for b in boards}
    lines += ["", f"Total: {task_runs} task runs, ${total:.2f}. Task set: {', '.join(sorted(tsets))}.", ""]
    if any(b["skipped"] for b in boards):
        lines.append("Some task runs were skipped by the spend cap; those cells are incomplete and rerun next time.\n")
    return "\n".join(lines)


def _style(ax, fig) -> None:
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def ablation_chart(boards: list[dict], configs: list[str], models: list[str], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by = {(b["config"], b["model"]): b for b in boards}
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=150)
    _style(ax, fig)
    xs = list(range(len(configs)))
    for i, m in enumerate(models):
        color = SERIES[i % len(SERIES)]
        dodge = (i - (len(models) - 1) / 2) * 0.07  # keep each model's whiskers apart
        pts = [(x + dodge, by[(c, m)]) for x, c in zip(xs, configs) if (c, m) in by]
        if not pts:
            continue
        px = [x for x, _ in pts]
        py = [b["resolve_rate_mean"] * 100 for _, b in pts]
        lo = [(b["resolve_rate_mean"] - b["resolve_rate_min"]) * 100 for _, b in pts]
        hi = [(b["resolve_rate_max"] - b["resolve_rate_mean"]) * 100 for _, b in pts]
        ax.errorbar(px, py, yerr=[lo, hi], color=color, linewidth=2, marker="o", markersize=7,
                    markeredgecolor=SURFACE, markeredgewidth=2, capsize=0, elinewidth=1.2, label=m, zorder=3)
        ax.annotate(m, (px[-1], py[-1]), xytext=(10, 0), textcoords="offset points", va="center",
                    fontsize=9, color=INK)
    ax.set_xticks(xs, [_rung(c) for c in configs])
    ax.set_xlim(-0.3, len(configs) - 0.3 + 1.1)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Resolve rate (%)", color=INK_2, fontsize=9)
    ax.set_title("Resolve rate at each rung of the ablation ladder", loc="left", color=INK, fontsize=12,
                 fontweight="bold", pad=12)
    ax.legend(frameon=False, fontsize=9, loc="lower left", labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def cost_quality_chart(boards: list[dict], configs: list[str], models: list[str], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by = {(b["config"], b["model"]): b for b in boards}
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=150)
    _style(ax, fig)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    for i, m in enumerate(models):
        color = SERIES[i % len(SERIES)]
        pts = [(c, by[(c, m)]) for c in configs if (c, m) in by and by[(c, m)]["cost_per_resolved_usd"] is not None]
        if not pts:
            continue
        xs = [b["cost_per_resolved_usd"] for _, b in pts]
        ys = [b["resolve_rate_mean"] * 100 for _, b in pts]
        ax.plot(xs, ys, color=color, linewidth=1.2, alpha=0.6, zorder=2)
        ax.scatter(xs, ys, s=64, color=color, edgecolors=SURFACE, linewidths=2, zorder=3, label=m)
        # Label the ends of each path only: where the model starts (bare) and ends (full harness).
        for (c, _), x, y in ((pts[0], xs[0], ys[0]), (pts[-1], xs[-1], ys[-1])):
            ax.annotate(f"{m}: {_rung(c)}", (x, y), xytext=(8, 6), textcoords="offset points", fontsize=8,
                        color=INK)
    ax.set_xlabel("Cost per resolved task (USD)", color=INK_2, fontsize=9)
    ax.set_ylabel("Resolve rate (%)", color=INK_2, fontsize=9)
    ax.set_ylim(0, 105)
    ax.set_xlim(left=0)
    ax.set_title("What each model pays per resolved task, bare loop to full harness", loc="left", color=INK,
                 fontsize=12, fontweight="bold", pad=12)
    ax.legend(frameon=False, fontsize=9, loc="lower right", labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def write_report(boards: list[dict], configs: list[str], models: list[str], out: Path, title: str) -> list[Path]:
    """Write report.md and, when matplotlib is installed, the two charts. Returns the files written."""
    written = []
    md = report_markdown(boards, configs, models, title)
    try:
        ablation_chart(boards, configs, models, out / "ablation.png")
        cost_quality_chart(boards, configs, models, out / "cost_quality.png")
        md += "\n![Resolve rate by rung](ablation.png)\n\n![Cost versus quality](cost_quality.png)\n"
        written += [out / "ablation.png", out / "cost_quality.png"]
    except ImportError:
        md += "\nCharts skipped: install the report extra (`uv sync --extra report`).\n"
    (out / "report.md").write_text(md, encoding="utf-8")
    return [out / "report.md", *written]
