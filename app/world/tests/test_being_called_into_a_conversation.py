"""Being called into a conversation must read as an invitation, not a dict.

THE DEFECT. Perrin Holloway convened his family about the hidden 400 sim
and named four people. What Ainsley's perception said, verbatim:

    - [agent.conversation.opened, about you] {'conversation_id':
      'fd8dda6a-1111-2222-3333-444455556666', 'topic': 'the household
      accounts and the hidden 400 sim', 'opened_by_agent_id': 'n04',
      'location_id': 'home:h_quarry', 'involved_agent_ids': ['n04', 'n02',
      'n07']}

A model has to reverse-engineer that it was invited to something before it
can consider joining, and the person who invited it is an id it cannot
address.

`format_heard_event`'s own docstring already made this argument, for the
other event that had it:

    Speech used to arrive as a raw payload dict... A model has to
    reverse-engineer that somebody spoke before it can consider answering,
    and the speaker is an id it cannot address. Measured against that
    rendering: 28 utterances and ZERO replies. Speech is the one event
    that must read as speech.

Convening is the other one. It was never given the same treatment, and the
consequence matches: across six runs `start_conversation` was called five
times, and when it WAS used the town frequently ignored the table and kept
talking in the room's implicit thread. Nobody could read the invitation.

WHAT THE RENDERING CARRIES. Who called it, what about, who else is in it,
and the id needed to answer into it - the room picture, delivered at the
moment it changes, without spending a `look_around` call. `look_around`
was the second most-called tool of the series (248 calls against 194
`say`); a resident should not have to buy the news that it was invited to
something.

WHAT IT DOES NOT CARRY. No instruction to join, speak or reply. Atlas -
the prototype this borrows from - follows its group announcement with a
loop that makes each member speak in turn, and that loop is what ROADMAP
Law 2 forbids. This says a thing happened and stops.
"""

from __future__ import annotations

import pytest

from world.core.perception import PerceivedEvent
from world.retrieval_depth import format_heard_event

NAMES = {
    "n04": "Perrin Holloway",
    "n02": "Ainsley Holloway",
    "n07": "Marta Thorne",
    "n09": "A passer-by",
}
TOPIC = "the household accounts and the hidden 400 sim"
CID = "fd8dda6a-1111-2222-3333-444455556666"


def opened(**over) -> dict:
    payload = {
        "conversation_id": CID,
        "topic": TOPIC,
        "opened_by_agent_id": "n04",
        "location_id": "home:h_quarry",
        "involved_agent_ids": ["n04", "n02", "n07"],
    }
    payload.update(over)
    return payload


def read_by(agent_id: str, payload: dict) -> str:
    event = PerceivedEvent(
        type="agent.conversation.opened",
        tick=37,
        payload=payload,
        involves_self=agent_id in (roster if isinstance(
            (roster := payload.get("involved_agent_ids")), (list, tuple, set)) else ()),
        for_agent_id=agent_id,
    )
    return format_heard_event(event, NAMES, here=payload.get("location_id"))


def test_an_invitee_is_told_in_words() -> None:
    text = read_by("n02", opened())
    assert "agent.conversation.opened" not in text, "still a raw event dump"
    assert "{" not in text, "still a raw payload dict"


def test_it_names_who_called_it() -> None:
    """An id cannot be answered. The speech renderer learned this."""
    assert "Perrin Holloway" in read_by("n02", opened())


def test_it_names_the_subject() -> None:
    assert TOPIC in read_by("n02", opened())


def test_it_names_who_else_is_in_it() -> None:
    """The room picture, delivered rather than bought. This is what a
    resident would otherwise spend a `look_around` call to learn."""
    text = read_by("n02", opened())
    assert "Marta Thorne" in text


def test_it_carries_the_id_needed_to_answer_into_it() -> None:
    """A resident that cannot name the thread cannot speak into it, and a
    room holding two conversations muddles them."""
    assert CID in read_by("n02", opened())


def test_the_invitee_is_not_listed_among_the_others() -> None:
    """"In it with you: Ainsley Holloway" reads as a stranger sharing
    your name."""
    text = read_by("n02", opened())
    assert text.count("Ainsley Holloway") == 0


def test_it_tells_nobody_to_speak() -> None:
    """Atlas drove its members round a loop after announcing. That loop is
    what ROADMAP Law 2 forbids, and a line here reading "it is your turn"
    would be the same orchestrator wearing a prompt as a disguise."""
    lowered = read_by("n02", opened()).lower()
    for order in ("your turn", "you must", "you should", "join it", "reply", "answer them"):
        assert order not in lowered, f"the invitation gives an order: {order!r}"


def test_the_convener_is_not_told_they_were_invited() -> None:
    text = read_by("n04", opened())
    assert "invited" not in text.lower()
    assert "opened" in text.lower() or "called" in text.lower()


def test_somebody_merely_nearby_reads_it_as_news_not_an_invitation() -> None:
    """Goffman's bystander again. A conversation opening in a room you
    happen to be standing in is a fact about the room; being NAMED in it
    is a fact about you."""
    text = read_by("n09", opened())
    assert "{" not in text
    assert "with you" not in text.lower()


def test_a_conversation_opened_with_no_topic_still_renders() -> None:
    assert "{" not in read_by("n02", opened(topic=""))


def test_a_missing_roster_does_not_break_a_turn() -> None:
    """This renders on every agent's every turn."""
    payload = opened()
    del payload["involved_agent_ids"]
    assert read_by("n02", payload)


@pytest.mark.parametrize("bad", [None, 7, "n07", {"a": 1}])
def test_a_malformed_roster_does_not_break_a_turn(bad) -> None:
    assert read_by("n02", opened(involved_agent_ids=bad))


def test_an_event_the_renderer_does_not_know_still_falls_back() -> None:
    """The dict dump is the right answer for an event nobody has written
    words for yet. What must not happen is that it silently becomes the
    answer for one somebody did."""
    event = PerceivedEvent(
        type="agent.something.nobody.rendered",
        tick=1,
        payload={"a": 1},
        involves_self=True,
        for_agent_id="n02",
    )
    assert "agent.something.nobody.rendered" in format_heard_event(event, NAMES)
