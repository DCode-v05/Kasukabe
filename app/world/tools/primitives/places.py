"""Place discovery: how an agent learns what Kasukabe contains.

Before this module the town had 27 tools and none of them answered "where
can I go?". An agent could call `move` with a destination_location_id, but
nothing ever told it which destinations exist, so the only places it could
name were ones it had already been told about in its persona. An agent
cannot build a life in a world it cannot perceive, and it certainly cannot
find the government store to buy the energy it needs to keep thinking.

Three tools, all knowledge rather than physical action:

- `list_places`   what exists in town, optionally filtered
- `describe_place` what one place is, who is there, what can be done there
- `look_around`   what is at and near the agent's current position

DESIGN.md's rule that the tool registry IS the physics still holds: none
of these mutate the world. They are perception, and perception of a public
town is not gated by location - an agent may recall that a temple exists
without standing in it. Actually going there is `move`, which is gated,
costs energy, and takes real ticks to walk.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from world.life.models import AgentLifeRecord
from world.tools.inventory import holdings_line
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState

# What each genesis sector is FOR, in the agent's terms. Keyed by a
# lowercase substring of the sector name so a renamed or extended map still
# matches ("Hobbs Cafe" -> cafe). Anything unmatched is still listed, just
# without a purpose line - never hidden, because a place an agent cannot
# see is a place it can never choose.
PLACE_PURPOSES: dict[str, str] = {
    "cafe": "Buy and sell food and drink. People gather and talk here.",
    "bar": "Drink, talk, and meet people.",
    "store": "Buy and sell goods.",
    "grocery": "Buy food and raw ingredients.",
    "pharmacy": "Buy treatment for short illnesses.",
    "supply": "Buy tools, materials and parts.",
    "park": "Open public ground. Rest, meet, gather.",
    "college": "Study and teach. Skills can be taught here.",
    "dorm": "Student housing.",
    "house": "A home. Households sleep and eat here.",
    "apartment": "A home. Households sleep and eat here.",
    "government": (
        "The government complex. Register a business, pay tax, petition, "
        "vote, and buy energy credit with sim currency at the government store."
    ),
    "temple": "Pray, marry, and hold ceremonies.",
    "farm": "Grow and harvest crops.",
    "ore": "Mine metals.",
    "mine": "Mine metals.",
    "forest": "Fell timber.",
    "office": "Work at a desk job.",
    "street": "Public road. Walk through, meet people in passing.",
    "pub": "Drink, talk, and meet people.",
    "co-living": "A shared home. Households sleep and eat here.",
}


# The workshops, keyed by their exact location id.
#
# Separate from PLACE_PURPOSES because that one matches on substrings, and
# substrings get these wrong in three different ways: "sawmill" contains
# "mill", "loom_house" contains "house", and "patent_office" contains
# "office". The first pass at this told residents the sawmill grinds grain
# and the patent office is a desk job. An exact id wins outright - the same
# rule the movement fold holds to for the same reason.
WORKSHOP_PURPOSES: dict[str, str] = {
    "mill": "Grind grain into flour. `craft` works here.",
    "kitchen": "Cook raw food into meals. `cook` works here.",
    "sawmill": "Saw felled timber into planks. `craft` works here.",
    "smithy": "Smelt ore and forge metal goods. `craft` works here.",
    "carpentry": "Build furniture and structures from planks. `craft` works here.",
    "loom_house": "Spin thread and weave cloth. `craft` works here.",
    "patent_office": "File a tool of your own invention. `define_tool` works here.",
}


def purpose_for(name: str) -> str:
    lowered = name.lower()
    exact = WORKSHOP_PURPOSES.get(lowered)
    if exact is not None:
        return exact
    for key, purpose in PLACE_PURPOSES.items():
        if key in lowered:
            return purpose
    return ""


class ListPlacesParams(BaseModel):
    query: str = Field(
        default="",
        description="Optional filter, matched against place names (e.g. 'cafe', 'farm'). Empty lists everything.",
    )


class DescribePlaceParams(BaseModel):
    place_id: str = Field(description="The place to describe, as returned by list_places.")


class LookAroundParams(BaseModel):
    pass


def known_places(world: WorldState) -> list[dict[str, str]]:
    """Every named sector on the map, every resource zone, and every place
    the town's own tools mint that no sector already covers.

    That third group is the one this function used to leave out, and it is
    where the town actually works: mill, kitchen, sawmill, smithy,
    carpentry, loom_house, patent_office. They have gates on them and no
    map sector of their own, so a resident calling `list_places` got forty
    places and not one workshop - while `craft` and `cook` refused it for
    standing somewhere else.

    Measured before this was fixed: fifteen of nineteen refusals in one run
    were a production tool naming a workshop the agent had no way to find.
    Marta Thorne was refused in her own bakery for not being in a kitchen,
    and `tools_registry` had never held a row, because `define_tool` is
    gated on a patent office nothing had ever mentioned.

    Covered-by-a-sector is tested with satisfies_gate rather than string
    equality, so `temple` and `government_complex` - which really are other
    names for sectors the map draws - are not listed twice. If a sector is
    ever added that covers a workshop, that workshop drops out of this list
    on its own.
    """
    places: list[dict[str, str]] = []
    movement = world.movement
    world_map = getattr(movement, "world_map", None) if movement is not None else None
    if world_map is not None:
        for sector in world_map.sectors.values():
            name = getattr(sector, "name", "")
            if not name:
                continue
            places.append({"place_id": name, "kind": "sector", "purpose": purpose_for(name)})
    for zone_id in world.resource_zones:
        name = str(zone_id)
        places.append({"place_id": name, "kind": "resource", "purpose": purpose_for(name)})

    from world.tools.place_aliases import known_tool_locations, satisfies_gate

    covered = [p["place_id"] for p in places]
    # Workshops a business provides are real ground: the shop is listed
    # below and standing in it satisfies the gate (TOOL-02). Listing the
    # bare id as well would offer a second, emptier door to the same trade.
    from world.tools.place_aliases import _business_rows

    provided: set[str] = set()
    for business in _business_rows(world):
        provided |= business.provides_workshops

    for location in sorted(known_tool_locations()):
        # Per-variety families (farmland:wheat, ore_outcrop:copper) are
        # already listed as resource zones above; the bare workshop ids are
        # the ones with nothing standing for them.
        if any(satisfies_gate(place, location) for place in covered):
            continue
        # TOOL-02/GROW-28: a workshop nobody provides is a string, not a
        # place. `move("mill")` took the opaque-jump fallback into a room
        # with no tiles, no people and no way for a customer to find it, and
        # this list was where residents were told it existed. Run 23's
        # `location:mill` refusals came from exactly this invitation: the
        # miller walked to the phantom while her own mill stood elsewhere.
        #
        # Kept out of the list, NOT out of the gate: a recipe still names
        # `mill`, and a town that builds one (GROW-05) will satisfy it the
        # same way a business does.
        if location in provided:
            continue
        places.append({
            "place_id": location, "kind": "workshop", "purpose": purpose_for(location),
        })

    # The shops. A business's premises are where its owner stands all day
    # and where `buy` and `sell` are gated, and this list - the only answer
    # a resident gets to "where can I go?" - never mentioned one.
    #
    # Run 19: Junia Vance, grocery clerk, spent fifty-nine ticks at
    # `business:b_grocery`, a room no customer could have found by asking.
    # Every shop in every run has been empty, and this is the half of the
    # reason that lives here: nobody could be told where they are.
    #
    # Listed under the name people call them by, with the id as the place,
    # and skipped when a sector already stands for the same ground so no
    # shop is listed twice.
    for business_id, business in sorted(world.businesses.items()):
        location_id = getattr(business, "location_id", None) or f"business:{business_id}"
        if any(satisfies_gate(place, location_id) for place in [p["place_id"] for p in places]):
            continue
        name = getattr(business, "name", None) or business_id
        places.append({
            "place_id": location_id,
            "kind": "business",
            "purpose": f"{name} - a business you can buy from and sell to.",
        })

    places.sort(key=lambda p: p["place_id"])
    return places


async def list_places_handler(
    world: WorldState, agent: AgentLifeRecord, params: ListPlacesParams
) -> ToolOutcome:
    places = known_places(world)
    if params.query:
        needle = params.query.lower()
        places = [p for p in places if needle in p["place_id"].lower()]
    here = world.locations.location_of(agent.agent_id)
    return ToolOutcome(
        result={"you_are_at": here, "place_count": len(places), "places": places},
        events=[],
    )


# How many offers the town-wide board carries. Every one of them is paid
# for on every call of every turn (there is no prompt caching), and fifty
# residents can post hundreds, so the board is the cheapest slice that
# still answers "where do I get food": in-stock first, then by item.
MARKET_BOARD_LIMIT = 12


def market_offers(world: WorldState, limit: int = MARKET_BOARD_LIMIT) -> list[dict]:
    """Everything for sale ANYWHERE in town, with the stock behind it.

    The 50-resident run refused 140 of 178 `buy` calls, 87 of them for want
    of a posted price, because a resident could only ever see the prices of
    whatever it was standing next to (`describe_prices`, below, is
    place-scoped by design and stays that way). Asked to find food, it
    asked whoever was beside it and was told that person sells no potatoes.

    Stock is carried because the third failure mode is a price over an
    empty shelf - `buy` accepts the post and then refuses for want of
    inventory, which is what a resident complained about in-world at tick
    36. An offer a resident cannot take up should look different from one
    it can, before the call is paid for.

    Bids as well as asks: `sell` failed 32 times for want of a posted bid,
    which is the same blindness in the other direction.

    Ordered so a short list is a useful list - in stock first, then by
    item, then cheapest - and bounded, because this board is re-read on
    every call of every turn.
    """
    from world.tools.primitives.commerce import is_minded, shop_is_open_for

    offers: list[dict] = []
    for holder_id, board in (world.price_board or {}).items():
        business = world.businesses.get(holder_id)
        if business is not None:
            who, where = business.name, business.location_id
        else:
            who = world.agent_names.get(holder_id, holder_id)
            where = world.location_of(holder_id)
        for item, post in board.items():
            if post.ask_sim is None and post.bid_sim is None:
                continue
            offers.append({
                "item": item,
                "seller_id": holder_id,
                "seller": who,
                "where": where,
                "ask_sim": post.ask_sim,
                "bid_sim": post.bid_sim,
                # What the seller actually holds. A bid needs none - a buyer
                # is not selling anything - so it is only meaningful for asks.
                "stock": world.inventory.quantity(holder_id, item),
                # Whether this can actually be bought right now - which is
                # what a resident reading the board needs, and no longer the
                # same question as "is somebody standing there".
                #
                # A shop with a posted price sells whoever is minding it
                # (`commerce.shop_is_open_for`), so reporting it as
                # unattended would send residents away from counters that
                # would serve them - the 87 refusals again, by the opposite
                # route. A PERSON selling from their own hands still has to
                # be present and spoken to.
                "minded": (
                    shop_is_open_for(world, holder_id, item)
                    or is_minded(world, holder_id)
                ),
            })
    offers.sort(key=lambda o: (
        # Takeable first: stocked AND somebody there to agree the sale.
        0 if o["minded"] and (o["ask_sim"] is None or o["stock"] > 0) else 1,
        o["item"],
        o["ask_sim"] if o["ask_sim"] is not None else 10**9,
    ))
    return offers[:limit]


def describe_prices(world: WorldState, location_id: str | None) -> dict[str, list[str]]:
    """Every standing offer visible from this place, as sentences.

    Grouped by holder and rendered in words rather than as nested dicts,
    for the same reason speech is: a model that has to reverse-engineer a
    payload before it can consider acting mostly does not act. The
    addendum's shape - "Prices here: bread 30 to buy, flour bought at 6."

    A business's board hangs at its premises whether the owner is standing
    there or not; a person's travels with them.
    """
    out: dict[str, list[str]] = {}
    for holder_id, board in sorted(world.prices_visible_at(location_id).items()):
        business = world.businesses.get(holder_id)
        who = business.name if business is not None else world.agent_names.get(
            holder_id, holder_id
        )
        lines: list[str] = []
        for item, post in sorted(board.items()):
            if post.ask_sim is not None and post.bid_sim is not None:
                lines.append(f"{item} {post.ask_sim} to buy, bought at {post.bid_sim}")
            elif post.ask_sim is not None:
                lines.append(f"{item} {post.ask_sim} to buy")
            elif post.bid_sim is not None:
                lines.append(f"{item} bought at {post.bid_sim}")
        if lines:
            out[f"{who} ({holder_id})"] = lines
    return out


async def describe_place_handler(
    world: WorldState, agent: AgentLifeRecord, params: DescribePlaceParams
) -> ToolOutcome:
    match = next(
        (p for p in known_places(world) if p["place_id"].lower() == params.place_id.lower()),
        None,
    )
    if match is None:
        # Not an error: an agent guessing at a name it half-remembers should
        # be told what does exist rather than punished with an exception.
        return ToolOutcome(
            result={
                "found": False,
                "message": f"No place named {params.place_id!r} in Kasukabe.",
                "did_you_mean": [
                    p["place_id"]
                    for p in known_places(world)
                    if params.place_id.lower()[:4] in p["place_id"].lower()
                ][:5],
            },
            events=[],
        )
    occupants = sorted(world.locations.agents_at(match["place_id"]))
    businesses = [
        {"business_id": bid, "name": getattr(b, "name", bid)}
        for bid, b in world.businesses.items()
        if getattr(b, "location_id", None) == match["place_id"]
    ]
    return ToolOutcome(
        result={
            "found": True,
            "place_id": match["place_id"],
            "kind": match["kind"],
            "purpose": match["purpose"],
            "people_here_now": occupants,
            "businesses_here": businesses,
            # A3: so a customer can compare before walking. A price a
            # resident can only discover by making the journey is a price
            # that cannot be shopped around, and E2 asks whether the spread
            # between shops narrows.
            "prices_there": describe_prices(world, match["place_id"]),
        },
        events=[],
    )


async def look_around_handler(
    world: WorldState, agent: AgentLifeRecord, params: LookAroundParams
) -> ToolOutcome:
    here = world.locations.location_of(agent.agent_id)
    others = sorted(a for a in world.locations.agents_at(here) if a != agent.agent_id) if here else []
    tile = world.agent_tiles.get(agent.agent_id)

    # What is being talked about here, reported the way people_here is.
    #
    # Runs 18 and 19 both ended with start_conversation registered, priced,
    # served to every engine and never called once. This result is why: it
    # named the people in the room and said nothing about the conversation
    # nine of them were in the middle of. No id to answer into, and nothing
    # anywhere in a resident's view referring to convening one. A tool that
    # nothing you can see refers to is not a tool you can choose.
    #
    # Facts only - who is talking, about what, how long it has run. Nobody
    # is told to join.
    conversations = [
        {
            "conversation_id": record["conversation_id"],
            "topic": record["topic"] or "(nobody named a topic)",
            "turns": record["turns"],
            "participants": [
                world.agent_names.get(other, other) for other in record["participants"]
            ],
        }
        for record in world.open_conversations_at(here, world.tick())
    ]

    # A3. Nothing is for sale until somebody posts a price, so "what is
    # priced where I stand" is a fact a resident cannot get any other way -
    # and `buy`'s refusal points here by name.
    prices = describe_prices(world, here)
    result: dict[str, Any] = {
        "location_id": here,
        "tile": {"x": tile[0], "y": tile[1]} if tile else None,
        "purpose": purpose_for(here or ""),
        "people_here": others,
        "conversations_here": conversations,
        "prices_here": prices or "Nothing is priced here.",
        # TOOL-04. What you are holding, in the same breath as what is for
        # sale beside you - the two facts a decision to buy is made from.
        # Free, and here rather than behind its own tool: a resident too
        # poor to act must not also be too poor to find out what it has.
        "you_are_holding": holdings_line(world, agent.agent_id)
        or "nothing you could give, eat, or sell",
        "places_you_could_go": [p["place_id"] for p in known_places(world)][:40],
    }
    if conversations:
        result["note"] = (
            "To answer into one of these, pass its conversation_id to say - "
            "otherwise your remark joins whichever is nearest and a room "
            "holding two conversations muddles them."
        )
    elif len(others) >= 2:
        # Standing with two or more people and nothing being said is the one
        # moment convening is worth anything, and `people_here` alone cannot
        # tell a resident that.
        result["note"] = (
            f"{len(others)} people are here and nobody is talking. "
            "start_conversation calls them together about something, and "
            "keeps what follows in one thread."
        )
    return ToolOutcome(result=result, events=[])


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="list_places",
            description=(
                # TOOL-26. The second sentence used to hand the resident a
                # plan - which errands to run and in what order - which is
                # the Addendum's rule 2 exactly: a tool blurb states the
                # physics and the prices and stops there. What a place is
                # for is already in the first sentence and in the rows this
                # returns; what to go and DO about it is the resident's.
                "List the places in Kasukabe you could travel to, with what each is for. "
                "A tool with a location gate can only be called from the place its gate names."
            ),
            params_model=ListPlacesParams,
            handler=list_places_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="describe_place",
            description=(
                "Learn what a specific place is for, who is standing there now, "
                "and which businesses operate there."
            ),
            params_model=DescribePlaceParams,
            handler=describe_place_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="look_around",
            description=(
                "Look at where you are standing right now: the place, who is with you, "
                "and where you could go from here."
            ),
            params_model=LookAroundParams,
            handler=look_around_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
