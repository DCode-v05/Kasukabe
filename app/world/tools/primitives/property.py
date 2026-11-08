"""transfer_tile, auction_land, set_will, read_will: The 250 section 31's
land and inheritance primitives.

DESIGN.md section 1 already establishes the map as a real tile grid and
lets government petition to grow it. What was missing is who actually
*owns* any given tile, which is what this module adds:

- **Ownership is per 1x1 unit** (section 31.1). A building, a farm, or a
  large landowner's estate is a contiguous *set* of these records, not a
  new kind of object - which is why nothing here takes a rectangle.
- **Two classes, and no third** (section 31.2). Unclaimed land IS
  government land, so a tile with no record is government-owned by
  reading rather than ownerless by omission; `require_tile` is the one
  place that reading lives, and it is why the map does not need an
  ownership row pre-seeded for all ~34,000 of its tiles at genesis.
- **Freshly expanded land is government revenue waiting to happen**
  (section 31.2). `auction_land` is the government-run sale that turns it
  into treasury income "the same way a tax or a fee already does" - which
  is why the money lands in the treasury account, not a seller's wallet.
- **A will overrides the default** (section 31.3). The automatic
  nearest-living-relative rule still holds absent one; `set_will` is the
  "specified" case, and `settle_property_estate` is what a death handler
  calls to apply it to land the same way `Ledger.settle_inheritance`
  applies it to money.

`transfer_tile` is deliberately the same shape as commerce.py's
`transfer`: validate, call the fallible external system (the ledger)
first, then mutate this package's own state, and publish what happened.
Ordering matters for the same reason it does there - registry.py's
discipline is that a handler must never leave the ledger and world state
disagreeing, and `world.ledger.move()` is not something this module can
roll back.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.ledger.models import Currency, TransactionReason
from world.ledger.money import TREASURY_ACCOUNT_ID
from world.life.models import AgentLifeRecord
from world.tools.errors import ActorRequirementError, NotFoundError
from world.tools.models import Bequest, OwnerClass, TileOwnership, Will
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState, tile_key

# The government's own owner id. The treasury account already exists under
# this id in the ledger (ledger/money.py), so land the town holds and money
# the town holds answer to the same name - an auction that credits
# TREASURY_ACCOUNT_ID is visibly the same entity releasing the tile.
GOVERNMENT_OWNER_ID = TREASURY_ACCOUNT_ID


def require_tile(world: WorldState, x: int, y: int) -> TileOwnership:
    """The owner record for one unit, materialising the government default.

    Section 31.2's "unclaimed land... is a government asset" is a reading,
    not a stored fact: a tile nobody has ever traded has no row, and this
    returns the government-owned record that implies. The record is NOT
    written back here - a read must not mutate state, and pre-seeding a
    row for every tile the map has would put tens of thousands of
    identical records into every checkpoint for no gain.
    """
    existing = world.tile_owners.get(tile_key(x, y))
    if existing is not None:
        return existing
    return TileOwnership(
        tile_x=x, tile_y=y, owner_id=GOVERNMENT_OWNER_ID, owner_class=OwnerClass.GOVERNMENT,
        # GROW-20: whether this ground arrived through a land petition.
        # The flag has existed since section 31.2 asked `auction_land` to
        # scope itself to "newly petitioned/expanded land" and nothing ever
        # set it, because the fact lives on the map and the record is
        # materialised lazily, one tile at a time, long after the strip was
        # added. `WorldState.expanded_rects` is where the answer is kept.
        from_expansion=world.came_from_expansion(x, y),
    )


def owned_tiles(world: WorldState, owner_id: str) -> list[TileOwnership]:
    """Every 1x1 unit one owner holds - their estate, in section 31.1's
    terms. Sorted so a caller listing an estate gets a stable order."""
    return sorted(
        (t for t in world.tile_owners.values() if t.owner_id == owner_id),
        key=lambda t: (t.tile_x, t.tile_y),
    )


class TransferTileParams(BaseModel):
    tile_x: int = Field(ge=0)
    tile_y: int = Field(ge=0)
    to_owner_id: str
    price_sim: int = Field(default=0, ge=0)
    memo: str = ""


async def transfer_tile_handler(
    world: WorldState, agent: AgentLifeRecord, params: TransferTileParams
) -> ToolOutcome:
    """Sell or give one 1x1 unit to another owner.

    A price of zero is a gift, which is why `price_sim` has a floor of
    zero and not one: section 31.2 puts no minimum on what a private
    holder may do with their own land.
    """
    tile = require_tile(world, params.tile_x, params.tile_y)
    if tile.owner_id != agent.agent_id:
        raise ActorRequirementError(
            "transfer_tile",
            [
                f"tile ({params.tile_x}, {params.tile_y}) belongs to {tile.owner_id}, "
                f"not {agent.agent_id}"
            ],
        )
    if params.to_owner_id == agent.agent_id:
        raise ActorRequirementError(
            "transfer_tile", ["cannot transfer a tile to its current owner"]
        )

    # Money first: the ledger is the system this handler cannot undo, so a
    # buyer who cannot pay must fail before the deed changes hands.
    if params.price_sim > 0:
        await world.ledger.move(
            from_account_id=params.to_owner_id,
            to_account_id=agent.agent_id,
            amount=params.price_sim,
            currency=Currency.SIM,
            reason=TransactionReason.TRANSFER,
            tick=world.tick(),
            memo=params.memo or "land sale",
        )

    sold = tile.model_copy(
        update={
            "owner_id": params.to_owner_id,
            "owner_class": OwnerClass.PRIVATE,
            "acquired_at_tick": world.tick(),
        }
    )
    world.tile_owners[tile_key(params.tile_x, params.tile_y)] = sold
    event = WorldEvent(
        type="world.land.transferred",
        tick=world.tick(),
        payload={
            "tile_x": params.tile_x,
            "tile_y": params.tile_y,
            "from_owner_id": agent.agent_id,
            "to_owner_id": params.to_owner_id,
            "price_sim": params.price_sim,
            "involved_agent_ids": [agent.agent_id, params.to_owner_id],
        },
    )
    return ToolOutcome(
        result={"tile": [params.tile_x, params.tile_y], "owner_id": params.to_owner_id},
        events=[event],
    )


class AuctionLandParams(BaseModel):
    tile_x: int = Field(ge=0)
    tile_y: int = Field(ge=0)
    to_owner_id: str
    price_sim: int = Field(gt=0)
    memo: str = ""


async def auction_land_handler(
    world: WorldState, agent: AgentLifeRecord, params: AuctionLandParams
) -> ToolOutcome:
    """Sell one unit of government land to a private buyer.

    Section 31.2: government "decides what happens to expanded land: keep
    it for public use... or auction it to private buyers - a real,
    government-run sale that turns freshly expanded land straight into
    treasury revenue, the same way a tax or a fee already does." The
    proceeds therefore land in the treasury, not in the seat-holder's own
    wallet, and `price_sim` has a floor of one: an auction that raises
    nothing is a grant, and a grant is `transfer_tile` at zero.
    """
    tile = require_tile(world, params.tile_x, params.tile_y)
    if tile.owner_class is not OwnerClass.GOVERNMENT:
        raise ActorRequirementError(
            "auction_land",
            [
                f"tile ({params.tile_x}, {params.tile_y}) is privately held by "
                f"{tile.owner_id} and is not the government's to auction"
            ],
        )
    if params.to_owner_id == GOVERNMENT_OWNER_ID:
        raise ActorRequirementError(
            "auction_land", ["the government cannot auction land to itself"]
        )

    await world.ledger.move(
        from_account_id=params.to_owner_id,
        to_account_id=TREASURY_ACCOUNT_ID,
        amount=params.price_sim,
        currency=Currency.SIM,
        reason=TransactionReason.TRANSFER,
        tick=world.tick(),
        memo=params.memo or "land auction",
    )

    sold = tile.model_copy(
        update={
            "owner_id": params.to_owner_id,
            "owner_class": OwnerClass.PRIVATE,
            "acquired_at_tick": world.tick(),
        }
    )
    world.tile_owners[tile_key(params.tile_x, params.tile_y)] = sold
    event = WorldEvent(
        type="world.land.auctioned",
        tick=world.tick(),
        payload={
            "tile_x": params.tile_x,
            "tile_y": params.tile_y,
            "to_owner_id": params.to_owner_id,
            "price_sim": params.price_sim,
            "sold_by": agent.agent_id,
            "from_expansion": tile.from_expansion,
            "involved_agent_ids": [agent.agent_id, params.to_owner_id],
        },
    )
    return ToolOutcome(
        result={
            "tile": [params.tile_x, params.tile_y],
            "owner_id": params.to_owner_id,
            "treasury_gain_sim": params.price_sim,
        },
        events=[event],
    )


class SetWillParams(BaseModel):
    """Replaces the caller's will outright rather than appending to it -
    a will is a statement of current wishes, and merging two of them would
    leave an owner unable to remove an heir they had named before."""

    bequests: list[Bequest] = Field(default_factory=list)


async def set_will_handler(
    world: WorldState, agent: AgentLifeRecord, params: SetWillParams
) -> ToolOutcome:
    """Record what this owner wants done with their estate (section 31.3).

    Naming an heir who is not currently alive is rejected here rather than
    at death: a will nobody can execute is worth catching while its author
    is still around to fix it.
    """
    for bequest in params.bequests:
        heir = world.agents.get(bequest.heir_id)
        if heir is None:
            raise NotFoundError("agent", bequest.heir_id)
        if not heir.alive:
            raise ActorRequirementError(
                "set_will", [f"cannot name {bequest.heir_id}, who is not alive, as heir"]
            )
        # C1: you can only leave what is yours. Checked while the author is
        # still here to fix it, for the same reason the heir check is.
        for tool_name in bequest.tools:
            if tool_name not in world.custom_tools:
                raise NotFoundError(
                    "invented tool", tool_name, sorted(world.custom_tools)
                )
            if world.tool_owner(tool_name) != agent.agent_id:
                raise ActorRequirementError(
                    "set_will",
                    [
                        f"{tool_name} is not yours to leave - "
                        f"{world.tool_owner(tool_name)} owns it."
                    ],
                )
        for skill in bequest.skills:
            if not world.skills.get(agent.agent_id, {}).get(skill):
                raise ActorRequirementError(
                    "set_will", [f"you do not hold the skill {skill!r}."]
                )

    will = Will(
        agent_id=agent.agent_id,
        bequests=list(params.bequests),
        updated_at_tick=world.tick(),
    )
    world.wills[agent.agent_id] = will
    event = WorldEvent(
        type="agent.will.recorded",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "heir_ids": [b.heir_id for b in will.bequests],
            "tiles_bequeathed": sum(len(b.tiles) for b in will.bequests),
            "tools_bequeathed": sum(len(b.tools) for b in will.bequests),
            "skills_bequeathed": sum(len(b.skills) for b in will.bequests),
        },
    )
    return ToolOutcome(result={"heirs": len(will.bequests)}, events=[event])


class ReadWillParams(BaseModel):
    agent_id: str | None = None


async def read_will_handler(
    world: WorldState, agent: AgentLifeRecord, params: ReadWillParams
) -> ToolOutcome:
    """Read a will back. Defaults to the caller's own.

    Readable by anyone, deliberately: DESIGN.md section 2's "complete
    causal record" already makes every tool call subpoenable, and a town
    where an heir cannot find out they are an heir cannot act on it.
    """
    subject = params.agent_id or agent.agent_id
    will = world.wills.get(subject)
    if will is None:
        return ToolOutcome(
            result={"agent_id": subject, "has_will": False, "bequests": []}, events=[]
        )
    return ToolOutcome(
        result={
            "agent_id": subject,
            "has_will": True,
            "bequests": [b.model_dump(mode="json") for b in will.bequests],
        },
        events=[],
    )


def settle_property_estate(
    world: WorldState, deceased_agent_id: str, default_heirs: list[str]
) -> list[WorldEvent]:
    """Move a dead owner's land on, will first, default second.

    Section 31.3: "if he'd owned his own home, that transfers too, by the
    identical rule: automatically to Lucia absent a will, or exactly as
    Kenji specified if he'd set one." So:

    - Tiles a will names explicitly go to the heir it names.
    - Anything the will did not mention - and everything, when there is no
      will - is divided round-robin across `default_heirs`, which is the
      caller's nearest-living-relative list.
    - With no heirs at all the estate escheats to government, exactly as
      `Ledger.settle_inheritance` escheats money to the treasury.

    A pure-ish function over WorldState rather than a tool: nobody *calls*
    their own death, so this has no actor and belongs to whatever death
    handler is running, the same way `Ledger.settle_inheritance` does.
    """
    estate = owned_tiles(world, deceased_agent_id)
    if not estate:
        return []

    will = world.wills.get(deceased_agent_id)
    willed: dict[str, str] = {}
    if will is not None:
        for bequest in will.bequests:
            if not world.agents.get(bequest.heir_id) or not world.agents[bequest.heir_id].alive:
                continue                       # a named heir who died first falls through
            for tx, ty in bequest.tiles:
                willed[tile_key(tx, ty)] = bequest.heir_id

    living = [h for h in default_heirs if world.agents.get(h) and world.agents[h].alive]
    events: list[WorldEvent] = []
    fallback_index = 0
    for tile in estate:
        key = tile_key(tile.tile_x, tile.tile_y)
        if key in willed:
            heir_id, route = willed[key], "will"
            owner_class = OwnerClass.PRIVATE
        elif living:
            heir_id = living[fallback_index % len(living)]
            fallback_index += 1
            route, owner_class = "default", OwnerClass.PRIVATE
        else:
            heir_id, route = GOVERNMENT_OWNER_ID, "escheat"
            owner_class = OwnerClass.GOVERNMENT
        world.tile_owners[key] = tile.model_copy(
            update={
                "owner_id": heir_id,
                "owner_class": owner_class,
                "acquired_at_tick": world.tick(),
            }
        )
        events.append(
            WorldEvent(
                type="world.land.inherited",
                tick=world.tick(),
                payload={
                    "tile_x": tile.tile_x,
                    "tile_y": tile.tile_y,
                    "from_owner_id": deceased_agent_id,
                    "to_owner_id": heir_id,
                    "route": route,
                },
            )
        )
    return events


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="transfer_tile",
            description="Sell or give one 1x1 unit of land you own to someone else.",
            params_model=TransferTileParams,
            handler=transfer_tile_handler,
            namespace=ToolNamespace.PUBLIC,
            # No location gate: a deed changes hands wherever the two
            # parties are, the same as commerce.py's `transfer`.
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="auction_land",
            description=(
                "Auction one unit of government land to a private buyer. "
                "The proceeds go to the treasury."
            ),
            params_model=AuctionLandParams,
            handler=auction_land_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            # Only a seat-holder may sell the town's own land.
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="set_will",
            description="Record who inherits your money and your land when you die.",
            params_model=SetWillParams,
            handler=set_will_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="read_will",
            description="Read a will back - your own, or someone else's.",
            params_model=ReadWillParams,
            handler=read_will_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
