"""A seat-holder who mistypes is pointed at the verbs it already holds.

Calling a government verb without a seat answers `no tool registered under
name 'set_tax_rate'`, and that is correct: DESIGN section 2 says "non-members
cannot see a confidential tool exists", so a hidden verb must answer exactly
as a nonsense name does. Nothing here changes that, and a test below holds
the line.

What was wrong is the other side of it. A resident who DOES hold the seat,
and types `set_tax_rates`, gets the same bare sentence and no way to tell a
typo from a verb it is not allowed to call. It already has access; naming
the near miss leaks nothing it could not already list, and it is the
difference between a resident correcting itself on the next turn and a
resident concluding the verb does not exist - which is what the proof
harness recorded as indistinguishable.

So: the suggestion is drawn ONLY from the tools this actor can already see.
"""

from __future__ import annotations

import pytest

from world.charter.models import GovernmentState
from world.tools.errors import ToolNotFoundError
from world.tools.tests.conftest import spawn_agent

TOWN_HALL = "town_hall"


async def _resident(world, agent_id: str = "a_seat") -> str:
    await spawn_agent(world, agent_id, TOWN_HALL)
    return agent_id


async def _seat(world, agent_id: str) -> None:
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id=agent_id, minister_agent_ids=[], terms_served={}, caretaker=True)
    )


@pytest.mark.asyncio
async def test_a_seat_holder_who_mistypes_is_told_what_it_can_call(world, registry) -> None:
    who = await _resident(world)
    await _seat(world, who)

    with pytest.raises(ToolNotFoundError) as caught:
        await registry.execute(world, who, "set_tax_rates", {"tax_rate_bps": 500})

    said = str(caught.value)
    assert "set_tax_rate" in said, said
    assert "did you mean" in said.lower(), said


@pytest.mark.asyncio
async def test_an_unseated_resident_is_never_pointed_at_the_hidden_verb(world, registry) -> None:
    """The confidentiality rule, stated exactly.

    The rule is not "say less to outsiders" - it is that nothing in the
    answer may confirm a hidden verb exists. A suggestion drawn from the
    public verbs does not: `set_will` is something this resident could
    have listed for itself. What must never appear is the hidden name.
    """
    who = await _resident(world, "a_nobody")

    with pytest.raises(ToolNotFoundError) as caught:
        await registry.execute(world, who, "set_tax_rate", {"tax_rate_bps": 500})

    suggestion = caught.value.near
    assert suggestion != "set_tax_rate", "that would confirm the verb exists"
    if suggestion is not None:
        spec = registry.tools[suggestion]
        assert registry.visible_to(spec, world, who), (
            f"{suggestion!r} was suggested to somebody who cannot see it"
        )


@pytest.mark.asyncio
async def test_a_nonsense_name_suggests_nothing_it_cannot_reach(world, registry) -> None:
    who = await _resident(world, "a_stranger")

    with pytest.raises(ToolNotFoundError) as caught:
        await registry.execute(world, who, "summon_dragon", {})

    said = str(caught.value).lower()
    assert "set_tax_rate" not in said and "government" not in said


@pytest.mark.asyncio
async def test_an_ordinary_typo_still_points_at_public_verbs(world, registry) -> None:
    """The same help for everybody, on the verbs everybody can see."""
    who = await _resident(world, "a_walker")

    with pytest.raises(ToolNotFoundError) as caught:
        await registry.execute(world, who, "look_arond", {})

    assert "look_around" in str(caught.value)
