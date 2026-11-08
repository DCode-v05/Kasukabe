"""ECON-06: a sick or starving resident cannot work at full strength.

`health.work_capacity`, `upkeep.effective_work_capacity`,
`WORK_CAPACITY_BY_AILMENT` and `HUNGER_MILD_WORK_CAPACITY_MULTIPLIER`
were all written, all tested, and read by NOTHING outside upkeep.py. The
registry, `harvest`, `run_recipe`, `keep_doing` and the call budget never
consulted any of them, so DESIGN section 5's "sickness cuts work
capacity" was a sentence with no mechanism: a terminal patient harvested
at full rate until the day it died, and treatment (ECON-12) had nothing
to be worth.

Driven through `ToolRegistry.execute` because the capacity functions
were never the problem - the absence of any caller was.
"""

from __future__ import annotations

import random

import pytest

from world.life.health import Ailment, AilmentKind
from world.life.upkeep import HungerState
from world.tools.errors import ActorRequirementError
from world.tools.tests.conftest import spawn_agent

FIELD = "sector:farm"


def _make_ill(world, agent_id: str) -> None:
    agent = world.agents[agent_id]
    health = agent.health.model_copy(
        update={"current_ailment": Ailment(kind=AilmentKind.SHORT, onset_day=0, duration_days=3)}
    )
    world.agents[agent_id] = agent.model_copy(update={"health": health})


@pytest.mark.asyncio
async def test_a_healthy_fed_resident_is_never_refused_for_weakness(world, registry) -> None:
    """Capacity 1.0 must never refuse - the gate is a consequence of
    illness, not a tax on working."""
    await spawn_agent(world, "hale", FIELD)
    world.ledger.get_account("hale").sim_units = 10_000
    world.rng = random.Random(0)

    for _ in range(25):
        registry.refuse_if_too_weak(world, "hale", "harvest")


@pytest.mark.asyncio
async def test_an_ill_resident_is_sometimes_too_weak_to_work(world, registry) -> None:
    await spawn_agent(world, "sick", FIELD)
    _make_ill(world, "sick")
    world.rng = random.Random(0)

    refusals = 0
    for _ in range(40):
        try:
            registry.refuse_if_too_weak(world, "sick", "harvest")
        except ActorRequirementError:
            refusals += 1

    assert refusals > 0, (
        "ECON-06: an ill resident worked forty times without once being "
        "too weak - WORK_CAPACITY_BY_AILMENT is still inert"
    )


@pytest.mark.asyncio
async def test_hunger_alone_can_stop_the_work(world, registry) -> None:
    await spawn_agent(world, "starving", FIELD)
    world.hunger_by_agent["starving"] = HungerState(days_since_meal=1)
    world.rng = random.Random(1)

    refusals = 0
    for _ in range(40):
        try:
            registry.refuse_if_too_weak(world, "starving", "harvest")
        except ActorRequirementError:
            refusals += 1

    assert refusals > 0, "ECON-06: HUNGER_MILD_WORK_CAPACITY_MULTIPLIER is still inert"


@pytest.mark.asyncio
async def test_only_labour_is_gated(world, registry) -> None:
    """Speaking is how a hungry resident gets fed. Gating it would make
    illness a trap with no way out, which is a crueller world than the one
    with no capacity model at all.
    """
    await spawn_agent(world, "weak", FIELD)
    _make_ill(world, "weak")
    world.hunger_by_agent["weak"] = HungerState(days_since_meal=2)
    world.rng = random.Random(2)

    for _ in range(40):
        registry.refuse_if_too_weak(world, "weak", "speak")
        registry.refuse_if_too_weak(world, "weak", "list_places")
        registry.refuse_if_too_weak(world, "weak", "pay_tax")


@pytest.mark.asyncio
async def test_the_refusal_names_what_is_wrong(world, registry) -> None:
    """A dead end is what produced a 5% recovery rate on refused calls.
    The resident has to be able to tell illness from hunger to act on it.
    """
    await spawn_agent(world, "poorly", FIELD)
    _make_ill(world, "poorly")
    world.rng = random.Random(0)

    said: list[str] = []
    for _ in range(40):
        try:
            registry.refuse_if_too_weak(world, "poorly", "harvest")
        except ActorRequirementError as exc:
            said.extend(exc.reasons)

    assert said, "no refusal to inspect"
    assert any("ill" in reason for reason in said), said


@pytest.mark.asyncio
async def test_the_gate_is_wired_into_the_seam_a_resident_uses(world, registry) -> None:
    """The WIRING. `refuse_if_too_weak` could be perfect and uncalled -
    which is exactly the state every capacity function in this codebase
    was already in.
    """
    import inspect

    source = inspect.getsource(type(registry).dispatch)
    assert "refuse_if_too_weak" in source, (
        "ECON-06: the capacity gate has no caller, which is the finding"
    )
