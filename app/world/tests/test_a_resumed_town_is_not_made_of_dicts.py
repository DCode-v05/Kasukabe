"""A checkpoint on disk is JSON, and JSON does not know what a PricePost is.

`WorldState.snapshot()` deepcopies, so an in-process round trip keeps real
model objects and `restore()`'s blind `setattr` looked correct forever. A
checkpoint written to DISK goes through JSON, so a resumed run got plain
dicts in every typed field, and the first line of code to touch one died:

    AttributeError: 'dict' object has no attribute 'ask_sim'

Observed killing a real run at its very first dispatch, on the path that
renders prices into a resident's perception.

It is worse than one crash. `wage_promises`, `intentions`, `loans` and
`crime_cases` have the same shape, so a resumed town owed nobody, intended
nothing and had no open cases - whichever one was read first decided how it
failed.
"""
from __future__ import annotations

import json


def _as_disk(snapshot):
    """What a checkpoint file holds: JSON, with no idea what a model is."""

    def encode(o):
        if hasattr(o, "model_dump"):
            return o.model_dump()
        if isinstance(o, (set, frozenset)):
            return sorted(o, key=repr)
        if hasattr(o, "__dict__"):
            return {k: v for k, v in vars(o).items() if not k.startswith("_")}
        return str(o)

    return json.loads(json.dumps(snapshot, default=encode))

import pytest

import pytest_asyncio

from world.tools.bootstrap import build_world
from world.tools.state import PricePost, WorldState, _rehydrated


@pytest_asyncio.fixture
async def world() -> WorldState:
    state, _registry = await build_world(genesis_tick=0, seed=1)
    return state


@pytest_asyncio.fixture
async def fresh_world() -> WorldState:
    state, _registry = await build_world(genesis_tick=0, seed=2)
    return state


def test_a_price_board_survives_a_json_round_trip():
    board = {"b_bakery": {"bread": PricePost(item="bread", ask_sim=30, bid_sim=None)}}
    through_json = _as_disk(board)

    assert isinstance(through_json["b_bakery"]["bread"], dict), "fixture is wrong"

    back = _rehydrated("price_board", through_json)
    post = back["b_bakery"]["bread"]
    assert isinstance(post, PricePost), (
        "a resumed run's price board is plain dicts, so the first code to "
        "read `.ask_sim` dies - which is what killed run 24's first attempt"
    )
    assert post.ask_sim == 30


def test_an_untyped_field_is_left_exactly_as_it_was():
    value = {"anything": [1, 2, 3]}
    assert _rehydrated("some_other_field", value) is value


def test_a_row_that_cannot_be_rebuilt_is_kept_not_lost():
    """A schema change must not cost the run its state.

    A dict the model genuinely refuses (a missing required field) is left
    exactly as it arrived - which is what happened before this function
    existed - rather than being dropped.
    """
    unbuildable = {"b": {"bread": {"no_item_field_at_all": 1}}}
    back = _rehydrated("price_board", unbuildable)
    assert back["b"]["bread"] == {"no_item_field_at_all": 1}


@pytest.mark.asyncio
async def test_restore_puts_real_objects_back(world, fresh_world):
    world.put_price("b_mill", PricePost(item="flour", ask_sim=6, bid_sim=None))
    on_disk = _as_disk(world.snapshot())

    fresh_world.restore(on_disk)

    post = fresh_world.price_board["b_mill"]["flour"]
    assert isinstance(post, PricePost)
    assert post.ask_sim == 6


@pytest.mark.asyncio
async def test_the_perception_path_that_crashed_now_works(world, fresh_world):
    """The exact call that died: describe_prices reading .ask_sim."""
    from world.tools.primitives.places import describe_prices

    world.put_price("someone", PricePost(item="bread", ask_sim=30, bid_sim=12))
    on_disk = _as_disk(world.snapshot())

    fresh_world.restore(on_disk)
    # Nothing is co-located, so nothing is visible - the point is that the
    # call COMPLETES instead of raising AttributeError.
    assert describe_prices(fresh_world, "anywhere") == {}
