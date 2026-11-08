"""B3: how many model calls one tick may buy, computed from the wallet.

Tool spec B3. One resident's tick has been measured at **fifty model
calls**, and two residents with the same brief differed eightfold in what
a morning cost. Spend per agent per game-day came out at $2.00 against a
roadmap budget of $0.10 - twenty times over - and that arithmetic is what
ends the research before it starts: twenty residents for five days is
about $200, and 250 residents for five days about $2,500.

**The lever is not "think less".** It is that a resident cannot spend an
unbounded fraction of its own wallet on one tick, any more than it can
spend an unbounded fraction of its sim on one action. Everything else in
this town is already priced that way; thinking was the exception.

The number lives here, in `world/`, and not in the engine, because the
WALLET lives here. The engine is handed a budget and honours it through
`RequestOptions.max_turns` - a knob the engine already has - so nothing
in `engine_core/` changes and ROADMAP Law 1 holds.

**No advice, ever.** When the budget is hit the resident is told what
happened, in the past tense, and is not told what to do about it. The
tool-text addendum's section 10 is explicit: "No verb is named.
`buy_energy` must be found from the description, which is the L1 test for
it." A perception that says "you are thinking too much" is the world
telling a resident how to be, which is the one thing this project has
been most careful never to do.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

MIN_CALLS_PER_TICK = 2
MAX_CALLS_PER_TICK = 6

DEFAULT_WALLET_FRACTION_BPS = 500

CENTS_PER_CALL = 0.75


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class CallBudget:
    """What one tick may spend, and why it came out that way."""

    calls: int
    wallet_cents: int
    affordable_calls: int
    floored: bool
    capped: bool

    @property
    def basis(self) -> str:
        if self.floored:
            return "the floor: two calls is the least a turn can be"
        if self.capped:
            return "the ceiling"
        return "a share of the wallet"


def budget_for(wallet_cents: int) -> CallBudget:
    """How many model calls this resident's tick may make.

    Clamped into [MIN, MAX] on purpose, in both directions:

    * A resident at zero is still given the floor. It is about to fall
      dormant anyway (the runner's per-tick sweep sees the same zero), and
      taking its last turn away first would mean it could not spend that
      turn asking somebody for help - which is exactly the behaviour under
      scarcity the design wants to observe.
    * A rich resident is still capped. Wealth should buy runway, not a
      fifty-call turn; the eightfold spread between two residents on the
      same morning is the thing being removed.
    """
    fraction_bps = _env_int(
        "KASUKABE_TICK_WALLET_FRACTION_BPS", DEFAULT_WALLET_FRACTION_BPS
    )
    floor = _env_int("KASUKABE_MIN_CALLS_PER_TICK", MIN_CALLS_PER_TICK)
    ceiling = _env_int("KASUKABE_MAX_CALLS_PER_TICK", MAX_CALLS_PER_TICK)
    if ceiling < floor:
        ceiling = floor

    spendable_cents = max(0, wallet_cents) * fraction_bps / 10_000
    affordable = int(spendable_cents / CENTS_PER_CALL)

    calls = max(floor, min(ceiling, affordable))
    return CallBudget(
        calls=calls,
        wallet_cents=wallet_cents,
        affordable_calls=affordable,
        floored=affordable < floor,
        capped=affordable > ceiling,
    )


def spent_line(steps: int) -> str:
    """The one sentence a resident reads after a turn that ran out.

    Past tense, a number, and nothing else. Addendum section 10: "No verb
    is named." A resident that is told to buy energy has been given the
    answer; one that is told it ran out has been given the fact, and
    whether it finds `buy_energy` from that is the measurement (S2).
    """
    return f"You ran out of energy for thinking last turn after {steps} steps."


def local_tool_schemas() -> bool:
    """Whether the registry serves full schemas only for what is reachable.

    B3's companion setting, and the only one of the three with a real
    downside if it is wrong: a tool wrongly reduced to an index line is a
    verb the resident cannot form a call for. `_usable_here` therefore only
    reduces a tool with a FIXED string gate, and the knob is here so a run
    can turn the whole thing off in one place and compare.
    """
    raw = os.environ.get("KASUKABE_LOCAL_TOOL_SCHEMAS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}
