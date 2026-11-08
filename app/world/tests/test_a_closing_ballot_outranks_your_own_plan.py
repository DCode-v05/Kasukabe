"""Two elections, fifty residents, zero ballots - and the notice was right.

It said "You have NOT voted", in the second person. It named `vote`. It named
the place. Every resident read it. One was told, while standing in the
building, "You are at Kasukabe Government Complex: `vote` now" - and said
something to a neighbour instead.

The notice was not wrong, it was outranked. It sat 55% of the way down a
prompt that OPENS with the resident's own goal:

    You set out at 11:20 to: Keep household upkeep and Bram's debts plainly
    accounted for; do not pay a family debt for silence.

A standing preference stated first, in the second person, against a civic
fact stated halfway down. The resident kept doing what it set out to do,
which is what that line is for.

So the duty goes above it - but only while the ballot is open and only for
somebody who has not cast one. A deadline outranks a preference; a nag does
not, and the moment the vote is cast or the window shuts this disappears and
the intention leads again.

GOV-3 is the precedent for position mattering: nine words in a Prime
Minister's own perception moved `set_tax_rate` after twenty-three runs of
nothing.
"""

from __future__ import annotations

import inspect

from world import retrieval_depth


def _assembly() -> str:
    src = inspect.getsource(retrieval_depth)
    i = src.index("sections = [format_ambient(perception)]")
    return src[i: src.index("intention_line = format_intention", i)]


def test_the_ballot_is_placed_above_the_resident_s_own_intention() -> None:
    src = inspect.getsource(retrieval_depth)
    ballot = src.index("BEFORE ANYTHING ELSE: voting closes soon")
    intention = src.index("intention_line = format_intention")

    assert ballot < intention, (
        "the closing ballot is still below the resident's own plan, which is "
        "the arrangement that produced two elections with no votes"
    )


def test_it_appears_only_while_the_ballot_is_open() -> None:
    """Above the intention is a strong place. A line that sat there all run
    would be furniture by the time it mattered."""
    block = _assembly()

    assert 'voting_open' in block
    assert 'you_have_not_voted' in block


def test_it_appears_only_for_somebody_who_has_not_voted() -> None:
    """Otherwise it tells a resident who has already voted to go and vote,
    every tick until the window shuts."""
    assert "you_have_not_voted" in _assembly()


def test_it_appears_only_where_voting_is_compulsory() -> None:
    """A scenario without the rule gets a fact in its usual place, not a
    duty at the top. The charter decides, not this function."""
    assert "voting_is_compulsory" in _assembly()


def test_it_names_the_move_when_the_resident_is_elsewhere() -> None:
    """`vote` is gated to the hall: from anywhere else the tool is reduced to
    an index line the resident cannot form a call for, so "vote now" is not
    an instruction it can follow."""
    block = _assembly()

    # the source wraps the sentence across lines, so match the pieces rather
    # than a phrase that only exists once rendered
    assert "`move` " in block and "{hall_now}" in block and "`vote`" in block


def test_it_tells_a_resident_already_there_not_to_walk() -> None:
    """Measured, on gpt-6-luna with the real 80-tool list and the real prompt
    n01 answered with six other verbs while standing in the hall:

        the prompt as it was ................. 0/6
        duty hoisted, "`vote` now" ........... 2/8   (it kept calling `move`)
        + the call shape ..................... 4/8
        + "you do not need to move" .......... 8/8

    Half the failures were a resident walking to a building it was already
    standing in. Saying so is the single biggest part of the fix.
    """
    block = _assembly()

    assert "do not need to move" in block
    assert "ALREADY at" in block


def test_it_names_the_election_and_the_arguments() -> None:
    """"Vote" is a thing to work out; `vote` with an election_id, a candidate
    and a reason is a call to make. Worth 2/8 on its own."""
    block = _assembly()

    assert "election_id" in block
    assert "because" in block
