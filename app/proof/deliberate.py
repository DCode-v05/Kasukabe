"""Refusals this project chose, written down so they stop reading as breakage.

`replay` pushes every argument spelling the corpus records as successful
back through the registry's real validator, and reports what no longer
validates. Thirteen rows fail. Some of them are the fix working: TRADE-01
turned `post_price`'s silent drop of `business_id` into an explicit refusal,
and a corpus call that "succeeded" by doing the wrong thing correctly fails
today. The audit says so itself - "unless the tightening was deliberate" -
and then has no way to be told which ones were.

This is that way. Each entry names the parameter at fault and why the town
refuses it, and the entry only counts if the refusal it meets actually names
that parameter. A declaration that no longer matches the refusal is still a
FAILURE, which is the whole point: this is a place to record a decision, not
a place to silence a row.

WHAT MAY GO IN HERE. A refusal that is the design working. Not a refusal
nobody has looked at, and not one that could be folded with a synonym - if a
resident named the right thing in the wrong word, `param_repair.SYNONYMS` is
where that belongs, because folding it helps the next live run and a
declaration here helps nobody.
"""

from __future__ import annotations

DECLARED: dict[str, tuple[str, str]] = {
    "sell(business_id, item, quantity, unit_price_sim)": (
        "unit_price_sim",
        "a sale settles at a price already posted, so `sell` names no price. "
        "The shop itself is now understood (`business_id` folds to `buyer_id`), "
        "but a price supplied at sale time would be ignored, and TRADE-01's rule "
        "is that an ignored parameter must be refused rather than dropped",
    ),
    "buy(business_id, item, quantity, unit_price_sim)": (
        "unit_price_sim",
        "the same rule from the buyer's side: the price is the seller's posted "
        "one, and `business_id` now folds to `seller_id`",
    ),
    "sell(buyer_id, item, price, quantity)": (
        "price",
        "`price` has no meaning on a sale that settles at the posted price; "
        "`post_price` is where a price is named",
    ),
    "post_price(ask_sim, bid_sim, business_id, item, quantity)": (
        "quantity",
        "a posted price is a rate, not a lot. How much is on the shelf is "
        "`stock_shelf`'s business, and pricing a quantity the board cannot show "
        "would post a promise the shop has not made",
    ),
    "stock_shelf(business_id, item, price_sim, quantity)": (
        "price_sim",
        "stocking and pricing are two acts on purpose - a shelf can hold what is "
        "not for sale, and a price can stand over an empty shelf. Accepting both "
        "in one call would hide which of the two failed",
    ),
    "find_people(for_trade, query)": (
        "for_trade",
        "there is no trade filter, and inventing one silently would answer a "
        "narrower question than the resident asked. `query` already takes the "
        "words a resident would put in that filter",
    ),
    "rest(conversation_id)": (
        "conversation_id",
        "resting is not addressed to anybody. A conversation id here is the "
        "model carrying its last call's argument forward, which is worth "
        "refusing loudly rather than ignoring",
    ),
    "look_around(conversation_id)": (
        "conversation_id",
        "looking around is not addressed to anybody either - the same carried "
        "argument, one verb later",
    ),
    "look_around(query)": (
        "query",
        "`look_around` reports what is here, whole. A query would make it a "
        "search that silently hid the rest of the room; `find_people` and "
        "`list_places` are the verbs that take words",
    ),
    "recall_memories(count, query)": (
        "query",
        "`recall_memories` lists what a resident lately remembered, by kind and "
        "count; it does not search. Searching your own memory is `search_memory`, "
        "which takes exactly the `query` this call was reaching for, so folding the "
        "word across would answer a different question from the one asked",
    ),
    "describe_place(location_id, place_id)": (
        "location_id",
        "both words for the same thing in one call, and they may disagree. "
        "`location_id` alone has folded to `place_id` since the synonym table "
        "was written; what is refused here is naming the place twice, because "
        "answering about one of the two would be answering a question the "
        "resident did not ask",
    ),
    "look_around(wait)": (
        "wait",
        "nothing in this world is waited for inside a call - a turn that wants "
        "time to pass calls `rest`, and `repeated_calls.py` says so explicitly",
    ),
}


def declared_reason(signature: str, refusal: str) -> str | None:
    """Why this refusal is intended, when it is, and None when it is not.

    The refusal has to name the parameter the declaration is about. A verb
    tightened somewhere else since this was written meets a different
    sentence, the match fails, and the row goes back to being a failure -
    which is what should happen to a decision that has quietly changed.
    """
    entry = DECLARED.get(signature)
    if entry is None:
        return None
    parameter, reason = entry
    return reason if parameter in (refusal or "") else None
