"""A question stays open until it is answered or it expires.

Build Spec v2 objective 3.1, threshold 9.1 (reply rate at or above 60%),
and the error-handling clause in section 6: "an unanswered question stays
open for a bounded window and then expires, visibly."

WHY THE REPLY RATE SITS AT 50%. Delivery is solved - the verification run
showed 20 directed remarks reaching their listener with no self-echo. What
is missing is persistence. A remark reaches a listener through
`perception.heard_events`, which spans `hearing_window_ticks`: four ticks
at the default retrieval depth. The scheduler wakes each agent every three.
So a resident gets roughly ONE turn in which the question is visible, and
if that turn is spent walking to the mill the question is gone forever -
not declined, not forgotten, simply never seen again.

That is a mechanism producing silence, not a model choosing it, and the two
are worth telling apart before anyone concludes anything about the agents.

WHAT THIS ADDS. The world already keeps every utterance with its speaker,
listener and tick. It can therefore answer a question nothing has ever
asked it: which remarks addressed to this resident have not been answered
yet? An open question outlives the hearing window, and expires on its own
bounded clock so a resident is not haunted by a remark from yesterday.

WHAT IT DELIBERATELY DOES NOT DO. It does not make anyone answer. The
perception says a question is open; whether the resident replies, deflects,
or ignores it stays the agent's decision and the research question. A
mechanism that forced a reply would score 100% and measure nothing.
"""

from __future__ import annotations


from world.tools.state import OPEN_QUESTION_TTL_TICKS, WorldState


def utterance(tick: int, speaker: str, listener: "str | None", content: str) -> dict:
    return {
        "tick": tick,
        "said_at": float(tick),
        "speaker_agent_id": speaker,
        "speaker_name": speaker.upper(),
        "listener_agent_id": listener,
        "listener_name": listener.upper() if listener else None,
        "location_id": "home:h_field",
        "content": content,
        "is_public": listener is None,
        "conversation_id": f"c{tick}",
    }


def world_with(*rows: dict) -> WorldState:
    state = WorldState.__new__(WorldState)
    state.utterances = list(rows)
    return state


def test_a_question_addressed_to_me_is_open() -> None:
    world = world_with(utterance(10, "n08", "n10", "Will you fetch the water?"))
    rows = WorldState.open_questions(world, "n10", now_tick=11)
    assert [r["content"] for r in rows] == ["Will you fetch the water?"]
    assert rows[0]["speaker_agent_id"] == "n08"


def test_a_question_i_answered_is_closed() -> None:
    """Answered means I spoke back TO THEM, at any later tick. That is the
    same definition the reply-rate measurement uses, deliberately: a
    mechanism scored by one rule and driven by another would be marking
    its own homework."""
    world = world_with(
        utterance(10, "n08", "n10", "Will you fetch the water?"),
        utterance(12, "n10", "n08", "Aye, after college."),
    )
    assert WorldState.open_questions(world, "n10", now_tick=13) == []


def test_answering_a_different_person_does_not_close_it() -> None:
    world = world_with(
        utterance(10, "n08", "n10", "Will you fetch the water?"),
        utterance(12, "n10", "n07", "Morning, Marta."),
    )
    rows = WorldState.open_questions(world, "n10", now_tick=13)
    assert len(rows) == 1


def test_speaking_aloud_does_not_close_a_directed_question() -> None:
    """A remark to the room is not an answer to a person. Counting it as
    one would let an agent discharge every obligation by muttering."""
    world = world_with(
        utterance(10, "n08", "n10", "Will you fetch the water?"),
        utterance(12, "n10", None, "Cold this morning."),
    )
    assert len(WorldState.open_questions(world, "n10", now_tick=13)) == 1


def test_a_question_older_than_the_ttl_has_expired() -> None:
    """Bounded, per section 6. A resident carrying every unanswered
    remark of the whole run would spend its context on a grievance list."""
    world = world_with(utterance(1, "n08", "n10", "Will you fetch the water?"))
    assert WorldState.open_questions(world, "n10", now_tick=1 + OPEN_QUESTION_TTL_TICKS + 1) == []


def test_a_question_at_the_ttl_boundary_is_still_open() -> None:
    world = world_with(utterance(1, "n08", "n10", "Water?"))
    assert WorldState.open_questions(world, "n10", now_tick=1 + OPEN_QUESTION_TTL_TICKS) != []


def test_the_ttl_outlives_the_hearing_window() -> None:
    """The entire point. If this were not true the mechanism would add
    nothing the hearing window did not already do."""
    from world.retrieval_depth import MAX_HEARING_WINDOW_TICKS

    assert OPEN_QUESTION_TTL_TICKS > MAX_HEARING_WINDOW_TICKS


