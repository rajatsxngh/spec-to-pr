"""Experiment report (milestone 7): the ablation table and two charts.

- ablation.png: resolve rate at each rung of the ladder, one line per model, whiskers for the
  spread across runs. Answers "which component moved resolve rate, and for which model".
- cost_quality.png: cost per resolved task against resolve rate, one path per model from the bare
  loop to the full harness. Answers "what each step costs for what it buys".
- report.md: the same numbers as a table (the charts' table view), with the change from the
  previous rung.

A mini-swe-agent baseline cell (scoreboard agent "mini-swe-agent") is not a rung: it is the reference
the harness has to beat. Tables list it first, the ablation chart shows it as a separate first column
plus a dashed reference line per model, and the cost chart as an open diamond.

Colors follow each model (fixed order, never by rank) and every series is also labeled directly,
so nothing depends on color alone. Charts need the `report` extra (matplotlib).
"""

from __future__ import annotations

from pathlib import Path

# Reference categorical palette, light mode, fixed order (validated for color-vision deficiency).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3de"
BASELINE_AGENT = "mini-swe-agent"
RUNG_LABELS = {"baseline": "mini-swe-agent", "bare": "Bare loop", "verification": "+ Verification", "skills": "+ Skills",
               "planning": "+ Planning", "context": "+ Context"}


def _rung(name: str) -> str:
    return RUNG_LABELS.get(name, name)


