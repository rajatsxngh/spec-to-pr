# Spec-to-PR

An open-source coding-agent harness that turns a written spec into a tested draft pull request.
This repository is at **milestone 1 (agent foundation)**: a sandboxed agent loop with five tools,
JSONL trajectory logging, and per-task budgets.

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
