"""`say` must record everybody the remark actually spoke to.

The world-side half of world/tests/test_being_spoken_to_reaches_you.py,
which covers the perception. This one covers the event `say` emits, and it
lives here because it needs the tools package's live world and registry.

`involved_agent_ids` was built from `[speaker, listener]` BEFORE
`addressed_agent_ids` was worked out, and never revisited - so a resident
named by a vocative rather than by the parameter was not "involved", and
would stop receiving the remark the moment they walked out of the room.
"""

from __future__ import annotations

import pytest

from .conftest import spawn_agent

REMARK = (
    "Anselm. You want temple bread, walk to temple. "
    "Tova. Two sim for walking, you said?"
)


@pytest.mark.asyncio
async def test_say_puts_every_addressee_in_involved(world, registry) -> None:
    """The other half of the fix, and it must be checked against what `say`
    actually builds rather than against this file's own fixture.

    `involves_self` is what carries an event to somebody who has walked out
    of the room, and a selected next speaker who steps outside must not
    stop being selected. `say` computed `involved_agent_ids` BEFORE it
    worked out who had been addressed, and never went back.
    """
    for agent_id, name in (("n05", "Ezra Okonjo"), ("n07", "Tova Okonjo"),
                           ("n09", "Father Anselm")):
        await spawn_agent(world, agent_id, "Kasukabe Temple")
        world.agent_names[agent_id] = name

    outcome = await registry.dispatch(world, "n05", "say", {
        "listener_agent_id": "n09", "content": REMARK,
    })
    spoken = next(e for e in outcome.events if e.type == "agent.utterance.spoken")
    assert "n09" in spoken.payload["addressed_agent_ids"]
    assert "n07" in spoken.payload["addressed_agent_ids"], (
        "Tova was named by a vocative and the world did not record it"
    )
    assert "n07" in spoken.payload["involved_agent_ids"], (
        "Tova is addressed but would stop hearing the remark the moment "
        "she left the room"
    )


def test_a_name_the_world_does_not_know_is_not_an_address(world, registry) -> None:
    """The honest limit of a name matcher, stated so nobody later reads
    silence as success.

    Ezra's real run-19 remark addressed Tova as "Girl." A vocative that
    uses a description, a role or an endearment rather than a name -
    "Girl", "lad", "love", "brother" - selects a next speaker just as
    surely in human conversation (Sacks/Schegloff/Jefferson: the vocative
    is one form of the address term, not the only one) and this world
    cannot see it.

    Recorded as a KNOWN GAP, not a bug to paper over: guessing which
    resident "Girl" meant would put words in a speaker's mouth, and a
    wrong guess creates an obligation on somebody who was never spoken to.
    """
    from world.tools.primitives.social import everyone_addressed

    class Town:
        agent_names = {"n05": "Ezra Okonjo", "n07": "Tova Okonjo"}
        agents = {"n05": None, "n07": None}

        def location_of(self, agent_id):
            return "temple"

    assert everyone_addressed(Town(), "n05", "temple", "Girl. Two sim?") == []
    assert everyone_addressed(Town(), "n05", "temple", "Tova. Two sim?") == ["n07"]
