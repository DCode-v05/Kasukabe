"""Part A end to end: acting costs sim currency, and doing it repeatedly
makes it cheaper.

Everything here runs through the REAL `ToolRegistry.execute()` against the
real `build_world()` composition root, not a hand-built schedule. The
arithmetic of `SCHEDULE_TABLE` already has thorough unit coverage in
world/ledger/tests/test_ledger_action_costs.py; what was missing, and what
this file covers, is that the registry actually calls it - the integration
point that module's docstring described but nothing invoked.
"""

import pytest

from world.ledger.action_costs import (
    ACTION_COST_MEMO_PREFIX,
    DEFAULT_SCHEDULE,
    REFUND_MEMO_PREFIX,
    REPS_TO_MASTERY,
    ActionActor,
    ActionUnaffordableError,
    proficiency_from_reps,
)
from world.tools.bootstrap import build_world
from world.tools.locations import farmland_location
from world.tools.errors import LocationGateError, NotFoundError
from world.tools.tests.conftest import spawn_agent

WHEAT_FIELD = farmland_location("wheat")


async def _harvest(registry, world, who, qty=1):
    return await registry.execute(world, who, "harvest", {"crop": "wheat", "quantity": qty})


@pytest.mark.asyncio
async def test_a_single_harvest_costs_the_farmer_money() -> None:
    world, registry = await build_world(seed=11)
    await spawn_agent(world, "farmer", WHEAT_FIELD, starting_sim=1_000)

    before = world.ledger.sim_balance("farmer")
    await _harvest(registry, world, "farmer", qty=1)
    after = world.ledger.sim_balance("farmer")

    expected = DEFAULT_SCHEDULE.cost_for("harvest", {"crop": "wheat", "quantity": 1}, ActionActor("farmer"))
    assert expected > 0
    assert before - after == expected


@pytest.mark.asyncio
async def test_the_charge_lands_in_the_treasury_not_the_void() -> None:
    """Spent sim is government revenue, not burned - the decided setting.
    Sim currency has no fixed-supply invariant (only USD does), so this is a
    behavioural choice and worth pinning: a town whose fees vanish cannot fund
    anything with them."""
    from world.ledger.money import TREASURY_ACCOUNT_ID

    world, registry = await build_world(seed=12)
    await spawn_agent(world, "farmer", WHEAT_FIELD, starting_sim=1_000)

    treasury_before = world.ledger.sim_balance(TREASURY_ACCOUNT_ID)
    agent_before = world.ledger.sim_balance("farmer")
    await _harvest(registry, world, "farmer")

    paid = agent_before - world.ledger.sim_balance("farmer")
    assert paid > 0
    assert world.ledger.sim_balance(TREASURY_ACCOUNT_ID) == treasury_before + paid


@pytest.mark.asyncio
async def test_practice_lowers_the_price_of_the_same_act() -> None:
    """The whole point of Part A: the tenth harvest is cheaper than the first,
    with nothing in between but repetition."""
    world, registry = await build_world(seed=13)
    await spawn_agent(world, "farmer", WHEAT_FIELD, starting_sim=100_000)

    first_before = world.ledger.sim_balance("farmer")
    await _harvest(registry, world, "farmer", qty=10)
    first_cost = first_before - world.ledger.sim_balance("farmer")

    for _ in range(REPS_TO_MASTERY):
        await _harvest(registry, world, "farmer", qty=10)

    last_before = world.ledger.sim_balance("farmer")
    await _harvest(registry, world, "farmer", qty=10)
    last_cost = last_before - world.ledger.sim_balance("farmer")

    assert world.skills["farmer"]["farming"] == 100
    assert last_cost < first_cost, "practice must make the act cheaper"


@pytest.mark.asyncio
async def test_repetitions_follow_the_square_root_curve() -> None:
    world, registry = await build_world(seed=14)
    await spawn_agent(world, "farmer", WHEAT_FIELD, starting_sim=100_000)

    for n in (1, 4, 15):
        while world.skill_reps.get("farmer", {}).get("farming", 0) < n:
            await _harvest(registry, world, "farmer")
        assert world.skills["farmer"]["farming"] == proficiency_from_reps(n)

    assert proficiency_from_reps(REPS_TO_MASTERY) == 100
    assert proficiency_from_reps(REPS_TO_MASTERY * 5) == 100, "proficiency is capped"


@pytest.mark.asyncio
async def test_only_a_completed_act_teaches_anything() -> None:
    """A refused action must not count as practice. Harvesting a crop the
    field does not grow raises, and the agent is no better at farming for
    having tried."""
    world, registry = await build_world(seed=15)
    await spawn_agent(world, "farmer", WHEAT_FIELD, starting_sim=1_000)

    with pytest.raises(Exception):
        await registry.execute(world, "farmer", "harvest", {"crop": "sapphires", "quantity": 1})

    assert world.skill_reps.get("farmer", {}).get("farming", 0) == 0


