"""What a resident meant, when it named a parameter the tool does not have.

Run `deni-a6f9eb4-5h` settled nothing. Fifty residents, 6,424 calls,
$27.78, 351 ticks, and **zero** completed sales. The trace says why, and
it is not that nobody tried.

THE ONE THAT COST THE RUN. `post_price` takes `on_behalf_of_business`.
Every resident that tried to price a shop's stock wrote `business_id`:

    post_price(item="bread", ask_sim=30, business_id="business:b_grocery")
    -> {"holder": "n13", "holder_kind": "person", ...}

Pydantic ignores unknown fields by default, so `business_id` was dropped
and the price was posted **on the person**. The bread was on the shop's
shelf, where `stock_shelf` had just put it. Two residents then walked to
the grocery and called `buy`, and `buy` checks the SELLER'S OWN
inventory:

    buy(item="bread", seller_id="n13") -> "n13 needs 1 bread, has 0"

The ask on one holder, the goods on another, and a success message in
between telling the seller it had posted a price. Nothing in the run
could have recovered from that.

THE REST, from the same trace. `transfer` wants `to_agent_id`; residents
wrote `to`, then `recipient_id`, then got it right on the third attempt -
three billed calls for one act. `give` wants `to_agent_id`; residents
wrote `agent_id`, `recipient_agent_id`. `trade` wants
`counterparty_agent_id`; a resident wrote `other_agent_id` and gave up.
`post_price` wants `ask_sim`; residents wrote `price`, `price_sim`,
`is_buying`, `is_buy`. Sixty-one refusals across the run were a resident
naming the right act with the wrong word for it.

TWO RULES, AND THE SECOND MATTERS MORE.

1. A synonym listed here is folded onto the real name. Only synonyms the
   trace actually shows - this is not a guessing layer, and a name that
   is not in the table is not renamed.

2. **An unknown parameter is a refusal, not a shrug.** This is the real
   fix. Silently dropping a parameter a resident deliberately passed is
   the worst behaviour available: it returns SUCCESS for an act the
   resident did not ask for, and the resident has no way to find out. A
   refusal that names the parameters the tool does have costs one turn
   and teaches; a silent drop cost this run every transaction in it.

Both rules produce a message that names the real parameter, because a
refusal a resident cannot act on is the same as no refusal - see
`world/tools/errors.py` on why every refusal here carries a vocabulary.
"""

from __future__ import annotations

from typing import Any

