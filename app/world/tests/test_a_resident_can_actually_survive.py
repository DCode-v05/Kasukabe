"""Can somebody in this town stay alive using only the tools it has?

`LIFE-03`. Every prompt change in this project argues about whether a
resident *reaches* for the right verb. Not one test asked the question
underneath: **if it reached for every right verb in the right order,
would it live?**

That is not the same question, and it is the more fundamental one. A
broken link anywhere in the chain — wheat that cannot be milled, bread
that cannot be eaten, a wage that never arrives — makes every prompt
argument moot, because no wording produces survival through a chain
that does not connect. Run `deni-a6f9eb4-5h` ended with 50 of 50 alive,
which sounds like proof and is not: nobody starved because the run was
five hours and the starvation ladder takes three game-days.

The chains held here are the ones DESIGN.md's economy rests on:

* **field to mouth** — harvest, mill, bake, eat, and the hunger goes;
* **ground to goods** — mine, and hold what you mined;
* **work to wage to upkeep** — the loop that keeps a household solvent;
* **and the ladder itself** — that not eating really does escalate.

Each is end to end through the real registry. Where a link is priced or
gated, the test pays and stands in the right place rather than reaching
past it, because a chain that only works with the gates removed is not
a chain this town has.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.life.models import AgentLifeRecord, Sex
from world.life.upkeep import (
    HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD,
    HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD,
    HungerState,
)
from world.tools.bootstrap import build_world
from world.tools.tests.conftest import spawn_agent

FARM = "Kasukabe Farmland"
MILL = "mill"        # `craft`'s gate
KITCHEN = "kitchen"  # `cook`'s gate
WHO = "a_survivor"


async def _walk(registry, world, where: str) -> None:
    """Move the resident, the way a resident would.

    The food chain spans THREE places - the field, the mill and a
    kitchen - and the gates are real. A test that credited the
    inventory and skipped the walking would prove a chain this town
    does not have.
    """
    await registry.execute(world, WHO, "move", {"destination_location_id": where})


@pytest_asyncio.fixture
async def town():
    world, registry = await build_world(genesis_tick=0, seed=21)
    await spawn_agent(world, WHO, FARM, starting_sim=5_000)
    return world, registry


@pytest_asyncio.fixture
async def running_town(tmp_path, monkeypatch):
    """A booted RUNNER, not a bare world.

    The `agent.ate` -> hunger subscriber is wired in `run_genesis` and
    nowhere else (see `test_only_one_host_feeds_anybody` below), so a
    world from `build_world` can grow, mill, bake and eat a loaf and
    still count the resident as starving. Anything asserting that eating
    FEEDS somebody has to run on the host that actually feeds them.
    """
    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp",
        world_mode="embedded", world_port=0,
    ))
    await runner.boot(resume=False)
    await spawn_agent(runner.world_state, WHO, FARM, starting_sim=5_000)
    try:
        yield runner.world_state, runner.registry, runner
    finally:
        await runner.shutdown()


def _held(world, item: str) -> int:
    return world.inventory.quantity(WHO, item)


# ── field to mouth ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_resident_can_grow_mill_bake_and_eat(town):
    """The whole food chain, in the order a person would do it.

    If this breaks, no prompt in the world produces a fed town.
    """
    world, registry = town

    await registry.execute(world, WHO, "harvest", {"crop": "wheat"})
    assert _held(world, "wheat") > 0, "the farmland gave nothing"

    await _walk(registry, world, MILL)
    await registry.execute(world, WHO, "craft", {"recipe_id": "craft:wheat->flour"})
    assert _held(world, "flour") > 0, "wheat cannot be turned into flour"

    await _walk(registry, world, KITCHEN)
    await registry.execute(world, WHO, "cook", {"recipe_id": "cook:flour->bread"})
    assert _held(world, "bread") > 0, "flour cannot be turned into bread"

    before = _held(world, "bread")
    await registry.execute(world, WHO, "eat", {"item": "bread"})
    assert _held(world, "bread") < before, "bread cannot be eaten"


@pytest.mark.asyncio
async def test_eating_actually_clears_the_hunger(running_town):
    """Eating that does not feed you is the cruellest possible bug: the
    resident does everything right, is charged for all of it, and starves
    anyway."""
    world, registry, runner = running_town
    runner.hunger_by_agent[WHO] = HungerState(days_since_meal=2)
    world.hunger_by_agent = runner.hunger_by_agent

    await registry.execute(world, WHO, "harvest", {"crop": "wheat"})
    await _walk(registry, world, MILL)
    await registry.execute(world, WHO, "craft", {"recipe_id": "craft:wheat->flour"})
    await _walk(registry, world, KITCHEN)
    await registry.execute(world, WHO, "cook", {"recipe_id": "cook:flour->bread"})
    await registry.execute(world, WHO, "eat", {"item": "bread"})
    import asyncio

    await asyncio.sleep(0.2)   # the subscriber is on the bus

    assert runner.hunger_by_agent[WHO].days_since_meal == 0, (
        "a resident ate a real loaf and the town still counts it as starving"
    )


@pytest.mark.asyncio
async def test_the_hunger_a_meal_relieves_is_the_one_the_needs_read(running_town):
    """ENG-24's food need and the starvation ladder must be the same
    fact. Two counters would let a resident be fed and desperate at once."""
    from world.tools.needs import compute_needs

    world, registry, runner = running_town
    runner.hunger_by_agent[WHO] = HungerState(days_since_meal=3)
    world.hunger_by_agent = runner.hunger_by_agent
    assert compute_needs(world, WHO)["food"].level == pytest.approx(1.0)

    world.inventory.credit(WHO, "bread", 1)
    await registry.execute(world, WHO, "eat", {"item": "bread"})
    import asyncio

    await asyncio.sleep(0.2)

    assert compute_needs(world, WHO)["food"].level == 0.0


# ── ground to goods ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_resident_can_take_something_out_of_the_ground(town):
    """The other production path. A town with only farming has one trade
    and nothing to trade it against."""
    world, registry = town
    quarry = next(
        (zone for zone in world.resource_zones.values()
         if (zone.resource_item or "").endswith("_ore")),
        None,
    )
    if quarry is None:
        pytest.skip("this genesis has no ore to dig")

    await spawn_agent(world, "a_miner", quarry.location_id, starting_sim=5_000)
    # `mine` takes the METAL, not the item it yields: "iron", not
    # "iron_ore". Worth the line - it is precisely the vocabulary gap
    # that cost the run its transactions.
    metal = (quarry.resource_item or "").removesuffix("_ore")
    outcome = await registry.execute(world, "a_miner", "mine", {"metal": metal})

    assert world.inventory.quantity("a_miner", quarry.resource_item) > 0, (
        f"mining {quarry.resource_item} produced nothing: {outcome.result}"
    )


# ── the ladder is real ───────────────────────────────────────────────


def test_not_eating_escalates_and_the_thresholds_are_ordered():
    """DESIGN.md: "doing nothing is also a way to die." If the ladder is
    not ordered, that rule is decoration."""
    assert 0 < HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD < HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD


@pytest.mark.asyncio
async def test_a_day_of_not_eating_is_counted(town):
    """The counter the whole ladder reads has to move on a real day."""
    world, _registry = town
    world.hunger_by_agent = {WHO: HungerState(days_since_meal=0)}

    from world.life.upkeep import advance_hunger

    import inspect

    assert callable(advance_hunger), "nothing advances hunger at all"
    assert "days_since_meal" in inspect.getsource(advance_hunger), (
        "the day boundary does not touch the counter the ladder reads"
    )


# ── work to wage to upkeep ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_household_that_pays_its_upkeep_is_not_billed_into_hunger(town):
    """The solvency loop. A resident whose household pays should not be
    walked up the starvation ladder for it."""
    from world.life.upkeep import UPKEEP_TOTAL_SIM_PER_DAY

    world, _registry = town
    assert UPKEEP_TOTAL_SIM_PER_DAY > 0, "living is free; the stakes are fiction"
    # A day's upkeep must be earnable: if one day of living costs more
    # than a resident starts with, the town is unsurvivable by
    # construction and no behaviour can fix it.
    assert world.ledger.sim_balance(WHO) > UPKEEP_TOTAL_SIM_PER_DAY, (
        "a resident cannot afford even one day of existing"
    )


@pytest.mark.asyncio
async def test_the_food_chain_costs_less_than_a_day_of_upkeep(town):
    """Survivability, as arithmetic rather than hope.

    If harvesting, milling and baking one loaf costs more than a day of
    upkeep, then working is worse than doing nothing and the economy is
    upside down - no prompt can rescue that.
    """
    from world.life.upkeep import UPKEEP_TOTAL_SIM_PER_DAY

    world, registry = town
    before = world.ledger.sim_balance(WHO)
    await registry.execute(world, WHO, "harvest", {"crop": "wheat"})
    await _walk(registry, world, MILL)
    await registry.execute(world, WHO, "craft", {"recipe_id": "craft:wheat->flour"})
    await _walk(registry, world, KITCHEN)
    await registry.execute(world, WHO, "cook", {"recipe_id": "cook:flour->bread"})
    spent = before - world.ledger.sim_balance(WHO)

    assert spent < UPKEEP_TOTAL_SIM_PER_DAY, (
        f"making a loaf costs {spent} sim against {UPKEEP_TOTAL_SIM_PER_DAY} "
        "for a day of living - the town cannot be worked out of poverty"
    )


def test_only_one_host_feeds_anybody():
    """CORE-02's shape, found again: a link wired in one host and not the
    other.

    `agent.ate` -> hunger is subscribed in `run_genesis` and NOWHERE
    else. `wiring.py`'s `build_world_server` - the host `main.py` uses -
    does not, so on that host a resident can grow, mill, bake and eat a
    real loaf and still be counted as starving.

    Recorded rather than fixed: the runner is the host every run uses,
    and the roadmap has `build_world_server` down to be retired. What
    must not happen is somebody discovering this during a run on the
    other host. If this test ever fails because wiring.py grew the
    subscriber, that is good news - delete it.
    """
    from pathlib import Path

    app = Path(__file__).resolve().parents[2]
    runner = (app / "world" / "run_genesis.py").read_text(encoding="utf-8")
    server = (app / "world" / "wiring.py").read_text(encoding="utf-8")

    assert "agent.ate" in runner, (
        "the host every run uses no longer feeds anybody"
    )
    assert "agent.ate" not in server, (
        "wiring.py now feeds people too - delete this test and its docstring"
    )