@pytest.mark.asyncio
async def test_a_failed_action_is_refunded_not_kept() -> None:
    """The ledger is NOT in execute()'s snapshot, so a charge for an action
    that then raised has to be reversed explicitly - otherwise the world rolls
    back and the wallet does not.

    `transfer` to a nonexistent agent, because it is one of the few failures
    that happens INSIDE a handler: every gate (visibility, actor
    requirements, params, location) is checked before the charge, so a gated
    call never reaches the money at all. Both legs stay in the log - the
    reversal is visible history, not an erasure.
    """
    world, registry = await build_world(seed=16)
    await spawn_agent(world, "payer", "park", starting_sim=1_000)

    before = world.ledger.sim_balance("payer")
    with pytest.raises(NotFoundError):
        await registry.execute(world, "payer", "transfer", {"to_agent_id": "ghost", "amount_sim": 10})

    assert world.ledger.sim_balance("payer") == before, "a failed act must cost nothing"

    memos = [t.memo for t in world.ledger.transactions]
    assert any(m.startswith(ACTION_COST_MEMO_PREFIX) for m in memos), "the charge happened"
    assert any(m.startswith(REFUND_MEMO_PREFIX) for m in memos), "and was reversed, visibly"


@pytest.mark.asyncio
async def test_a_gated_action_never_reaches_the_charge_at_all() -> None:
    """Cheaper than a refund, and the reason the refund path is rare: an act
    the agent was never allowed to attempt leaves no pair of transactions
    behind, because charge() is reached only after every gate has passed."""
    world, registry = await build_world(seed=21)
    await spawn_agent(world, "farmer", WHEAT_FIELD, starting_sim=1_000)

    with pytest.raises(LocationGateError):
        await registry.execute(world, "farmer", "harvest", {"crop": "rice", "quantity": 1})

    assert world.ledger.transactions == []
    assert world.ledger.sim_balance("farmer") == 1_000


@pytest.mark.asyncio
async def test_an_agent_too_poor_to_act_is_refused_and_the_world_is_untouched() -> None:
    """DESIGN.md's "an agent that cannot fund its own cognition falls silent",
    now true of its hands as well as its head."""
    world, registry = await build_world(seed=17)
    await spawn_agent(world, "pauper", WHEAT_FIELD, starting_sim=0)

    with pytest.raises(ActionUnaffordableError):
        await _harvest(registry, world, "pauper", qty=1)

    assert world.ledger.sim_balance("pauper") == 0
    assert world.inventory.quantity("pauper", "wheat") == 0
    assert world.skill_reps.get("pauper", {}).get("farming", 0) == 0


@pytest.mark.asyncio
async def test_a_tool_no_skill_discounts_teaches_nothing() -> None:
    """`move` is nobody's trade. It still costs - nothing is free - but walking
    around must not accumulate a proficiency in anything."""
    world, registry = await build_world(seed=18)
    await spawn_agent(world, "walker", WHEAT_FIELD, starting_sim=10_000)

    await registry.execute(world, "walker", "move", {"destination_location_id": "square"})

    assert world.skill_reps.get("walker", {}) == {}
    assert world.skills.get("walker", {}) == {}


@pytest.mark.asyncio
async def test_a_taught_level_is_never_lowered_by_a_beginner_s_own_practice() -> None:
    """Someone taught farming at 80 who then harvests once has 1 repetition,
    worth 13 - and is still at 80. The two routes to competence do not fight."""
    world, registry = await build_world(seed=19)
    await spawn_agent(world, "student", WHEAT_FIELD, starting_sim=10_000)
    world.skills["student"] = {"farming": 80}

    await _harvest(registry, world, "student")

    assert world.skill_reps["student"]["farming"] == 1
    assert world.skills["student"]["farming"] == 80


@pytest.mark.asyncio
async def test_skill_reps_survive_a_snapshot_restore_round_trip() -> None:
    """skill_reps is owned state, so it must be in OWNED_STATE_FIELDS - if it
    were not, a rolled-back call would leave a phantom repetition behind."""
    world, registry = await build_world(seed=20)
    await spawn_agent(world, "farmer", WHEAT_FIELD, starting_sim=10_000)
    await _harvest(registry, world, "farmer")

    snap = world.snapshot()
    await _harvest(registry, world, "farmer")
    assert world.skill_reps["farmer"]["farming"] == 2

    world.restore(snap)
    assert world.skill_reps["farmer"]["farming"] == 1
