"""Exact-integer cost accounting - no floats, no silent rounding bias.

DESIGN.md section 11 gives flex-tier prices in USD per 1,000,000 tokens
(e.g. Kimi $0.36 in / $1.80 out). app/CONVENTIONS.md's ledger law requires
every money value to be an integer count of cents; it also requires the
$1000 invariant to hold exactly at all times. Those two rules collide at
this exact spot: $0.36 per 1,000,000 tokens is $0.00000036 per token, and
a single agent-tick burns a few thousand tokens - individually worth a
small fraction of one cent. Round every call's cost to the nearest cent
independently and you either round every call to 0 cents (the sim burns
nothing, ever) or round every call up (the sim burns far more than $1000
of real inference actually cost).

The fix is to never round per-call at all. Prices convert to nano-dollars
per token (1 nano-dollar = 1e-9 USD) instead of cents, which is exact:
USD-per-million is already nano-dollars-per-token times 1000, so
0.36 * 1000 = 360 exactly, with no floating point involved anywhere
(MODEL_PRICES_USD_PER_MILLION values are the only floats in this module,
and they are converted to integers once, at import time, in
_nano_dollars_per_token). Cost-per-call is then
input_tokens * input_nano + output_tokens * output_nano - an exact
integer, always.

Converting nano-dollars to the cents the ledger actually debits still
needs *a* rounding rule, but CostAccountant applies it with a carried
remainder (a Bresenham-style error-diffusion accumulator) keyed per payer:
each debit consumes the previous remainder, hands the ledger
whole-cents-only, and keeps the leftover fraction for next time. Summed
over any number of calls for one payer, total cents debited equals
total nano-dollars burned, rounded down by less than one cent - not
"less than one cent per call, accumulating into real drift over
thousands of ticks."
"""

from __future__ import annotations

from dataclasses import dataclass, field

from broker.config import MODEL_ALLOWLIST, MODEL_PRICES_USD_PER_MILLION
from broker.errors import ModelNotAllowedError

NANO_PER_DOLLAR = 1_000_000_000
NANO_PER_CENT = NANO_PER_DOLLAR // 100


@dataclass(frozen=True)
class ModelPrice:
    """Price for one model, in exact integer nano-dollars per token."""

    input_nano_per_token: int
    output_nano_per_token: int


def nano_dollars_per_token(usd_per_million: float) -> int:
    """USD-per-million-tokens -> nano-dollars-per-token, exactly.

    1 nano-dollar = 1e-9 USD, so nano-dollars-per-token = USD-per-token *
    1e9 = (USD-per-million / 1e6) * 1e9 = USD-per-million * 1000. Every
    price in MODEL_PRICES_USD_PER_MILLION has at most 2 decimal digits
    (DESIGN.md section 11: 0.36, 1.80, 0.37, 1.11; Qwen 0.60, 1.44), so
    multiplying by
    1000 always lands on an integer; round() only absorbs float64
    representation noise (0.36 * 1000 can come out 359.99999999999994),
    never a real fractional cent.
    """
    return round(usd_per_million * 1000)


def build_price_table() -> dict[str, ModelPrice]:
    table = {
        model_id: ModelPrice(
            input_nano_per_token=nano_dollars_per_token(input_usd_per_million),
            output_nano_per_token=nano_dollars_per_token(output_usd_per_million),
        )
        for model_id, (input_usd_per_million, output_usd_per_million) in (
            MODEL_PRICES_USD_PER_MILLION.items()
        )
    }
    if unpriced := [m for m in MODEL_ALLOWLIST if m not in table]:
        raise ModelNotAllowedError(
            "no price configured for allowlisted model(s): "
            + ", ".join(sorted(unpriced))
            + " - add flex-tier USD per million tokens (input, output) to "
            "MODEL_PRICES_USD_PER_MILLION, or remove from MODEL_ALLOWLIST. "
            "A model that can be routed to but not priced would burn real "
            "Bedrock spend and record nothing."
        )
    return table


