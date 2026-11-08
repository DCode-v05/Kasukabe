"""There is one way to open a conversation, and the perception says so.

WHY THIS EXISTS. Two builds of the same idea landed in this tree within a
day of each other, from two directions, and the merge left both standing:

  * `start_conversation` writing into `world.conversations` - a topic, a
    place, a roster, an open/close window, threaded by `say`, drawn on the
    township screen and shown by `look_around`;
  * `convene` writing into `world.group_sessions` - a topic, a place, a
    roster, a TTL, and a line in the perception text.

Two registries holding the same fact is worse than either alone. A
resident could convene a group and start a conversation in the same room
about the same thing and be in two records that do not know about each
other; `say` threads into one of them; the screen draws the other.

So: the registry survives, the second one goes, and the ONE thing the
retired half did better comes with it. That thing is where the news
appears. Upstream's finding from runs 18 and 19 was that
`start_conversation` was "registered, priced, served to all twenty engines
and never once called", because nothing in a resident's view referred to
it. The fix put conversations into `look_around` - which is itself a tool
call, so a resident who does not look still never learns. The perception
text is read every single turn, by everyone, without spending anything.

WHAT MUST STAY TRUE. Nothing here makes anybody convene. The perception
reports what is happening and names the verb; whether a resident uses it
is the experiment.
"""

from __future__ import annotations


from world.tools.state import CONVENE_HINT_MIN_PRESENT, WorldState


# ── One mechanism, not two ───────────────────────────────────────────


def test_the_duplicate_registry_is_gone() -> None:
    """`group_sessions` was the second store of the same fact. If it comes
    back, two records of one conversation come back with it."""
    assert not hasattr(WorldState, "convene_group")
    assert not hasattr(WorldState, "open_group_here")
    assert not hasattr(WorldState, "active_group_for")


def test_convene_is_no_longer_a_tool() -> None:
    """Two tools that open a conversation is a fork in the road with no
    signpost. A resident picking the wrong one lands in the registry the
    screen does not draw and `say` does not thread."""
    from world.tools.primitives import social

    assert not hasattr(social, "convene_handler")
    assert "convene" not in {spec.name for spec in social.build_specs()}


def test_convening_writes_into_the_one_conversation_store() -> None:
    """`convene` is BACK, as Part D of the tool spec asks for, and this
    test is what makes that safe rather than a regression.

    It was removed once, and the reason was right: there were two tools
    that opened a conversation and they wrote to DIFFERENT stores, so a
    resident picking the wrong one landed in a registry the screen does
    not draw and `say` does not thread. That defect was the duplicate
    store, not the second verb - `group_sessions` is gone and is not
    coming back (see the test above).

    The new `convene` calls `WorldState.open_conversation`, the same
    method `start_conversation` calls, so there is one record of one
    conversation. What it adds is the thing Part D needs and
    `start_conversation` cannot express: a seat calling the sitting seats
    to the town hall, which is a different act from calling whoever
    happens to be standing near you. Its description names
    `start_conversation` as the sibling for that case, per the addendum's
    rule 1.

    A government surface that could not convene is a government that can
    only govern by standing next to people.
    """
    import inspect

    from world.ledger.action_costs import SCHEDULE_TABLE
    from world.tools.primitives import institutions

    assert "start_conversation" in SCHEDULE_TABLE
    # Priced identically, because it spends the same thing: everyone's
    # attention, not only the convener's.
    assert SCHEDULE_TABLE["convene"].base_cost == SCHEDULE_TABLE["start_conversation"].base_cost

    source = inspect.getsource(institutions.convene_handler)
    assert "world.open_conversation" in source, (
        "convene is writing somewhere other than the one conversation store"
    )
    assert "group_session" not in source


# ── The news reaches the perception, not only look_around ────────────


def world_with_conversation(**over) -> WorldState:
    world = WorldState.__new__(WorldState)
    world.conversations = {}
    world.agent_names = {"n01": "Marta Thorne", "n02": "Bram Colley", "n03": "Wren Ash"}
    world.current_tick = 10
    world.utterances = []
    for key, value in over.items():
        setattr(world, key, value)
    return world


def test_the_conversation_you_are_in_is_rendered_for_the_perception() -> None:
    from world.retrieval_depth import format_conversation

    text = format_conversation(
        {
            "conversation_id": "c1",
            "topic": "the boundary marker",
            "participants": ["Marta Thorne", "Bram Colley"],
            "turns": 3,
        }
    )
    assert "the boundary marker" in text
    assert "Marta Thorne" in text and "Bram Colley" in text
    assert "c1" in text, "without the id a resident cannot answer into it"


def test_no_conversation_renders_to_nothing_at_all() -> None:
    """A heading over an empty list is a paragraph every resident pays for
    every tick to learn nothing."""
    from world.retrieval_depth import format_conversation

    assert format_conversation(None) == ""


# ── The hint names the verb that still exists ────────────────────────


def test_the_hint_names_start_conversation() -> None:
    """It used to name `convene`, which no longer exists. A hint naming a
    verb the registry will refuse is worse than no hint: the resident
    spends a turn finding out."""
    world = world_with_conversation()
    world.agents = {"n01": None, "n02": None, "n03": None}

    class Locations:
        def location_of(self, agent_id):
            return "market"

    world.locations = Locations()
    world.utterances = [
        {"tick": 9, "speaker_agent_id": "n02", "location_id": "market"}
    ]
    hint = WorldState.convening_hint(world, "n01")
    assert hint is not None
    assert "start_conversation" in hint
    assert "convene`" not in hint


def test_the_hint_is_silent_once_a_conversation_is_open_here() -> None:
    """Nagging somebody to open one they are already in is how a hint
    stops being information."""
    world = world_with_conversation()
    world.agents = {"n01": None, "n02": None, "n03": None}

    class Locations:
        def location_of(self, agent_id):
            return "market"

    world.locations = Locations()
    world.utterances = [
        {"tick": 9, "speaker_agent_id": "n02", "location_id": "market"}
    ]
    WorldState.open_conversation(
        world, place="market", topic="the boundary", participants=["n01", "n02"], now=10
    )
    assert WorldState.convening_hint(world, "n01") is None


def test_the_hint_is_silent_in_an_empty_room() -> None:
    world = world_with_conversation()
    world.agents = {"n01": None}

    class Locations:
        def location_of(self, agent_id):
            return "market"

    world.locations = Locations()
    assert WorldState.convening_hint(world, "n01") is None


def test_the_hint_needs_people_actually_talking() -> None:
    """People standing together in silence are not a discussion waiting to
    happen, and a hint that fires on a crowd fires on the market at noon
    every day of the run."""
    world = world_with_conversation()
    world.agents = {f"n0{i}": None for i in range(1, CONVENE_HINT_MIN_PRESENT + 1)}

    class Locations:
        def location_of(self, agent_id):
            return "market"

    world.locations = Locations()
    world.utterances = []
    assert WorldState.convening_hint(world, "n01") is None