def test_a_public_remark_is_never_an_open_question() -> None:
    world = world_with(utterance(10, "n08", None, "Anyone buying copper?"))
    assert WorldState.open_questions(world, "n10", now_tick=11) == []


def test_a_question_to_somebody_else_is_not_mine() -> None:
    world = world_with(utterance(10, "n08", "n07", "Will you fetch the water?"))
    assert WorldState.open_questions(world, "n10", now_tick=11) == []


def test_my_own_question_is_not_open_for_me() -> None:
    """An agent asked to answer its own question is the self-echo defect
    in a new coat, and it happened before: three of nine delivered remarks
    in one run were the reader's own words."""
    world = world_with(utterance(10, "n10", "n08", "Will you fetch the water?"))
    assert WorldState.open_questions(world, "n10", now_tick=11) == []


def test_several_open_questions_come_back_oldest_first() -> None:
    """Conversation order. The oldest unanswered question is the one
    somebody has been waiting on longest."""
    world = world_with(
        utterance(10, "n08", "n10", "first"),
        utterance(11, "n07", "n10", "second"),
    )
    rows = WorldState.open_questions(world, "n10", now_tick=12)
    assert [r["content"] for r in rows] == ["first", "second"]


def test_only_the_latest_question_from_one_speaker_is_kept() -> None:
    """Bram asked the room for a copper buyer five times in one tick. A
    resident shown all five reads a wall, not a question."""
    world = world_with(
        utterance(10, "n08", "n10", "old ask"),
        utterance(11, "n08", "n10", "newer ask"),
    )
    rows = WorldState.open_questions(world, "n10", now_tick=12)
    assert [r["content"] for r in rows] == ["newer ask"]


def test_the_list_is_capped() -> None:
    world = world_with(
        *[utterance(10 + i, f"n{i:02d}", "n10", f"q{i}") for i in range(12)]
    )
    assert len(WorldState.open_questions(world, "n10", now_tick=25)) <= 3


def test_an_empty_town_has_no_open_questions() -> None:
    assert WorldState.open_questions(world_with(), "n10", now_tick=5) == []


def test_malformed_rows_do_not_break_the_query() -> None:
    """world.utterances is a plain list of dicts written by an event
    subscriber. A missing key must not take down every agent's turn."""
    world = world_with({}, {"tick": None}, {"listener_agent_id": "n10"})
    assert WorldState.open_questions(world, "n10", now_tick=5) == []


# ── Rendering: the agent has to be able to read it ───────────────────


def test_open_questions_render_into_the_perception() -> None:
    from world.retrieval_depth import format_open_questions

    text = format_open_questions(
        [
            {
                "speaker_agent_id": "n08",
                "speaker_name": "Idris Thorne",
                "content": "Will you fetch the water?",
                "tick": 10,
            }
        ],
        now_tick=16,
    )
    assert text is not None
    assert "Idris Thorne" in text and "n08" in text
    assert "Will you fetch the water?" in text


def test_the_rendering_says_how_long_they_have_waited() -> None:
    """"Six ticks ago" is the difference between a question and a nag."""
    from world.retrieval_depth import format_open_questions

    text = format_open_questions(
        [{"speaker_agent_id": "n08", "speaker_name": "Idris", "content": "Water?", "tick": 10}],
        now_tick=16,
    )
    assert "6" in text


def test_nothing_open_renders_nothing() -> None:
    from world.retrieval_depth import format_open_questions

    assert format_open_questions([], now_tick=5) is None
    assert format_open_questions(None, now_tick=5) is None


def test_the_perception_carries_open_questions_when_given_them() -> None:
    from world.core.perception import AmbientInfo, Perception
    from world.retrieval_depth import RetrievalDepthConfig, render_perception_text

    perception = Perception(
        agent_id="n10",
        tick=16,
        location_id="home:h_field",
        ambient=AmbientInfo(game_day=0, hour=6, minute=20, is_daytime=True),
        visible_agents=[],
        heard_events=[],
    )
    text = render_perception_text(
        perception,
        depth=RetrievalDepthConfig(fraction=0.0),
        open_questions=[
            {
                "speaker_agent_id": "n08",
                "speaker_name": "Idris Thorne",
                "content": "Will you fetch the water?",
                "tick": 10,
            }
        ],
    )
    assert "Will you fetch the water?" in text


