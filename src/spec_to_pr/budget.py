"""Budgets (F9): max steps, tokens, dollars and wall clock per task."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import Budget
from .pricing import Usage


@dataclass
class BudgetTracker:
    budget: Budget
    steps: int = 0
    tokens: int = 0
    usd: float = 0.0
    started: float = field(default_factory=time.monotonic)

    def record(self, usage: Usage, cost: float) -> None:
        self.steps += 1
        self.tokens += usage.total_tokens
        self.usd += cost

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started

    def exceeded(self) -> str | None:
        """Name the first cap that is used up, or None. Checked before each model call."""
        b = self.budget
        if self.steps >= b.max_steps:
            return f"max_steps ({b.max_steps})"
        if self.tokens >= b.max_tokens:
            return f"max_tokens ({b.max_tokens:,})"
        if self.usd >= b.max_usd:
            return f"max_usd (${b.max_usd:.2f})"
        if self.elapsed_s >= b.max_seconds:
            return f"max_seconds ({b.max_seconds:.0f})"
        return None

    def snapshot(self) -> dict:
        return {
            "steps": self.steps,
            "tokens": self.tokens,
            "cost_usd": round(self.usd, 6),
            "elapsed_s": round(self.elapsed_s, 1),
            "limits": {
                "max_steps": self.budget.max_steps,
                "max_tokens": self.budget.max_tokens,
                "max_usd": self.budget.max_usd,
                "max_seconds": self.budget.max_seconds,
            },
        }
