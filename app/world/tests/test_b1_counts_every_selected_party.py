"""B1 must count every resident a remark selected, not just the first.

`EVAL-11`. `behavioural.selections` implements Sacks, Schegloff and
Jefferson's "current speaker selects next" - the denominator is every
remark that put an obligation on somebody. It read `listener_agent_id`
alone, and `say` records `addressed_agent_ids` for every name in the
remark: run 23 carried more than one on **172 of 229** utterances.

So a remark naming three residents counted as one selection owed by one
person, and an answer from either of the other two counted as nothing -
which drags the measured answer rate down for behaviour that is correct.

The companion to the same fix in `loop_report` (G1) and in the run
report's thread grouping (CORE-28). One record, three readers, all three
narrower than what was written.
"""
from __future__ import annotations

from world.evals.behavioural import _addressees, _selected_by, answer_rate, selections


def _remark(speaker, listener, addressed, tick):
    return {
        "speaker": speaker, "listener": listener, "addressed": list(addressed),
        "conversation": None, "location": "Kasukabe Bakery",
        "content": "…", "tick": tick,
    }


class TestWhoWasSelected:
    def test_every_named_resident_is_selected(self):
        u = _remark("n01", "n02", ["n02", "n03"], 10)
        assert _selected_by(u) == ["n02", "n03"]

    def test_a_bare_listener_still_counts(self):
        """Rows written before the column existed must behave as before."""
        assert _selected_by(_remark("n01", "n02", [], 10)) == ["n02"]

    def test_an_undirected_remark_selects_nobody(self):
        assert _selected_by(_remark("n01", None, [], 10)) == []

    def test_a_remark_to_the_room_is_not_an_obligation(self):
        """'Morning, all' selects nobody and owes nobody an answer -
        counting it would drag the rate down for perfectly human
        behaviour, which is what the module's own docstring argues."""
        assert not selections([_remark("n01", None, [], 10)])


class TestAnsweringIt:
    def test_a_reply_from_the_second_named_party_answers_it(self):
        utts = [
            _remark("n01", "n02", ["n02", "n03"], 10),
            _remark("n03", "n01", ["n01"], 11),
        ]
        answered, selected, unanswered = answer_rate(utts)
        assert (answered, selected) == (1, 2), (
            "EVAL-11: an answer from a selected party who was not the "
            "primary listener did not close the obligation"
        )
        assert not [u for u in unanswered if u["tick"] == 10]

    def test_silence_from_all_of_them_is_still_unanswered(self):
        """Widening who may answer must not make questions answer
        themselves."""
        utts = [
            _remark("n01", "n02", ["n02", "n03"], 10),
            _remark("n04", "n05", ["n05"], 11),
        ]
        answered, _, unanswered = answer_rate(utts)
        assert answered == 0
        assert len(unanswered) == 2


class TestDecodingTheColumn:
    def test_json_text_decodes(self):
        assert _addressees('["n02","n03"]') == ["n02", "n03"]

    def test_a_list_passes_through(self):
        assert _addressees(["n02"]) == ["n02"]

    def test_nothing_and_nonsense_are_empty(self):
        assert _addressees(None) == []
        assert _addressees("not json") == []
