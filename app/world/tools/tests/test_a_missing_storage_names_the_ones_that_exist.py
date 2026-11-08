"""A resident who guesses a storage id is told which ones are really there.

`withdraw_item` has been reached once in the project's whole history:

    withdraw_item(storage_id="field_storage", item="wheat", quantity=1)
    -> Error: no storage with id 'field_storage'

One paid call, an invented id, a refusal that named nothing, and the verb
was never tried again. `store_item`'s gate and `steal`'s target raise the
identical bare error from two more call sites.

`NotFoundError` has taken an `available` list since it was written, and
`test_not_found_names_the_options.py` already asserts it renders one - "a
bare 'no business with id b_smelter' tells an agent its guess was wrong and
leaves it guessing from the same empty vocabulary". These three sites never
passed one.

What to name is the question the fix turns on. Every storage in the town is
noise - most are somewhere else, and a storage the resident cannot reach is
not an answer. The ones where it is standing are the useful ones, and the
refusal falls back to the wider list only when there is nothing here.
"""

from __future__ import annotations

import pytest

from world.tools.errors import NotFoundError
from world.tools.models import Storage
from world.tools.primitives.commerce import WithdrawItemParams, withdraw_item_handler
from world.tools.tests.conftest import spawn_agent

BARN = "farmland"
MARKET = "market_row"


async def _resident_at(world, where: str = BARN) -> str:
    await spawn_agent(world, "farmhand", where)
    return "farmhand"


def _storage(world, storage_id: str, location_id: str, owner: str = "farmhand") -> None:
    world.storages[storage_id] = Storage(
        storage_id=storage_id, owner_id=owner, location_id=location_id
    )


async def _withdraw(world, who: str, storage_id: str):
    return await withdraw_item_handler(
        world,
        world.agents[who],
        WithdrawItemParams(storage_id=storage_id, item="wheat", quantity=1),
    )


@pytest.mark.asyncio
async def test_the_refusal_names_the_storages_standing_here(world) -> None:
    who = await _resident_at(world)
    _storage(world, "s_barn", BARN)
    _storage(world, "s_loft", BARN)
    _storage(world, "s_far_away", MARKET)

    with pytest.raises(NotFoundError) as caught:
        await _withdraw(world, who, "field_storage")

    said = str(caught.value)
    assert "s_barn" in said and "s_loft" in said, said
    assert "s_far_away" not in said, "a storage in another place is not an answer to this question"


@pytest.mark.asyncio
async def test_with_nothing_here_it_names_what_there_is(world) -> None:
    """Silence would send the resident back to the same empty vocabulary."""
    who = await _resident_at(world)
    _storage(world, "s_far_away", MARKET)

    with pytest.raises(NotFoundError) as caught:
        await _withdraw(world, who, "field_storage")

    assert "s_far_away" in str(caught.value)


@pytest.mark.asyncio
async def test_a_town_with_no_storages_says_so_without_inventing_one(world) -> None:
    who = await _resident_at(world)
    world.storages.clear()

    with pytest.raises(NotFoundError) as caught:
        await _withdraw(world, who, "field_storage")

    said = str(caught.value)
    assert "field_storage" in said
    assert "are:" not in said, "an empty list must not be rendered as though it were an answer"


@pytest.mark.asyncio
async def test_a_near_miss_is_still_pointed_at(world) -> None:
    """The existing `did you mean` path must survive being given a list."""
    who = await _resident_at(world)
    _storage(world, "field_store", BARN)

    with pytest.raises(NotFoundError) as caught:
        await _withdraw(world, who, "field_storage")

    assert "did you mean" in str(caught.value)
