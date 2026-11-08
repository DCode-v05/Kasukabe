"""A business's people are briefed to that business.

`GROW-10`. The genesis construction company (`b_yard`, owner `n01`, hands
`n03` and `n09`, 6000 sim) is seeded as a `Business` at
`business:b_yard`, and that was all it was. `WORKPLACE_BY_OCCUPATION` is
keyed on TRADE, so it sent every `construction_worker` to the abstract
`"carpentry"` while the company itself stood at `business:b_yard` - an id
no static table can name, because it comes from the genesis file. The
hands were briefed to a place their employer was not.

Run 23: Silas Vance alone at `business:b_yard` saying "the carpenter
needs timber", with the two people employed to bring it briefed somewhere
else.

The premises are strictly MORE than the trade string, which is why they
win. TOOL-02 already gave the yard `provides_workshops={"carpentry",
"sawmill"}`, so a resident standing there satisfies the carpentry gate
AND is with the people they work with. Briefing them to "carpentry"
bought the first and threw away the second.

ORDERING IS THE WHOLE FIX. The first version of this put the override in
`boot()`'s trade loop, which runs BEFORE `seed_genesis()` creates any
business - so `world_state.businesses` was empty, the override answered
None every time, and the change was inert on every run while looking
completely correct. That is the defect this review finds more often than
any other, and it was very nearly the fix for it. Hence
`test_the_briefing_happens_after_the_businesses_exist` below, which
drives a real boot rather than reading the source.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.run_genesis import (
    WORKPLACE_BY_OCCUPATION,
    GenesisRunner,
    GenesisRunnerConfig,
)


@pytest_asyncio.fixture
async def runner(tmp_path):
    built = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await built.boot()
    return built


def _a_business_with_staff(world):
    from world.tools.models import Business

    for business in world.businesses.values():
        if not isinstance(business, Business) or not business.location_id:
            continue
        if business.employee_agent_ids:
            return business
    return None


@pytest.mark.asyncio
async def test_the_briefing_happens_after_the_businesses_exist(runner) -> None:
    """The finding, and the ordering trap. A booted runner must already
    have its staff briefed - if the override ran too early it would have
    seen no businesses and silently done nothing."""
    business = _a_business_with_staff(runner.world_state)
    assert business is not None, "this genesis seeds no business with staff"

    for agent_id in business.employee_agent_ids:
        assert runner.world_state.workplaces.get(agent_id) == business.location_id, (
            f"GROW-10: {agent_id} works for {business.business_id} and is "
            f"briefed to {runner.world_state.workplaces.get(agent_id)!r} "
            f"instead of {business.location_id!r} - the hands are sent "
            "somewhere their employer is not"
        )


@pytest.mark.asyncio
async def test_the_owner_is_briefed_there_too(runner) -> None:
    """Silas was already at the yard; the point is that everyone is."""
    business = _a_business_with_staff(runner.world_state)
    assert runner.world_state.workplaces.get(business.owner_agent_id) == (
        business.location_id
    )


@pytest.mark.asyncio
async def test_a_resident_with_no_employer_keeps_their_trade(runner) -> None:
    """The trade table is not deleted. A miner with no employer still has
    an ore outcrop to walk to, and that is the whole reason it exists."""
    from world.tools.models import Business

    staffed = set()
    for business in runner.world_state.businesses.values():
        if not isinstance(business, Business):
            continue
        staffed |= set(business.employee_agent_ids or set())
        if business.owner_agent_id:
            staffed.add(business.owner_agent_id)

    checked = 0
    for agent_id, occupation in runner.world_state.occupations.items():
        if agent_id in staffed:
            continue
        expected = WORKPLACE_BY_OCCUPATION.get(occupation)
        if expected is None:
            continue
        assert runner.world_state.workplaces.get(agent_id) == expected, (
            f"GROW-10: {agent_id} staffs no business but its trade "
            f"workplace was overwritten"
        )
        checked += 1
    assert checked, "no unemployed-by-a-business resident to check"


@pytest.mark.asyncio
async def test_the_briefed_address_is_somewhere_they_can_stand(runner) -> None:
    """A briefing to a place `move` cannot route to is the defect one
    layer along - run 19 left the grocery clerk at `business:b_grocery`,
    a room no customer could walk into."""
    from world.tools.place_aliases import is_real_destination

    business = _a_business_with_staff(runner.world_state)
    assert is_real_destination(runner.world_state, business.location_id), (
        f"GROW-10: staff are briefed to {business.location_id!r}, which is "
        "not a place anybody can walk to"
    )


@pytest.mark.asyncio
async def test_the_yards_premises_still_satisfy_the_carpentry_gate(runner) -> None:
    """The reason the premises may replace the trade string at all. If
    this fails, briefing them to the yard costs them their craft verbs
    and the trade string was the better answer after all."""
    from world.tools.place_aliases import satisfies_gate_in
    from world.tools.models import Business

    yard = None
    for business in runner.world_state.businesses.values():
        if isinstance(business, Business) and business.business_type == (
            "construction_company"
        ):
            yard = business
            break
    assert yard is not None, "this genesis seeds no construction company"

    assert satisfies_gate_in(runner.world_state, yard.location_id, "carpentry"), (
        "GROW-10: standing at the yard no longer satisfies the carpentry "
        "gate, so briefing the hands there takes their craft verbs away"
    )


@pytest.mark.asyncio
async def test_re_briefing_is_idempotent(runner) -> None:
    """It runs once at boot today. If a caller ever runs it twice - on a
    resume, say - the second must be a no-op rather than a second count."""
    assert runner.brief_staff_to_their_workplace() == 0, (
        "GROW-10: a second briefing pass moved somebody, so the count a "
        "run reports is not the number of people who were re-briefed"
    )
