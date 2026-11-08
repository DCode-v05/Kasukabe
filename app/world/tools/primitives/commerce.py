"""buy, sell, transfer, store_item, withdraw_item: DESIGN.md section 2's
trade and storage primitives.

buy/sell move sim-currency through the business's own ledger account
(opened by `world.ledger.Ledger.register_business` - see civic.py's
`register_business`, which is this package's only writer of a `Business`
row) and move the traded item through the SAME business's inventory
bucket (`world.tools.inventory`, keyed by business_id exactly like an
agent_id - Inventory does not distinguish the two). Both are gated to the
business's own location_id: DESIGN.md's "at the shop to buy" example is
literally this primitive.

store_item/withdraw_item move an item between an agent's personal
inventory and a named Storage container (models.py). A Storage is
created the first time store_item names an id that doesn't exist yet, at
wherever the caller currently stands - no separate "register storage"
primitive exists in DESIGN.md's list, so first-use registration is the
simplest physics that doesn't invent one. `steal`'s "unattended
cash/inventory" targets exactly these Storage containers - see
primitives/crime.py.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.ledger.models import Currency, TransactionReason
from world.life.models import AgentLifeRecord
from world.tools.errors import (
    ActorRequirementError,
    InsufficientInventoryError,
    LocationGateError,
    NotFoundError,
    ToolValidationError,
)
from world.tools.models import PricePost, Storage
from world.tools.place_aliases import resolve_business, satisfies_gate
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState


def require_business(world: WorldState, business_id: str):
    business = resolve_business(world, business_id)
    if business is None:
        # Read off the world, so a business registered mid-run appears here
        # the moment it exists.
        raise NotFoundError("business", business_id, list(world.businesses))
    return business


def _holder_name(world: WorldState, holder_id: str) -> str:
    business = resolve_business(world, holder_id)
    if business is not None:
        return business.name
    return world.agent_names.get(holder_id, holder_id)


def _resolve_holder(
    world: WorldState, tool_name: str, holder_id: str
) -> tuple[object, str, str | None]:
    """Who is on the other side of this trade, and may the caller reach them.

    A4 lets `seller_id`/`buyer_id` be a business OR a co-located person, so
    the gate cannot be a declared `location_gate` any more - a business
    gates on its premises, a person on standing with them, and which of the
    two applies is not known until the id is looked up. Same reason `trade`
    checks co-location in its handler.
    """
    business = resolve_business(world, holder_id)
    if business is not None:
        return business, "business", business.location_id

    if holder_id not in world.agents:
        # People by name as well as number: a resident reads names in every
        # perception, and a list of `n07, n13, n18` told it nothing it could
        # use (runs 28 and 28b).
        raise NotFoundError(
            "seller or buyer",
            holder_id,
            [
                *sorted(world.businesses),
                *(
                    f"{world.agent_names.get(agent_id, agent_id)} ({agent_id})"
                    for agent_id in sorted(world.agents)
                ),
            ],
        )
    return world.agents[holder_id], "person", world.location_of(holder_id)


def _canonical_holder_id(world: WorldState, holder_id: str) -> str:
    """The key the town files this holder under, not the one it was called.

    Folding at `_resolve_holder` alone is not enough, and the test that
    found this says why. `buy(seller_id="business:b_bakery")` resolved the
    shop correctly and then looked its price board up under the resident's
    spelling - `world.posted("business:b_bakery", "bread")` - which is not
    where `post_price` filed it. The board, the stock, the ledger account
    and the event row are all keyed on the bare `business_id`, so the
    resolved id has to reach every one of them or the fold only moves the
    refusal one line down.
    """
    business = resolve_business(world, holder_id)
    return business.business_id if business is not None else holder_id


def _require_reachable(
    world: WorldState, tool_name: str, agent_id: str, holder_id: str
) -> str | None:
    """The caller has to be standing in the shop, or with the person."""
    _, kind, there = _resolve_holder(world, tool_name, holder_id)
    here = world.location_of(agent_id)
    if not satisfies_gate(there, here or ""):
        raise LocationGateError(tool_name, there, here)
    return here


# Whether a sale needs the two parties to be talking. On by default; off only
# for a control arm measuring what the rule costs. Recorded in the manifest.
SALES_NEED_CONVERSATION = os.environ.get(
    "KASUKABE_SALES_NEED_CONVERSATION", "1"
).strip().lower() not in {"0", "false", "no", "off"}


def _is_world_run(world: WorldState, actor_id: str) -> bool:
    """Whether this actor is one of the world's own scripts, not a resident.

    Two signals, because both are real: the runner names control owners
    `control:<business>` and registers them in `world.control_actors`. A
    scripted actor cannot talk, so a rule that needs a conversation of it
    would stop the experiment's baseline without a word.
    """
    return str(actor_id).startswith("control:") or actor_id in (world.control_actors or {})


def _minders_of(world: WorldState, holder_id: str) -> "list[str] | None":
    """Who can agree a sale for this holder, or None if the world runs it.

    A person sells for themself. A shop is minded by the people who run it -
    its owner and its employees. The world's own scripted control shops
    (owner `control:*`, see `run_genesis`'s control bakery) are the
    experiment's baseline with no resident to talk to, so they are treated
    as always minded - shutting them would silently remove the control.
    """
    business = world.businesses.get(holder_id)
    if business is None:
        return [holder_id]
    if _is_world_run(world, business.owner_agent_id):
        return None
    return [business.owner_agent_id, *sorted(business.employee_agent_ids)]


def is_minded(world: WorldState, holder_id: str) -> bool:
    """Whether anybody who can sell for this holder is at its place now."""
    minders = _minders_of(world, holder_id)
    if minders is None:
        return True
    business = world.businesses.get(holder_id)
    if business is None:
        return world.location_of(holder_id) is not None
    return any(
        satisfies_gate(business.location_id, world.location_of(m) or "") for m in minders
    )


def shop_is_open_for(world: WorldState, holder_id: str, item: "str | None") -> bool:
    """Whether this holder is a SHOP with a published price for this item.

    A price board is a standing offer. `post_price` is the shopkeeper saying,
    in public and in advance, "this is what I sell it for" - so a customer who
    pays that price is not buying from somebody who never agreed. That is the
    whole of what `_require_dealing` exists to prevent, and a posted price
    satisfies it a different way.

    Narrow on purpose, and each narrowing is load-bearing:

      * a BUSINESS, because a shop is a thing that opens to the public and a
        person is not. Two residents in a room who have never spoken still
        cannot trade, which is the case the 2026-09-21 rule reproduced;
      * this ITEM, not any item, so a shop is open for what it has priced and
        shut for everything else;
      * and `post_price` remains an act a resident performs, standing in the
        shop. Nobody is opened for business without having said so.
    """
    if item is None or holder_id not in world.businesses:
        return False
    post = world.posted(holder_id, item)
    return post is not None and (post.ask_sim is not None or post.bid_sim is not None)


def _require_dealing(
    world: WorldState,
    tool_name: str,
    agent_id: str,
    holder_id: str,
    *,
    item: "str | None" = None,
) -> "str | None":
    """A sale is agreed between two people who are talking to each other.

    `buy`, `sell` and `trade` used to check only that both parties stood in
    the same place. Reproduced on 2026-09-21: two residents in one room who
    had never spoken - `buy` and `sell` both went through; a shop whose owner
    and staff were all elsewhere - a buyer walked in and bought from it.
    Every price in that economy was paid to somebody who never agreed to it.

    So the parties must share an OPEN conversation where they stand. For a
    shop, somebody who runs it must be there and in that conversation with
    the buyer. `say` addressed to somebody puts both of you on the roster
    (`primitives/social.py`), so the honest path costs one remark.

    Returns the conversation the deal is agreed in, so the settlement can be
    recorded against it (A2A: a session resolves into its outcome). None
    when no conversation was required - the rule switched off, a holder
    dealing with itself, the world's own actors.
    """
    if not SALES_NEED_CONVERSATION or holder_id == agent_id:
        return None
    # A shop that has posted a price is open, whoever is standing in it.
    #
    # election30: buyers were turned away because "nobody is minding Kasukabe
    # Cafe right now", or because they were not already talking to a seller
    # who was not there to talk to. 9 of 47 sell calls landed, and the town
    # ate raw flour beside shelves it could not buy from. The shop had
    # already named its price; there was nobody there to agree it twice.
    #
    # This restores the spec's `test_absent_owner_still_sells_at_the_posted_
    # price` and its reason - "a shop that only trades while its owner is
    # standing in it is a person, not a business" - WITHOUT giving back the
    # defect that replaced it, because the posted price is the agreement.
    if shop_is_open_for(world, holder_id, item):
        return None
    # The world's own scripted actors (`control:*`) deal on EITHER side - the
    # control bakery sells bread and also buys flour from residents - and
    # cannot talk. Requiring a conversation of them would stop the
    # experiment's baseline restocking without a word.
    if _is_world_run(world, agent_id):
        return None
    minders = _minders_of(world, holder_id)
    if minders is None:
        return None
    here = world.location_of(agent_id)
    present = [m for m in minders if satisfies_gate(world.location_of(m) or "", here or "")]
    # People are written as their agent ID, not their name: the refusal is
    # passed through `names.name_mentions`, which rewrites every id into
    # "Name (id)" - and a resident needs the id to address them. A bare name
    # would leave it knowing who, but not how to say so.
    who = _holder_name(world, holder_id)
    if not present:
        runner = minders[0] if minders else "its owner"
        where = world.location_of(minders[0]) if minders else None
        raise ActorRequirementError(tool_name, [
            f"nobody is minding {who} right now - {runner} is "
            f"{'at ' + where if where else 'away'}. A shop sells when somebody "
            "who runs it is there to agree the sale."
        ])
    talking = [
        record for record in world.open_conversations_at(here, world.tick())
        if agent_id in record["participants"]
        and any(m in record["participants"] for m in present)
    ]
    if not talking:
        name = present[0]
        raise ActorRequirementError(tool_name, [
            f"you and {name} are not in a conversation. A sale is agreed between "
            f"two people who are talking - speak to {name} first."
        ])
    return talking[0]["conversation_id"]


def _settled_in_conversation(world: WorldState, conversation_id: "str | None", deal: dict) -> None:
    """Record a completed deal against the conversation it was agreed in.

    A2A_CONFORMANCE §2, "a session's outcome": a conversation closed and
    nothing said what it had settled - the price agreed, the trade it led
    to - so "does what is agreed in speech become an act" (N6) could only be
    answered by matching ticks by hand. Called only after the money and the
    goods have both moved, so a deal on the record is a deal that happened.
    """
    if conversation_id:
        world.record_conversation_deal(conversation_id, {**deal, "tick": world.tick()})


async def _require_price_in_band(
    world: WorldState, tool_name: str, item: str, unit_price_sim: float
) -> None:
    """GOV-4. The government's price caps and floors, read at dispatch.

    One rule for posting and for settling (`GovernmentPolicy.price_band_refusal`),
    so a sign in the window and the till cannot disagree. Checked BEFORE the
    conversation: talking to the seller cannot make an unlawful price lawful,
    and telling a resident to go and speak first would buy it a wasted call.
    """
    policy = await world.charter.store.get_policy()
    if not (policy.price_caps or policy.price_floors):
        return
    why = policy.price_band_refusal(item, unit_price_sim)
    if why:
        band = []
        if item in policy.price_floors:
            band.append(f"at least {policy.price_floors[item]}")
        if item in policy.price_caps:
            band.append(f"at most {policy.price_caps[item]}")
        raise ToolValidationError(tool_name, [
            f"{why}. A lawful price for {item} is {' and '.join(band)} sim a unit; "
            "a seat at the government complex can change that with `set_price_control`."
        ])


def _shop_that_sells_for(world: WorldState, person_id: str, buyer_id: str, params) -> object | None:
    """The shop a named shopkeeper sells through, when the person cannot sell.

    Runs 28 and 28b's commonest failed purchase, 18 times in run 28b:
    `buy(seller_id="n07", item="bread")` -> "n07 needs 2 bread, has 0". n07
    is Marta Thorne; her hands were empty and her bakery's shelf held bread.
    A resident names the baker, not the bakery's key.

    Only when the person has no price or not the stock of their own, and
    only for a business they own or staff that prices or holds the item.
    Standing in that shop, the purchase is from the shop. Standing anywhere
    else, the refusal names the shop and where it is - facts, not a walk.
    """
    own = world.posted(person_id, params.item)
    if own is not None and own.ask_sim is not None and (
        world.inventory.quantity(person_id, params.item) >= params.quantity
    ):
        return None
    for business in world.businesses.values():
        if person_id != business.owner_agent_id and person_id not in business.employee_agent_ids:
            continue
        post = world.posted(business.business_id, params.item)
        if (post is None or post.ask_sim is None) and (
            world.inventory.quantity(business.business_id, params.item) <= 0
        ):
            continue
        if satisfies_gate(business.location_id, world.location_of(buyer_id) or ""):
            return business
        from world.tools.place_aliases import display_place

        person = _holder_name(world, person_id)
        raise NotFoundError(
            "posted price",
            f"{params.item} at {person}",
            [business.business_id],
            hint=(
                f"{person} holds no {params.item} to sell and has posted no price "
                f"for it. {business.name}, which {person} runs, sells it at "
                f"{display_place(business.location_id)}."
            ),
        )
    return None


class BuyParams(BaseModel):
    seller_id: str = Field(
        description="A shop, or a person beside you - by name or by id."
    )
    item: str
    quantity: int = Field(gt=0)


def _sold_elsewhere(world: WorldState, item: str, asked: str) -> str:
    """Who DOES sell this thing, for the refusal that follows.

    The old refusal listed what the seller in front of the buyer happened
    to have priced, which helps only somebody who already picked the right
    seller. 87 of the 140 `buy` refusals in the 50-resident run were a
    resident asking the person beside it for something that person had
    never priced, and the reply told them nothing about where to go.

    Empty string when nobody sells it anywhere - then the refusal is the
    plain fact, and `trade` really is the only route.
    """
    from world.tools.primitives.places import market_offers

    others = sorted(
        (o for o in market_offers(world, limit=60)
         if o["item"] == item and o["seller_id"] != asked and o["ask_sim"] is not None),
        # Somebody who is there to agree the sale before an unattended shelf.
        key=lambda o: (0 if o.get("minded", True) else 1),
    )
    if not others:
        return ""
    best = others[0]
    held = "" if best["stock"] else " (none in stock)"
    return (
        f"{best['seller']} sells {item} at {best['ask_sim']} sim, at "
        f"{best['where']}{held}. "
    )


async def buy_handler(world: WorldState, agent: AgentLifeRecord, params: BuyParams) -> ToolOutcome:
    """Buy at the price the seller posted, not at one you name yourself.

    A4. `unit_price_sim` used to be the BUYER's parameter and was checked
    against nothing, so a bakery could not have a price - only the baker's
    speech - and a customer could pay one sim a loaf. There was nothing for
    price discovery to converge to, which is why evaluation E2 (does the
    spread across shops narrow over days) could not be asked at all.

    `seller_id` may be a business or a co-located person, so an
    individual's post is accepted with the same verb rather than needing a
    second one. `trade` is untouched: haggling still lives there, and the
    refusal below names it.
    """
    holder, kind, _ = _resolve_holder(world, "buy", params.seller_id)
    redirected_from: str | None = None
    if kind == "person":
        shop = _shop_that_sells_for(world, params.seller_id, agent.agent_id, params)
        if shop is not None:
            redirected_from = _holder_name(world, params.seller_id)
            params = params.model_copy(update={"seller_id": shop.business_id})
            holder, kind, _ = _resolve_holder(world, "buy", params.seller_id)
    where = _require_reachable(world, "buy", agent.agent_id, params.seller_id)
    seller_id = _canonical_holder_id(world, params.seller_id)
    post = world.posted(seller_id, params.item)
    if post is None or post.ask_sim is None:
        raise NotFoundError(
            "posted price",
            f"{params.item} at {_holder_name(world, seller_id)}",
            sorted(
                item for item, p in world.posts_of(seller_id).items()
                if p.ask_sim is not None
            ),
            hint=(
                f"{_holder_name(world, seller_id)} has not posted a price for "
                f"{params.item}. Nothing is for sale here at a fixed price. "
                + _sold_elsewhere(world, params.item, seller_id)
                + "`trade` is how two people agree one."
            ),
        )

    unit_price = post.ask_sim
    total_price = unit_price * params.quantity

    # Checked before the ledger call, not after: world.ledger.move() is an
    # external system this module never rolls back (see registry.py's
    # ordering discipline), so a buyer must never be able to pay for stock
    # the seller does not have. Inventory.move() would itself raise on
    # a shortfall, but only *after* the payment already landed.
    available = world.inventory.quantity(seller_id, params.item)
    if available < params.quantity:
        raise InsufficientInventoryError(
            seller_id, params.item, params.quantity, available
        )

    # GOV-4: a price posted before a cap is still not payable after it.
    await _require_price_in_band(world, "buy", params.item, unit_price)

    # Last before the money moves, deliberately: after "do they sell it" and
    # "is it on the shelf", because telling a resident to go and talk to a
    # seller whose shelf is empty buys it a wasted call - it would speak,
    # and then be told there is nothing to buy.
    conversation_id = _require_dealing(
        world, "buy", agent.agent_id, seller_id, item=params.item
    )

    await world.ledger.move(
        from_account_id=agent.agent_id,
        to_account_id=seller_id,
        amount=total_price,
        currency=Currency.SIM,
        reason=TransactionReason.PURCHASE,
        tick=world.tick(),
        memo=f"bought {params.quantity} {params.item} from {seller_id}",
    )
    world.inventory.move(seller_id, agent.agent_id, params.item, params.quantity)
    _settled_in_conversation(world, conversation_id, {
        "kind": "buy", "seller_id": seller_id, "buyer_id": agent.agent_id,
        "item": params.item, "quantity": params.quantity, "total_sim": total_price,
    })

    event = WorldEvent(
        type="business.sale.completed",
        tick=world.tick(),
        payload={
            "business_id": seller_id if kind == "business" else None,
            "seller_id": seller_id,
            "buyer_agent_id": agent.agent_id,
            "item": params.item,
            "quantity": params.quantity,
            "total_price_sim": total_price,
            # A4's event row: the report can now tell a sale at a posted
            # price from a bargain struck in speech without parsing prose.
            "posted_price_sim": unit_price,
            "holder_kind": kind,
            "location_id": where,
            "conversation_id": conversation_id,
            "involved_agent_ids": (
                [agent.agent_id] if kind == "business"
                else [agent.agent_id, seller_id]
            ),
        },
    )
    return ToolOutcome(
        result={
            "bought": params.item,
            "quantity": params.quantity,
            "unit_price_sim": unit_price,
            "total_sim": total_price,
            "from": _holder_name(world, seller_id),
            **(
                {"note": f"{redirected_from} sells {params.item} through "
                         f"{_holder_name(world, seller_id)}; you bought from the shop."}
                if redirected_from else {}
            ),
            "your_sim_after": world.ledger.sim_balance(agent.agent_id),
            "you_now_hold": {
                params.item: world.inventory.quantity(agent.agent_id, params.item)
            },
            # Kept for every caller and test written against the old shape.
            "total_price_sim": total_price,
        },
        events=[event],
    )


class SellParams(BaseModel):
    buyer_id: str = Field(
        description="A business id, or the agent id of a person beside you."
    )
    item: str
    quantity: int = Field(gt=0)


async def sell_handler(world: WorldState, agent: AgentLifeRecord, params: SellParams) -> ToolOutcome:
    """Sell at the price the buyer posted. Mirrors `buy` exactly - see it."""
    holder, kind, _ = _resolve_holder(world, "sell", params.buyer_id)
    where = _require_reachable(world, "sell", agent.agent_id, params.buyer_id)
    buyer_id = _canonical_holder_id(world, params.buyer_id)
    post = world.posted(buyer_id, params.item)
    if post is None or post.bid_sim is None:
        raise NotFoundError(
            "posted bid",
            f"{params.item} at {_holder_name(world, buyer_id)}",
            sorted(
                item for item, p in world.posts_of(buyer_id).items()
                if p.bid_sim is not None
            ),
            hint=(
                f"{_holder_name(world, buyer_id)} has not posted what they pay "
                f"for {params.item}. `trade` is how two people agree a price."
            ),
        )

    unit_price = post.bid_sim
    total_price = unit_price * params.quantity

    # Same ordering reason as buy_handler: check the seller actually has
    # the goods before the buyer's ledger account pays for them.
    available = world.inventory.quantity(agent.agent_id, params.item)
    if available < params.quantity:
        raise InsufficientInventoryError(agent.agent_id, params.item, params.quantity, available)

    await _require_price_in_band(world, "sell", params.item, unit_price)

    # Last before the money moves - see buy_handler for why it is last.
    conversation_id = _require_dealing(
        world, "sell", agent.agent_id, buyer_id, item=params.item
    )

    await world.ledger.move(
        from_account_id=buyer_id,
        to_account_id=agent.agent_id,
        amount=total_price,
        currency=Currency.SIM,
        reason=TransactionReason.SALE,
        tick=world.tick(),
        memo=f"sold {params.quantity} {params.item} to {buyer_id}",
    )
    world.inventory.move(agent.agent_id, buyer_id, params.item, params.quantity)
    _settled_in_conversation(world, conversation_id, {
        "kind": "sell", "seller_id": agent.agent_id, "buyer_id": buyer_id,
        "item": params.item, "quantity": params.quantity, "total_sim": total_price,
    })

    event = WorldEvent(
        type="business.purchase.completed",
        tick=world.tick(),
        payload={
            "business_id": buyer_id if kind == "business" else None,
            "buyer_id": buyer_id,
            "seller_agent_id": agent.agent_id,
            "item": params.item,
            "quantity": params.quantity,
            "total_price_sim": total_price,
            "posted_price_sim": unit_price,
            "holder_kind": kind,
            "location_id": where,
            "conversation_id": conversation_id,
            "involved_agent_ids": (
                [agent.agent_id] if kind == "business"
                else [agent.agent_id, buyer_id]
            ),
        },
    )
    return ToolOutcome(
        result={
            "sold": params.item,
            "quantity": params.quantity,
            "unit_price_sim": unit_price,
            "total_sim": total_price,
            "to": _holder_name(world, buyer_id),
            "your_sim_after": world.ledger.sim_balance(agent.agent_id),
            "total_price_sim": total_price,
        },
        events=[event],
    )


class TransferParams(BaseModel):
    to_agent_id: str
    amount_sim: int = Field(gt=0)
    memo: str = ""
    reason: str = Field(
        default="other",
        description="gift, loan, payment or other. Say `loan` so the ledger knows one from a gift.",
    )


TRANSFER_REASONS = ("gift", "loan", "payment", "other")


async def transfer_handler(
    world: WorldState, agent: AgentLifeRecord, params: TransferParams
) -> ToolOutcome:
    """Move sim from your hand to somebody else's, and say why.

    C2 added the `reason`. The 400-sim story - a debt carried unspoken for
    five weeks, then named, acknowledged and repaid - is the best thing
    the series produced and it had to be reconstructed by a human reading
    transcripts, because the world recorded a transfer and nothing more. A
    `loan` now creates a row the borrower's own `check_debts` reads back,
    and a later `payment` from the borrower reduces it.

    Untagged transfers stay legal and stay `other`. Forcing a resident to
    classify every gift would make the tag a formality rather than a
    statement.
    """
    if params.to_agent_id not in world.agents:
        raise NotFoundError("agent", params.to_agent_id)
    reason = params.reason.strip().lower()
    if reason not in TRANSFER_REASONS:
        raise ToolValidationError(
            "transfer",
            [f"reason must be one of: {', '.join(TRANSFER_REASONS)}. You said {params.reason!r}."],
        )

    await world.ledger.move(
        from_account_id=agent.agent_id,
        to_account_id=params.to_agent_id,
        amount=params.amount_sim,
        currency=Currency.SIM,
        reason=TransactionReason.TRANSFER,
        tick=world.tick(),
        memo=params.memo,
    )

    repaid = 0
    if reason == "loan":
        world.record_loan(
            agent.agent_id, params.to_agent_id, params.amount_sim, world.tick()
        )
    elif reason == "payment":
        # The payer is the borrower here: money going back the other way.
        repaid = world.record_repayment(
            agent.agent_id, params.to_agent_id, params.amount_sim
        )

    event = WorldEvent(
        type="economy.wallet.credited",
        tick=world.tick(),
        payload={
            "account_id": params.to_agent_id,
            "from_agent_id": agent.agent_id,
            "agent_id": agent.agent_id,
            "amount_sim": params.amount_sim,
            "reason": reason,
            "repaid_sim": repaid,
            "involved_agent_ids": [agent.agent_id, params.to_agent_id],
        },
    )
    return ToolOutcome(
        result={
            "amount_sim": params.amount_sim,
            "reason": reason,
            "repaid_sim": repaid,
        },
        events=[event],
    )


class StoreItemParams(BaseModel):
    storage_id: str
    item: str
    quantity: int = Field(gt=0)


async def store_item_handler(
    world: WorldState, agent: AgentLifeRecord, params: StoreItemParams
) -> ToolOutcome:
    storage = world.storages.get(params.storage_id)
    if storage is None:
        storage = Storage(
            storage_id=params.storage_id,
            owner_id=agent.agent_id,
            location_id=world.location_of(agent.agent_id) or "",
        )
        world.storages[params.storage_id] = storage

    world.inventory.move(agent.agent_id, storage.storage_id, params.item, params.quantity)
    event = WorldEvent(
        type="business.storage.stocked",
        tick=world.tick(),
        payload={
            "storage_id": storage.storage_id,
            "agent_id": agent.agent_id,
            "item": params.item,
            "quantity": params.quantity,
        },
    )
    return ToolOutcome(result={"storage_id": storage.storage_id}, events=[event])


class WithdrawItemParams(BaseModel):
    storage_id: str
    item: str
    quantity: int = Field(gt=0)


def storages_worth_naming(world: WorldState, agent_id: str) -> list[str]:
    """Which storage ids to put in a refusal, for a resident standing here.

    Every storage in the town is noise - a container in another place is
    not an answer to "which one did you mean". The ones where the resident
    is standing are the useful ones; the wider list is the fallback, so a
    resident is never sent back to the same empty vocabulary it guessed
    `field_storage` out of.
    """
    here = world.location_of(agent_id)
    nearby = [s.storage_id for s in world.storages.values() if here and s.location_id == here]
    return sorted(nearby) if nearby else sorted(world.storages)


async def withdraw_item_handler(
    world: WorldState, agent: AgentLifeRecord, params: WithdrawItemParams
) -> ToolOutcome:
    storage = world.storages.get(params.storage_id)
    if storage is None:
        # The one recorded attempt at this verb in the project's history
        # invented `field_storage`, was told only that it did not exist,
        # and never tried again.
        raise NotFoundError(
            "storage", params.storage_id, storages_worth_naming(world, agent.agent_id)
        )

    world.inventory.move(storage.storage_id, agent.agent_id, params.item, params.quantity)
    event = WorldEvent(
        type="business.storage.withdrawn",
        tick=world.tick(),
        payload={
            "storage_id": storage.storage_id,
            "agent_id": agent.agent_id,
            "item": params.item,
            "quantity": params.quantity,
        },
    )
    return ToolOutcome(result={"storage_id": storage.storage_id}, events=[event])


def business_location_gate(world: WorldState, params: BaseModel) -> str:
    """A declared gate for any tool whose params name one business.

    `buy` and `sell` no longer use it: A4 lets their counterparty be a
    business OR a co-located person, and which place is required is not
    knowable until the id is looked up. Kept because `set_wage`, `pay_wage`
    and the business tools do gate exactly this way.
    """
    return require_business(world, getattr(params, "business_id")).location_id


def storage_location_gate_optional(world: WorldState, params: StoreItemParams) -> str | None:
    """store_item may create a brand-new Storage right where the caller
    is standing, so an as-yet-unknown storage_id has no location to gate
    against yet; an existing one must be visited in person, same as
    withdraw_item.
    """
    storage = world.storages.get(params.storage_id)
    return storage.location_id if storage is not None else None


def storage_location_gate_required(world: WorldState, params: WithdrawItemParams) -> str:
    storage = world.storages.get(params.storage_id)
    if storage is None:
        # The gate runs BEFORE the handler, so this is the sentence a wrong
        # id actually gets. It has no actor to stand beside, so it names
        # every storage rather than the nearby ones.
        raise NotFoundError("storage", params.storage_id, sorted(world.storages))
    return storage.location_id



class TradeParams(BaseModel):
    counterparty_agent_id: str = Field(min_length=1, max_length=64)
    item: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=10_000)
    # 0 is legal: a gift is a trade at a price of nothing. DESIGN.md's
    # research question is whether residents give things away, and a tool
    # that forbade it would answer that question for them.
    price_sim: int = Field(ge=0, le=10_000_000)


async def trade_handler(
    world: WorldState, agent: AgentLifeRecord, params: TradeParams
) -> ToolOutcome:
    """Hand goods to somebody standing here and take their money for it, in
    one indivisible act.

    DESIGN.md section 2 lists `trade` among the primitives; ROADMAP T7 lists
    it as still missing ("an atomic two-sided swap distinct from `buy` and
    `sell` against a posted price"). This is that, and the reason it finally
    got built is measured: six residents held a real market negotiation at
    `home:h_field`, agreed 45 sim a unit for wheat out loud, and then nothing
    happened - because nothing COULD. `buy` and `sell` are both gated to a
    business's own location, `transfer` moves sim but no goods, and two
    people standing in a kitchen with wheat and money between them had no way
    to complete the bargain they had just struck. Zero settlements, and not
    because anybody refused.

    ATOMIC, and that is the whole design. Split into "you pay, then I hand
    over", either half can be the last thing that happens, and the loser has
    no recourse in a town whose justice system cannot see intent. Making the
    bargain the unit removes that failure mode rather than adjudicating it.
    Whether residents trade FAIRLY stays wide open, which is the question
    worth keeping.

    Ordering follows registry.py's atomicity discipline exactly: everything
    checkable is checked first, then the fallible external call (the ledger),
    and only then the tools-owned bookkeeping that cannot itself fail. So a
    buyer who cannot pay leaves the seller's goods exactly where they were.
    """
    other_id = params.counterparty_agent_id
    if other_id == agent.agent_id:
        raise ToolValidationError("trade", ["you cannot trade with yourself"])
    if other_id not in world.agents:
        raise NotFoundError("agent", other_id)

    here = world.location_of(agent.agent_id)
    there = world.location_of(other_id)
    if here is None or not satisfies_gate(here, there or ""):
        # Goods change hands where the hands are. Not a dynamic
        # location_gate, because the required place depends on the
        # COUNTERPARTY rather than on the params alone, and the gate
        # resolver cannot see who that is standing anywhere.
        #
        # `satisfies_gate` rather than `!=` (TOOL-19): every other
        # co-location check in the town already folds - `give`
        # (living.py:142), `revive` (:299), `teach_skill`
        # (social.py:475), `conveyance.py:73` - and two spellings of one
        # room refusing a trade is the same defect those were fixed for.
        raise LocationGateError("trade", here or "the same place", there)
    # GOV-4. Per unit, because a cap is per unit; a price of 0 is a gift and
    # no floor forbids one (`price_band_refusal`).
    await _require_price_in_band(
        world, "trade", params.item, params.price_sim / params.quantity
    )
    conversation_id = _require_dealing(world, "trade", agent.agent_id, other_id)

    held = world.inventory.quantity(agent.agent_id, params.item)
    if held < params.quantity:
        raise ToolValidationError(
            "trade",
            [f"you hold {held} {params.item}, not {params.quantity}"],
        )

    if params.price_sim:
        # Fallible, and therefore first. An InsufficientFundsError here
        # leaves the goods untouched.
        await world.ledger.move(
            from_account_id=other_id,
            to_account_id=agent.agent_id,
            amount=params.price_sim,
            currency=Currency.SIM,
            reason=TransactionReason.TRADE,
            tick=world.tick(),
            memo=f"{params.quantity} {params.item}",
        )
    world.inventory.move(agent.agent_id, other_id, params.item, params.quantity)
    _settled_in_conversation(world, conversation_id, {
        "kind": "trade", "seller_id": agent.agent_id, "buyer_id": other_id,
        "item": params.item, "quantity": params.quantity, "total_sim": params.price_sim,
    })

    event = WorldEvent(
        type="economy.trade.settled",
        tick=world.tick(),
        payload={
            "seller_agent_id": agent.agent_id,
            "buyer_agent_id": other_id,
            "item": params.item,
            "quantity": params.quantity,
            "price_sim": params.price_sim,
            "location_id": here,
            "conversation_id": conversation_id,
            # Both sides perceive it on their next turn, which is what lets a
            # settlement be reconstructed from the trace and what lets the
            # buyer act on having the goods.
            "involved_agent_ids": [agent.agent_id, other_id],
        },
    )
    return ToolOutcome(
        result={
            "traded": True,
            "item": params.item,
            "quantity": params.quantity,
            "price_sim": params.price_sim,
            "with": world.agent_names.get(other_id, other_id),
        },
        events=[event],
    )


class PostPriceParams(BaseModel):
    item: str = Field(
        min_length=1, max_length=64, description="The item, as inventory names it."
    )
    ask_sim: int | None = Field(
        default=None, gt=0,
        description="Price you sell one unit for. Omit to not sell.",
    )
    bid_sim: int | None = Field(
        default=None, gt=0,
        description="Price you pay for one unit. Omit to not buy.",
    )
    on_behalf_of_business: str | None = Field(
        default=None,
        description="A business you own or work for. Omit to post for yourself.",
    )


class WithdrawPriceParams(BaseModel):
    item: str
    on_behalf_of_business: str | None = None


def _the_shop_holding_it_here(
    world: WorldState, agent: AgentLifeRecord, item: str | None
) -> "Business | None":
    """The one business at this spot that this resident may sell for and
    that actually has the item - or None, which means "post on yourself".

    Deliberately unanimous: exactly ONE candidate, or nothing. A resident
    that owns two shops in one place asked an ambiguous question, and
    guessing which till gets paid is worse than posting on the person and
    letting `post_price`'s own on_hand note say so.
    """
    if not item or world.inventory.quantity(agent.agent_id, item) > 0:
        # Holding it yourself is an ordinary personal sale.
        return None
    here = world.location_of(agent.agent_id)
    if not here:
        return None
    candidates = [
        business
        for business in world.businesses.values()
        if (
            agent.agent_id == business.owner_agent_id
            or agent.agent_id in business.employee_agent_ids
        )
        and satisfies_gate(business.location_id, here)
        and world.inventory.quantity(business.business_id, item) > 0
    ]
    return candidates[0] if len(candidates) == 1 else None


def _posting_holder(
    world: WorldState,
    tool_name: str,
    agent: AgentLifeRecord,
    business_id: str | None,
    item: str | None = None,
) -> tuple[str, str]:
    """Whose board is being written, and may this caller write it.

    An individual posts on themselves and needs no permission. A business's
    board is written by its owner or a granted employee, standing on the
    premises - the same access `hire_employee` already grants, so a wage
    and a price key off one relationship rather than two.
    """
    if business_id is None:
        # TRADE-07. Post where the GOODS are, when they are plainly not
        # on you.
        #
        # This is the deadlock that cost run `deni-a6f9eb4-5h` every
        # transaction in it, in its last surviving form. A resident does
        # `stock_shelf` (the goods leave its hands for the shop's shelf)
        # and then `post_price` with no business named - so the ask goes
        # on the PERSON, who now holds none of it, and `buy` answers
        # "n13 needs 1 bread, has 0" to anybody who walks in.
        #
        # TRADE-01 fixed the case where the resident NAMED the business
        # and the name was dropped. This is the case where it named
        # nothing, which is the commoner one and reads as more obviously
        # correct to a shopkeeper standing in its own shop.
        #
        # Only when all four are true, so it can never quietly sell
        # somebody else's stock: the resident holds none of the item, it
        # owns or works for exactly one business here, that business
        # holds some, and it is standing on the premises. Anything less
        # and it posts on itself, which is what it asked for.
        shop = _the_shop_holding_it_here(world, agent, item)
        if shop is not None:
            return shop.business_id, "business"
        return agent.agent_id, "person"

    business = require_business(world, business_id)
    if (
        agent.agent_id != business.owner_agent_id
        and agent.agent_id not in business.employee_agent_ids
    ):
        raise ToolValidationError(
            tool_name,
            [
                f"you do not own or work for {business.name}. Post for yourself "
                "by leaving on_behalf_of_business unset."
            ],
        )
    here = world.location_of(agent.agent_id)
    if not satisfies_gate(business.location_id, here or ""):
        raise LocationGateError(tool_name, business.location_id, here)
    return business.business_id, "business"


async def post_price_handler(
    world: WorldState, agent: AgentLifeRecord, params: PostPriceParams
) -> ToolOutcome:
    """Put a standing price on something, so it can be bought without asking.

    A3, and the thing `buy` was missing. `buy` took the buyer's own
    `unit_price_sim` and checked it against nothing, so a bakery could not
    have a price - only the baker's speech - and there was nothing for
    price discovery to converge to.

    Nothing is for sale at genesis. Opening stock exists and stays
    unpriced until somebody posts, which is what keeps the standing
    question honest: the tick of the town's first posted price is a
    finding, not a fixture.
    """
    if params.ask_sim is None and params.bid_sim is None:
        raise ToolValidationError(
            "post_price",
            [
                "give ask_sim, bid_sim or both. To take a price down use "
                "`withdraw_price`."
            ],
        )

    # GOV-4. A price the law forbids is not posted, rather than posted and
    # then refused to every buyer who walks over to pay it.
    for side in (params.ask_sim, params.bid_sim):
        if side is not None:
            await _require_price_in_band(world, "post_price", params.item, side)

    holder_id, kind = _posting_holder(
        world, "post_price", agent, params.on_behalf_of_business, params.item
    )
    previous = world.posted(holder_id, params.item)
    post = PricePost(
        item=params.item,
        ask_sim=params.ask_sim,
        bid_sim=params.bid_sim,
        posted_tick=world.tick(),
        poster_id=agent.agent_id,
    )
    world.put_price(holder_id, post)

    event = WorldEvent(
        type="economy.price.posted",
        tick=world.tick(),
        payload={
            "holder_id": holder_id,
            "holder_kind": kind,
            "poster_id": agent.agent_id,
            "agent_id": agent.agent_id,
            "item": params.item,
            "ask_sim": params.ask_sim,
            "bid_sim": params.bid_sim,
            "previous_ask": previous.ask_sim if previous else None,
            "previous_bid": previous.bid_sim if previous else None,
            "location_id": world.location_of(agent.agent_id),
        },
    )
    # TRADE-03. Say, at POST time, whether anyone could actually buy this.
    #
    # `buy` reads the HOLDER'S inventory, so an ask posted by somebody who
    # holds none of the item is a listing nobody can complete. In run
    # `deni-a6f9eb4-5h` that is precisely what n13 created - the bread had
    # gone onto the shop's shelf one call earlier - and it found out only
    # when two other residents walked over and were refused. The seller
    # was told its posting had succeeded and never learned otherwise.
    #
    # A note, not a refusal. Pricing something you are about to acquire is
    # a legitimate thing to do and refusing it would take a real move away
    # from a resident; what was missing was being told, while there is
    # still a turn left to do something about it.
    on_hand = world.inventory.quantity(holder_id, params.item)
    note = None
    if params.ask_sim is not None and on_hand <= 0:
        whose = "this business holds" if kind == "business" else "you hold"
        elsewhere = ""
        if kind == "person":
            # The exact mistake, named: the goods are usually on a shelf.
            shelves = [
                business_id
                for business_id in (world.businesses or {})
                if world.inventory.quantity(business_id, params.item) > 0
            ]
            if shelves:
                elsewhere = (
                    f" {shelves[0]} has some on its shelf - post on behalf of it "
                    "with on_behalf_of_business, or take them into your own hands "
                    "with take_from_shelf."
                )
        note = (
            f"The price is posted, but {whose} 0 {params.item}, so nobody can "
            f"buy at it yet - `buy` takes the goods from whoever posted.{elsewhere}"
        )

    result = {
        "holder": holder_id,
        "holder_kind": kind,
        "item": params.item,
        "ask_sim": params.ask_sim,
        "bid_sim": params.bid_sim,
        "on_hand": on_hand,
        "replaced": (
            {"ask_sim": previous.ask_sim, "bid_sim": previous.bid_sim}
            if previous
            else None
        ),
        "visible_at": (
            f"business:{holder_id}" if kind == "business" else "wherever you stand"
        ),
    }
    if note:
        result["note"] = note
    return ToolOutcome(result=result, events=[event])


async def withdraw_price_handler(
    world: WorldState, agent: AgentLifeRecord, params: WithdrawPriceParams
) -> ToolOutcome:
    """Take a price down. The item stops being for sale at it."""
    # The same holder resolution as `post_price`, item included: a price
    # that TRADE-07 put on the shop must be findable by the resident that
    # posted it, or it could post and never take down.
    holder_id, kind = _posting_holder(
        world, "withdraw_price", agent, params.on_behalf_of_business, params.item
    )
    removed = world.drop_price(holder_id, params.item)
    if removed is None:
        raise ToolValidationError(
            "withdraw_price", [f"no price is posted for {params.item}."]
        )

    event = WorldEvent(
        type="economy.price.withdrawn",
        tick=world.tick(),
        payload={
            "holder_id": holder_id,
            "holder_kind": kind,
            "poster_id": agent.agent_id,
            "agent_id": agent.agent_id,
            "item": params.item,
            "was_ask_sim": removed.ask_sim,
            "was_bid_sim": removed.bid_sim,
        },
    )
    return ToolOutcome(
        result={
            "withdrew": params.item,
            "holder": holder_id,
            "was_ask_sim": removed.ask_sim,
            "was_bid_sim": removed.bid_sim,
        },
        events=[event],
    )


# ── the shelf and the till (TOOL-03) ──────────────────────────────────
#
# A business's stock lives under its business_id in `world.inventory`
# (inventory.py keys on any owner id, agent or business), and its money in
# a ledger account of the same name. Neither had a door.
#
# `craft` and `cook` put goods in the AGENT's hand (production.py:47-48).
# Nothing moved them to the shop: `store_item` builds a `Storage`, not a
# business; `transfer` and `give` are agent-to-agent. Nothing moved sim out
# of the till either - `pay_wage` only pays `employee_agent_ids`, and
# `transfer` spends the actor's own account.
#
# So Marta baked bread into her own hands and could not shelve it, while
# `buy` at `b_bakery` needed the shop's stock and the shop had only what
# genesis put there. The only route the physics allowed was to hire
# yourself and pay yourself a wage - which works today because
# `hire_employee` never checks that the employee is somebody else. That is
# an accident, not a feature, and TOOL-15 closes it below.
#
# The scripted control bakery already does all of this through a private
# path - `world.inventory.move(self.owner_id, self.business_id, ...)`
# (control_bakery.py:116) - directly contradicting its own docstring that
# "everything it does goes through registry.execute". A control that
# bypasses the physics is measuring a different world than the residents
# live in, which is exactly what a control must not do.


def _business_counter(
    world: WorldState,
    tool_name: str,
    agent: AgentLifeRecord,
    business_ref: str,
    *,
    owner_only: bool,
):
    """The business whose counter this agent is standing at, if they may.

    Same shape as `labour._business_at_hand`, kept here rather than
    imported so commerce does not depend on labour for a check about
    commerce; the two are three lines each and diverge in what they allow
    (`owner_only` means different things for a wage and for a till).
    """
    business = resolve_business(world, business_ref)
    if business is None:
        raise NotFoundError("business", business_ref, sorted(world.businesses))

    is_owner = agent.agent_id == business.owner_agent_id
    is_staff = agent.agent_id in business.employee_agent_ids
    if owner_only and not is_owner:
        raise ActorRequirementError(
            tool_name, [f"you do not own {business.name}."]
        )
    if not owner_only and not (is_owner or is_staff):
        raise ActorRequirementError(
            tool_name,
            [f"you do not own or work for {business.name}."],
        )

    here = world.location_of(agent.agent_id)
    if not satisfies_gate(business.location_id, here or ""):
        raise LocationGateError(tool_name, business.location_id, here)
    return business


class ShelfParams(BaseModel):
    business_id: str = Field(
        description="A business you own or work for, and are standing in."
    )
    item: str
    quantity: int = Field(gt=0)


class TillParams(BaseModel):
    business_id: str = Field(
        description="A business you own or work for, and are standing in."
    )
    amount_sim: int = Field(gt=0)


def _stock_event(world: WorldState, business, agent_id: str, params, direction: str):
    return WorldEvent(
        type="business.stock.moved",
        tick=world.tick(),
        payload={
            "business_id": business.business_id,
            "agent_id": agent_id,
            "direction": direction,
            "item": params.item,
            "quantity": params.quantity,
            "location_id": business.location_id,
            "shelf_after": world.inventory.quantity(business.business_id, params.item),
        },
    )


async def stock_shelf_handler(
    world: WorldState, agent: AgentLifeRecord, params: ShelfParams
) -> ToolOutcome:
    """Put what you are holding onto the shop's shelf, where `buy` can reach it."""
    business = _business_counter(
        world, "stock_shelf", agent, params.business_id, owner_only=False
    )
    held = world.inventory.quantity(agent.agent_id, params.item)
    if held < params.quantity:
        raise InsufficientInventoryError(
            agent.agent_id, params.item, params.quantity, held
        )
    world.inventory.move(
        agent.agent_id, business.business_id, params.item, params.quantity
    )
    return ToolOutcome(
        result={
            "business": business.business_id,
            "item": params.item,
            "quantity": params.quantity,
            "shelf_now_holds": world.inventory.quantity(
                business.business_id, params.item
            ),
            "you_now_hold": world.inventory.quantity(agent.agent_id, params.item),
        },
        events=[_stock_event(world, business, agent.agent_id, params, "onto_shelf")],
    )


async def take_from_shelf_handler(
    world: WorldState, agent: AgentLifeRecord, params: ShelfParams
) -> ToolOutcome:
    """Take stock back off the shelf and into your own hands."""
    business = _business_counter(
        world, "take_from_shelf", agent, params.business_id, owner_only=False
    )
    on_shelf = world.inventory.quantity(business.business_id, params.item)
    if on_shelf < params.quantity:
        raise InsufficientInventoryError(
            business.business_id, params.item, params.quantity, on_shelf
        )
    world.inventory.move(
        business.business_id, agent.agent_id, params.item, params.quantity
    )
    return ToolOutcome(
        result={
            "business": business.business_id,
            "item": params.item,
            "quantity": params.quantity,
            "shelf_now_holds": world.inventory.quantity(
                business.business_id, params.item
            ),
            "you_now_hold": world.inventory.quantity(agent.agent_id, params.item),
        },
        events=[_stock_event(world, business, agent.agent_id, params, "off_shelf")],
    )


def _till_event(world: WorldState, business, agent_id: str, amount: int, direction: str):
    return WorldEvent(
        type="business.till.moved",
        tick=world.tick(),
        payload={
            "business_id": business.business_id,
            "agent_id": agent_id,
            "direction": direction,
            "amount_sim": amount,
            "location_id": business.location_id,
            "till_after": world.ledger.sim_balance(business.business_id),
        },
    )


async def deposit_to_till_handler(
    world: WorldState, agent: AgentLifeRecord, params: TillParams
) -> ToolOutcome:
    """Put your own money into the business's account."""
    business = _business_counter(
        world, "deposit_to_till", agent, params.business_id, owner_only=False
    )
    await world.ledger.move(
        from_account_id=agent.agent_id,
        to_account_id=business.business_id,
        amount=params.amount_sim,
        currency=Currency.SIM,
        reason=TransactionReason.TILL,
        tick=world.tick(),
        memo=f"deposit to {business.business_id}",
    )
    return ToolOutcome(
        result={
            "business": business.business_id,
            "amount_sim": params.amount_sim,
            "till_now_holds": world.ledger.sim_balance(business.business_id),
            "your_sim_after": world.ledger.sim_balance(agent.agent_id),
        },
        events=[_till_event(world, business, agent.agent_id, params.amount_sim, "into_till")],
    )


async def draw_from_till_handler(
    world: WorldState, agent: AgentLifeRecord, params: TillParams
) -> ToolOutcome:
    """Take the takings out of a business you own.

    Owner only, unlike the other three. Staff may stock the shelf, take
    stock off it and put money in, because all three are things an employee
    does in the course of the job. Taking money OUT is the one act where
    "employee" and "owner" have to differ - otherwise a wage is pointless
    and `steal` has a legal synonym.
    """
    business = _business_counter(
        world, "draw_from_till", agent, params.business_id, owner_only=True
    )
    held = world.ledger.sim_balance(business.business_id)
    if held < params.amount_sim:
        raise ToolValidationError(
            "draw_from_till",
            [f"{business.name} holds {held} sim, not {params.amount_sim}."],
        )
    await world.ledger.move(
        from_account_id=business.business_id,
        to_account_id=agent.agent_id,
        amount=params.amount_sim,
        currency=Currency.SIM,
        reason=TransactionReason.TILL,
        tick=world.tick(),
        memo=f"draw from {business.business_id}",
    )
    return ToolOutcome(
        result={
            "business": business.business_id,
            "amount_sim": params.amount_sim,
            "till_now_holds": world.ledger.sim_balance(business.business_id),
            "your_sim_after": world.ledger.sim_balance(agent.agent_id),
        },
        events=[_till_event(world, business, agent.agent_id, params.amount_sim, "out_of_till")],
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="stock_shelf",
            description=(
                "Move something from your own hands onto the shelf of a "
                "business you own or work for, standing at its premises. "
                "Only what is on the shelf can be bought from the shop; what "
                "you baked or made is in your hands until you put it there. "
                "`take_from_shelf` is the way back. Cost: 1 sim plus 1 per unit."
            ),
            params_model=ShelfParams,
            handler=stock_shelf_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="take_from_shelf",
            description=(
                "Take stock off the shelf of a business you own or work for, "
                "into your own hands, standing at its premises. It stops "
                "being for sale. Cost: 1 sim plus 1 per unit."
            ),
            params_model=ShelfParams,
            handler=take_from_shelf_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="deposit_to_till",
            description=(
                "Put your own sim into the account of a business you own or "
                "work for, standing at its premises. A shop needs money to "
                "buy stock and pay wages. Cost: 1 sim."
            ),
            params_model=TillParams,
            handler=deposit_to_till_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="draw_from_till",
            description=(
                "Take sim out of the account of a business you OWN, standing "
                "at its premises. Employees cannot: they are paid with "
                "`pay_wage`. This is how a shop's takings become the owner's "
                "money. Cost: 1 sim."
            ),
            params_model=TillParams,
            handler=draw_from_till_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="post_price",
            description=(
                # Addendum section 3's 89-word text, to rule 1: the physics,
                # then the sibling verb, and nothing about when to use it.
                "Put a standing price on an item so others can `buy` from you or "
                "`sell` to you without bargaining each time. ask_sim is what you "
                "sell one unit for; bid_sim is what you pay for one. Post for "
                "yourself and the price travels with you, visible wherever you "
                "stand. Name a business you own or work for, standing at its "
                "premises, and the price hangs there whether you are present or "
                "not. Nothing is for sale until a price is posted. For a one-off "
                "deal agreed in speech use `trade`. Cost: 1 sim."
            ),
            params_model=PostPriceParams,
            handler=post_price_handler,
            namespace=ToolNamespace.PUBLIC,
            # The gate depends on whether a business was named, which the
            # params alone do not decide - see _posting_holder.
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="withdraw_price",
            description=(
                "Take down a price you posted, for yourself or for a business you "
                "own or work for. The item stops being for sale or wanted at that "
                "price. Cost: 1 sim."
            ),
            params_model=WithdrawPriceParams,
            handler=withdraw_price_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="buy",
            description=(
                # Addendum section 4, 78 words. Names `trade` and
                # `look_around`, because run 19 proved a refusal that names
                # no other verb sends the town somewhere else entirely.
                "Buy an item at the price its seller has posted, paying sim from "
                "your hand. The seller is a business whose premises you stand in, "
                "or a person standing with you who has posted a price. You pay the "
                "posted ask; there is no price to name. If nothing is posted, "
                "nothing is for sale here; `trade` is how two people agree a price "
                "between them in speech, and `look_around` shows what is priced "
                "where you stand. Cost: 1% of the price."
            ),
            params_model=BuyParams,
            handler=buy_handler,
            namespace=ToolNamespace.PUBLIC,
            # The seller may be a business OR a person beside you, so which
            # place is required is not knowable from the params - the check
            # is in the handler. See _require_reachable.
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="sell",
            description=(
                "Sell an item you hold at the price the buyer has posted for it. "
                "The buyer is a business whose premises you stand in, or a person "
                "standing with you who has posted a bid. You receive the posted "
                "bid; there is no price to name. If no bid is posted, nobody here "
                "is buying at a fixed price; `trade` is how two people agree a "
                "price between them in speech. Cost: 1% of the price."
            ),
            params_model=SellParams,
            handler=sell_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="trade",
            description=(
                "Sell something to a PERSON rather than to a shop: hand goods to "
                "whoever is standing with you and take their payment, in one act. "
                "No business, no premises and no registration are needed - that is "
                "`sell`, which is for selling into a business at its shop. You hand "
                "over the item, they hand over price_sim. Use a price of 0 to give "
                "something away."
            ),
            params_model=TradeParams,
            handler=trade_handler,
            namespace=ToolNamespace.PUBLIC,
            # The gate depends on the counterparty, not the params, so the
            # co-location check lives in the handler.
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="transfer",
            description=(
                "Pay another agent sim-currency directly. Say `reason` so the "
                "ledger knows a loan from a gift: gift, loan, payment or other. A "
                "loan you make shows up in both parties' `check_debts` until a "
                "transfer back with reason=payment clears it."
            ),
            params_model=TransferParams,
            handler=transfer_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="store_item",
            description="Stash an item in a named storage container.",
            params_model=StoreItemParams,
            handler=store_item_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=storage_location_gate_optional,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="withdraw_item",
            description="Take an item back out of a named storage container.",
            params_model=WithdrawItemParams,
            handler=withdraw_item_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=storage_location_gate_required,
            actor_requirements=ActorRequirements(),
        ),
    ]
