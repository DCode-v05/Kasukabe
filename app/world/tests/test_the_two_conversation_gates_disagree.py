"""Two gates score the same conversation in opposite directions.

`EVAL-12`. `G3` passes any thread with three or more distinct speakers
and has NO ceiling, so a conversation scores better the larger it grows.
`B3` fails a town whose median conversation runs above four speakers -
Dunbar, observed mean 3.4, fissioning at five - and `B4` fails one whose
over-large threads do not split.

Run 23's 98-turn, 17-person bakery thread passes G3 and fails B3 and B4
on the same evidence. `HUMAN_BASELINES.md` section 2.2 already names the
contradiction, and the series has been graded against both anyway.

THIS FILE DOES NOT RESOLVE IT. The review's instruction is "decide one",
and that is a research decision - whether the town is measured against
what a human group does, or against whether it can hold a conversation at
all. Nobody has made it, and a test is not the place to.

What it does is stop the decision being made by ACCIDENT. `behavioural.py`
has documented the conflict since it was written; `loop_report.py`'s G3
said nothing, so a reader of the gates saw no conflict. Now both ends
carry it, and moving either number without the other fails here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from loop_report import GROUP_SPEAKERS_MIN
from world.evals.behavioural import CONVERSATION_SIZE_CEILING, FISSION_SHARE_FLOOR

APP = Path(__file__).resolve().parents[2]


def test_the_gates_really_do_conflict() -> None:
    """The premise. If someone reconciles them, this fails and the rest of
    the file should be deleted rather than kept as a monument."""
    assert GROUP_SPEAKERS_MIN <= CONVERSATION_SIZE_CEILING, (
        "EVAL-12 is resolved: G3's floor is now above B3's ceiling, which "
        "means they can no longer both be satisfied at once. Whoever did "
        "this should delete this test and record the decision."
    )
    assert CONVERSATION_SIZE_CEILING >= 4


def test_a_seventeen_person_thread_passes_one_and_fails_the_other() -> None:
    """Run 23's bakery thread, as a number. This is the case that makes
    the contradiction concrete rather than theoretical."""
    speakers = 17
    assert speakers >= GROUP_SPEAKERS_MIN, "G3 would not pass it"
    assert speakers > CONVERSATION_SIZE_CEILING, "B3 would not fail it"


def test_both_ends_say_so() -> None:
    """The actual fix. `behavioural.py` carried this and `loop_report.py`
    did not, so half the readers of the gates never saw it."""
    for module, name in (
        (APP / "loop_report.py", "G3"),
        (APP / "world" / "evals" / "behavioural.py", "B3"),
    ):
        source = module.read_text(encoding="utf-8")
        assert "EVAL-12" in source or "G3 gets backwards" in source, (
            f"{module.name} defines {name} and does not mention that it "
            "contradicts the other gate, so a reader of this end sees no "
            "conflict at all"
        )


def test_the_g3_comment_names_the_evidence() -> None:
    """A stated contradiction with no example gets read as a quibble. The
    98-turn, 17-person thread is what makes it a real disagreement."""
    source = (APP / "loop_report.py").read_text(encoding="utf-8")
    block = source[source.index("G3: three or more distinct speakers"):][:1600]
    assert "17-person" in block
    assert "decide one" in block, (
        "the comment does not record that this is an open DECISION, so the "
        "next reader treats it as a bug to be fixed by moving a number"
    )


def test_the_fission_floor_is_still_a_first_bar() -> None:
    """B4's own reasoning: fission is what happens, not what usually
    happens, so a first bar should be reachable. A change that quietly
    raised it would be deciding EVAL-12 by moving B4 instead of G3."""
    assert FISSION_SHARE_FLOOR == 0.50


def test_neither_number_has_moved_alone() -> None:
    """The guard the comment promises. These three are the whole of the
    disagreement; any of them changing is the decision being made, and it
    should be made deliberately."""
    assert (GROUP_SPEAKERS_MIN, CONVERSATION_SIZE_CEILING, FISSION_SHARE_FLOOR) == (
        3, 4, 0.50
    ), (
        "EVAL-12: one of the conversation thresholds moved. If that was the "
        "decision, record which gate the series is now graded against and "
        "update this test; if it was not, it has been made by accident."
    )
