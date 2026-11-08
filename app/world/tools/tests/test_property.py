"""Land ownership, the land auction, and wills: The 250 section 31.

Runs against the real wired world (conftest's build_world), not a stub,
so every assertion about money is an assertion about the real ledger.
"""

from __future__ import annotations

import pytest

from world.ledger.money import TREASURY_ACCOUNT_ID
from world.tools.errors import ActorRequirementError, NotFoundError
from world.tools.models import Bequest, OwnerClass, TileOwnership
from world.tools.primitives.property import (
    GOVERNMENT_OWNER_ID,
    owned_tiles,
    require_tile,
    settle_property_estate,
)
from world.tools.state import tile_key
from world.tools.tests.conftest import action_cost, spawn_agent


async def seat_prime_minister(world, agent_id: str) -> None:
    """Put someone in the chair the way charter/ actually models it, so the
    registry's own `require_government_seat` check sees them."""
    from world.charter.models import GovernmentState

    await world.charter.store.set_government_state(GovernmentState(pm_agent_id=agent_id))


def give_tile(world, x: int, y: int, owner_id: str, *, from_expansion: bool = False) -> None:
    world.tile_owners[tile_key(x, y)] = TileOwnership(
        tile_x=x, tile_y=y, owner_id=owner_id,
        owner_class=OwnerClass.PRIVATE if owner_id != GOVERNMENT_OWNER_ID else OwnerClass.GOVERNMENT,
        from_expansion=from_expansion,
    )


@pytest.mark.asyncio
async def test_unclaimed_land_reads_as_government_owned(world) -> None:
    """Section 31.2: unclaimed land IS a government asset. No row is
    written for it, so the map does not carry 34,000 identical records."""
    tile = require_tile(world, 142, 87)
    assert tile.owner_class is OwnerClass.GOVERNMENT
    assert tile.owner_id == GOVERNMENT_OWNER_ID
    assert world.tile_owners == {}


@pytest.mark.asyncio
async def test_transfer_tile_moves_the_deed_and_the_money(world, registry) -> None:
    await spawn_agent(world, "seller", "square")
    await spawn_agent(world, "buyer", "square")
    give_tile(world, 10, 20, "seller")

    before = world.ledger.sim_balance("seller")
    result = await registry.execute(
        world, "seller", "transfer_tile",
        {"tile_x": 10, "tile_y": 20, "to_owner_id": "buyer", "price_sim": 2_500},
    )

    assert result.result["owner_id"] == "buyer"
    assert require_tile(world, 10, 20).owner_id == "buyer"
    assert require_tile(world, 10, 20).owner_class is OwnerClass.PRIVATE
    assert world.ledger.sim_balance("seller") == before + 2_500 - action_cost(
        "transfer_tile", {}
    )


@pytest.mark.asyncio
async def test_a_gift_is_a_sale_at_zero(world, registry) -> None:
    await spawn_agent(world, "parent", "square")
    await spawn_agent(world, "child", "square")
    give_tile(world, 3, 4, "parent")
    before = world.ledger.sim_balance("child")

    await registry.execute(
        world, "parent", "transfer_tile", {"tile_x": 3, "tile_y": 4, "to_owner_id": "child"}
    )
    assert require_tile(world, 3, 4).owner_id == "child"
    assert world.ledger.sim_balance("child") == before


@pytest.mark.asyncio
async def test_you_cannot_sell_land_you_do_not_own(world, registry) -> None:
    await spawn_agent(world, "chancer", "square")
    await spawn_agent(world, "owner", "square")
    give_tile(world, 7, 7, "owner")

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "chancer", "transfer_tile",
            {"tile_x": 7, "tile_y": 7, "to_owner_id": "chancer", "price_sim": 1},
        )
    assert require_tile(world, 7, 7).owner_id == "owner"


@pytest.mark.asyncio
async def test_a_failed_payment_leaves_the_deed_alone(world, registry) -> None:
    """registry.py's ordering discipline: the ledger call this handler
    cannot roll back happens before world state changes."""
    await spawn_agent(world, "seller", "square")
    await spawn_agent(world, "pauper", "square", starting_sim=10)
    give_tile(world, 9, 9, "seller")

    with pytest.raises(Exception):
        await registry.execute(
            world, "seller", "transfer_tile",
            {"tile_x": 9, "tile_y": 9, "to_owner_id": "pauper", "price_sim": 999_999},
        )
    assert require_tile(world, 9, 9).owner_id == "seller"


@pytest.mark.asyncio
async def test_auction_turns_expanded_land_into_treasury_revenue(world, registry) -> None:
    await spawn_agent(world, "pm", "square")
    await spawn_agent(world, "buyer", "square")
    await seat_prime_minister(world, "pm")
    give_tile(world, 200, 5, GOVERNMENT_OWNER_ID, from_expansion=True)

    treasury_before = world.ledger.sim_balance(TREASURY_ACCOUNT_ID)
    result = await registry.execute(
        world, "pm", "auction_land",
        {"tile_x": 200, "tile_y": 5, "to_owner_id": "buyer", "price_sim": 4_000},
    )

    assert result.result["treasury_gain_sim"] == 4_000
    assert world.ledger.sim_balance(TREASURY_ACCOUNT_ID) == treasury_before + 4_000 + action_cost(
        "auction_land", {}
    )
    assert require_tile(world, 200, 5).owner_id == "buyer"
    assert require_tile(world, 200, 5).owner_class is OwnerClass.PRIVATE
    sold = [e for e in result.events if e.type == "world.land.auctioned"][0]
    assert sold.payload["from_expansion"] is True


