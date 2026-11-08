"""The brief tells a resident its obligations, its stores and its walk.

`CORE-16`. The standing brief named a resident's trade, workplace, food
and balance, and said nothing about what it owed, what it was owed, what
was in its granary, what its own shop held, or that it was three ticks
into a walk. Every one of those facts was already computed:

  * `WorldState.debts_of` - wages either way, loans either way, head-tax
    and upkeep arrears - had exactly ONE caller, the `check_debts` tool,
    which a resident has to think of calling. One that never thinks of it
    is never told, and cannot act on a debt it has no way to know about.
  * `movement.journey_for` was read only inside the movement tools, so
    `look_around` mid-walk showed the ORIGIN (run 23) and the brief said
    nothing about being in transit at all.
  * `retrieval_depth.py:276-281` renders `upkeep_due_sim` and
    `head_tax_due_sim`; `wallet_line` produced neither, so that branch
    could never fire.

This is this project's most common defect in its usual shape: five
finished, tested mechanisms with nothing reading them.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.core.movement import Journey
from world.map.model import TileCoord
from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tools.models import Business, Storage
from world.tools.state import ContinuingAction


@pytest_asyncio.fixture
async def runner(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    return runner


def _someone(runner) -> str:
    return next(iter(runner.world_state.agents))


def _a_genuinely_quiet_one(runner) -> str:
    """A resident on neither side of any loan.

    ECON-13 seeds the genesis debts into `world.loans`, so the FIRST
    resident is no longer a safe stand-in for "owes nothing and is owed
    nothing" - Ezra Lund is a lender at tick 0, and this test used to
    assert that a real creditor was shown no `owed_to_me`. The premise
    went stale, not the code: the town simply has debts now, which is
    what ECON-13 was for.
    """
    encumbered = set()
    for key in runner.world_state.loans:
        lender, _, borrower = key.partition("|")
        encumbered.update((lender, borrower))
    for agent_id in runner.world_state.agents:
        if agent_id not in encumbered:
            return agent_id
    raise AssertionError("every resident is on one side of a loan")


@pytest.mark.asyncio
async def test_a_quiet_resident_is_told_nothing_extra(runner) -> None:
    """A resident who owes nothing and stands still must read exactly as
    before - the block is additive, not a wall of empty keys."""
    brief = runner.standing_brief(_a_genuinely_quiet_one(runner))
    for key in ("owe", "owed_to_me", "in_storage", "walking", "keeping_at"):
        assert key not in brief, f"CORE-16: {key} rendered with nothing in it"


@pytest.mark.asyncio
async def test_a_debt_reaches_the_brief(runner) -> None:
    """The finding itself: an arrear the town is waiting on."""
    who = _someone(runner)
    runner.world_state.arrears[who] = {"head_tax": 40, "upkeep": 15}

    brief = runner.standing_brief(who)
    assert brief["due_today_sim"] == 55, (
        "CORE-16: the resident owes the town 55 sim and its own brief does "
        f"not mention it. Got: {sorted(brief)}"
    )
    kinds = {row["kind"] for row in brief["owe"]}
    assert kinds == {"head_tax", "upkeep"}


@pytest.mark.asyncio
async def test_the_two_dead_keys_now_have_a_producer(runner) -> None:
    """`retrieval_depth` renders these and nothing ever produced them."""
    who = _someone(runner)
    runner.world_state.arrears[who] = {"head_tax": 40, "upkeep": 15}
    brief = runner.standing_brief(who)
    assert brief["head_tax_due_sim"] == 40
    assert brief["upkeep_due_sim"] == 15


@pytest.mark.asyncio
async def test_the_parts_sum_to_the_total(runner) -> None:
    """Two numbers describing one debt must not be able to disagree."""
    who = _someone(runner)
    runner.world_state.arrears[who] = {"head_tax": 7, "upkeep": 3}
    brief = runner.standing_brief(who)
    assert brief["head_tax_due_sim"] + brief["upkeep_due_sim"] == brief["due_today_sim"]


@pytest.mark.asyncio
async def test_a_full_granary_is_not_read_as_destitution(runner) -> None:
    who = _someone(runner)
    runner.world_state.storages["st-1"] = Storage(
        storage_id="st-1", owner_id=who, location_id="home:h1"
    )
    runner.world_state.inventory.balances["st-1"] = {"wheat": 40, "timber": 6}

    brief = runner.standing_brief(who)
    assert brief["in_storage"] == {"wheat": 40, "timber": 6}, (
        "CORE-16: 46 units in this resident's own store and its brief shows "
        "only what is in its hands"
    )


@pytest.mark.asyncio
async def test_somebody_elses_store_is_not_mine(runner) -> None:
    who = _someone(runner)
    other = [a for a in runner.world_state.agents if a != who][0]
    runner.world_state.storages["st-2"] = Storage(
        storage_id="st-2", owner_id=other, location_id="home:h2"
    )
    runner.world_state.inventory.balances["st-2"] = {"wheat": 99}
    assert "in_storage" not in runner.standing_brief(who)


@pytest.mark.asyncio
async def test_an_owner_is_told_the_state_of_their_shop(runner) -> None:
    who = _someone(runner)
    staff = [a for a in runner.world_state.agents if a != who][0]
    runner.world_state.businesses["b-mill"] = Business(
        business_id="b-mill", name="The Mill", business_type="mill",
        owner_agent_id=who, employee_agent_ids={staff}, location_id="business:b-mill",
    )
    runner.world_state.inventory.balances["b-mill"] = {"flour": 12}

    shop = runner.standing_brief(who)["my_business"][0]
    assert shop["name"] == "The Mill"
    assert shop["stock"] == {"flour": 12}
    assert len(shop["staff"]) == 1, (
        "CORE-16: an owner is told nothing about their own till, stock or "
        "staff, so running the shop means guessing at the state of it"
    )


@pytest.mark.asyncio
async def test_a_resident_in_transit_knows_it(runner) -> None:
    """Run 23: `look_around` mid-walk showed the origin, and the brief did
    not mention the walk at all."""
    who = _someone(runner)
    movement = runner.world_state.movement
    if movement is None:
        pytest.skip("no map on this host - movement is None (GROW-27)")
    movement.journeys[who] = Journey(
        agent_id=who,
        path=[TileCoord(x=1, y=1), TileCoord(x=2, y=1), TileCoord(x=3, y=1)],
        destination_location_id="Kasukabe Market Square",
        step_index=0,
        ticks_remaining=4,
    )
    walking = runner.standing_brief(who)["walking"]
    assert walking == {"to": "Kasukabe Market Square", "eta_ticks": 4}


@pytest.mark.asyncio
async def test_a_commitment_says_what_and_how_far_in(runner) -> None:
    """Only the ENDING of a commitment was rendered, so a resident under
    one simply found itself doing the same thing again."""
    who = _someone(runner)
    runner.world_state.continuing[who] = ContinuingAction(
        tool="world_harvest", params={}, until={"repetitions": 20},
        started_tick=3, location_id="Kasukabe Farmland", repetitions=7,
    )
    assert runner.standing_brief(who)["keeping_at"] == {
        "action": "world_harvest", "times_done": 7, "until": {"repetitions": 20},
    }


@pytest.mark.asyncio
async def test_a_broken_reading_does_not_end_the_turn(runner, monkeypatch) -> None:
    """A brief that raises trades a whole turn for a readout."""
    who = _someone(runner)

    def boom(_agent_id):
        raise RuntimeError("the ledger is on fire")

    monkeypatch.setattr(runner.world_state, "debts_of", boom)
    brief = runner.standing_brief(who)
    assert brief["name"], "CORE-16: one broken reading cost the whole brief"
    assert "owe" not in brief
