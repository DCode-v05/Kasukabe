"""One rule for who was asked, and who has answered.

`CORE-15`. `say` records a primary `listener_agent_id` AND the full
`addressed_agent_ids` list, and the town had two definitions of "said to
you":

  * `PerceptionSystem.build_perception` read `listener_agent_id` alone,
    both for deciding a remark was addressed to somebody and for deciding
    they had answered it;
  * `WorldState.open_questions` and `format_addressed_to_you` read both.

Two mechanisms scoring one question is how a resident ends up being handed
something it has already answered - which, from outside, reads as a
resident ignoring somebody. And a remark naming three residents reached
the second and third as background chatter under "Recently" rather than as
a question they had been asked.

Run 23 carried more than one addressee on 172 of its 229 utterances, so
this was the ordinary case, not an edge.
"""
from __future__ import annotations

from world.core.perception import _addressees_of


class TestWhoWasNamed:
    def test_every_addressee_counts(self):
        assert _addressees_of(
            {"listener_agent_id": "n02", "addressed_agent_ids": ["n02", "n03"]}
        ) == {"n02", "n03"}

    def test_the_primary_listener_is_included_even_if_the_list_omits_it(self):
        """The two fields are written by different code paths and one has
        gone missing before. Union, not either-or."""
        assert _addressees_of(
            {"listener_agent_id": "n02", "addressed_agent_ids": ["n03"]}
        ) == {"n02", "n03"}

    def test_a_remark_written_before_the_list_existed_still_works(self):
        assert _addressees_of({"listener_agent_id": "n02"}) == {"n02"}

    def test_a_remark_to_the_room_names_nobody(self):
        """'Morning, all' owes nobody an answer, and counting it would put
        a question in front of every resident in the place."""
        assert _addressees_of({}) == set()
        assert _addressees_of(None) == set()

    def test_empty_values_are_not_addressees(self):
        assert _addressees_of(
            {"listener_agent_id": None, "addressed_agent_ids": [None, ""]}
        ) == set()
