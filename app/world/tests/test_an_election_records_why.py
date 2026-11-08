"""An election that records who won and never why is half a record.

A ballot was a voter and a candidate and no sentence. A candidacy was a name
on a list. So the one question a 50-resident election run exists to answer -
what was this election between, and why did the town decide as it did - could
only be approached by reading the conversation around it and guessing.

Both fields are REQUIRED, and that is the whole point. The last run recorded

    0 of 1523 remarks said why (an optional intent, kept on the record and
    never heard by anyone in the town)

against an optional field of exactly this kind. A town given the option to
explain itself did not once take it in 1,523 chances.

`declare_candidacy` in the elections engine has accepted `because` since
candidacy was wired, and nothing ever passed it or wrote it down.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from world.charter import elections as elections_engine
from world.tools.primitives.civic import VoteParams
from world.tools.primitives.institutions import DeclareCandidacyParams


def test_a_vote_must_say_why() -> None:
    with pytest.raises(ValidationError):
        VoteParams(election_id="e1", candidate_agent_id="n02")

    ok = VoteParams(election_id="e1", candidate_agent_id="n02",
                    because="She kept the mill weights honest all season.")
    assert ok.because


def test_a_candidacy_must_say_what_for() -> None:
    with pytest.raises(ValidationError):
        DeclareCandidacyParams(election_id="e1")

    ok = DeclareCandidacyParams(election_id="e1",
                                platform="One measure for every household at the mill.")
    assert ok.platform


def test_a_reason_of_two_words_is_not_a_reason() -> None:
    """`min_length` is what stops "ok" and "good" being compliance. Not a
    guarantee of thought, but it costs a model nothing to write a sentence
    and it stops the field being filled with punctuation."""
    with pytest.raises(ValidationError):
        VoteParams(election_id="e1", candidate_agent_id="n02", because="good")
    with pytest.raises(ValidationError):
        DeclareCandidacyParams(election_id="e1", platform="yes")


def test_the_engine_actually_ACCEPTS_the_reason() -> None:
    """The test below checked the source string and passed while the code was
    broken: `cast_vote` put `because` in its event payload and never took it
    as a parameter, so every ballot raised

        ElectionEngine.cast_vote() got an unexpected keyword argument 'because'

    Residents were voting - n01 called `world:vote` as its first action of the
    turn - and every ballot was rejected by this. Two elections tallied with
    zero votes and the cause was here, not in the model.

    Reading the signature is what a string match could not do.
    """
    import inspect

    for fn in (elections_engine.ElectionEngine.cast_vote,
               elections_engine.ElectionEngine.declare_candidacy):
        params = inspect.signature(fn).parameters
        assert "because" in params, f"{fn.__name__} does not accept a reason"


def test_the_reason_reaches_the_event_the_trace_records() -> None:
    """A field the handler accepts and drops is the state this was in."""
    source = inspect.getsource(elections_engine)

    cast = source[source.index("async def cast_vote"): source.index("async def tally")]
    assert '"because": because' in cast, "the vote's reason never reaches the event"

    declared = source[source.index("async def declare_candidacy"): source.index("async def cast_vote")]
    assert '"because": because' in declared, "the candidate's platform never reaches the event"


def test_the_tools_actually_pass_what_they_collect() -> None:
    from world.tools.primitives import civic, institutions

    assert "because=params.because" in inspect.getsource(civic.vote_handler)
    assert "because=params.platform" in inspect.getsource(
        institutions.declare_candidacy_handler
    )


def test_the_ballot_stays_secret_while_voting_is_open() -> None:
    """The reason goes out; the candidate does not. A live running count
    would let late voters read the race before voting in it, which is the
    deliberate choice the vote event was written with - and the store still
    holds the full ballot for a report written after the tally.
    """
    source = inspect.getsource(elections_engine)
    cast = source[source.index("async def cast_vote"): source.index("async def tally")]
    payload = cast[cast.index("build_event("): cast.index("return election")]

    assert '"candidate_agent_id": candidate_agent_id' not in payload, (
        "the live event now names who was voted for"
    )


def test_a_replayed_tally_is_not_a_second_election() -> None:
    """A run stopped and resumed replays the ticks between its checkpoint and
    where it stopped, so one election tallies once per resume. The section
    printed the same ballot five times - three of them before any candidate
    had declared, as "0 stood. 0 vote(s) cast." - which reads as five
    elections the town never held.

    Keyed by election_id, last write wins: the last tally is the one whose
    candidate list is complete.
    """
    import inspect

    from world.run_report import elections_section

    src = inspect.getsource(elections_section)
    assert "by_id[d.get(\"election_id\", \"\")] = d" in src, (
        "tallies are still collected as a list, so a replay prints twice"
    )
    assert "tallies = list(by_id.values())" in src
