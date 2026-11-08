"""A reply belongs to the conversation it is replying to.

`say` minted a fresh uuid4 for every remark. A live run produced 14
utterances and 14 conversation ids - despite six of those remarks being
direct, by-name answers to the person who had just spoken. The town was
holding conversations and the record showed fourteen monologues, so the
Talk panel had nothing to group and every reply-rate measure read zero.

These tests exercise _continuing_conversation directly against the same
shape world.utterances carries (tools/bootstrap.py's on_utterance), so
they pin the matching rules without needing a booted world.
"""

from world.tools.primitives.social import (
    CONVERSATION_WINDOW_TICKS,
    _continuing_conversation,
)


class _World:
    """Only what the helper reads: an utterance tail and a tick."""

    def __init__(self, utterances: list[dict]) -> None:
        self.utterances = utterances


def _said(tick: int, speaker: str, listener: str | None, conv: str,
          place: str = "Kasukabe Farmland", public: bool = False) -> dict:
    return {
        "tick": tick,
        "speaker_agent_id": speaker,
        "listener_agent_id": listener,
        "location_id": place,
        "conversation_id": conv,
        "is_public": public,
    }


def test_an_answer_joins_the_conversation_it_answers() -> None:
    """The exact live case: Tobias spoke to Wren at tick 39, Wren answered
    him at tick 43. Two remarks, one conversation."""
    world = _World([_said(39, "n03", "n06", "conv-1")])
    assert _continuing_conversation(world, "n06", "n03", "Kasukabe Farmland", 43) == "conv-1"


def test_the_speaker_may_also_continue_their_own_thread() -> None:
    world = _World([_said(39, "n03", "n06", "conv-1")])
    assert _continuing_conversation(world, "n03", "n06", "Kasukabe Farmland", 41) == "conv-1"


def test_a_first_remark_starts_a_new_conversation() -> None:
    assert _continuing_conversation(_World([]), "n03", "n06", "Kasukabe Farmland", 10) is None


def test_a_world_with_no_utterance_tail_is_a_first_remark_not_a_crash() -> None:
    """Absence of history genuinely means nobody has spoken yet."""

    class Bare:
        pass

    assert _continuing_conversation(Bare(), "n03", "n06", "here", 10) is None


def test_a_stale_exchange_is_a_new_conversation() -> None:
    world = _World([_said(1, "n03", "n06", "conv-1")])
    stale = 1 + CONVERSATION_WINDOW_TICKS + 1
    assert _continuing_conversation(world, "n06", "n03", "Kasukabe Farmland", stale) is None


def test_the_window_boundary_itself_still_joins() -> None:
    world = _World([_said(1, "n03", "n06", "conv-1")])
    edge = 1 + CONVERSATION_WINDOW_TICKS
    assert _continuing_conversation(world, "n06", "n03", "Kasukabe Farmland", edge) == "conv-1"


def test_a_different_pair_in_the_same_room_joins_the_same_conversation() -> None:
    """REVERSED, deliberately, when group chat arrived. This asserted the
    opposite while conversation was modelled as a private line between two
    people. A room where four residents are talking is one conversation,
    not six pairwise ones - see the four-way street moment below."""
    world = _World([_said(39, "n03", "n06", "conv-1")])
    assert _continuing_conversation(world, "n04", "n09", "Kasukabe Farmland", 40) == "conv-1"


def test_a_shared_participant_keeps_the_room_s_conversation() -> None:
    """REVERSED with group chat. Wren turning from Tobias to Marta, all
    three standing in the same field, is one conversation turning - which
    is what it looks like to anybody in the room."""
    world = _World([_said(39, "n03", "n06", "conv-1")])
    assert _continuing_conversation(world, "n06", "n09", "Kasukabe Farmland", 40) == "conv-1"


def test_a_conversation_does_not_resume_across_town() -> None:
    world = _World([_said(39, "n03", "n06", "conv-1", place="Kasukabe Farmland")])
    assert _continuing_conversation(world, "n06", "n03", "Kasukabe Quarry", 41) is None


def test_public_speech_is_now_a_thread_to_join() -> None:
    """REVERSED with group chat, and the most consequential of the three.
    Speaking to a room used to mint a thread nobody could ever answer
    into - 30 such orphans in a single run."""
    world = _World([_said(39, "n03", None, "conv-1", public=True)])
    assert _continuing_conversation(world, "n06", "n03", "Kasukabe Farmland", 41) == "conv-1"


def test_the_most_recent_exchange_wins() -> None:
    world = _World([
        _said(30, "n03", "n06", "conv-old"),
        _said(39, "n06", "n03", "conv-new"),
    ])
    assert _continuing_conversation(world, "n03", "n06", "Kasukabe Farmland", 41) == "conv-new"


