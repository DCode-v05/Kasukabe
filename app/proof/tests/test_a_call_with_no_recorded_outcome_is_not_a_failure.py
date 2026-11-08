""""Never once succeeded" has to mean the corpus saw it fail.

`search_memory` was reported as "reached for 13 times and never once
succeeded - every one a paid call that changed nothing". It was not. All
thirteen calls were well formed, every one carried the `query` the engine's
schema requires, and eight also carried the `limit` it declares. What the
corpus does not carry is an OUTCOME: those rows are written by the platform
seam with no `status` field at all, so `status` reads as the empty string and
`successes` stays at zero.

The difference matters more here than anywhere else in this package, because
the sentence is an accusation. Four verbs - `pay_tax`, `teach_skill`,
`withdraw_item`, `work_site` - carry a real `error` status on every call, and
for those the sentence is earned and should stay exactly as it is. For a verb
whose outcome was never written down, the honest answer is that nobody knows,
which is what UNPROVEN was invented to say.
"""

from __future__ import annotations

from proof.reach import Reach, reach_sentence


def a_verb_that(calls: int, successes: int = 0, unknown: int = 0) -> Reach:
    r = Reach()
    r.calls, r.successes, r.unknown = calls, successes, unknown
    return r


def test_calls_the_corpus_saw_fail_are_still_named_as_such() -> None:
    """pay_tax: ten calls, ten recorded errors."""
    sentence = reach_sentence(a_verb_that(calls=10, unknown=0))

    assert "never once succeeded" in sentence
    assert "paid call that changed nothing" in sentence


def test_calls_with_no_recorded_outcome_are_not_called_failures() -> None:
    """search_memory: thirteen calls, thirteen rows with no status field."""
    sentence = reach_sentence(a_verb_that(calls=13, unknown=13))

    assert "never once succeeded" not in sentence, sentence
    assert "no outcome" in sentence and "13" in sentence


def test_a_mix_is_reported_as_what_was_actually_seen() -> None:
    """Two refusals and one silent row is still two refusals."""
    sentence = reach_sentence(a_verb_that(calls=3, unknown=1))

    assert "2" in sentence and "never once succeeded" in sentence
    assert "1 with no outcome recorded" in sentence


def test_search_memory_reads_as_unknown_in_the_real_corpus() -> None:
    """The row that started this, end to end against the recorded runs."""
    from proof.reach import merge, reach_from_databases, reach_from_sessions

    found = merge(reach_from_sessions(), reach_from_databases())
    r = found.get("search_memory")

    assert r is not None and r.calls > 0, "the corpus records calls to it"
    # Not a fixed count: the corpus grows every time a run's session files
    # are recovered, and a test that pins the number would fail for the
    # right thing happening.
    assert r.unknown == r.calls, "every one of them written without a status"
    assert "never once succeeded" not in reach_sentence(r)
