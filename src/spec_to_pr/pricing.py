"""Token usage and dollar cost per model call (feeds F3 logging and F9 budgets)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    output: float
    cache_read: float

    @property
    def cache_write(self) -> float:
        # 5-minute cache writes bill at 1.25x the input rate.
        return self.input * 1.25


# First-party Claude API list prices. Update when prices change.
PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.00, output=20.00, cache_read=0.20),
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.20),
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01),
    "claude-fable-5-1": Price(input=10.00, output=50.00, cache_read=0.25),
    "claude-opus-5": Price(input=5.00, output=25.00, cache_read=0.50),
    "claude-opus-4-8": Price(input=5.00, output=25.00, cache_read=0.50),
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


class UnknownModelPrice(KeyError):
    pass


def price_for(model: str) -> Price:
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelPrice(
            f"No price for model {model!r}; add it to spec_to_pr.pricing.PRICES so dollar budgets work."
        ) from None


def cost_usd(model: str, usage: Usage) -> float:
    p = price_for(model)
    return (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_read_tokens * p.cache_read
        + usage.cache_write_tokens * p.cache_write
    ) / 1_000_000
