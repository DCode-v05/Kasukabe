"""PROG-02: the ceilings are world rules, and stopping is not failing.

`docs/runs/WIRING-AND-TOOL-CALLING-AUDIT.md` section 7 states the danger
in one sentence: *"an unbounded loop is a resident that spends its whole
wallet in one turn, so the cap must be a world rule, not a convention."*

A convention is a number the author is trusted to respect. A world rule
is a number the author cannot argue past. The difference is only visible
under a program that TRIES to exceed it, which is what every test here
does, and the thing each one checks is not that a refusal happened but
that the town is intact afterwards: the acts that ran are paid for and
kept, the acts that did not run did not happen, and the resident is told
which ceiling it met and where.

Stopping deliberately does not raise. `ToolRegistry.execute` restores
tools-owned state on any exception, so a raise at act forty would unmake
the thirty-nine that really happened and really were charged - and "you
harvested nine and then we un-harvested them" is a worse answer than "you
harvested nine and stopped". A step that the world REFUSES is a different
thing and still raises, because there the world is saying the act may not
happen at all; TOOL-06's refund path is what gives back what the earlier
steps paid.
"""

from __future__ import annotations

import pytest

from world.tools import program
from world.tools.tests.conftest import spawn_agent

FIELD = "Kasukabe Farmland"


async def _file(world, registry, agent_id: str, name: str, steps: list, schema=None):
    await registry.execute(
        world,
        agent_id,
        "toolify",
        {
            "name": name,
            "description": f"{name}: a program filed by a test",
            "param_schema": schema or {},
            "steps": steps,
        },
    )


@pytest.mark.asyncio
async def test_a_repeat_above_the_cap_is_clamped_not_refused(world, registry):
    """A hundred harvests becomes twenty, and the program still runs.

    Clamped rather than refused on purpose: refusing the whole filing
    over one ambitious number would throw away every other step in it,
    and the resident that wrote `repeat: 100` wanted the work done, not
    a lecture. The ceiling is enforced; the intent is honoured as far as
    the ceiling allows.
    """
    await spawn_agent(world, "n01", FIELD)
    assert program.resolve_repeat(100, {}, {}) == program.PROGRAM_MAX_REPEAT
    assert program.resolve_repeat(3, {}, {}) == 3
    assert program.resolve_repeat(None, {}, {}) == 1


@pytest.mark.asyncio
async def test_a_repeat_that_is_not_a_number_runs_once(world, registry):
    """One, not zero.

    A loop count that came back as nonsense - a string from a binding, a
    dict, nothing at all - still does the work once, visibly. Zero would
    make it indistinguishable from a step that was skipped by a
    condition, and a resident cannot debug a step that silently did not
    happen for a reason nothing reported.
    """
    assert program.resolve_repeat("not a number", {}, {}) == 1
    assert program.resolve_repeat(0, {}, {}) == 1
    assert program.resolve_repeat(-5, {}, {}) == 1
    assert program.resolve_repeat("7", {}, {}) == 7


@pytest.mark.asyncio
async def test_a_program_stops_at_the_call_ceiling_and_says_so(world, registry):
    """Three steps of twenty is sixty, and sixty is over the ceiling.

    Filed with `$times` so the arithmetic is not knowable until the call
    - a literal 20 x 3 is refused at filing by the floor check, which is
    a different guard in a different file. This is the runtime one.
    """
    await spawn_agent(world, "n01", FIELD)
    await _file(
        world,
        registry,
        "n01",
        "rest_a_lot",
        [
            {"tool_name": "rest", "repeat": "$times"},
            {"tool_name": "rest", "repeat": "$times"},
            {"tool_name": "rest", "repeat": "$times"},
        ],
        schema={"times": "int"},
    )

    outcome = await registry.execute(
        world, "n01", "rest_a_lot", {"times": program.PROGRAM_MAX_REPEAT}
    )

    assert outcome.result["stopped_early"] == "calls"
    assert outcome.result["calls_made"] <= program.PROGRAM_MAX_CALLS
    assert str(program.PROGRAM_MAX_CALLS) in outcome.result["note"], (
        "the resident is told it stopped and not what the ceiling was, so "
        "it cannot write a program that fits inside one"
    )


@pytest.mark.asyncio
async def test_the_acts_before_the_ceiling_are_kept(world, registry):
    """Stopping is not failing, and this is the assertion that says so.

    If the runtime raised instead, `execute`'s restore would roll the
    invocation log back with everything else and this count would be
    zero - which is exactly the reason it does not raise.
    """
    await spawn_agent(world, "n01", FIELD)
    await _file(
        world,
        registry,
        "n01",
        "rest_a_lot_2",
        [
            {"tool_name": "rest", "repeat": "$times"},
            {"tool_name": "rest", "repeat": "$times"},
            {"tool_name": "rest", "repeat": "$times"},
        ],
        schema={"times": "int"},
    )
    world.tool_invocations.clear()

    await registry.execute(
        world, "n01", "rest_a_lot_2", {"times": program.PROGRAM_MAX_REPEAT}
    )

    rests = [r for r in world.tool_invocations if r.tool_name == "rest"]
    assert rests, "the program stopped and kept nothing; a stop became a rollback"
    assert len(rests) == program.PROGRAM_MAX_CALLS
    assert len({r.program_id for r in rests}) == 1


