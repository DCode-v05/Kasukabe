"""The scripted bakery: what a shop with no judgement achieves.

Evaluation spec §2.7. One rule-based bakery, no model calls, on the same
map with the same stock and the same customers as the engine-run one.

**It is the floor, not the ceiling.** The project has to end by saying
what N engines do in a real business unsupervised for D days, and that
sentence means nothing without a number to compare against. R1 to R7 -
opened for trade each day, solvent at stop, wages and tax paid,
intentions fulfilled, directed questions answered, cost per sale,
failure taxonomy - are all *comparative* measures, and until now they had
nothing to sit beside. A bakery that stays solvent is only interesting if
a script that cannot think stays solvent too, or does not.

Six rules, verbatim from the spec:

    Posts a fixed ask at dawn. Buys flour at a fixed bid when stock is
    low. Bakes when it holds flour and the shelf is below a threshold.
    Pays a fixed wage on the day boundary if the account covers it.
    Answers a directed question with a fixed line quoting its prices.
    Never leaves its premises.

**Everything it does goes through `registry.execute`.** It calls
`post_price`, `cook`, `buy`, `pay_wage` and `say` exactly as a resident
does, so it is priced by the same `SCHEDULE_TABLE`, gated by the same
location rules, and recorded in the same `tool_invocations` and event
stream. A control that took a private path through world state would be
measuring a different world, and every difference the comparison found
would be a difference in plumbing.

**It costs zero dollars.** No engine, no broker call, no wallet burn -
which is precisely what makes R6 ("cost per sale in USD") a comparison
against zero rather than against a smaller number.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from world.core.clock import TICKS_PER_DAY

logger = logging.getLogger(__name__)

# Every number the script decides by. Fixed, published here, and never
# adapted mid-run: the moment a control adjusts to what the town is doing
# it stops being a control and becomes a second, worse agent.
#
# The prices are the ones Marta Thorne actually reached in run 20 - retail
# 30, wholesale 24 by the dozen, flour bought at 6 - so the script is not
# handicapped by a made-up price book. The comparison is about JUDGEMENT,
# not about who guessed the market better.
ASK_SIM = 30
FLOUR_BID_SIM = 6
RESTOCK_FLOUR_BELOW = 4
BAKE_WHEN_SHELF_BELOW = 6
BAKE_BATCHES = 2
WAGE_SIM = 40
FLOUR_RECIPE = "cook:flour->bread"


@dataclass
class ControlBakery:
    """A bakery run by six rules and nothing else.

    `tick()` is called once per world tick by the runner, in the same
    place `dispatch_due_agents` would have spent a model call. The owner
    is a real resident with a real ledger account; what it does not have
    is an engine.
    """

    business_id: str
    owner_id: str
    employee_id: str | None = None
    # Every rule that fired, with the tick it fired on. R7's failure
    # taxonomy is built from this and from the refusals below: a control
    # whose failures are invisible cannot be compared on failures.
    actions: list[dict[str, Any]] = field(default_factory=list)
    refusals: list[dict[str, Any]] = field(default_factory=list)
    posted_today: int | None = None
    paid_through_day: int | None = None

    async def act(self, world: Any, registry: Any, tick: int) -> list[str]:
        """One tick of the script. Returns the rules that fired.

        Never raises. A control that can end the run is a control that
        decides what the treatment's data looks like, and a refusal is a
        FINDING here rather than a fault - `buy` refused for want of a
        posted bid is exactly the kind of thing R7 is a taxonomy of.
        """
        fired: list[str] = []
        day = tick // TICKS_PER_DAY

        for rule, coroutine in (
            ("post_price", self._post_at_dawn(world, registry, tick, day)),
            ("cook", self._bake_when_low(world, registry, tick)),
            ("buy", self._restock_flour(world, registry, tick)),
            ("pay_wage", self._pay_on_the_day_boundary(world, registry, tick, day)),
            ("say", self._answer_whoever_asked(world, registry, tick)),
        ):
            try:
                if await coroutine:
                    fired.append(rule)
            except Exception as exc:  # noqa: BLE001 - see the docstring
                self.refusals.append({
                    "tick": tick, "rule": rule, "reason": f"{type(exc).__name__}: {exc}",
                })
                logger.info("control bakery: %s refused at tick %d: %s", rule, tick, exc)
        return fired

    def _held(self, world: Any, owner: str, item: str) -> int:
        return int(world.inventory.quantity(owner, item))

    async def _post_at_dawn(self, world, registry, tick: int, day: int) -> bool:
        """Rule 1. Once a day, and only once: a shop that re-posts the same
        price every tick would out-spend the engine bakery on signage
        alone and the comparison would be about that."""
        if self.posted_today == day:
            return False
        if world.posted(self.business_id, "bread") is not None and self.posted_today is not None:
            self.posted_today = day
            return False
        await registry.execute(world, self.owner_id, "post_price", {
            "item": "bread", "ask_sim": ASK_SIM,
            "on_behalf_of_business": self.business_id,
        })
        await registry.execute(world, self.owner_id, "post_price", {
            "item": "flour", "bid_sim": FLOUR_BID_SIM,
            "on_behalf_of_business": self.business_id,
        })
        self.posted_today = day
        self.actions.append({"tick": tick, "rule": "post_price", "ask_sim": ASK_SIM})
        return True

    async def _bake_when_low(self, world, registry, tick: int) -> bool:
        """Rule 3. Flour in hand and the shelf below the threshold."""
        if self._held(world, self.business_id, "bread") >= BAKE_WHEN_SHELF_BELOW:
            return False
        if self._held(world, self.owner_id, "flour") < BAKE_BATCHES:
            return False
        await registry.execute(world, self.owner_id, "cook", {
            "recipe_id": FLOUR_RECIPE, "batches": BAKE_BATCHES,
        })
        # Baked into the owner's hands; the shelf is the business's.
        #
        # This used to be a bare `world.inventory.move(...)`, contradicting
        # the docstring above ("everything it does goes through
        # registry.execute") for the only reason it could: until TOOL-03
        # there WAS no tool that moved goods from a hand to a shelf, so the
        # control had a door the residents did not. That is precisely the
        # difference a control must not have - every gap the comparison
        # found between the scripted bakery and Marta's would have been a
        # gap in plumbing.
        onto_shelf = min(BAKE_BATCHES, self._held(world, self.owner_id, "bread"))
        if onto_shelf > 0:
            await registry.execute(world, self.owner_id, "stock_shelf", {
                "business_id": self.business_id, "item": "bread",
                "quantity": onto_shelf,
            })
        self.actions.append({"tick": tick, "rule": "cook", "batches": BAKE_BATCHES})
        return True

    async def _restock_flour(self, world, registry, tick: int) -> bool:
        """Rule 2. Buys flour at a fixed bid when stock is low.

        Through `buy`, so it needs somebody to have POSTED flour for sale -
        which is the same wall the engine bakery hits, and whether either
        of them finds a supplier is part of what is being compared.
        """
        if self._held(world, self.owner_id, "flour") >= RESTOCK_FLOUR_BELOW:
            return False
        here = world.location_of(self.owner_id)
        for holder_id, board in world.prices_visible_at(here).items():
            if holder_id == self.business_id:
                continue
            post = board.get("flour")
            if post is None or post.ask_sim is None or post.ask_sim > FLOUR_BID_SIM:
                continue
            await registry.execute(world, self.owner_id, "buy", {
                "seller_id": holder_id, "item": "flour", "quantity": RESTOCK_FLOUR_BELOW,
            })
            self.actions.append({
                "tick": tick, "rule": "buy", "from": holder_id, "unit_price": post.ask_sim,
            })
            return True
        return False

    async def _pay_on_the_day_boundary(self, world, registry, tick: int, day: int) -> bool:
        """Rule 4. A fixed wage, if the account covers it.

        "If the account covers it" is the whole point: the script does not
        borrow, does not sell stock to make payroll, and does not
        apologise. Whether an engine-run owner does any of those is R3.
        """
        if self.employee_id is None or self.paid_through_day == day or day < 1:
            return False
        self.paid_through_day = day
        if world.ledger.sim_balance(self.business_id) < WAGE_SIM:
            self.refusals.append({
                "tick": tick, "rule": "pay_wage", "reason": "the till could not cover it",
            })
            return False
        await registry.execute(world, self.owner_id, "pay_wage", {
            "business_id": self.business_id,
            "employee_id": self.employee_id,
            "amount_sim": WAGE_SIM,
        })
        self.actions.append({"tick": tick, "rule": "pay_wage", "amount_sim": WAGE_SIM})
        return True

    async def _answer_whoever_asked(self, world, registry, tick: int) -> bool:
        """Rule 5. One fixed line, quoting the prices.

        This is the rule the comparison turns on. G1 says two thirds of
        residents addressed by name never speak back; a script that always
        answers sets the ceiling that failure is measured against, and if
        the engine bakery answers LESS often than a hard-coded line, that
        is the finding.
        """
        asked = world.open_questions(self.owner_id, tick)
        if not asked:
            return False
        await registry.execute(world, self.owner_id, "say", {
            "content": (
                f"Bread is {ASK_SIM} sim. I buy flour at {FLOUR_BID_SIM}."
            ),
        })
        self.actions.append({"tick": tick, "rule": "say", "answered": len(asked)})
        return True

    # ── R1 to R7, for the report ──────────────────────────────────────

    def scorecard(self, world: Any, tick: int) -> dict[str, Any]:
        """The control's side of the business-readiness table.

        Every field is the same question asked of the engine-run bakery,
        so the report can print them in two columns without either side
        being computed a different way.
        """
        sales = [a for a in self.actions if a["rule"] == "buy"]
        return {
            # R1: opened for trade - a posted price and stock.
            "days_opened": len({a["tick"] // TICKS_PER_DAY for a in self.actions
                                if a["rule"] == "post_price"}),
            "posted_now": world.posted(self.business_id, "bread") is not None,
            # R2: solvent at stop.
            "sim_at_stop": world.ledger.sim_balance(self.business_id),
            "stock_at_stop": self._held(world, self.business_id, "bread"),
            # R3: wages paid.
            "wages_paid": sum(
                a.get("amount_sim", 0) for a in self.actions if a["rule"] == "pay_wage"
            ),
            # R4 is not asked of the control: a script sets no intentions,
            # and reporting 0/0 as "0% fulfilled" would read as a failure
            # rather than as a category that does not apply.
            "intentions": None,
            # R5: directed questions answered. Always, by construction.
            "questions_answered": sum(
                a.get("answered", 0) for a in self.actions if a["rule"] == "say"
            ),
            # R6: cost per sale in USD. Zero, and that is the point.
            "usd_spent": 0,
            "restocks": len(sales),
            # R7: the failure taxonomy.
            "refusals": [dict(row) for row in self.refusals],
        }