def test_a_place_spelled_differently_is_still_the_same_place() -> None:
    """satisfies_gate, not string equality - the same reason speech to a
    co-located neighbour could be refused outright."""
    world = _World([_said(39, "n03", "n06", "conv-1", place="farmland:wheat")])
    got = _continuing_conversation(world, "n06", "n03", "farmland:wheat", 41)
    assert got == "conv-1"


def test_an_absent_named_listener_no_longer_throws_the_turn_away() -> None:
    """`say` raised LocationGateError when the model set
    listener_agent_id to somebody standing elsewhere. The model had
    already composed the remark and the room it stood in really did hear
    it, but what came back was a tool failure carrying no usable
    correction - and the by-name path handled the identical situation the
    other way, with `not_here` and "walk to them, or ask someone here".
    The same mistake got a teachable answer or a hard error depending only
    on whether the model set a parameter or wrote a name in the words.

    Observed live: three of Tobias's four remarks in one morning died this
    way, including one to Wren while both stood in the same field under
    two spellings of its name.
    """
    import ast
    import inspect

    from world.tools.primitives.social import say_handler

    tree = ast.parse(inspect.getsource(say_handler).lstrip())
    raises = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and "LocationGateError" in ast.unparse(node)
    ]
    assert not raises, (
        "say must not refuse a remark outright for a listener who walked "
        "off - it reports who missed it instead"
    )


def test_the_absent_listener_is_still_reported_to_the_speaker() -> None:
    """Softening the refusal must not make it silent: the agent has to
    learn that the person it addressed was not there."""
    import ast
    import inspect

    from world.tools.primitives.social import say_handler

    source = inspect.getsource(say_handler)
    tree = ast.parse(source.lstrip())
    code = "\n".join(
        ast.unparse(n)
        for n in tree.body[0].body
        if not (
            isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        )
    )
    assert "spoke_to_absent" in code
    assert "not_here" in code


def test_a_third_person_joins_the_conversation_already_happening() -> None:
    """The whole point. Two people talking in a room, a third speaks:
    same thread, not a new one."""
    world = _World([_said(40, "n02", "n04", "conv-street", place="street")])
    got = _continuing_conversation(world, "n03", "n10", "street", 41)
    assert got == "conv-street"


def test_a_remark_to_the_room_joins_the_room_s_conversation() -> None:
    """A broadcast used to be skipped outright, so 30 of them in one run
    each became a thread of one line."""
    world = _World([_said(40, "n02", None, "conv-street", place="street", public=True)])
    got = _continuing_conversation(world, "n03", "n10", "street", 41)
    assert got == "conv-street"


def test_a_directed_remark_joins_a_room_that_was_broadcasting() -> None:
    world = _World([_said(40, "n02", None, "conv-street", place="street", public=True)])
    assert _continuing_conversation(world, "n04", None, "street", 42) == "conv-street"


def test_a_different_room_is_a_different_conversation() -> None:
    """Group threading must not merge the whole town into one log."""
    world = _World([_said(40, "n02", "n04", "conv-street", place="street")])
    assert _continuing_conversation(world, "n03", "n10", "Hobbs Cafe", 41) is None


def test_a_room_that_fell_quiet_starts_a_new_conversation() -> None:
    world = _World([_said(1, "n02", "n04", "conv-old", place="street")])
    stale = 1 + CONVERSATION_WINDOW_TICKS + 1
    assert _continuing_conversation(world, "n03", "n10", "street", stale) is None


def test_the_most_recent_conversation_in_the_room_wins() -> None:
    world = _World([
        _said(30, "n02", "n04", "conv-old", place="street"),
        _said(39, "n07", "n08", "conv-new", place="street"),
    ])
    assert _continuing_conversation(world, "n03", None, "street", 41) == "conv-new"


def test_a_room_spelled_differently_is_still_the_same_room() -> None:
    world = _World([_said(39, "n02", "n04", "conv-1", place="farmland:wheat")])
    assert _continuing_conversation(world, "n06", None, "farmland:wheat", 41) == "conv-1"


def test_the_four_way_street_moment_is_one_thread() -> None:
    """The exact remarks above, in order, must all land in one thread."""
    history: list[dict] = []
    world = _World(history)
    threads = []
    for tick, speaker, listener in (
        (247, "n10", "n04"),
        (248, "n07", "n03"),
        (248, "n03", "n10"),
        (248, "n04", "n10"),
    ):
        cid = _continuing_conversation(world, speaker, listener, "street", tick) or f"new-{tick}-{speaker}"
        threads.append(cid)
        history.append(_said(tick, speaker, listener, cid, place="street"))

    assert len(set(threads)) == 1, f"four remarks in one street became {len(set(threads))} threads"
