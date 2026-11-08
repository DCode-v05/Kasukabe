"""One life tick, not two that drift.

ECON-22. `world/life/tick.py` and an inline copy of it inside
`world/run_genesis.py`'s `run_life_ticks` both claimed to be "the per-
agent life tick", and they had already stopped agreeing. The inline copy
watched for `agent.aged_to_adult` and ran the coming-of-age transition
off it - ending the ledger's parent-billing relationship and moving the
household - and routed every death through a settlement that inherited
the estate and took the corpse off the scheduler. `life/tick.py`, the
copy `wiring.py` drives, did neither: it aged the minor, fired the
event, and dropped it. So on the root ROADMAP T3 says becomes the only
server, a resident who crossed eighteen was still, financially, a child
for the rest of the run, and their parent went on paying for their
thinking indefinitely. That is not a hypothetical drift between two
copies; it is the drift, already shipped, in the direction the roadmap
is migrating towards.

The consolidation is that `life/tick.py` is now the only implementation
and takes the two effects the runner's copy needed - coming of age and a
consequential death - as optional hooks, so a root supplies its own
collaborators instead of its own tick. The coming-of-age rule itself
moved out of the runner into `life/household.py::bring_of_age`, where
both roots read one copy of "staying is the default, the billing ends
whether or not you ever think about it".

These tests drive `WorldServer.advance_tick()`, the real tick loop of
the real root, for the wiring half; the hook contract itself is checked
directly, because a hook nobody passes is exactly the shape of defect
this file exists to catch and a wiring test alone cannot tell "the hook
fired" from "the default did the same thing". The run_genesis call-site
half of the consolidation is not in this task's ownership and is
reported as blocked.
"""

from __future__ import annotations

import random

import pytest

from world.core.events import WorldEvent
from world.life.clock import GameClock
from world.life.models import (
    AgentLifeRecord,
    DeathCause,
    DormancyState,
    LifeEvent,
    PopulationDials,
    Sex,
)
from world.life.tick import run_hourly_life_tick
from world.wiring import PACKAGE_DIR, WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

# TOOL-27. This module needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped, and red for environment hides real regressions inside the
# noise - which is how TOOL-09 stayed invisible for a whole cycle.
pytestmark = needs_the_map

SMALL_GENESIS = PACKAGE_DIR / "genesis_10.json"

# Just under the eighteenth birthday: at one year per game-hour and ten
# game-minutes per tick, a single tick carries this over the line.
ALMOST_EIGHTEEN = 17.9


async def boot():
    return await build_world_server(
        WorldConfig(genesis_json_path=SMALL_GENESIS, genesis_tick=10)
    )


def a_child_and_its_parent(server) -> tuple[str, str]:
    """Pick a household with at least two members and make the first one
    a minor billed to the second, at both layers the transition has to
    clear: the life record's sponsor and the ledger account's flags.
    """
    for household in server.world.households.values():
        members = [m for m in household.member_ids if m in server.world.agents]
        if len(members) >= 2:
            child_id, parent_id = members[0], members[1]
            break
    else:  # pragma: no cover - genesis always has a multi-member household
        pytest.skip("this genesis has no household with two members")

    server.world.agents[child_id] = server.world.agents[child_id].model_copy(
        update={"age_years": ALMOST_EIGHTEEN, "sponsor_parent_id": parent_id}
    )
    account = server.world.ledger.get_account(child_id)
    account.is_minor = True
    account.parent_id = parent_id
    assert server.world.ledger.resolve_usd_payer(child_id) == parent_id
    return child_id, parent_id


