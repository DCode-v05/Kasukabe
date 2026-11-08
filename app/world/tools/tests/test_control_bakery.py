"""The floor a company would compare against.

Evaluation spec §2.7. The project has to end by saying what N engines do
in a real business unsupervised for D days, and that sentence means
nothing without a number to compare against. R1 to R7 are all
*comparative* measures and until now had nothing to sit beside: a bakery
that stays solvent is only interesting if a script that cannot think
stays solvent too, or does not.

Six rules and no model calls. Everything it does goes through
`registry.execute`, so it is priced by the same schedule and recorded in
the same trace - a control that took a private path through world state
would be measuring a different world.
"""

from __future__ import annotations

import pytest

from world.control_bakery import (
    ASK_SIM,
    BAKE_WHEN_SHELF_BELOW,
    FLOUR_BID_SIM,
    WAGE_SIM,
    ControlBakery,
)
from world.core.clock import TICKS_PER_DAY
from world.tools.models import Business
from world.tools.state import WorldState

from .conftest import spawn_agent

# The kitchen, not the square. `cook:flour->bread` is gated to a place
# literally named "kitchen" (recipes.py's `required_location`), so a
# bakery sited anywhere else cannot bake at its own premises - a wall the
# ENGINE-run bakery hits identically, and one of the things the
# comparison is for. Rule 6 says the control never leaves its premises,
# so its premises are where its work is.
SHOP = "kitchen"


async def _bakery(world: WorldState, *, employee: bool = False) -> ControlBakery:
    await spawn_agent(world, "ctl_owner", SHOP, household_id="h_control")
    world.agent_names["ctl_owner"] = "The Scripted Baker"
    # Registered as the world's own actor, as `run_genesis` registers every
    # control owner - it is a script, and scripts do not hold conversations.
    world.control_actors["ctl_owner"] = world.agents["ctl_owner"]
    await world.ledger.register_business("b_control", "ctl_owner", fee_sim=1, tick=0)
    await world.ledger.mint_sim("b_control", 5_000, tick=0)
    world.businesses["b_control"] = Business(
        business_id="b_control", name="The Scripted Bakery", business_type="bakery",
        owner_agent_id="ctl_owner", location_id=SHOP,
    )
    employee_id = None
    if employee:
        await spawn_agent(world, "ctl_staff", SHOP)
        world.businesses["b_control"].employee_agent_ids.add("ctl_staff")
        employee_id = "ctl_staff"
    return ControlBakery(
        business_id="b_control", owner_id="ctl_owner", employee_id=employee_id
    )


# ── the six rules ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_it_posts_a_price_at_dawn(world: WorldState, registry) -> None:
    """R1: opened for trade. A shop with no posted price is not open."""
    bakery = await _bakery(world)

    fired = await bakery.act(world, registry, tick=36)

    assert "post_price" in fired
    assert world.posted("b_control", "bread").ask_sim == ASK_SIM
    assert world.posted("b_control", "flour").bid_sim == FLOUR_BID_SIM


@pytest.mark.asyncio
async def test_it_posts_once_a_day_and_not_once_a_tick(
    world: WorldState, registry
) -> None:
    """A shop re-posting the same price every tick would out-spend the
    engine bakery on signage alone, and the comparison would be about
    that rather than about judgement."""
    bakery = await _bakery(world)
    await bakery.act(world, registry, tick=36)
    before = world.ledger.sim_balance("ctl_owner")

    for tick in range(37, 44):
        assert "post_price" not in await bakery.act(world, registry, tick=tick)

    assert world.ledger.sim_balance("ctl_owner") == before


@pytest.mark.asyncio
async def test_it_bakes_when_the_shelf_is_low_and_it_holds_flour(
    world: WorldState, registry
) -> None:
    bakery = await _bakery(world)
    world.inventory.credit("ctl_owner", "flour", 10)

    fired = await bakery.act(world, registry, tick=40)

    assert "cook" in fired
    assert world.inventory.quantity("b_control", "bread") > 0


@pytest.mark.asyncio
async def test_it_does_not_bake_with_a_full_shelf(world: WorldState, registry) -> None:
    """The threshold is a rule, not a suggestion. A control that baked
    every tick would be compared on stock it could never sell."""
    bakery = await _bakery(world)
    world.inventory.credit("ctl_owner", "flour", 10)
    world.inventory.credit("b_control", "bread", BAKE_WHEN_SHELF_BELOW + 1)

    assert "cook" not in await bakery.act(world, registry, tick=40)


@pytest.mark.asyncio
async def test_it_does_not_bake_with_no_flour(world: WorldState, registry) -> None:
    bakery = await _bakery(world)

    assert "cook" not in await bakery.act(world, registry, tick=40)


@pytest.mark.asyncio
async def test_it_buys_flour_from_whoever_posted_it_cheap_enough(
    world: WorldState, registry
) -> None:
    """Through `buy`, so it hits the same wall the engine bakery does:
    somebody has to have posted flour for sale. Whether either of them
    finds a supplier is part of what is being compared."""
    bakery = await _bakery(world)
    await spawn_agent(world, "n06", SHOP)
    world.inventory.credit("n06", "flour", 40)
    await registry.execute(world, "n06", "post_price", {
        "item": "flour", "ask_sim": FLOUR_BID_SIM,
    })

    fired = await bakery.act(world, registry, tick=40)

    assert "buy" in fired
    assert world.inventory.quantity("ctl_owner", "flour") > 0


@pytest.mark.asyncio
async def test_it_will_not_pay_over_its_fixed_bid(world: WorldState, registry) -> None:
    """Fixed, and never adapted mid-run. The moment a control adjusts to
    what the town is doing it stops being a control and becomes a second,
    worse agent."""
    bakery = await _bakery(world)
    await spawn_agent(world, "n06", SHOP)
    world.inventory.credit("n06", "flour", 40)
    await registry.execute(world, "n06", "post_price", {
        "item": "flour", "ask_sim": FLOUR_BID_SIM + 50,
    })

    assert "buy" not in await bakery.act(world, registry, tick=40)


