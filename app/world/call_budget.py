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

# B3's own floor and ceiling, verbatim from the spec: "at least 2, at most
# 12".
#
# The floor is 2 and not 1 because one call is a turn that can think or
# act but not both: the model answers, sees a tool result, and has nothing
# left to say about it. A resident that can never respond to what its own
# action returned is not bounded, it is mute.
#
# The ceiling was 12 on the reasoning that the largest turn worth having is
# a short chain of related acts - walk, look, speak, trade - and that the
# fifty-call turns which prompted this were not that.
#
# It is 6 from 2026-09-18, because the 50-resident run finally measured
# what those turns are made of: 1,957 turns, 9,488 tool calls, a mean of
# 4.85 and a median of 3. A turn where nothing is refused is three actions.
# The long ones are not rich turns - they are turns that could not get the
# first thing to work: 19% of turns hit a refusal, and those average 6.73
# calls against 4.42 for the rest.
#
# At 6 the median turn is untouched, 24% of turns are cut short, and 19% of
# every call the run makes is not made at all. 5 would take 26% and cut
# 31%; 12 took 2.5%, which is to say it was not a ceiling. A resident doing
# ten things in ten game-minutes is not having a full morning.
MIN_CALLS_PER_TICK = 2
MAX_CALLS_PER_TICK = 6

# The fraction of what is left in the wallet that one tick may consume.
#
# A twentieth, so a resident with a full morning of runway can still have
# a big turn when it wants one, and a resident down to its last few cents
# gets two calls rather than twelve and lasts twenty more ticks instead of
# two. This is the whole mechanism: the budget tightens as the wallet
# empties, which is what makes `buy_energy` a decision rather than a
# curiosity.
DEFAULT_WALLET_FRACTION_BPS = 500  # 5%

# What one model call costs, in USD cents, for the purpose of this
# division only. NOT a billing figure - the broker's own metering is what
# actually debits the wallet (ledger.burn_for_inference). This is the
# planning number the budget is derived from, and it is deliberately a
# measured average rather than a per-call estimate: a budget that had to
# predict the size of a call before making it would need the call.
#
# Measured across run 19 and run 20: about 19,400 input tokens and 291
# output at Kimi's flex prices ($0.36/$1.80 per million) is roughly
# $0.0070 + $0.0005, call it three quarters of a cent.
CENTS_PER_CALL = 0.75


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        # A typo must not silently remove the bound. Everything this module
        # exists for is the bound.
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
