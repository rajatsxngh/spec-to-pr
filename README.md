# Spec-to-PR

An open-source coding-agent harness that turns a written spec into a tested draft pull request.
This repository is at **milestone 3 (verification and skills)**:

- **M1:** a sandboxed agent loop with five tools, JSONL trajectory logging and per-task budgets.
- **M2:** a task set with hidden tests, a parallel eval runner with a hard spend cap, and a scoreboard.
- **M3:** test-first editing, a finish gate that proves the new test reproduces the issue, a
  fresh-context reviewer, a test-weakening detector, and on-demand skills plus `AGENTS.md`.

`spec-to-pr solve` runs with verification and skills on (`--no-verification`, `--no-skills` to
turn them off). Eval configs switch them per ablation rung: `configs/bare.toml`,
`configs/verification.toml`, `configs/skills.toml` (`make ablate` runs all three).

## Setup

```bash
uv sync
```

Running the agent for real needs:

- **Docker** (Docker Desktop on Windows/macOS) for the isolated sandbox. Then build the image once:
  `uv run spec-to-pr build-image`
- **Claude API credentials**: `ANTHROPIC_API_KEY`, or a profile from `ant auth login`.

## Run

```bash
uv run spec-to-pr solve examples/planted_bug --task-file examples/planted_bug/TASK.md
```

Each run writes `runs/<timestamp>/` with `trajectory.jsonl` (every model call and tool call, with
tokens and cost), `report.md`, `report.json` and `patch.diff`.

Useful flags: `--model`, `--effort`, `--max-steps`, `--max-tokens`, `--max-usd`, `--max-minutes`,
`--no-fallback` (pin one model, for evals), `--sandbox local` (no isolation; development only).

## Eval

```bash
make eval      # 3 runs of every task with configs/bare.toml, $20 cap by default
make smoke     # first 5 tasks, $5 cap (what CI runs on every PR)
make oracle    # applies each task's reference fix: checks the pipeline with no API calls
make validate  # every task fails on the original code and passes with its reference fix
```

Without `make`, call `uv run spec-to-pr eval ...` directly (see the Makefile). Each eval writes
`results/<stamp>-<config>-<agent>/` with `results.jsonl`, `scoreboard.md`, `scoreboard.json` and
every trajectory under `runs/`.

- **Task set:** `tasks/self/` (version `self-v1`): 10 self-written Python tasks. Each has `repo/`
  (what the agent sees), `hidden/` (tests copied in only after the agent finishes, plus the original
  visible tests so weakened tests don't count), and `solution/` (reference fix for validation).
- **Spend cap:** `--max-total-usd` is strict. A task starts only if the money spent so far plus the
  per-task `max_usd` of every running task still fits.
- **Configs:** `configs/*.toml` set the model, budgets and which harness components are on. Turning
  on a component before its milestone is an error.

## Test

```bash
uv run pytest
```

The tests use a scripted model and a local sandbox, so they need no API key. The Docker tests run
only when Docker and the sandbox image are available.

## Layout

| Module | What it does | PRD |
| --- | --- | --- |
| `sandbox.py` | Docker sandbox (network off, limits, no host env) and a local dev sandbox | F2 |
| `models.py` | Model adapter over the Anthropic SDK, plus a scripted model for tests | F11 |
| `tools.py` | `run`, `read_file`, `edit_file`, `search`, `finish` | F1 |
| `agent.py` | The agent loop, append-only history, reports | F1 |
| `trajectory.py` | JSONL logging | F3 |
| `budget.py` | Step, token, dollar and time caps | F9 |
| `pricing.py` | Per-model prices and cost per call | F3, F9 |
| `tasks.py` | Task format, hidden-test grading, task validation | F12, N5 |
| `evaluate.py` | Parallel runner, spend cap, scoreboard, ablation configs | F12 |
| `verification.py` | Test-first gate, finish gate, test-weakening detector | F7, N2 |
| `review.py` | Fresh-context reviewer subagent | F7 |
| `skills.py`, `skills/` | On-demand skills and `AGENTS.md` loading | F8 |
| `.github/workflows/ci.yml` | Tests, task validation, oracle eval, 5-task smoke eval | N6 |