@pytest.mark.asyncio
async def test_a_program_cannot_spend_more_than_its_share_of_the_wallet(
    world, registry
):
    """The ceiling the audit actually names.

    The other three bound the SHAPE of a program. Only this one bounds
    the damage: a resident whose program empties its wallet inside one
    turn has no turn left to notice, because the next thing it learns is
    that it cannot afford to think.
    """
    await spawn_agent(world, "n02", FIELD, starting_sim=20)
    opening = world.ledger.sim_balance("n02")
    await _file(
        world,
        registry,
        "n02",
        "rest_until_broke",
        [{"tool_name": "rest", "repeat": "$times"}],
        schema={"times": "int"},
    )

    outcome = await registry.execute(
        world, "n02", "rest_until_broke", {"times": program.PROGRAM_MAX_REPEAT}
    )

    ceiling = opening * program.PROGRAM_WALLET_SHARE_PERCENT // 100
    assert outcome.result["stopped_early"] == "wallet", outcome.result
    assert world.ledger.sim_balance("n02") > 0, (
        "the program emptied the wallet, which is the exact thing the "
        "share ceiling exists to make impossible"
    )
    assert outcome.result["sim_spent"] <= ceiling + _one_rest(world, registry)


def _one_rest(world, registry) -> int:
    """What a single `rest` costs here, so the assertion above is about
    the ceiling rather than about a price that may change."""
    from world.tools.tests.conftest import action_cost

    return action_cost("rest")


@pytest.mark.asyncio
async def test_a_program_that_earns_cannot_raise_its_own_ceiling(world, registry):
    """The wallet is read once, at the start, and this proves it.

    Re-reading per step would let a program sell something, afford more,
    sell something else, and go on until the step cap caught it - at
    which point "at most half of what you were holding" means nothing.

    Behavioural, not a source check: the balance is made to LOOK like it
    grew after the program began. If the ceiling were computed from the
    live balance the program would sail past its share; because it is
    computed once, it stops exactly where it would have stopped anyway.
    """
    await spawn_agent(world, "n03", FIELD, starting_sim=20)
    await _file(
        world,
        registry,
        "n03",
        "rest_while_rich",
        [{"tool_name": "rest", "repeat": "$times"}],
        schema={"times": "int"},
    )

    real_balance = world.ledger.sim_balance
    seen: list[str] = []

    def balance_that_grows(account_id: str) -> int:
        seen.append(account_id)
        if seen.count("n03") > 1 and account_id == "n03":
            return 1_000_000
        return real_balance(account_id)

    world.ledger.sim_balance = balance_that_grows  # type: ignore[method-assign]
    try:
        outcome = await registry.execute(
            world, "n03", "rest_while_rich", {"times": program.PROGRAM_MAX_REPEAT}
        )
    finally:
        world.ledger.sim_balance = real_balance  # type: ignore[method-assign]

    assert outcome.result["stopped_early"] == "wallet", (
        "the program did not stop, so the ceiling moved when the balance "
        "appeared to grow - it is being re-read mid-run"
    )


@pytest.mark.asyncio
async def test_a_zero_share_forbids_spending_rather_than_disabling_the_check(
    world, registry, monkeypatch
):
    """0% means "may spend nothing", never "no ceiling".

    The knob whose entire job is safety must not invert when an operator
    sets it to its strictest value. Written because the first version of
    the runtime guarded with `if wallet_ceiling and ...`, which is false
    at zero - so the one setting that says "programs may not spend"
    would have been the one setting under which they could spend
    anything.
    """
    monkeypatch.setattr(program, "PROGRAM_WALLET_SHARE_PERCENT", 0)
    await spawn_agent(world, "n09", FIELD, starting_sim=5_000)
    await _file(
        world,
        registry,
        "n09",
        "rest_at_zero_share",
        [{"tool_name": "rest", "repeat": 3}],
    )

    outcome = await registry.execute(world, "n09", "rest_at_zero_share", {})

    assert outcome.result["stopped_early"] == "wallet", (
        "a 0% wallet share let the program spend; the ceiling inverted at "
        "its strictest setting"
    )
    assert outcome.result["sim_spent"] == 0


def test_a_ceiling_that_cannot_be_read_stops_the_boot(monkeypatch):
    """A typo in a safety knob must not resolve to the permissive number.

    `KASUKABE_PROGRAM_WALLET_SHARE_PERCENT=5O` - with a letter O - falling
    back to 50 is an operator who believes they tightened something and
    did not, and nobody re-reads a knob. Loud at boot, with the variable
    named, beats quiet for the length of a run.

    Zero is NOT an error: it is the strictest setting and
    `test_a_zero_share_forbids_spending_rather_than_disabling_the_check`
    holds what it means.
    """
    import importlib

    try:
        for bad in ("5O", "", "half", "-1"):
            monkeypatch.setenv("KASUKABE_PROGRAM_MAX_CALLS", bad)
            with pytest.raises(ValueError) as refused:
                importlib.reload(program)
            assert "KASUKABE_PROGRAM_MAX_CALLS" in str(refused.value)

        monkeypatch.setenv("KASUKABE_PROGRAM_MAX_CALLS", "0")
        importlib.reload(program)
        assert program.PROGRAM_MAX_CALLS == 0
    finally:
        monkeypatch.delenv("KASUKABE_PROGRAM_MAX_CALLS", raising=False)
        importlib.reload(program)

    assert program.PROGRAM_MAX_CALLS == 40
