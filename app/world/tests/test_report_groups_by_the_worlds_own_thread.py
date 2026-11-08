"""The report must group conversation the way the world threaded it.

Every utterance carries a `conversation_id`, assigned by
`world/tools/primitives/social.py` when the speech happens and stored on
the row. `run_report.threads()` ignored it and re-derived exchanges from
place, participant overlap and a tick gap.

On run 16 the world recorded 8 conversations, 5 of them with a real
second speaker and the longest running 15 utterances. The report rendered
40 exchanges, 29 of them headed "1 turn (no reply)". A reader - the
person the document exists for - would conclude the town was talking to
itself. It was not.

This is the same heuristic that was removed from the Talk panel in
`app/game/static/js/township/talk.js`, which now groups by
`conversation_id`. The report kept its copy. A fix applied to one surface
and not the other is how a bug survives being fixed.
"""

from __future__ import annotations

from world.run_report import threads


def _line(conv: str, speaker: str, tick: int, place: str, listener: str | None = None) -> dict:
    return {
        "conversation_id": conv,
        "speaker_agent_id": speaker,
        "listener_agent_id": listener,
        "location_id": place,
        "tick": tick,
        "content": f"{speaker} says something at {tick}",
    }


def test_one_conversation_stays_one_exchange() -> None:
    """Five turns the world put on one thread must render as one."""
    rows = [
        _line("c1", "n01", 37, "home:h_quarry", "n02"),
        _line("c1", "n02", 37, "home:h_quarry", "n01"),
        _line("c1", "n01", 38, "home:h_quarry", "n02"),
        _line("c1", "n05", 38, "home:h_quarry"),
        _line("c1", "n02", 39, "home:h_quarry", "n05"),
    ]
    out = threads(rows)
    assert len(out) == 1, f"one conversation rendered as {len(out)} exchanges"
    assert len(out[0]["lines"]) == 5


def test_two_conversations_in_one_room_are_not_merged() -> None:
    """The heuristic's other failure, less visible than the fragmenting:
    two threads in the same place among overlapping people become one,
    and a reply lands under something it never answered."""
    rows = [
        _line("c1", "n01", 37, "market_square", "n02"),
        _line("c2", "n03", 37, "market_square", "n04"),
        _line("c1", "n02", 37, "market_square", "n01"),
        _line("c2", "n04", 37, "market_square", "n03"),
    ]
    out = threads(rows)
    assert len(out) == 2, f"two conversations rendered as {len(out)}"
    assert {len(t["lines"]) for t in out} == {2}


def test_a_wandering_conversation_survives_a_change_of_room() -> None:
    """People walk while they talk. The world keeps the thread; a
    place-keyed heuristic cuts it in half."""
    rows = [
        _line("c1", "n02", 38, "home:h_quarry", "n01"),
        _line("c1", "n01", 38, "street:main", "n02"),
        _line("c1", "n02", 39, "mill", "n01"),
    ]
    out = threads(rows)
    assert len(out) == 1, "a conversation that moved rooms was split"


def test_speech_with_no_thread_id_still_renders() -> None:
    """Older rows, and any the world could not thread, must not vanish
    from the report - the fallback is the previous behaviour, not a
    dropped line."""
    rows = [
        {"speaker_agent_id": "n01", "listener_agent_id": None,
         "location_id": "mill", "tick": 40, "content": "anyone here?"},
        {"speaker_agent_id": "n07", "listener_agent_id": None,
         "location_id": "mill", "tick": 40, "content": "aye"},
    ]
    out = threads(rows)
    assert sum(len(t["lines"]) for t in out) == 2


def test_the_reply_count_a_reader_sees_matches_the_world() -> None:
    """The number that misled: exchanges with a second speaker. Run 16
    had five and the report showed eleven of forty."""
    rows = [
        _line("c1", "n01", 37, "home:h_quarry", "n02"),
        _line("c1", "n02", 37, "home:h_quarry", "n01"),
        _line("c2", "n03", 38, "sawmill"),
        _line("c3", "n04", 39, "mill", "n11"),
        _line("c3", "n11", 39, "mill", "n04"),
    ]
    out = threads(rows)
    answered = [t for t in out if len({ln["speaker_agent_id"] for ln in t["lines"]}) > 1]
    assert len(out) == 3
    assert len(answered) == 2
