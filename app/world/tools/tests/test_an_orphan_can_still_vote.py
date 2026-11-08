"""Losing your family does not lose you the vote.

`GOV-25`. `eligibility_facts_for` set `is_resident = household_id is not
None`, on the reasoning that DESIGN section 4 gives every genesis agent a
household and nothing else in the system distinguishes a resident from a
visitor.

The premise is true and the conclusion does not follow. A resident can
LOSE a household: a newborn does not have one yet, and a household
dissolves when its last other member dies. Each of them stopped being a
citizen - and silently, which is the part that matters. `check_suffrage`
refuses with "not a resident", so an orphan who tried to vote was told
by its own town that it did not live there.

Being on the roster is the honest proxy, and it rests on exactly the
reasoning the old rule used: nothing here distinguishes residents from
visitors, and everyone in `world.agents` was either seeded as a resident
or born to one. If Kasukabe ever admits visitors, THIS is the line that
learns the difference - not the household table.
"""
from __future__ import annotations

import pytest

from world.tools.primitives.civic import eligibility_facts_for

from .test_construction import spawn_agent  # noqa: F401


@pytest.mark.asyncio
async def test_a_resident_with_a_household_is_a_resident(world) -> None:
    """The control. The old rule was right about the ordinary case."""
    await spawn_agent(world, "villager", "town_hall", household_id="hh-1")
    facts = eligibility_facts_for(world, world.agents["villager"])
    assert facts.is_resident is True


@pytest.mark.asyncio
async def test_an_orphan_is_still_a_resident(world) -> None:
    """The finding. A household dissolves when its last other member
    dies, and the survivor lost the vote for it."""
    await spawn_agent(world, "orphan", "town_hall")
    world.agents["orphan"].household_id = None

    facts = eligibility_facts_for(world, world.agents["orphan"])
    assert facts.is_resident is True, (
        "GOV-25: a resident whose household dissolved is no longer a "
        "citizen of the town it has lived in all its life"
    )


@pytest.mark.asyncio
async def test_a_newborn_is_a_resident_before_it_has_a_household(world) -> None:
    """DESIGN section 5 pairs birth with death and the newborn is a real
    resident from that day. It should not have to wait for a household
    row to become one."""
    await spawn_agent(world, "newborn", "town_hall")
    world.agents["newborn"].household_id = None
    world.agents["newborn"].age_years = 0.0

    facts = eligibility_facts_for(world, world.agents["newborn"])
    assert facts.is_resident is True
    # Age still gates the vote - residency and suffrage are different
    # questions, and this fix must not blur them.
    assert facts.age_years == 0.0


@pytest.mark.asyncio
async def test_somebody_not_on_the_roster_is_not_a_resident(world) -> None:
    """The other control. If residency were simply True, the field would
    stop carrying information and `check_suffrage` could never refuse."""
    from world.life.models import AgentLifeRecord, MaritalStatus, Sex

    stranger = AgentLifeRecord(
        agent_id="passing-trader",
        age_years=40.0,
        sex=Sex.MALE,
        marital_status=MaritalStatus.SINGLE,
        household_id="hh-elsewhere",
    )
    facts = eligibility_facts_for(world, stranger)
    assert facts.is_resident is False, (
        "GOV-25: anybody at all now counts as a resident, so the town has "
        "no way to refuse a visitor the vote"
    )


@pytest.mark.asyncio
async def test_the_rule_no_longer_reads_the_household(world) -> None:
    """The regression guard. A future edit that reaches back for
    `household_id` reintroduces exactly this defect."""
    import inspect

    from world.tools.primitives import civic

    source = inspect.getsource(civic.eligibility_facts_for)
    assert "is_resident=agent.agent_id in world.agents" in source
    assert "is_resident=agent.household_id" not in source, (
        "GOV-25: residency is derived from the household again, so an "
        "orphan silently loses the vote"
    )