@pytest.mark.asyncio
async def test_a_minor_who_turns_eighteen_stops_being_billed_to_a_parent() -> None:
    """The wiring half: the hourly tick of the real root reacts to the
    birthday it has been firing and ignoring."""
    server = await boot()
    try:
        child_id, parent_id = a_child_and_its_parent(server)
        seen: list[WorldEvent] = []

        async def collect(event: WorldEvent) -> None:
            seen.append(event)

        server.event_bus.subscribe("*", collect)
        await server.advance_tick()

        grown = server.world.agents[child_id]
        assert grown.age_years == 18.0
        assert grown.is_minor is False
        assert any(e.type == "agent.aged_to_adult" for e in seen)
        assert server.world.ledger.resolve_usd_payer(child_id) == child_id, (
            "ECON-22: this root's tick fired agent.aged_to_adult into the "
            "void, so a new adult's thinking was still billed to a parent"
        )
        assert any(e.type == "agent.parent_support.ended" for e in seen), (
            "the transition has to be announced, not just applied"
        )
        assert grown.sponsor_parent_id is None
        assert parent_id in server.world.agents
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_the_new_adult_is_recorded_as_a_contributor_to_the_household() -> None:
    """Coming of age is a household fact as well as a wallet one - the
    half that proves the transition ran rather than only the flag being
    cleared by `aging.advance_age`, which clears the life record's
    sponsor all on its own."""
    server = await boot()
    try:
        child_id, _ = a_child_and_its_parent(server)
        household_id = server.world.agents[child_id].household_id
        seen_types: list[str] = []

        async def collect(event: WorldEvent) -> None:
            seen_types.append(event.type)

        server.event_bus.subscribe("*", collect)
        await server.advance_tick()

        assert "agent.household.contributor_added" in seen_types
        assert child_id in server.world.households[household_id].member_ids
    finally:
        await server.close()


def make_minor() -> AgentLifeRecord:
    return AgentLifeRecord(
        agent_id="a1", age_years=ALMOST_EIGHTEEN, sex=Sex.MALE, sponsor_parent_id="p1"
    )


@pytest.mark.asyncio
async def test_the_hourly_tick_with_no_hook_is_still_the_pure_original() -> None:
    """A caller that passes no hooks gets exactly the behaviour the sync
    original had. That is what makes the consolidation safe: the
    unhooked path is the one every pure unit test in life/tests drives."""
    grown, events = await run_hourly_life_tick(
        random.Random(0),
        make_minor(),
        now=GameClock(total_hours=5.0),
        elapsed_hours=1.0,
        dials=PopulationDials(),
    )
    assert grown.age_years == 18.0
    assert [e.type for e in events] == ["agent.aged_to_adult"]


@pytest.mark.asyncio
async def test_the_coming_of_age_hook_is_awaited_on_the_birthday_and_only_then() -> None:
    """The seam the runner's inline copy needed and this module did not
    have. Called once, on the tick the agent crosses eighteen, with the
    game-hour it happened at - and never for an agent who did not."""
    calls: list[tuple[str, float]] = []

    async def hook(agent: AgentLifeRecord, total_hours: float):
        calls.append((agent.agent_id, total_hours))
        return agent, [LifeEvent(type="agent.parent_support.ended", agent_id=agent.agent_id)]

    grown, events = await run_hourly_life_tick(
        random.Random(0),
        make_minor(),
        now=GameClock(total_hours=7.5),
        elapsed_hours=1.0,
        dials=PopulationDials(),
        on_coming_of_age=hook,
    )
    assert calls == [("a1", 7.5)]
    assert "agent.parent_support.ended" in [e.type for e in events]

    already_adult = grown.model_copy(update={"agent_id": "a2"})
    await run_hourly_life_tick(
        random.Random(0),
        already_adult,
        now=GameClock(total_hours=8.5),
        elapsed_hours=1.0,
        dials=PopulationDials(),
        on_coming_of_age=hook,
    )
    assert len(calls) == 1, "an adult has no birthday to transition on"


@pytest.mark.asyncio
async def test_the_death_hook_replaces_the_record_only_death() -> None:
    """The other half the runner's copy carried: a death that settles an
    estate and takes the dead off the scheduler, rather than only writing
    `alive=False`. Without this seam the second root's death was a
    strictly smaller event than the first's."""
    settled: list[DeathCause] = []

    async def hook(agent: AgentLifeRecord, cause: DeathCause):
        settled.append(cause)
        return (
            agent.model_copy(update={"alive": False, "death_cause": cause}),
            [LifeEvent(type="agent.estate.settled", agent_id=agent.agent_id)],
        )

    class AlwaysDies(random.Random):
        def random(self) -> float:
            return 0.0

    dormant = AgentLifeRecord(
        agent_id="a1",
        age_years=30.0,
        sex=Sex.MALE,
        dormancy=DormancyState(is_dormant=True, dormant_since_hours=0.0),
    )
    dead, events = await run_hourly_life_tick(
        AlwaysDies(),
        dormant,
        now=GameClock(total_hours=24.0),
        elapsed_hours=1.0,
        dials=PopulationDials(),
        on_death=hook,
    )
    assert settled == [DeathCause.USD_ZERO_UNREVIVED]
    assert dead.alive is False
    assert [e.type for e in events] == ["agent.estate.settled"]
