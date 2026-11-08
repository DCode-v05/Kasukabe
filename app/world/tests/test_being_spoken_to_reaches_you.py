"""A resident named in a remark is told they were spoken to.

THE DEFECT, reproduced before it was fixed. Ezra Okonjo answers two people
in one remark, with `listener_agent_id` set to Anselm:

    "Anselm. You want temple bread, walk to temple. Girl. Two sim for
     walking, you said?"

    Anselm (named in the parameter)  involves_self=True   "... said to you: ..."
    Tova   (named in the sentence)   involves_self=False  "... said: ..."

The world KNOWS Tova was addressed - `addressed_agent_ids` holds her, and
`open_questions` and `awaiting_reply` both read it since run 19's issue 7.
The PERCEPTION never learned. Two holes:

  * `social.py` builds `involved_agent_ids = [speaker, listener]` before
    `addressed_agent_ids` is computed and never updates it, so a secondary
    addressee has `involves_self=False` and does not even receive the event
    once they step out of the room;
  * `retrieval_depth.format_heard_event` decides "said to you" from
    `listener_agent_id` alone.

WHY THIS IS A MECHANISM DEFECT AND NOT A PREFERENCE. Sacks, Schegloff and
Jefferson (1974) on turn allocation:

    If the turn-so-far is constructed as to involve the use of a "current
    speaker selects next" technique, then the party so selected has rights,
    and is obliged, to take next turn to speak, and no others have such
    rights or obligations.

    In its simplest form, a vocative such as the name of the next speaker
    is uttered to designate the next speaker.

A vocative IS the selection. It creates an obligation on exactly one person
and on nobody else. Rendering it to that person as ambient speech deletes
the single most load-bearing act in conversation - which is why the reply
rate has sat at 32-52% across every run against a human floor of 64%
(Stivers et al., PNAS 2009). See docs/HUMAN_BASELINES.md §1.

WHAT THIS IS NOT. It is not an instruction to reply. Telling somebody they
were spoken to is not telling them to answer; it is repairing a world that
was lying to them about what happened. The obligation is theirs to honour
or refuse, and refusing is data.
"""

from __future__ import annotations

import pytest

from world.core.perception import PerceivedEvent
from world.retrieval_depth import format_heard_event

NAMES = {"n05": "Ezra Okonjo", "n07": "Tova Okonjo", "n09": "Father Anselm"}

REMARK = (
    "Anselm. You want temple bread, walk to temple. "
    "Girl. Two sim for walking, you said?"
)


def heard_by(agent_id: str, payload: dict) -> str:
    """What this resident's perception says about that remark."""
    event = PerceivedEvent(
        type="agent.utterance.spoken",
        tick=40,
        payload=payload,
        involves_self=agent_id in (payload.get("involved_agent_ids") or []),
        for_agent_id=agent_id,
    )
    return format_heard_event(event, NAMES, here=payload.get("location_id"))


def utterance(**over) -> dict:
    payload = {
        "speaker_agent_id": "n05",
        "listener_agent_id": "n09",
        "addressed_agent_ids": ["n09", "n07"],
        "involved_agent_ids": ["n05", "n09", "n07"],
        "location_id": "temple",
        "content": REMARK,
    }
    payload.update(over)
    return payload


def test_the_person_named_in_the_parameter_is_told() -> None:
    assert "said to you" in heard_by("n09", utterance())


def test_a_person_named_in_the_words_is_told_too() -> None:
    """Tova. The defect this file exists for."""
    assert "said to you" in heard_by("n07", utterance()), (
        "Tova was named by a vocative, which SELECTS her as next speaker "
        "and obliges her alone to answer, and her perception reported "
        "ordinary overheard speech"
    )


def test_somebody_merely_present_is_not_told_they_were_addressed() -> None:
    """Goffman's third role. If everybody in earshot reads "said to you",
    the phrase stops meaning anything and the obligation it carries is
    spread across the room, which is the same as deleting it."""
    assert "said to you" not in heard_by("n02", utterance())
    assert "said" in heard_by("n02", utterance())


def test_a_remark_to_the_room_addresses_nobody() -> None:
    payload = utterance(
        listener_agent_id=None,
        addressed_agent_ids=[],
        involved_agent_ids=["n05"],
        content="Morning, all.",
    )
    for who in ("n07", "n09", "n02"):
        assert "said to you" not in heard_by(who, payload)


def test_an_old_record_with_no_addressed_list_still_renders() -> None:
    """Runs before this change wrote no `addressed_agent_ids`. Reading one
    back must fall through to the single listener rather than raise."""
    payload = utterance()
    del payload["addressed_agent_ids"]
    assert "said to you" in heard_by("n09", payload)
    assert "said to you" not in heard_by("n07", payload)


@pytest.mark.parametrize("bad", [None, "n07", 7, {"n07": True}])
def test_a_malformed_addressed_list_does_not_break_a_turn(bad) -> None:
    """This renders on every agent's every turn. A bad payload must not
    take a resident's turn down with it."""
    assert heard_by("n07", utterance(addressed_agent_ids=bad))


def test_the_speaker_is_never_told_they_addressed_themselves() -> None:
    payload = utterance(addressed_agent_ids=["n05", "n09"])
    assert "said to you" not in heard_by("n05", payload)


def test_the_world_can_say_which_of_the_three_roles_you_had() -> None:
    """Goffman: addressed recipient / ratified unaddressed / bystander.
    Kasukabe had two states - the one `listener_agent_id` names, and
    everybody else - which is the dyadic model Goffman wrote to replace,
    in a town whose rooms hold five people."""
    from world.retrieval_depth import participation_role

    payload = utterance()
    assert participation_role("n09", payload) == "addressed"
    assert participation_role("n07", payload) == "addressed"
    assert participation_role("n05", payload) == "speaker"
    assert participation_role("n02", payload) == "bystander"


def test_a_participant_in_the_thread_is_ratified_not_a_bystander() -> None:
    """Somebody on the conversation's roster who was not named this turn
    is still IN the conversation - expected to listen, free to take the
    next turn. A passer-by is neither."""
    from world.retrieval_depth import participation_role

    payload = utterance(conversation_participants=["n05", "n09", "n07", "n11"])
    assert participation_role("n11", payload) == "ratified"
    assert participation_role("n02", payload) == "bystander"