# tool -> {what a resident wrote: what the tool calls it}
#
# Every entry below was observed in a real session log. Adding one that
# was not is how a synonym table becomes a guessing layer: a resident
# that writes `price` for `bid_sim` and gets `ask_sim` has been silently
# misunderstood, which is the defect this module exists to end.
SYNONYMS: dict[str, dict[str, str]] = {
    "post_price": {
        # The run-killer. Five residents, every one of them.
        "business_id": "on_behalf_of_business",
        "business": "on_behalf_of_business",
        "for_business": "on_behalf_of_business",
        # "price" alone is ambiguous between ask and bid - but every
        # observed use was a seller pricing its own stock, and the tool
        # refuses when both are absent anyway.
        "price": "ask_sim",
        "price_sim": "ask_sim",
        "sell_price": "ask_sim",
        "ask": "ask_sim",
        "buy_price": "bid_sim",
        "bid": "bid_sim",
    },
    "withdraw_price": {
        "business_id": "on_behalf_of_business",
        "business": "on_behalf_of_business",
    },
    "transfer": {
        "to": "to_agent_id",
        "recipient_id": "to_agent_id",
        "recipient_agent_id": "to_agent_id",
        "agent_id": "to_agent_id",
        "amount": "amount_sim",
        "sim": "amount_sim",
    },
    "give": {
        "to": "to_agent_id",
        "recipient_id": "to_agent_id",
        "recipient_agent_id": "to_agent_id",
        "agent_id": "to_agent_id",
        "qty": "quantity",
    },
    "trade": {
        "other_agent_id": "counterparty_agent_id",
        "with_agent_id": "counterparty_agent_id",
        "partner_agent_id": "counterparty_agent_id",
        "agent_id": "counterparty_agent_id",
        "price": "price_sim",
        "qty": "quantity",
    },
    "buy": {
        "from_agent_id": "seller_id",
        "seller_agent_id": "seller_id",
        "seller": "seller_id",
        # 25 recorded successes wrote this, and `seller_id` has always been
        # allowed to name a business - "A4 lets seller_id/buyer_id be a
        # business OR a co-located person" (primitives/commerce.py). The
        # resident named the right thing in the wrong word.
        "business_id": "seller_id",
        "shop_id": "seller_id",
        "qty": "quantity",
    },
    "sell": {
        "to_agent_id": "buyer_id",
        "buyer_agent_id": "buyer_id",
        "buyer": "buyer_id",
        # The commonest spelling in the whole corpus: 57 recorded successes
        # naming the shop being sold to. Same rule as `buy` above.
        "business_id": "buyer_id",
        "shop_id": "buyer_id",
        "qty": "quantity",
    },
    "stock_shelf": {"business": "business_id", "qty": "quantity"},
    "take_from_shelf": {"business": "business_id", "qty": "quantity"},
    "deposit_to_till": {"business": "business_id", "amount": "amount_sim"},
    "draw_from_till": {"business": "business_id", "amount": "amount_sim"},
    "pay_tax": {"amount": "amount_sim", "sim": "amount_sim"},
    "say": {"to": "listener_agent_id", "to_agent_id": "listener_agent_id",
            "message": "content", "text": "content"},
    "move": {"destination": "destination_location_id",
             "location_id": "destination_location_id",
             "to": "destination_location_id"},
    "harvest": {"item": "crop", "resource": "crop"},
    "mine": {"item": "metal", "ore": "metal", "resource": "metal"},
    "describe_place": {"place": "place_id", "location_id": "place_id",
                       "name": "place_id"},
    "quote_action_cost": {
        # `params` for `call_params`, written once in a smoke run and
        # refused. The shorter word is the one a model reaches for.
        "params": "call_params",
        "arguments": "call_params",
        "args": "call_params",
        "tool": "tool_name",
    },
    "describe_person": {"person": "agent_id", "name": "agent_id"},
    "find_people": {"q": "query", "search": "query", "who": "query"},
}


def repair(tool_name: str, raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Fold known synonyms. Returns (params, notes about what was renamed).

    The notes are not cosmetic: a resident whose parameter was renamed is
    told so in the result, because a tool that quietly does something
    adjacent to what it was asked is the failure this module is about.
    """
    table = SYNONYMS.get(tool_name)
    if not table or not isinstance(raw, dict):
        return raw, []
    out: dict[str, Any] = {}
    notes: list[str] = []
    for key, value in raw.items():
        target = table.get(key)
        if target is None or target in raw:
            # `target in raw` - the resident gave the real name too, so
            # its own value wins and the synonym is dropped rather than
            # overwriting it.
            out[key] = value
            continue
        out[target] = value
        notes.append(f"{key} -> {target}")
    return out, notes


def unknown_parameters(params_model: Any, raw: dict[str, Any]) -> list[str]:
    """Parameter names this tool has never heard of.

    Returned rather than raised so the caller decides; `registry.
    validate_params` turns them into a refusal that names the real ones.
    """
    if not isinstance(raw, dict):
        return []
    # Direct, not a three-argument getattr: every `ToolSpec.params_model`
    # is a pydantic model and always has `model_fields`. A default here
    # could only ever hide a rename, and would silently switch this whole
    # guard off - which is the defect it exists to prevent.
    fields = set(params_model.model_fields)
    return sorted(key for key in raw if key not in fields)
