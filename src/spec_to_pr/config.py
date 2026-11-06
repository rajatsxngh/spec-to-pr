"""Run configuration: budgets (F9), sandbox limits (F2) and model settings (F11).

Defaults here are placeholders until the PRD's open questions are decided
("Default budget caps", "Sandbox limits").
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Budget:
    """Per-task caps. Any cap that is hit stops the run with a partial report."""

    max_steps: int = 50  # model calls
    max_tokens: int = 2_000_000  # input + output + cache tokens, summed over all calls
    max_usd: float = 5.00
    max_seconds: float = 30 * 60  # wall clock for the whole task


@dataclass(frozen=True)
class SandboxLimits:
    cpus: float = 2.0
    memory: str = "4g"
    pids: int = 512
    command_timeout_s: int = 120  # default per `run` call; the agent may ask for less


@dataclass(frozen=True)
class ModelSettings:
    model: str = "claude-opus-5-5"
    effort: str = "high"  # low | medium | high | xhigh | max
    max_tokens: int = 16_000  # per response (non-streaming)
    # Server-side refusal fallback. Eval runs that must pin one model can turn it off.
    fallback: bool = True
    prompt_caching: bool = True


@dataclass(frozen=True)
class RunConfig:
    budget: Budget = field(default_factory=Budget)
    sandbox: SandboxLimits = field(default_factory=SandboxLimits)
    model: ModelSettings = field(default_factory=ModelSettings)
    # Baseline cap on a single tool output sent back to the model. Smarter trimming
    # (keep head and tail, compaction) is F6, built in milestone 6.
    max_tool_output_chars: int = 20_000
    # Harness components (ablation switches).
    verification: bool = False  # F7: test-first, finish gate, reviewer
    skills: bool = False  # F8: skills folder + AGENTS.md
    planning: bool = False  # F4: plan.md + approval gate before any edit
    skills_dir: str | None = None  # default: <repo>/skills
    max_review_rounds: int = 1  # times the reviewer can send the agent back (open question in the PRD)
