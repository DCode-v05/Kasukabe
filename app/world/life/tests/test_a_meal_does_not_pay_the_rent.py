"""ECON-16: eating answers hunger; it does not settle the bill.

`attach_eating` wrote `HungerState(consecutive_unpaid_days=0)` on every
`agent.ate` event - the MONEY counter. So a household could skip upkeep
forever as long as each member ate one apple a day: the escalating hunger
ladder is the only consequence of not paying, and a meal reset it to
zero. The `eat` tool's own note already said "Your household still owes
its daily upkeep" and nothing in the code enforced it.

The counter is now two counters, and only paying clears the arrears one.
"""

from __future__ import annotations

import random

import pytest

from world.life.health import HealthState
from world.life.upkeep import (
    HUNGER_MILD_WORK_CAPACITY_MULTIPLIER,
    HungerState,
    advance_hunger,
    effective_work_capacity,
)
from world.life.tests.test_upkeep import make_agent


def test_a_meal_does_not_clear_what_the_household_owes() -> None:
    """The finding, in one assertion."""
    agent = make_agent()
    behind = HungerState(consecutive_unpaid_days=3, days_since_meal=3)

    _, after, _ = advance_hunger(
        random.Random(0), agent, behind, day=9, paid_in_full=False, ate_today=True
    )

    assert after.consecutive_unpaid_days == 4, (
        "ECON-16: eating reset the arrears - four skipped bills became zero "
        "for the price of an apple"
    )
    assert after.days_since_meal == 0, "ECON-16: the meal did not relieve hunger"


def test_only_paying_clears_the_arrears() -> None:
    agent = make_agent()
    behind = HungerState(consecutive_unpaid_days=3, days_since_meal=3)

    _, after, _ = advance_hunger(
        random.Random(0), agent, behind, day=9, paid_in_full=True
    )

    assert after.consecutive_unpaid_days == 0
    assert after.days_since_meal == 0


def test_a_resident_who_eats_every_day_still_runs_up_a_debt() -> None:
    """The exploit, run for a fortnight.

    Subsistence is allowed to keep you alive - `eat` costs real inventory
    you had to harvest, cook or buy - but it must not make you solvent.
    """
    agent = make_agent()
    hunger = HungerState()
    for day in range(14):
        agent, hunger, _ = advance_hunger(
            random.Random(0), agent, hunger, day=day, paid_in_full=False, ate_today=True
        )

    assert hunger.consecutive_unpaid_days == 14, (
        "ECON-16: fourteen unpaid days recorded as "
        f"{hunger.consecutive_unpaid_days}"
    )
    assert agent.dormancy.is_dormant is False, (
        "a resident who fed itself every day starved anyway"
    )


def test_hunger_not_arrears_is_what_weakens_a_worker() -> None:
    """ECON-06's input. Being behind on rent is not a physical state."""
    fed_but_broke = HungerState(consecutive_unpaid_days=9, days_since_meal=0)
    hungry_but_paid = HungerState(consecutive_unpaid_days=0, days_since_meal=1)

    assert effective_work_capacity(HealthState(), fed_but_broke) == pytest.approx(1.0)
    assert effective_work_capacity(HealthState(), hungry_but_paid) == pytest.approx(
        HUNGER_MILD_WORK_CAPACITY_MULTIPLIER
    )


@pytest.mark.asyncio
async def test_the_subscriber_the_town_actually_runs_keeps_the_debt() -> None:
    """The WIRING, driven through the real event bus.

    Every test above calls `advance_hunger` directly, and the defect was
    never in `advance_hunger` - it was in `GenesisRunner.attach_eating`,
    which overwrote the whole HungerState on `agent.ate`. Reverting that
    one line leaves every unit test above green, which is exactly how this
    codebase keeps shipping fixes that change nothing.
    """
    from types import SimpleNamespace

    from world.core.events import EventBus, WorldEvent
    from world.run_genesis import GenesisRunner

    runner = GenesisRunner.__new__(GenesisRunner)
    bus = EventBus()
    runner.world_state = SimpleNamespace(event_bus=bus)
    runner.hunger_by_agent = {"amara": HungerState(consecutive_unpaid_days=5, days_since_meal=2)}
    runner.fed_today = set()

    runner.attach_eating()
    await bus.publish(
        WorldEvent(type="agent.ate", tick=0, payload={"agent_id": "amara", "item": "apple"})
    )

    after = runner.hunger_by_agent["amara"]
    assert after.consecutive_unpaid_days == 5, (
        "ECON-16: the live subscriber wiped five days of arrears for one apple"
    )
    assert after.days_since_meal == 0
    assert "amara" in runner.fed_today, (
        "ECON-16: the day boundary will not know this resident ate"
    )


def test_the_worsening_event_names_which_streak_it_carries() -> None:
    """`streak` counts days since a MEAL now. Publishing it under
    `consecutive_unpaid_days` would put the hunger number under the money
    name for every consumer of this event.
    """
    agent = make_agent()
    _, _, events = advance_hunger(
        random.Random(0),
        agent,
        HungerState(consecutive_unpaid_days=4, days_since_meal=1),
        day=5,
        paid_in_full=False,
    )

    worsened = [e for e in events if e.type == "health.hunger.worsened"]
    assert worsened, "no worsening event"
    payload = worsened[0].payload
    assert payload["days_since_meal"] == 2
    assert payload["consecutive_unpaid_days"] == 5, (
        "the arrears streak is not what this event reported"
    )
