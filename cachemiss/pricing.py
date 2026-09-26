"""Per-token prices used to put a dollar figure on rebuilds.

Base prices are Anthropic first-party API list prices per million tokens
(reference table dated 2026-06-24). Cache reads cost 0.1x base input on most
models, 0.025x on Claude Fable 5.1, 0.05x on Claude Opus 5.5; cache writes cost
1.25x for the 5-minute TTL and 2x for the 1-hour TTL. Claude Code subscription
quotas are not billed in dollars; the figures here are the API-equivalent
value of the tokens, which is the only common yardstick.
"""

from __future__ import annotations

from dataclasses import dataclass

WRITE_5M = 1.25
WRITE_1H = 2.0


@dataclass(frozen=True)
class Price:
    input_per_m: float
    output_per_m: float
    read_multiplier: float = 0.10

    @property
    def read_per_m(self) -> float:
        return self.input_per_m * self.read_multiplier

    def write_per_m(self, ttl: str) -> float:
        return self.input_per_m * (WRITE_1H if ttl == "1h" else WRITE_5M)


# Prefix match, longest first.
_TABLE: list[tuple[str, Price]] = [
    ("claude-fable-5-1", Price(10.0, 50.0, 0.025)),
    ("claude-mythos-5-1", Price(10.0, 50.0, 0.025)),
    ("claude-fable-5", Price(10.0, 50.0)),
    ("claude-mythos-5", Price(10.0, 50.0)),
    ("claude-opus-5-5", Price(4.0, 20.0, 0.05)),
    ("claude-opus-5", Price(5.0, 25.0)),
    ("claude-opus-4", Price(5.0, 25.0)),
    ("claude-sonnet-5", Price(2.0, 10.0)),
    ("claude-sonnet-4", Price(3.0, 15.0)),
    ("claude-haiku-4", Price(1.0, 5.0)),
    ("claude-haiku", Price(1.0, 5.0)),
    ("claude-sonnet", Price(3.0, 15.0)),
    ("claude-opus", Price(5.0, 25.0)),
]
DEFAULT = Price(5.0, 25.0)


def price_for(model: str | None) -> Price:
    if not model:
        return DEFAULT
    for prefix, price in _TABLE:
        if model.startswith(prefix):
            return price
    return DEFAULT