# -- The closing line, and what it was doing ---------------------------
#
# MEASURED on run5 (docs/runs/series/run5): eight turns carried an open
# question and ALL EIGHT produced no tool call whatsoever - not a
# different action, nothing. The baseline on turns with no open question
# was 57 of 94, so 8 of 8 is not what that rate predicts.
#
# The section ended:
#
#     You have not answered. Answer with `say` if you mean to, or let it
#     go and do what matters more.
#
# The second half was written to keep the mechanism from forcing a reply -
# a mechanism that forced one would score 100% and measure nothing. But
# "let it go and do what matters more" is not neutral. It is the last
# thing in the section, it is an explicit invitation to do neither, and it
# arrives attached to a question somebody is waiting on.
#
# Marta asked Wren three times in that run, in three separate remarks, and
# he never answered once.
#
# The fix is to STOP EARLIER. Reporting that a question is open and that
# it has not been answered is a fact. Telling a resident it may let the
# question go is advice, and this section has no business giving any -
# the same rule the convening hint and the silent-turn report already
# follow.


def test_the_section_does_not_offer_to_let_it_go() -> None:
    from world.retrieval_depth import format_open_questions

    text = format_open_questions(
        [{"speaker_agent_id": "n08", "speaker_name": "Marta", "content": "Flour?", "tick": 5}],
        now_tick=9,
    )
    lowered = text.lower()
    assert "let it go" not in lowered
    assert "what matters more" not in lowered


def test_it_still_says_the_question_is_unanswered() -> None:
    """Removing the advice must not remove the fact. Without it the
    section is a transcript of something already in `Recently:`."""
    from world.retrieval_depth import format_open_questions

    text = format_open_questions(
        [{"speaker_agent_id": "n08", "speaker_name": "Marta", "content": "Flour?", "tick": 5}],
        now_tick=9,
    )
    assert "not answered" in text.lower()


def test_it_still_names_the_verb_and_gives_no_order() -> None:
    """`say` is named because a resident that has not connected the
    sentence to the verb is not declining - it is the same finding that
    made the authoring hint name `skillify`. What must not appear is an
    instruction to use it."""
    from world.retrieval_depth import format_open_questions

    text = format_open_questions(
        [{"speaker_agent_id": "n08", "speaker_name": "Marta", "content": "Flour?", "tick": 5}],
        now_tick=9,
    )
    assert "say" in text
    lowered = text.lower()
    for order in ("you must", "you should", "make sure", "remember to", "answer them now"):
        assert order not in lowered


# ── TOOL-17: a question you cannot answer is not a question ──────────


def _placed(state: WorldState, where: dict[str, str]) -> WorldState:
    """Give a synthetic world enough to answer "where is everybody"."""
    state.location_of = lambda agent_id: where.get(agent_id)  # type: ignore[method-assign]
    return state


def test_a_question_from_somebody_who_has_left_is_not_shown() -> None:
    """`TOOL-17`. `say` reaches only somebody in the same place - the
    perception says so in as many words - so once the asker has walked
    off, the prompt was carrying an obligation the resident had no way
    to discharge, for the full 36-tick TTL.

    That is worse than clutter. An open question outranks the town
    roster in the prompt precisely because another resident is waiting
    on it; a stale one displaces a live one.
    """
    world = _placed(
        world_with(utterance(10, "n01", "n10", "Do you have flour?")),
        {"n10": "home:h_field", "n01": "business:b_mill"},
    )
    assert WorldState.open_questions(world, "n10", now_tick=11) == []


def test_a_question_from_somebody_still_here_is_shown() -> None:
    world = _placed(
        world_with(utterance(10, "n01", "n10", "Do you have flour?")),
        {"n10": "home:h_field", "n01": "home:h_field"},
    )
    rows = WorldState.open_questions(world, "n10", now_tick=11)
    assert [r["speaker_agent_id"] for r in rows] == ["n01"]


def test_a_world_that_cannot_say_where_anyone_is_shows_the_question() -> None:
    """Permissive on anything it cannot establish.

    Dropping a live question is a worse error than keeping a stale one -
    the resident never learns it was asked at all - so a world with no
    location index changes nothing.
    """
    world = world_with(utterance(10, "n01", "n10", "Do you have flour?"))
    rows = WorldState.open_questions(world, "n10", now_tick=11)
    assert [r["speaker_agent_id"] for r in rows] == ["n01"]


def test_the_utterance_itself_is_not_deleted() -> None:
    """Dropped from the PROMPT only. "Asked and never answered" has to
    stay countable in the trace, or the measurement goes with the
    clutter."""
    world = _placed(
        world_with(utterance(10, "n01", "n10", "Do you have flour?")),
        {"n10": "home:h_field", "n01": "business:b_mill"},
    )
    assert WorldState.open_questions(world, "n10", now_tick=11) == []
    assert len(world.utterances) == 1
    assert world.utterances[0]["content"] == "Do you have flour?"
