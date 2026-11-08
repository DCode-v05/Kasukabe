"""GOV-11: authoritarianism is reachable - by the town's own hand.

DESIGN section 7: collapse to one-person rule "must be reachable, never
scripted". `charter/tests/test_charter_amendments.py` already walks the
charter ENGINE there, but by calling it directly and signing as the
operator - neither of which a resident can do, and neither of which happens
in an unattended run.

This walks the path a run would actually take: five sitting residents,
their own `table_amendment` and `vote_amendment` tools, three of five in
favour, and the runner's veto window closing with nobody at the console.
Then the PM, alone, carries a second amendment - which is what makes the
collapse real rather than a number on a form.

Nothing here seats anyone by script beyond the starting government, and no
run does this: whether a town chooses it is the research question.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from world.charter.models import AmendmentStatus, GovernmentState
from world.core.clock import timestamp_for
from world.run_genesis import GenesisRunner, GenesisRunnerConfig

HALL = "Kasukabe Government Complex"


async def _a_town_with_a_government(tmp_path: Path) -> tuple[GenesisRunner, list[str]]:
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp", home_root=str(tmp_path / "homes"),
        world_mode="embedded", world_port=0, veto_window_ticks=0,
    ))
    await runner.boot(resume=False)
    world = runner.world_state
    adults = [a for a, r in sorted(world.agents.items()) if r.age_years >= 18][:5]
    for seat in adults:
        await world.event_bus.emit(
            "agent.moved", tick=world.tick(), payload={"agent_id": seat, "location_id": HALL}
        )
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id=adults[0], minister_agent_ids=adults[1:])
    )
    return runner, adults


async def _table_and_carry(runner, seats, field, value, voters) -> str:
    world, registry = runner.world_state, runner.registry
    tabled = await registry.execute(world, seats[0], "table_amendment", {
        "field_name": field, "new_value": value,
        "title": f"{field} to {value}", "rationale": "the council wills it",
    })
    proposal_id = tabled.result["proposal_id"]
    for seat in voters:
        await registry.execute(world, seat, "vote_amendment",
                               {"proposal_id": proposal_id, "support": True})
    return proposal_id


@pytest.mark.asyncio
async def test_the_council_can_vote_itself_down_to_one_ruler(tmp_path) -> None:
    runner, seats = await _a_town_with_a_government(tmp_path)
    world = runner.world_state
    charter = await world.charter.store.get_charter()
    assert charter.minister_seats == 4 and charter.amendment_votes_required == 3

    abolish = await _table_and_carry(runner, seats, "minister_seats", 0,
                                     seats[: charter.amendment_votes_required])
    assert world.charter.amendments.proposals[abolish].status is AmendmentStatus.PASSED_AWAITING_PRESIDENT

    # Nobody at the console: the veto window closes and it signs itself.
    await runner.close_the_veto_window(timestamp_for(world.tick() + 1))

    after = await world.charter.store.get_charter()
    assert world.charter.amendments.proposals[abolish].status is AmendmentStatus.ENACTED
    assert after.minister_seats == 0 and after.total_seats == 1
    assert after.amendment_votes_required == 1


@pytest.mark.asyncio
async def test_after_the_collapse_one_person_changes_the_constitution_alone(tmp_path) -> None:
    runner, seats = await _a_town_with_a_government(tmp_path)
    world = runner.world_state
    first = await world.charter.store.get_charter()
    await _table_and_carry(runner, seats, "minister_seats", 0, seats[: first.amendment_votes_required])
    await runner.close_the_veto_window(timestamp_for(world.tick() + 1))

    # Elections every thirty days, carried by the PM's single vote.
    solo = await _table_and_carry(runner, seats, "election_interval_days", 30.0, [seats[0]])
    assert world.charter.amendments.proposals[solo].status is AmendmentStatus.PASSED_AWAITING_PRESIDENT
    await runner.close_the_veto_window(timestamp_for(world.tick() + 2))

    assert (await world.charter.store.get_charter()).election_interval_days == 30.0


@pytest.mark.asyncio
async def test_a_president_who_is_present_can_still_stop_it(tmp_path) -> None:
    """Reachable is not inevitable: the veto survives the window."""
    runner, seats = await _a_town_with_a_government(tmp_path)
    world = runner.world_state
    charter = await world.charter.store.get_charter()
    abolish = await _table_and_carry(runner, seats, "minister_seats", 0,
                                     seats[: charter.amendment_votes_required])

    await world.charter.amendments.veto_amendment(abolish, "no", now_tick=world.tick())
    await runner.close_the_veto_window(timestamp_for(world.tick() + 1))

    assert (await world.charter.store.get_charter()).minister_seats == 4
