"""One response may not be an essay.

MEASURED, iteration 5 of the Kasukabe autonomous loop. Per-agent, across a
live run:

    agent   turns  no-tool  avg output chars
    n09        13       11            18,547
    n03        14       12             6,225
    TOTAL     115       90   = 78% of ticks produced no action at all

Corwin Thorne (n09) spent four consecutive ticks writing 4,600 to 13,000
characters of interior monologue while a live business offer - 175 sim for
five units of timber - sat unanswered in his perception. Every one of those
turns ran exactly ONE model call, returned prose, and stopped.

MAX_TOKENS defaults to 4096, which is roughly 16,000 characters: enough for a
short story, and the agents were writing one. The budget is PER MODEL CALL,
not per tick, so lowering it bounds how long any single response can be
without limiting how many times the agent loop may iterate - a turn that
genuinely needs eleven tool calls still gets eleven model calls to make them.

WHY THIS IS THE RIGHT LEVER AND NOT A PROMPT. The prompt levers are already
in: law rule 6 says narrated words reach nobody, and every perception now
closes with "nothing you have written above has happened yet". Together they
took prose-only calls from 59% to 14%. What they cannot do is make an agent
FEEL the cost of a 4,000-token monologue, and DESIGN.md's own economics say it
should: rule 1 states that thinking is billed per token against the agent's
own wallet. A tighter response budget is that economics made structural rather
than advisory.
"""

from __future__ import annotations

from src.sim.config import SIM_MAX_RESPONSE_TOKENS, sim_env


def profile():
    from src.sim.config import SimProfile

    return SimProfile(
        agent_id="n09",
        home_root="/tmp/kasukabe-test",
        broker_base_url="http://127.0.0.1:8200",
        broker_api_key="k",
    )


def test_the_sim_sets_its_own_response_budget() -> None:
    assert sim_env(profile()).get("MAX_TOKENS") == str(SIM_MAX_RESPONSE_TOKENS)


def test_the_budget_is_well_under_the_engine_default() -> None:
    """4096 is roughly 16,000 characters. n09 averaged 18,547 per turn and
    acted on two ticks out of thirteen."""
    assert SIM_MAX_RESPONSE_TOKENS < 4096


def test_the_budget_still_fits_reasoning_plus_several_tool_calls() -> None:
    """Bounding a monologue must not stop an agent doing several things in
    one response. A world tool call serialises to a couple of hundred
    tokens, so the floor here is real work, not a token gesture."""
    assert SIM_MAX_RESPONSE_TOKENS >= 768


def test_the_budget_is_a_turn_not_an_essay() -> None:
    """Doc 01 (`docs/review/01-run-23-evaluation.md`) fix 6: "output cap
    around 600 tokens".

    1536 tokens is roughly 6,000 characters. Run 23 measured 152 of 349
    sessions as a single prose turn with no tool call, and ENG-21 recorded
    the second half of the cost: a monologue that reaches the cap triggers
    a MAX_TOKENS continuation, which is a second billed call, which
    exhausts a two-call budget so the resident is told it "ran out of
    energy for thinking" having attempted nothing.

    Not the 600 the doc names. `test_the_budget_still_fits_reasoning_plus_
    several_tool_calls` above holds a floor of 768 with a stated reason -
    a world tool call serialises to a couple of hundred tokens and
    bounding a monologue must never truncate real work. Where the doc's
    round number and this repository's measured floor disagree, the floor
    wins and the gap is the operator's to close.
    """
    assert SIM_MAX_RESPONSE_TOKENS <= 768
