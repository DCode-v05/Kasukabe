"""GOV-03/GOV-13: a charter nobody could change.

`amendments.cast_amendment_vote` has zero callers anywhere in the tree.
The only proposer is `skillify` with `governmental=True`, which files a
`standing_orders` change that then sits PROPOSED forever. `table_amendment`
was "deliberately absent" on the grounds that specifying it against a
charter nobody had tried to amend would be guessing twice - reasonable, and
wrong in one direction: nobody had tried because there was no way to.

So DESIGN section 7's "charter amendment by three-fifths vote, including
election rules and collapse to one-person rule" is false at runtime, and
`charter/models.py:20-27`'s claim that one-person rule is reachable is not
true of any run this project has made.

GOV-13 rides along: `vote` carried `min_age=18` in the registry while
`elections.check_suffrage` read `charter.suffrage_min_age`. Two rules for
one question, and the stricter won - so an amendment LOWERING suffrage
changed nothing, while one raising it worked. The sort of asymmetry nobody
notices until the town votes for the other one.
"""

from __future__ import annotations

import pytest

from world.charter.models import AmendmentStatus, GovernmentState
from world.tools.state import WorldState

from .conftest import spawn_agent

HALL = "Kasukabe Government Complex"
SEATS = ["pm", "m1", "m2", "m3", "m4"]


async def _a_sitting_government(world: WorldState) -> None:
    for seat in SEATS:
        await spawn_agent(world, seat, HALL, age_years=40.0, household_id="h1")
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id="pm", minister_agent_ids=SEATS[1:])
    )


@pytest.mark.asyncio
async def test_a_seat_holder_can_table_a_change(world, registry):
    await _a_sitting_government(world)

    outcome = await registry.execute(
        world,
        "pm",
        "table_amendment",
        {
            "field_name": "election_interval_days",
            "new_value": 6.0,
            "title": "Longer terms",
            "rationale": "Three days is not a term, it is a fortnight of campaigning.",
        },
    )

    assert outcome.result["status"] == AmendmentStatus.PROPOSED.value
    assert outcome.result["proposal_id"] in world.charter.amendments.proposals


@pytest.mark.asyncio
async def test_a_resident_with_no_seat_cannot_table_one(world, registry):
    from world.tools.errors import ToolNotFoundError

    await _a_sitting_government(world)
    await spawn_agent(world, "n02", HALL, age_years=40.0, household_id="h2")

    with pytest.raises(ToolNotFoundError):
        await registry.execute(
            world,
            "n02",
            "table_amendment",
            {
                "field_name": "election_interval_days",
                "new_value": 6.0,
                "title": "x",
                "rationale": "y",
            },
        )


@pytest.mark.asyncio
async def test_three_of_five_carries_it(world, registry):
    await _a_sitting_government(world)
    tabled = await registry.execute(
        world,
        "pm",
        "table_amendment",
        {
            "field_name": "election_interval_days",
            "new_value": 6.0,
            "title": "Longer terms",
            "rationale": "because",
        },
    )
    proposal_id = tabled.result["proposal_id"]

    charter = await world.charter.store.get_charter()
    for seat in SEATS[: charter.amendment_votes_required]:
        outcome = await registry.execute(
            world, seat, "vote_amendment", {"proposal_id": proposal_id, "support": True}
        )

    assert outcome.result["status"] == AmendmentStatus.PASSED_AWAITING_PRESIDENT.value
    proposal = world.charter.amendments.proposals[proposal_id]
    assert proposal.passed_tick is not None


@pytest.mark.asyncio
async def test_a_seat_may_change_its_mind_before_it_resolves(world, registry):
    await _a_sitting_government(world)
    tabled = await registry.execute(
        world, "pm", "table_amendment",
        {"field_name": "term_limit_terms", "new_value": 4, "title": "t", "rationale": "r"},
    )
    proposal_id = tabled.result["proposal_id"]

    await registry.execute(
        world, "m1", "vote_amendment", {"proposal_id": proposal_id, "support": True}
    )
    outcome = await registry.execute(
        world, "m1", "vote_amendment", {"proposal_id": proposal_id, "support": False}
    )

    assert outcome.result["yes_votes"] == 0


@pytest.mark.asyncio
async def test_the_charter_decides_who_may_vote_not_the_registry(world, registry):
    """An amendment lowering suffrage has to actually lower it."""
    from world.charter.models import Candidacy, ElectionPhase

    await _a_sitting_government(world)
    await spawn_agent(world, "young", HALL, age_years=16.0, household_id="h3")

    election = await world.charter.elections.schedule_election(0)
    election.candidates["pm"] = Candidacy(agent_id="pm", declared_tick=0)
    await world.charter.store.put_election(election)
    world.current_tick = election.voting_opens_tick + 1

    charter = await world.charter.store.get_charter()
    lowered = charter.model_copy(
        update={"version": charter.version + 1, "suffrage_min_age": 16.0}
    )
    await world.charter.store.push_charter(lowered)

    outcome = await registry.execute(
        world,
        "young",
        "vote",
        {"election_id": election.election_id, "candidate_agent_id": "pm", "because": "She has kept the mill weights honest all season."},
    )
    assert outcome.result["election_id"] == election.election_id


@pytest.mark.asyncio
async def test_the_charters_own_bar_is_still_enforced(world, registry):
    """Removing the registry's copy must not remove the rule."""
    from world.charter.models import Candidacy, ElectionPhase

    await _a_sitting_government(world)
    await spawn_agent(world, "child", HALL, age_years=9.0, household_id="h3")

    election = await world.charter.elections.schedule_election(0)
    election.candidates["pm"] = Candidacy(agent_id="pm", declared_tick=0)
    await world.charter.store.put_election(election)
    world.current_tick = election.voting_opens_tick + 1

    with pytest.raises(Exception) as raised:
        await registry.execute(
            world,
            "child",
            "vote",
            {"election_id": election.election_id, "candidate_agent_id": "pm", "because": "She has kept the mill weights honest all season."},
        )
    assert "suffrage" in str(raised.value).lower()
    assert world.tool_refusals and world.tool_refusals[-1].tool_name == "vote"
