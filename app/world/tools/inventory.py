"""Physical goods: crops, ore, timber, and every crafted good the recipe
table (recipes.py) produces.

No other module in this repo owns item quantities - world.ledger moves
sim/USD currency only (ledger/models.py's Account has no item slots), and
nothing else models "how much flour does agent-7 have." DESIGN.md's
primitives (harvest, mine, fell_timber, craft, cook, buy, sell,
store_item, withdraw_item, steal) all move items around, so this module
owns that state the same way ledger/ owns money: one mutation primitive
(move_items / credit_items / debit_items) everything else funnels
through.

Quantities are always non-negative integers - a fractional loaf of bread
is not a thing DESIGN.md's economy needs, and CONVENTIONS.md's "money is
always an integer" rule reads the same way for physical goods here: a
float item count is a bug, not a style choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from world.tools.errors import InsufficientInventoryError


@dataclass
class Inventory:
    """Item ledger for every owner (an agent_id, a business_id, or a
    storage_id - this module does not care which namespace an id comes
    from, callers do). Plain dataclass per CONVENTIONS.md's "internal-only
    data can be a plain dataclass," matching ledger/models.py's Account.
    """

    balances: dict[str, dict[str, int]] = field(default_factory=dict)

    def quantity(self, owner_id: str, item: str) -> int:
        return self.balances.get(owner_id, {}).get(item, 0)

    def credit(self, owner_id: str, item: str, qty: int) -> None:
        """Add `qty` of `item` to `owner_id`'s holdings. `qty` must be
        positive - crediting zero or a negative amount is always a caller
        bug (use debit for a negative movement), so it fails loudly
        instead of silently becoming a no-op or an inventory bug.
        """
        if qty <= 0:
            raise ValueError(f"credit qty must be positive, got {qty}")
        owner = self.balances.setdefault(owner_id, {})
        owner[item] = owner.get(item, 0) + qty

    def debit(self, owner_id: str, item: str, qty: int) -> None:
        if qty <= 0:
            raise ValueError(f"debit qty must be positive, got {qty}")
        available = self.quantity(owner_id, item)
        if available < qty:
            raise InsufficientInventoryError(owner_id, item, qty, available)
        owner = self.balances[owner_id]
        remaining = owner[item] - qty
        if remaining == 0:
            del owner[item]
        else:
            owner[item] = remaining
        if not owner:
            del self.balances[owner_id]

    def move(self, from_owner_id: str, to_owner_id: str, item: str, qty: int) -> None:
        """Debit then credit. Debit is checked and applied first so a
        move that would overdraw the source raises before anything is
        credited to the destination - no partial transfer.
        """
        self.debit(from_owner_id, item, qty)
        self.credit(to_owner_id, item, qty)

    def holdings(self, owner_id: str) -> dict[str, int]:
        """Read-only snapshot of one owner's full inventory."""
        return dict(self.balances.get(owner_id, {}))


# TOOL-04: a resident could not see what it was holding.
#
# The standing brief reduced a whole inventory to one number - "food
# units", the sum of everything in `living.EDIBLE` - and `look_around`
# returned place, people, conversations and prices but never holdings. No
# `check_inventory` tool existed. `authoring_context` was the only surface
# in the town that returned `holding`, and only on an authoring call.
#
# So the model read the brief's own word back at the world. Run 23's
# refusal taxonomy:
#
#     eat: 'food' is not food                        x4
#     you hold N X, not M   (give / trade / eat)     x13
#     nN needs N timber, has N                       x2
#
# and twenty `check_debts` calls, several of which were residents hunting
# for any read of their own state at all.
#
# Free, and in the brief rather than behind a tool: a resident too poor to
# act must never also be too poor to find out what it has. Same argument
# `check_debts` and `quote_action_cost` are already priced at zero for.
MAX_HOLDINGS_SHOWN = 8


def holdings_line(world, owner_id: str, *, limit: int = MAX_HOLDINGS_SHOWN) -> dict[str, int]:
    """What this owner holds, biggest piles first, capped.

    Capped because a resident with forty item types would otherwise push
    the roster and the price board out of a prompt that is already the
    dominant cost. The tail is reported as a count rather than dropped
    silently - "and 3 more kinds" is a fact the resident can act on by
    asking; a truncated list that looks complete is not.
    """
    try:
        held = dict(world.inventory.balances.get(owner_id, {}) or {})
    except Exception:  # noqa: BLE001 - a brief must never break a tick
        return {}
    held = {item: qty for item, qty in held.items() if qty > 0}
    if not held:
        return {}
    ranked = sorted(held.items(), key=lambda kv: (-kv[1], kv[0]))
    shown = dict(ranked[:limit])
    if len(ranked) > limit:
        shown["…and more kinds"] = len(ranked) - limit
    return shown
