"""Replay a recorded run from its trajectory.jsonl, step by step. No model, sandbox or network needed.

`frames(run_dir)` turns the log into (pause_seconds, text) pairs; the CLI prints them with the pauses
scaled by --speed. Pauses follow the recorded timestamps, capped so a slow test run doesn't stall the replay.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

MAX_PAUSE_S = 2.5
BOLD, DIM, GREEN, RED, CYAN, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[36m", "\033[0m"


class ReplayError(ValueError):
    pass


def load_events(run_dir: Path) -> list[dict]:
    f = run_dir / "trajectory.jsonl" if run_dir.is_dir() else run_dir
    if not f.is_file():
        raise ReplayError(f"{f}: no trajectory.jsonl (pass a run directory, e.g. runs/<timestamp>)")
    events = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not events or events[0].get("event") != "run_start":
        raise ReplayError(f"{f}: not a spec-to-pr trajectory (no run_start event)")
    return events


def _clip(text: str, max_lines: int, width: int = 160) -> str:
    lines = [ln if len(ln) <= width else ln[: width - 1] + "…" for ln in text.rstrip().splitlines()]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ({len(lines) - max_lines} more lines)"]
    return "\n".join("    " + ln for ln in lines)


def _tool_line(tool: str, args: dict) -> str:
    if tool in ("run", "bash"):
        cmd = str(args.get("command", "")).strip().splitlines() or [""]
        more = f"  … ({len(cmd) - 6} more lines)" if len(cmd) > 6 else ""
        return "$ " + "\n  ".join(cmd[:6]) + more
    if tool == "read_file":
        return f"read {args.get('path', '')}"
    if tool == "edit_file":
        return f"edit {args.get('path', '')}"
    if tool == "search":
        return f"search {args.get('pattern', args.get('query', ''))!r}"
    if tool == "load_skill":
        return f"load skill {args.get('name', '')}"
    if tool == "submit_plan":
        return "submit plan"
    if tool == "finish":
        return "finish"
    return f"{tool} {json.dumps(args)[:120]}"


def frames(run_dir: Path, max_lines: int = 12, color: bool = False) -> Iterator[tuple[float, str]]:
    """Yield (pause before printing, text) for each thing worth showing."""
    def c(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if color else text

    events = load_events(run_dir)
    start = events[0]
    agent = start.get("agent", "spec-to-pr")
    on = [k for k in ("verification", "skills", "planning", "context") if (start.get("config") or {}).get(k)]
    yield 0.0, c(BOLD, f"Replay: {agent} · {start.get('model', '?')} · " + (" + ".join(on) if on else "bare loop"))
    task = (start.get("task") or "").strip().splitlines()
    yield 0.5, c(BOLD, "Task: ") + (task[0] if task else "(empty)") + (f" … ({len(task) - 1} more lines)" if len(task) > 1 else "")

    prev_ts = start.get("ts", 0.0)
    for e in events[1:]:
        pause = max(0.0, min(MAX_PAUSE_S, e.get("ts", prev_ts) - prev_ts))
        prev_ts = e.get("ts", prev_ts)
        kind = e.get("event")
        if kind == "repo_map":
            yield pause, c(DIM, f"[context] repo map built (~{e.get('approx_tokens', '?')} tokens)")
        elif kind == "agents_md":
            yield pause, c(DIM, f"[skills] loaded the repo's AGENTS.md ({e.get('chars', '?')} chars)")
        elif kind == "model_call":
            for block in e.get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    yield pause, c(CYAN, f"[step {e.get('step')}] agent: ") + block["text"].strip()
                    pause = 0.0
        elif kind == "tool_call":
            text = c(BOLD, f"[step {e.get('step')}] {_tool_line(e.get('tool', '?'), e.get('input') or {})}")
            out = str(e.get("output") or "").strip()
            if out and e.get("tool") not in ("finish", "submit_plan"):
                text += "\n" + c(DIM, _clip(out, max_lines))
            yield pause, text
        elif kind == "plan":
            plan = e.get("approved") or e.get("submitted") or ""
            yield pause, c(BOLD, f"[plan] {e.get('action', '?')} (by {e.get('by', '?')})") + "\n" + _clip(plan, 40)
        elif kind == "plan_phase_changes_reverted":
            yield pause, c(DIM, "[plan] changes made while planning were reverted")
        elif kind == "compaction":
            yield pause, c(DIM, f"[context] compacted the conversation at {e.get('context_tokens_before', '?')} tokens")
        elif kind == "weakening_flags":
            yield pause, c(RED, "[integrity] test-weakening flags: " + "; ".join(e.get("flags") or []))
        elif kind == "review":
            ok = e.get("verdict") == "approve"
            text = c(GREEN if ok else RED, f"[review] fresh-context reviewer: {e.get('verdict')}")
            for issue in e.get("issues") or []:
                text += f"\n    - {issue if isinstance(issue, str) else json.dumps(issue)}"
            yield pause, text
        elif kind == "error":
            yield pause, c(RED, f"[error] {e.get('message', '')}")
        elif kind == "run_end":
            v = e.get("verification") or {}
            if v:
                ok = bool(v.get("verified"))
                yield pause, (c(GREEN if ok else RED, f"[verify] {'verified' if ok else 'NOT verified'}: ")
                              + f"new test fails without the fix: {v.get('fails_without_change')}; "
                              f"passes with it: {v.get('passes_with_change')}\n    {v.get('test_command', '')}")
                pause = 0.8
            b = e.get("budget") or {}
            yield pause, (c(BOLD, f"[done] {e.get('status')}: ") + f"{b.get('steps', '?')} steps, "
                          f"{b.get('tokens', 0):,} tokens, ${b.get('cost_usd', 0):.3f}, {b.get('elapsed_s', 0):.0f}s")
            if e.get("summary"):
                yield 0.5, _clip(e["summary"], 30, width=10_000)

    patch = run_dir / "patch.diff" if run_dir.is_dir() else None
    if patch and patch.is_file() and patch.read_text(encoding="utf-8").strip():
        yield 1.0, c(BOLD, "[patch]") + "\n" + _clip(patch.read_text(encoding="utf-8"), 60)