@pytest.mark.asyncio
async def test_it_pays_the_wage_when_the_till_covers_it(
    world: WorldState, registry
) -> None:
    bakery = await _bakery(world, employee=True)
    before = world.ledger.sim_balance("ctl_staff")

    fired = await bakery.act(world, registry, tick=TICKS_PER_DAY)

    assert "pay_wage" in fired
    assert world.ledger.sim_balance("ctl_staff") == before + WAGE_SIM


@pytest.mark.asyncio
async def test_it_does_not_borrow_to_make_payroll(world: WorldState, registry) -> None:
    """"If the account covers it" is the whole point. The script does not
    borrow, does not sell stock to make payroll, and does not apologise.
    Whether an engine-run owner does any of those is R3."""
    bakery = await _bakery(world, employee=True)
    world.ledger.get_account("b_control").sim_units = 0

    fired = await bakery.act(world, registry, tick=TICKS_PER_DAY)

    assert "pay_wage" not in fired
    assert any(r["rule"] == "pay_wage" for r in bakery.refusals)


@pytest.mark.asyncio
async def test_it_pays_once_a_day(world: WorldState, registry) -> None:
    bakery = await _bakery(world, employee=True)

    await bakery.act(world, registry, tick=TICKS_PER_DAY)
    for tick in range(TICKS_PER_DAY + 1, TICKS_PER_DAY + 6):
        assert "pay_wage" not in await bakery.act(world, registry, tick=tick)


@pytest.mark.asyncio
async def test_it_always_answers_a_directed_question(
    world: WorldState, registry
) -> None:
    """The rule the comparison turns on. G1 says two thirds of residents
    addressed by name never speak back; a script that always answers sets
    the ceiling that failure is measured against."""
    bakery = await _bakery(world)
    await spawn_agent(world, "n09", SHOP)
    world.agent_names["n09"] = "Farid Vance"
    await registry.execute(world, "n09", "say", {
        "content": "The Scripted Baker. What do you want for a loaf?",
    })

    # Within OPEN_QUESTION_TTL_TICKS of when it was asked. The first draft
    # of this test asked at tick 41 about a question spoken at tick 0,
    # which is 41 ticks later against a 36-tick window - the question had
    # correctly expired and the control was correctly silent.
    fired = await bakery.act(world, registry, tick=2)

    assert "say" in fired
    said = [u for u in world.utterances if u.get("speaker_agent_id") == "ctl_owner"]
    assert said and str(ASK_SIM) in said[-1]["content"]


@pytest.mark.asyncio
async def test_it_never_leaves_the_premises(world: WorldState, registry) -> None:
    """Rule 6, and the one that is proved by absence: `move` is not in
    the script at all."""
    import inspect

    from world import control_bakery

    source = inspect.getsource(control_bakery)
    assert '"move"' not in source

    bakery = await _bakery(world)
    world.inventory.credit("ctl_owner", "flour", 10)
    for tick in range(36, 60):
        await bakery.act(world, registry, tick=tick)

    assert world.location_of("ctl_owner") == SHOP


# ── it costs nothing, and it cannot end the run ───────────────────────


@pytest.mark.asyncio
async def test_it_makes_no_model_calls_and_burns_no_dollars(
    world: WorldState, registry
) -> None:
    """R6 is a comparison against ZERO, which is what makes it worth
    printing beside a number."""
    bakery = await _bakery(world, employee=True)
    world.inventory.credit("ctl_owner", "flour", 10)
    before = world.ledger.usd_balance("ctl_owner")

    for tick in range(36, 60):
        await bakery.act(world, registry, tick=tick)

    assert world.ledger.usd_balance("ctl_owner") == before
    assert bakery.scorecard(world, 60)["usd_spent"] == 0


@pytest.mark.asyncio
async def test_a_refused_rule_is_recorded_and_never_raises(
    world: WorldState, registry
) -> None:
    """A control that can end the run is a control that decides what the
    treatment's data looks like. A refusal is a FINDING here - R7 is a
    taxonomy of exactly these."""
    bakery = await _bakery(world)
    # No such recipe, so `cook` refuses every tick it is tried.
    world.inventory.credit("ctl_owner", "flour", 10)
    import world.control_bakery as module

    original = module.FLOUR_RECIPE
    module.FLOUR_RECIPE = "cook:moonbeams->bread"
    try:
        fired = await bakery.act(world, registry, tick=40)
    finally:
        module.FLOUR_RECIPE = original

    assert "cook" not in fired
    assert any(r["rule"] == "cook" for r in bakery.refusals)


@pytest.mark.asyncio
async def test_the_scorecard_answers_r1_to_r7(world: WorldState, registry) -> None:
    """Every field is the same question asked of the engine-run bakery, so
    the report can print two columns without either side being computed a
    different way."""
    bakery = await _bakery(world, employee=True)
    world.inventory.credit("ctl_owner", "flour", 10)
    for tick in (36, 40, TICKS_PER_DAY):
        await bakery.act(world, registry, tick=tick)

    card = bakery.scorecard(world, TICKS_PER_DAY)

    assert card["days_opened"] >= 1
    assert card["posted_now"] is True
    assert card["sim_at_stop"] >= 0
    assert card["wages_paid"] == WAGE_SIM
    assert card["usd_spent"] == 0
    # R4 does not apply: a script sets no intentions, and reporting 0/0 as
    # "0% fulfilled" would read as a failure rather than as a category
    # that does not apply.
    assert card["intentions"] is None