PRICE_TABLE: dict[str, ModelPrice] = build_price_table()


def price_for_model(model_id: str) -> ModelPrice:
    price = PRICE_TABLE.get(model_id)
    if price is None:
        raise ModelNotAllowedError(f"model {model_id!r} has no configured price")
    return price


def exact_cost_nano_dollars(model_id: str, input_tokens: int, output_tokens: int) -> int:
    """Exact integer cost of one call, in nano-dollars. No rounding."""
    price = price_for_model(model_id)
    return input_tokens * price.input_nano_per_token + output_tokens * price.output_nano_per_token


def estimate_cost_cents_ceiling(model_id: str, input_tokens: int, output_tokens: int) -> int:
    """Conservative (rounded UP) cost estimate in cents, for the
    pre-dispatch wallet refusal check only.

    Ceiling, not floor: this number gates whether a call is allowed to
    dispatch at all, so it must never understate the true cost - an
    agent that can afford exactly N cents must not be waved through on a
    call that will actually cost N+1. This is the one place in the
    module where rounding bias is deliberately one-directional; it is
    never used to compute what gets debited (see CostAccountant below),
    only what gets refused.
    """
    nano = exact_cost_nano_dollars(model_id, input_tokens, output_tokens)
    return -(-nano // NANO_PER_CENT)


@dataclass
class CostAccountant:
    """Converts exact nano-dollar costs into whole-cent ledger debits
    with zero long-run drift, via a per-payer carried remainder.

    `debit_cents` is a plain synchronous dict read-modify-write with no
    `await` inside it, so under asyncio's single-threaded cooperative
    scheduling it cannot be interleaved with another coroutine's call for
    the same payer - two of one parent's children debiting "concurrently"
    (DESIGN.md section 4: a minor's parent can have more than one child)
    still serializes correctly with no explicit lock needed, as long as
    nothing here ever awaits mid-update. If this method grows an `await`
    later, that guarantee breaks and it will need a real per-payer lock -
    see broker/ledger.py's InMemoryLedgerClient for the equivalent case
    on the actual wallet mutation, which DOES await (an HTTP call in
    production) and therefore DOES need one.
    """

    remainders_nano: dict[str, int] = field(default_factory=dict)

    def debit_cents(self, payer_agent_id: str, cost_nano_dollars: int) -> int:
        """Fold `cost_nano_dollars` into `payer_agent_id`'s running
        remainder and return the whole cents to actually debit now
        (0 is a valid, expected return value for a cheap call)."""
        total_nano = self.remainders_nano.get(payer_agent_id, 0) + cost_nano_dollars
        cents = total_nano // NANO_PER_CENT
        self.remainders_nano[payer_agent_id] = total_nano % NANO_PER_CENT
        return cents

    def credit_back_cents(self, payer_agent_id: str, cents: int) -> None:
        """Undo a `debit_cents` result that was never actually collected
        from the ledger - broker/service.py calls this when the ledger's
        real debit call fails (a genuine race: world's ledger enforces a
        strict non-negative balance floor and rejects a debit it can't
        cover, per world/ledger/core.py's apply_movement) AFTER Bedrock's
        spend already happened and the caller's response has already
        been computed. The un-collected cost is folded back into the
        remainder rather than dropped, so the very next call for this
        payer bills it again - no dollar is invented (nothing was ever
        actually debited) and none is silently forgiven either.
        """
        self.remainders_nano[payer_agent_id] = (
            self.remainders_nano.get(payer_agent_id, 0) + cents * NANO_PER_CENT
        )

    def pending_remainder_cents_fraction(self, payer_agent_id: str) -> float:
        """Debug/observability only: how much of a cent is currently
        carried, unbilled, for this payer. Never used in a billing
        decision - see the module docstring for why an unbilled
        fractional cent is correct, not a bug."""
        return self.remainders_nano.get(payer_agent_id, 0) / NANO_PER_CENT
