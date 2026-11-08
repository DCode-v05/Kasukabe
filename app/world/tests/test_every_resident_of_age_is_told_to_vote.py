"""A resident is told the vote is its own, not that the town is holding one.

Four of twenty voted in the 28 September run. The sixteen were not
uninformed. `n03` read this on six separate turns and went on working:

    The town: an election is open for voting (election_id ...). Standing:
    Ainsley Holloway (n02), Delia Okonjo (n16).
    Candidates declare, and residents vote, in person at Kasukabe Government
    Complex; residents aged 18 and over may do both. Voting closes game-day
    2, 15:00.

Everything a voter needs is in those two sentences - who is standing, where
to go, the deadline - and all of it is in the third person. It is a fact
about the town. The notice said "You have voted" to people who already had,
and to everyone else it said nothing at all, because the absence of a
sentence is not a sentence.

This is GOV-3's finding again. A government was named in the third person for
twenty-three runs and used no power until a seat-holder was told, in the
second person, which verbs were its own - and then cut the head tax nine
words later.

WHAT COMPULSORY DOES NOT MEAN HERE. Nobody is voted for. A ballot the world
casts on a resident's behalf is evidence of nothing, and the reason to count
votes at all is that residents chose to cast them. The rule changes what a
resident is TOLD, and leaves the walk to the hall and the choice of candidate
where they were.
"""

from __future__ import annotations

import pytest

from world.charter.models import Charter
from world.retrieval_depth import format_civic_notice
from world.scenario import Scenario

OPEN = {
    "election_id": "e1",
    "phase": "voting_open",
    "candidates": ["Ainsley Holloway (n02)", "Delia Okonjo (n16)"],
    "where": "Kasukabe Government Complex",
    "min_age": 18,
    "voting_closes": "game-day 2, 15:00",
}


def notice(**over) -> str:
    return format_civic_notice({**OPEN, **over}) or ""


def test_the_rule_is_off_unless_a_scenario_asks_for_it() -> None:
    """Runs before and after have to stay comparable on turnout."""
    assert Charter().compulsory_voting is False


def test_a_resident_who_has_not_voted_is_told_so_in_the_second_person() -> None:
    said = notice(you_have_voted=False, you_have_not_voted=True,
                  voting_is_compulsory=True)

    assert "You have NOT voted" in said
    assert "compulsory" in said
    assert "`vote`" in said, "name the verb, the way the seat notice names its own"


def test_it_says_where_to_go_because_voting_is_not_done_from_a_field() -> None:
    """`vote` is gated to the town hall, so a resident that has not walked
    there cannot comply however willing it is.

    It names the PLACE, not the word "hall". The first 50-resident election
    tallied with zero ballots: every resident read "go to the hall and
    `vote`", all fifty saw it, and `vote` was called not once. "The hall" is
    a word - it is not somewhere `move` can take you, and the town's own
    name for that building is Kasukabe Government Complex.
    """
    said = notice(you_have_voted=False, you_have_not_voted=True,
                  voting_is_compulsory=True)

    assert "Kasukabe Government Complex" in said
    assert "`vote`" in said
    assert "15:00" in said, "and by when"


def test_without_the_rule_it_is_a_statement_and_not_a_duty() -> None:
    """The second person still helps; "compulsory" is the part a scenario
    opts into."""
    said = notice(you_have_voted=False, you_have_not_voted=True,
                  voting_is_compulsory=False)

    assert "You have not voted yet" in said
    assert "compulsory" not in said


def test_somebody_who_voted_is_not_nagged() -> None:
    said = notice(you_have_voted=True)

    assert "You have voted." in said
    assert "NOT voted" not in said and "compulsory" not in said


def test_a_child_is_told_nothing_about_a_duty_it_does_not_have() -> None:
    """Eligibility is read from the resident's own age before the flag is
    ever set, so a nine-year-old's notice carries no duty. Telling a child
    it must vote is the same error as telling nobody at all."""
    said = notice(you_have_voted=False)

    assert "NOT voted" not in said
    assert "compulsory" not in said
    assert "an election is open for voting" in said, "it still learns of the election"


def test_the_duty_is_only_said_while_voting_is_actually_open() -> None:
    """During candidacy or campaigning there is nothing yet to comply with,
    and a duty announced too early is noise by the time it is real."""
    said = format_civic_notice(
        {**OPEN, "phase": "campaigning", "you_have_voted": False,
         "you_have_not_voted": True, "voting_is_compulsory": True}
    ) or ""

    assert "NOT voted" not in said


def test_a_scenario_can_turn_it_on(tmp_path) -> None:
    """The whole point of the knob: the operator asks for it per run."""
    import json

    body = {
        "name": "compulsory",
        "world": {"genesis": "world/genesis_20_funded.json",
                  "start_tick": 324, "stop_tick": 396, "budget_cap_usd": 20.0},
        "charter": {"suffrage_min_age": 18, "compulsory_voting": True},
        "election": {"opens_at": "start"},
    }
    path = tmp_path / "s.json"
    path.write_text(json.dumps(body), encoding="utf-8")

    loaded = Scenario.load(str(path))

    assert loaded.charter.compulsory_voting is True
    assert loaded.charter.as_charter_fields()["compulsory_voting"] is True


def test_nothing_here_casts_a_ballot() -> None:
    """The line this change must not cross.

    A vote the world casts proves nothing about what residents do, which is
    the only reason the project counts votes. If `compulsory_voting` ever
    starts filling ballots, every turnout figure in the series stops being
    evidence - so the rule lives in the charter and in one sentence of
    perception, and nowhere near `civic.py`'s ballot box.
    """
    import inspect

    from world.tools.primitives import civic

    source = inspect.getsource(civic)
    assert "compulsory_voting" not in source, (
        "the voting primitive learned about the rule; a world that can vote "
        "for a resident makes its own turnout figures meaningless"
    )