def _split(boards: list[dict], configs: list[str]) -> tuple[list[str], list[str]]:
    """(baseline configs, ladder configs), each in the given order."""
    base = {b["config"] for b in boards if b.get("agent") == BASELINE_AGENT}
    return [c for c in configs if c in base], [c for c in configs if c not in base]


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
    base, ladder = _split(boards, configs)
    for m in models:
        prev = None
        for c in base + ladder:
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
            prev = None if c in base else b  # the first rung is compared with nothing, not the baseline
    if base and ladder:
        lines += ["", "Full harness against the baseline (same model, tasks, sandbox, budgets and grading):", ""]
        for m in models:
            for bc in base:
                bb, full = by.get((bc, m)), by.get((ladder[-1], m))
                if bb and full:
                    lines.append(
                        f"- {m}: {_rung(ladder[-1])} {_pct(full['resolve_rate_mean'])} vs {_rung(bc)} "
                        f"{_pct(bb['resolve_rate_mean'])} ({_delta(full['resolve_rate_mean'], bb['resolve_rate_mean'])}); "
                        f"cost per resolved {_usd(full['cost_per_resolved_usd'])} vs {_usd(bb['cost_per_resolved_usd'])}.")
    total = sum(b["total_cost_usd"] for b in boards)
    task_runs = sum(b["task_runs"] for b in boards)
    tsets = {f"{b['taskset']['name']} {b['taskset']['version']} ({b['taskset']['hash']})" for b in boards}
    lines += ["", f"Total: {task_runs} task runs, ${total:.2f}. Task set: {', '.join(sorted(tsets))}.", ""]
    if any(b.get("skipped_account_error") for b in boards):
        lines.append("Some task runs were skipped because the API account failed (no credit or a bad key); they are "
                     "left out of the rates, and those cells are incomplete and rerun next time.\n")
    if any(b["skipped"] - b.get("skipped_account_error", 0) for b in boards):
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
    base, ladder = _split(boards, configs)
    configs = base + ladder
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=150)
    _style(ax, fig)
    xs = list(range(len(configs)))
    if base and ladder:  # the baseline is a reference column, not a rung of the ladder
        ax.axvline(len(base) - 0.5, color=GRID, linewidth=1.2, linestyle=":")
    for i, m in enumerate(models):
        color = SERIES[i % len(SERIES)]
        dodge = (i - (len(models) - 1) / 2) * 0.07  # keep each model's whiskers apart
        for x, c in zip(xs, base):
            if (c, m) not in by:
                continue
            b = by[(c, m)]
            y = b["resolve_rate_mean"] * 100
            ax.errorbar([x + dodge], [y], yerr=[[y - b["resolve_rate_min"] * 100], [b["resolve_rate_max"] * 100 - y]],
                        color=color, marker="D", markersize=7, markerfacecolor=SURFACE, markeredgewidth=2,
                        linestyle="none", capsize=0, elinewidth=1.2, zorder=3)
            if ladder:
                ax.hlines(y, len(base) - 0.4, len(configs) - 0.7, colors=color, linewidth=1, linestyles="--",
                          alpha=0.45, zorder=1)
        pts = [(x + dodge, by[(c, m)]) for x, c in zip(xs, configs) if c not in base and (c, m) in by]
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
    ax.set_title("Resolve rate at each rung of the ablation ladder"
                 + (" (dashed: mini-swe-agent)" if base else ""), loc="left", color=INK, fontsize=12,
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
    base, configs = _split(boards, configs)
    for i, m in enumerate(models):
        color = SERIES[i % len(SERIES)]
        for c in base:
            b = by.get((c, m))
            if b and b["cost_per_resolved_usd"] is not None:
                x, y = b["cost_per_resolved_usd"], b["resolve_rate_mean"] * 100
                ax.scatter([x], [y], s=64, marker="D", facecolors=SURFACE, edgecolors=color, linewidths=2, zorder=3)
                ax.annotate(f"{m}: {_rung(c)}", (x, y), xytext=(8, -12), textcoords="offset points", fontsize=8,
                            color=INK)
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


def headline(boards: list[dict], configs: list[str], models: list[str]) -> dict:
    """The numbers the README and posts lead with: which component helped most, and bare vs full."""
    by = {(b["config"], b["model"]): b for b in boards}
    base, configs = _split(boards, configs)
    gains: dict[str, list[float]] = {}
    for m in models:
        for prev, cur in zip(configs, configs[1:]):
            if (prev, m) in by and (cur, m) in by:
                gains.setdefault(cur, []).append(by[(cur, m)]["resolve_rate_mean"] - by[(prev, m)]["resolve_rate_mean"])
    best = max(gains, key=lambda c: sum(gains[c]) / len(gains[c])) if gains else None
    per_model = {}
    for m in models:
        first, last = (by.get((configs[0], m)), by.get((configs[-1], m))) if configs else (None, None)
        if first and last:
            per_model[m] = {"bare": first["resolve_rate_mean"], "full": last["resolve_rate_mean"],
                            "bare_cpr": first["cost_per_resolved_usd"], "full_cpr": last["cost_per_resolved_usd"]}
            bb = by.get((base[0], m)) if base else None
            if bb:
                per_model[m] |= {"baseline": bb["resolve_rate_mean"], "baseline_cpr": bb["cost_per_resolved_usd"]}
    return {"best_component": best, "best_gain_pts": round(100 * sum(gains[best]) / len(gains[best]), 1) if best else None,
            "per_model": per_model}


def readme_results(boards: list[dict], configs: list[str], models: list[str], chart_dir: str) -> str:
    """Markdown for the README results section: headline, compact table, charts."""
    by = {(b["config"], b["model"]): b for b in boards}
    h = headline(boards, configs, models)
    lines = []
    if h["best_component"] and h["best_gain_pts"] <= 0:
        lines.append("No component raised the resolve rate over the rung before it in this run; "
                     "the components added cost only.")
        lines.append("")
    elif h["best_component"]:
        lines.append(f"**{_rung(h['best_component']).lstrip('+ ')}** was the most valuable component: "
                     f"{h['best_gain_pts']:+.1f} points of resolve rate on average across models.")
        lines.append("")
    for m, v in h["per_model"].items():
        lines.append(f"- {m}: {_pct(v['bare'])} → {_pct(v['full'])} resolved, bare loop to full harness; "
                     f"cost per resolved task {_usd(v['bare_cpr'])} → {_usd(v['full_cpr'])}"
                     + (f"; mini-swe-agent baseline {_pct(v['baseline'])} at {_usd(v['baseline_cpr'])}."
                        if "baseline" in v else ".")
                     + (" Some of this model's cells are incomplete (see the table), so this is not a like-for-like "
                        "comparison." if any(b.get("skipped") for b in boards if b["model"] == m) else ""))
    lines += ["", "| Rung | " + " | ".join(models) + " |", "| --- | " + " | ".join("---" for _ in models) + " |"]
    for c in configs:
        cells = []
        for m in models:
            b = by.get((c, m))
            cell = f"{_pct(b['resolve_rate_mean'])} · {_usd(b['cost_per_resolved_usd'])}" if b else "not run"
            if b and b.get("skipped"):
                cell += f" (incomplete: {b['task_runs']} of {b['task_runs'] + b['skipped']} runs)"
            cells.append(cell)
        lines.append(f"| {_rung(c)} | " + " | ".join(cells) + " |")
    runs = max((b.get("runs", 0) for b in boards), default=0)
    tsets = sorted({f"{b['taskset']['name']} {b['taskset']['version']}" for b in boards})
    lines += ["", f"Each cell: resolve rate · cost per resolved task, mean of {runs} run(s) on {', '.join(tsets)}."]
    if any(b.get("skipped") for b in boards):
        lines.append("Incomplete cells lost runs to the spend cap or an API account error; "
                     "their rates cover only the runs that happened.")
    lines += ["",
              f"![Resolve rate by rung]({chart_dir}/ablation.png)", "",
              f"![Cost versus quality]({chart_dir}/cost_quality.png)"]
    return "\n".join(lines)


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
