"""The agent inspector must show what the world already sent it.

`UI-12`. `world/agents_api.py`'s `/agents/{id}` serves `bio`,
`personality_traits`, `habits`, `spouse_id`, `parent_ids`, `children_ids`,
`marital_status`, `skills` and `co_located`.
`world_client._agent_detail_from_world` hard-coded `bio=""` and
`occupation=None` - with a comment saying the world exposed no bio "yet" -
and returned an empty `relationships` list.

So the inspector, which DESIGN section 8 defines as "click any agent ->
live conversation, memories, wallet, relationships, trajectory", showed a
blank card for a resident the world could describe in full. The data
crossed the wire and was dropped on the floor at the last step.

`relationships` here is KINSHIP only - spouse, parents, children - and is
labelled as such. The sentiment/trust social graph lives in each engine's
own brain.sqlite and no HTTP route serves it (EVAL-23); filling this list
with kin and calling it a social graph would be a different lie from the
empty one.
"""
from __future__ import annotations

from game.world_client import _agent_detail_from_world


def _raw(**over):
    base = {
        "agent_id": "n01",
        "name": "Marta Thorne",
        "location_id": "business:b_bakery",
        "bio": "A baker who counts every loaf.",
        "occupation": "baker",
        "age_years": 41.0,
        "spouse_id": "n02",
        "parent_ids": ["n20"],
        "children_ids": ["n03", "n04"],
        "usd_wallet_cents": 600,
        "sim_balance": 3200,
        "alive": True,
    }
    base.update(over)
    return base


class TestTheFieldsThatWereDropped:
    def test_the_bio_arrives(self):
        detail = _agent_detail_from_world(_raw())
        assert detail.bio == "A baker who counts every loaf.", (
            "UI-12: the world sent a bio and the client hard-coded it empty"
        )

    def test_the_occupation_arrives(self):
        assert _agent_detail_from_world(_raw()).occupation == "baker"

    def test_a_resident_with_no_bio_is_not_broken_by_it(self):
        """Older genesis files and newborns have none."""
        detail = _agent_detail_from_world(_raw(bio=None, occupation=None))
        assert detail.bio == ""
        assert detail.occupation is None


class TestKin:
    def test_a_spouse_becomes_a_relationship(self):
        kin = _agent_detail_from_world(_raw()).relationships
        assert any(
            r.other_agent_id == "n02" and r.relationship_type == "spouse" for r in kin
        )

    def test_parents_and_children_are_labelled_apart(self):
        kin = _agent_detail_from_world(_raw()).relationships
        by_type = {r.relationship_type for r in kin}
        assert {"spouse", "parent", "child"} <= by_type
        assert {r.other_agent_id for r in kin if r.relationship_type == "child"} == {
            "n03", "n04"
        }

    def test_kin_carries_no_invented_sentiment(self):
        """The social graph is a different thing and lives elsewhere
        (EVAL-23). A trust score made up here would be worse than the empty
        list this replaces."""
        for record in _agent_detail_from_world(_raw()).relationships:
            assert record.sentiment is None
            assert record.trust is None

    def test_somebody_with_no_family_gets_an_empty_list(self):
        detail = _agent_detail_from_world(
            _raw(spouse_id=None, parent_ids=[], children_ids=[])
        )
        assert detail.relationships == []
