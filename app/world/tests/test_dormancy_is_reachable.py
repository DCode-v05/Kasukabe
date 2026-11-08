"""Running out of thinking has to actually happen, and read as words.

`life/death.py::mark_dormant` opens with "Called by the ledger when a
debit leaves an agent at $0" and had no caller anywhere in the tree. So
`is_dormant` was never set by anything: `revive` had nobody to wake,
`check_dormancy_death` - which the runner's hourly life pass has been
calling all along - could not return True however long a run went on, and
every report that said "no dormancies" was describing a missing call site
rather than a healthy town.

The second half is the perception. A resident that comes back from being
unable to think reads its own resurrection first, and the fallback
renderer would have handed it a bracketed payload dict - the exact
failure `format_heard_event`'s docstring records for speech ("28
utterances and ZERO replies").
"""

from __future__ import annotations

import pytest

from world.core.perception import PerceivedEvent
from world.life.constants import DORMANCY_DEATH_HOURS
from world.retrieval_depth import format_heard_event

NAMES = {"n01": "Nell Holloway", "n03": "Tobias Holloway"}


@pytest.mark.asyncio
async def test_a_resident_whose_wallet_hits_zero_falls_dormant(tmp_path) -> None:
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    agent_id = next(iter(runner.world_state.agents))

    runner.ledger.get_account(agent_id).usd_cents = 0
    from world.core.clock import timestamp_for

    await runner.mark_the_broke_dormant(timestamp_for(40))

    assert runner.world_state.agents[agent_id].dormancy.is_dormant is True


@pytest.mark.asyncio
async def test_a_solvent_resident_is_left_alone(tmp_path) -> None:
    from world.core.clock import timestamp_for
    from world.tests.test_run_genesis import make_runner

    from world.life.constants import DORMANCY_THRESHOLD_USD_CENTS

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    agent_id = next(iter(runner.world_state.agents))
    runner.ledger.get_account(agent_id).usd_cents = DORMANCY_THRESHOLD_USD_CENTS + 1

    await runner.mark_the_broke_dormant(timestamp_for(40))

    assert runner.world_state.agents[agent_id].dormancy.is_dormant is False


@pytest.mark.asyncio
async def test_a_resident_with_one_cent_is_swept(tmp_path) -> None:
    """ECON-21: the dead zone between a broker refusal and dormancy.

    A resident holding one cent could not think (every call refused), was
    not dormant (balance above zero), could not be revived (`revive`
    requires `is_dormant`), still owed upkeep and head tax every day, and
    never started the 24-hour clock. No death, and no life either - for
    exactly the residents the dormancy -> revive -> `buy_energy` chain
    exists to create stakes for.
    """
    from world.core.clock import timestamp_for
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    agent_id = next(iter(runner.world_state.agents))
    runner.ledger.get_account(agent_id).usd_cents = 1

    await runner.mark_the_broke_dormant(timestamp_for(40))

    assert runner.world_state.agents[agent_id].dormancy.is_dormant is True, (
        "ECON-21: a resident holding one cent is neither able to act nor "
        "dormant - it is stopped, and nothing in the town will notice"
    )


@pytest.mark.asyncio
async def test_falling_dormant_ends_a_continuing_action(tmp_path) -> None:
    """A revived resident must not resume a job it committed to before it
    went dark - it has been away, and the reason it stopped is not one it
    chose."""
    from world.core.clock import timestamp_for
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    agent_id = next(iter(runner.world_state.agents))
    runner.world_state.start_continuing(
        agent_id, "harvest", {"crop": "wheat"}, {"dusk": True}
    )
    runner.ledger.get_account(agent_id).usd_cents = 0

    await runner.mark_the_broke_dormant(timestamp_for(40))

    assert runner.world_state.is_working(agent_id) is False


@pytest.mark.asyncio
async def test_dormancy_is_announced_to_the_household_with_where_and_how_long(
    tmp_path,
) -> None:
    """`revive` is gated on standing over them, and nothing else in a
    household member's perception says where "them" is."""
    from world.core.clock import timestamp_for
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    published: list = []

    async def record(event) -> None:  # noqa: ANN001 - the bus awaits subscribers
        published.append(event)

    runner.world_state.event_bus.subscribe("health", record)
    agent_id = next(iter(runner.world_state.agents))
    runner.ledger.get_account(agent_id).usd_cents = 0

    await runner.mark_the_broke_dormant(timestamp_for(40))

    dormant = [e for e in published if e.type == "health.agent.dormant"]
    assert len(dormant) == 1
    payload = dormant[0].payload
    assert payload["agent_id"] == agent_id
    assert payload["location_id"] is not None
    assert payload["deadline_hours"] == DORMANCY_DEATH_HOURS


@pytest.mark.asyncio
async def test_a_resident_already_dormant_is_not_re_marked(tmp_path) -> None:
    """A second zero-balance tick must not reset the 24-hour countdown -
    that would make the deadline unreachable and revival pointless."""
    from world.core.clock import timestamp_for
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    agent_id = next(iter(runner.world_state.agents))
    runner.ledger.get_account(agent_id).usd_cents = 0

    await runner.mark_the_broke_dormant(timestamp_for(40))
    since = runner.world_state.agents[agent_id].dormancy.dormant_since_hours
    await runner.mark_the_broke_dormant(timestamp_for(80))

    assert runner.world_state.agents[agent_id].dormancy.dormant_since_hours == since


def test_the_revived_resident_is_told_what_happened_to_it() -> None:
    event = PerceivedEvent(
        type="health.agent.revived",
        tick=61,
        payload={
            "revived_id": "n03",
            "payer_id": "n01",
            "ticks_dormant": 41,
            "sim_paid": 10_000,
        },
        involves_self=True,
        for_agent_id="n03",
    )

    line = format_heard_event(event, NAMES)

    assert "You were dormant for 41 ticks" in line
    assert "Nell Holloway (n01) paid 10000 sim to wake you" in line
    assert "{" not in line


def test_the_payer_is_told_what_their_money_bought() -> None:
    event = PerceivedEvent(
        type="health.agent.revived",
        tick=61,
        payload={"revived_id": "n03", "payer_id": "n01", "sim_paid": 10_000},
        involves_self=True,
        for_agent_id="n01",
    )

    line = format_heard_event(event, NAMES)

    assert "You paid 10000 sim to wake Tobias Holloway (n03)" in line


def test_a_bystander_reads_it_as_news_about_two_other_people() -> None:
    event = PerceivedEvent(
        type="health.agent.revived",
        tick=61,
        payload={"revived_id": "n03", "payer_id": "n01", "sim_paid": 10_000},
        involves_self=False,
        for_agent_id="n09",
    )

    line = format_heard_event(event, NAMES)

    assert "Nell Holloway (n01) paid to wake Tobias Holloway (n03)" in line
    assert "You" not in line


def test_the_household_is_told_where_they_fell_and_how_long_there_is() -> None:
    event = PerceivedEvent(
        type="health.agent.dormant",
        tick=54,
        payload={
            "agent_id": "n03",
            "location_id": "Kasukabe Farmland",
            "deadline_hours": 24.0,
        },
        involves_self=True,
        for_agent_id="n01",
    )

    line = format_heard_event(event, NAMES)

    assert "Tobias Holloway (n03) has fallen dormant at Kasukabe Farmland" in line
    assert "24 game-hours" in line
    assert "{" not in line
