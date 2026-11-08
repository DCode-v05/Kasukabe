"""Who a remark is addressed to, read out of the words.

Agents address each other by name and leave `listener_agent_id` unset. In a
live run, 8 of 15 remarks opened by naming somebody and exactly 1 set the
parameter - so the intent was in the text and the world threw it away. Every
remark became a broadcast, nothing was ever a reply, and the Talk book had
nothing to thread.

The resolver is deliberately conservative. A wrong addressee is worse than
none: it puts words in one resident's ear that were meant for another.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from world.life.models import AgentLifeRecord, Sex
from world.tools.bootstrap import build_world
from world.tools.primitives.social import addressee_from_content

HERE = "Hobbs Cafe"
AWAY = "Kasukabe Farmland"


@pytest_asyncio.fixture
async def town():
    world, _ = await build_world()
    where = {"me": HERE, "a0087": HERE, "a0016": HERE, "a0121": AWAY}
    for agent_id, name in [
        ("me", "Speaker Self"), ("a0087", "Greta Quarshie"),
        ("a0016", "Ezra Jarrah"), ("a0121", "Arjun Holloway"),
    ]:
        world.agents[agent_id] = AgentLifeRecord(
            agent_id=agent_id, age_years=30, sex=Sex.FEMALE)
        world.agent_names[agent_id] = name
    world.location_of = where.get  # type: ignore[method-assign]
    return world


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("Greta, can I help with breakfast?", "a0087"),
        ("Ezra—good morning. Are you seeking work?", "a0016"),
        ("Morning to you, Greta.", "a0087"),
        ("Greta Quarshie, a word.", "a0087"),
        ("Greta! Where's the flour?", "a0087"),
    ],
)
async def test_a_named_neighbour_is_the_addressee(town, content, expected):
    assert addressee_from_content(town, "me", HERE, content) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "I saw Greta yesterday at the mill.",
        "Greta's wheat was the best this year.",
        "Anyone selling flour?",
        "",
    ],
)
async def test_a_mention_in_passing_addresses_nobody(town, content):
    assert addressee_from_content(town, "me", HERE, content) is None


@pytest.mark.asyncio
async def test_someone_who_is_not_here_cannot_be_addressed(town):
    """Speech is face-to-face. Naming somebody across town resolves to
    nobody rather than reaching them."""
    assert addressee_from_content(town, "me", HERE, "Arjun, got any timber?") is None


@pytest.mark.asyncio
async def test_the_first_person_named_is_the_one_addressed(town):
    """"Greta, have you seen Ezra?" is addressed to Greta and asks about
    Ezra. Treating both as candidates and giving up made every remark like
    this public - and that is most of how people actually talk."""
    assert addressee_from_content(
        town, "me", HERE, "Greta, have you seen Ezra?") == "a0087"


@pytest.mark.asyncio
async def test_two_names_is_ambiguous_and_stays_public(town):
    """Addressed to both, so addressed to the room - never a coin flip
    between two residents."""
    assert addressee_from_content(
        town, "me", HERE, "Greta and Ezra, both of you listen.") is None


@pytest.mark.asyncio
async def test_the_speaker_never_addresses_themselves(town):
    assert addressee_from_content(
        town, "a0087", HERE, "Greta, pull yourself together.") is None


@pytest.mark.asyncio
async def test_no_location_resolves_to_nobody(town):
    assert addressee_from_content(town, "me", None, "Greta, hello.") is None


@pytest.mark.asyncio
async def test_a_name_inside_a_longer_word_is_not_a_match(town):
    """"Gretachen" is not Greta, and neither is "ungrateful"."""
    assert addressee_from_content(town, "me", HERE, "Gretachen, hello.") is None


@pytest.mark.asyncio
async def test_a_remark_addressed_to_oneself_is_public(town):
    """Observed live: 1 remark in 26 named the speaker as the listener.
    The words were really said and the room really heard them, so the
    remark stands - it is simply not a conversation with oneself."""
    from world.life.models import AgentLifeRecord, Sex
    from world.tools.primitives.social import SayParams, say_handler

    speaker = AgentLifeRecord(agent_id="a0087", age_years=30, sex=Sex.FEMALE)
    town.agents["a0087"] = speaker

    outcome = await say_handler(
        town, speaker,
        SayParams(content="Right. Iron ore to move.", listener_agent_id="a0087"),
    )
    payload = outcome.events[0].payload
    assert payload["listener_agent_id"] is None
    assert payload["is_public"] is True


@pytest.mark.asyncio
async def test_say_reports_who_actually_heard_it(town):
    from world.life.models import AgentLifeRecord, Sex
    from world.tools.primitives.social import SayParams, say_handler

    speaker = AgentLifeRecord(agent_id="me", age_years=30, sex=Sex.FEMALE)
    town.agents["me"] = speaker

    out = await say_handler(town, speaker, SayParams(content="Morning, all."))
    assert set(out.result["heard_by"]) == {"Greta Quarshie", "Ezra Jarrah"}
    assert "not_here" not in out.result


@pytest.mark.asyncio
async def test_calling_to_an_absent_neighbour_says_they_did_not_hear(town):
    from world.life.models import AgentLifeRecord, Sex
    from world.tools.primitives.social import SayParams, say_handler

    speaker = AgentLifeRecord(agent_id="me", age_years=30, sex=Sex.FEMALE)
    town.agents["me"] = speaker

    out = await say_handler(
        town, speaker, SayParams(content="Arjun! You walking or what?"))
    assert out.result["not_here"] == ["Arjun Holloway"]
    assert "did not hear that" in out.result["note"]


@pytest.mark.asyncio
async def test_merely_mentioning_someone_absent_is_not_a_failed_address(town):
    """"Has Arjun got wheat?" is a question about him, not a shout at him.
    Reporting it as a failed address would train agents out of talking about
    anybody who is not in the room."""
    from world.life.models import AgentLifeRecord, Sex
    from world.tools.primitives.social import SayParams, say_handler

    speaker = AgentLifeRecord(agent_id="me", age_years=30, sex=Sex.FEMALE)
    town.agents["me"] = speaker

    out = await say_handler(
        town, speaker, SayParams(content="Does anyone know if Arjun has wheat?"))
    assert "not_here" not in out.result


@pytest.mark.asyncio
async def test_speaking_to_an_empty_room_says_so(town):
    from world.life.models import AgentLifeRecord, Sex
    from world.tools.primitives.social import SayParams, say_handler

    speaker = AgentLifeRecord(agent_id="alone", age_years=30, sex=Sex.FEMALE)
    town.agents["alone"] = speaker
    town.agent_names["alone"] = "Solitary Sam"
    original = town.location_of
    town.location_of = lambda a: "Empty Field" if a == "alone" else original(a)

    out = await say_handler(town, speaker, SayParams(content="Anyone about?"))
    assert out.result["heard_by"] == []
    assert "Nobody was here to hear that" in out.result["note"]