@pytest.mark.asyncio
async def test_only_a_seat_holder_may_auction(world, registry) -> None:
    await spawn_agent(world, "nobody", "square")
    await spawn_agent(world, "buyer", "square")
    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "nobody", "auction_land",
            {"tile_x": 201, "tile_y": 5, "to_owner_id": "buyer", "price_sim": 100},
        )


@pytest.mark.asyncio
async def test_the_government_cannot_auction_private_land(world, registry) -> None:
    await spawn_agent(world, "pm", "square")
    await spawn_agent(world, "buyer", "square")
    await seat_prime_minister(world, "pm")
    give_tile(world, 12, 12, "buyer")

    with pytest.raises(ActorRequirementError):
        await registry.execute(
            world, "pm", "auction_land",
            {"tile_x": 12, "tile_y": 12, "to_owner_id": "pm", "price_sim": 100},
        )


@pytest.mark.asyncio
async def test_set_and_read_a_will(world, registry) -> None:
    await spawn_agent(world, "kenji", "square")
    await spawn_agent(world, "lucia", "square")

    await registry.execute(
        world, "kenji", "set_will",
        {"bequests": [{"heir_id": "lucia", "sim_share": 1, "tiles": [[5, 5]]}]},
    )
    read = await registry.execute(world, "lucia", "read_will", {"agent_id": "kenji"})
    assert read.result["has_will"] is True
    assert read.result["bequests"][0]["heir_id"] == "lucia"


@pytest.mark.asyncio
async def test_a_will_cannot_name_someone_who_does_not_exist(world, registry) -> None:
    await spawn_agent(world, "kenji", "square")
    with pytest.raises(NotFoundError):
        await registry.execute(
            world, "kenji", "set_will", {"bequests": [{"heir_id": "ghost", "sim_share": 1}]}
        )


@pytest.mark.asyncio
async def test_setting_a_will_replaces_the_old_one(world, registry) -> None:
    """An owner must be able to remove an heir they named before."""
    await spawn_agent(world, "kenji", "square")
    await spawn_agent(world, "lucia", "square")
    await spawn_agent(world, "hana", "square")

    await registry.execute(world, "kenji", "set_will", {"bequests": [{"heir_id": "lucia"}]})
    await registry.execute(world, "kenji", "set_will", {"bequests": [{"heir_id": "hana"}]})
    assert [b.heir_id for b in world.wills["kenji"].bequests] == ["hana"]


@pytest.mark.asyncio
async def test_the_will_overrides_the_default_heir(world) -> None:
    """Section 31.3's whole point: Kenji's house goes where Kenji said."""
    await spawn_agent(world, "kenji", "square")
    await spawn_agent(world, "lucia", "square")
    await spawn_agent(world, "hana", "square")
    give_tile(world, 5, 5, "kenji")
    give_tile(world, 6, 6, "kenji")
    await registry_free_will(world, "kenji", "hana", [(5, 5)])

    events = settle_property_estate(world, "kenji", default_heirs=["lucia"])

    assert require_tile(world, 5, 5).owner_id == "hana"
    assert require_tile(world, 6, 6).owner_id == "lucia"
    routes = {(e.payload["tile_x"], e.payload["tile_y"]): e.payload["route"] for e in events}
    assert routes[(5, 5)] == "will" and routes[(6, 6)] == "default"


@pytest.mark.asyncio
async def test_without_a_will_everything_follows_the_default(world) -> None:
    await spawn_agent(world, "kenji", "square")
    await spawn_agent(world, "lucia", "square")
    give_tile(world, 1, 1, "kenji")
    give_tile(world, 2, 2, "kenji")

    settle_property_estate(world, "kenji", default_heirs=["lucia"])
    assert [t.tile_x for t in owned_tiles(world, "lucia")] == [1, 2]


@pytest.mark.asyncio
async def test_an_estate_with_no_heirs_escheats_to_government(world) -> None:
    """The land counterpart of Ledger.settle_inheritance escheating money."""
    await spawn_agent(world, "hermit", "square")
    give_tile(world, 30, 30, "hermit")

    events = settle_property_estate(world, "hermit", default_heirs=[])
    tile = require_tile(world, 30, 30)
    assert tile.owner_id == GOVERNMENT_OWNER_ID
    assert tile.owner_class is OwnerClass.GOVERNMENT
    assert events[0].payload["route"] == "escheat"


@pytest.mark.asyncio
async def test_an_heir_who_died_first_falls_through_to_the_default(world) -> None:
    await spawn_agent(world, "kenji", "square")
    await spawn_agent(world, "lucia", "square")
    await spawn_agent(world, "hana", "square")
    give_tile(world, 8, 8, "kenji")
    await registry_free_will(world, "kenji", "hana", [(8, 8)])
    world.agents["hana"] = world.agents["hana"].model_copy(update={"alive": False})

    settle_property_estate(world, "kenji", default_heirs=["lucia"])
    assert require_tile(world, 8, 8).owner_id == "lucia"


async def registry_free_will(world, owner: str, heir: str, tiles) -> None:
    """Write a will straight into state, for the estate tests - set_will's
    own tool path is covered above and re-running it here would only be
    testing the registry twice."""
    from world.tools.models import Will

    world.wills[owner] = Will(
        agent_id=owner,
        bequests=[Bequest(heir_id=heir, sim_share=1, tiles=list(tiles))],
        updated_at_tick=world.tick(),
    )
