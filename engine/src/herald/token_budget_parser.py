"""Token budget shorthand parser and nudge formatter — pure functions.

Ports src/utils/tokenBudget.ts to Python with 1:1 semantics. Three patterns,
tried in order:
  1. Start shorthand:  "+500k refactor this"
  2. End shorthand:    "refactor this +500k"    (optional trailing ./!/?)
  3. Verbose form:     "use 500k tokens" / "spend 2m tokens"

Multipliers: k=1_000, m=1_000_000, b=1_000_000_000. Decimals allowed.

All regexes are case-insensitive. Non-positive results (0, rounded-to-zero
fractions, negative via absence of the `+`) yield None — callers can treat
"no budget" as "feature inert this turn".
"""

from __future__ import annotations

import re

# Match "+500k" at message start (optional leading whitespace).
SHORTHAND_START_RE = re.compile(
    r"^\s*\+(\d+(?:\.\d+)?)\s*(k|m|b)\b",
    re.IGNORECASE,
)

# Match "+500k" at message end, optionally followed by ./!/? and whitespace.
SHORTHAND_END_RE = re.compile(
    r"\s\+(\d+(?:\.\d+)?)\s*(k|m|b)\s*[.!?]?\s*$",
    re.IGNORECASE,
)

# Match "use 500k tokens" / "spend 2m tokens" anywhere in the text.
VERBOSE_RE = re.compile(
    r"\b(?:use|spend)\s+(\d+(?:\.\d+)?)\s*(k|m|b)\s*tokens?\b",
    re.IGNORECASE,
)

MULTIPLIERS: dict[str, int] = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
}


def parse_match(value: str, suffix: str) -> int:
    """Apply multiplier and floor to an int. Assumes suffix is k/m/b (any case)."""
    return int(float(value) * MULTIPLIERS[suffix.lower()])


def parse_token_budget(text: str) -> int | None:
    """Parse a user-facing output token budget from free-form message text.

    Returns the integer budget on match, or None if no pattern matches or
    the result is non-positive (zero, fractional-rounding-to-zero).
    """
    if not text:
        return None

    for pattern in (SHORTHAND_START_RE, SHORTHAND_END_RE, VERBOSE_RE):
        match = pattern.search(text)
        if match:
            budget = parse_match(match.group(1), match.group(2))
            if budget > 0:
                return budget
            # Match but zero result — treat as no budget, do not fall through
            # to other patterns (matches src: first matching pattern wins).
            return None

    return None


def get_budget_continuation_message(pct: int, turn_tokens: int, budget: int) -> str:
    """Format the nudge message injected as a user message during continuation.

    Matches src/utils/tokenBudget.ts:66-73 output shape exactly, including
    the Unicode em-dash (\u2014) used by src.
    """
    return (
        f"Stopped at {pct}% of token target "
        f"({turn_tokens:,} / {budget:,}). "
        f"Keep working \u2014 do not summarize."
    )
