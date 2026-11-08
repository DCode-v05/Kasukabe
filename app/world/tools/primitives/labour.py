"""set_wage, pay_wage, check_debts: what one person owes another, and when.

Tool spec A5 and C2.

`hire_employee` grants access to a business's tools and pays nobody. A
student is told "your work is at Oak Hill College" and nothing there
earns. Jobs were labels, so promise-keeping under scarcity - standing
question 23 - had nothing to keep and nothing to break.

**The world never pays on its own. It counts.** That is the whole design
and the reason `pay_wage` is a separate act from `set_wage`: automatic
payroll would answer the question the run is asking. The trace can now
tell "hired and paid" from "hired and never promised" from "promised and
never paid", and Charness & Dufwenberg's finding that a bare promise
raises follow-through to a majority becomes S6, a measurement rather
than an assumption.

`check_debts` is the read side, and it is free. An agent that cannot
afford to find out what it owes cannot act on it, which is exactly the
moment the interesting behaviour (borrow, sell, ask, default) starts.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from world.core.clock import TICKS_PER_DAY
from world.core.events import WorldEvent
from world.ledger.models import Currency, TransactionReason
from world.life.models import AgentLifeRecord
from world.tools.errors import (
    ActorRequirementError,
    LocationGateError,
    NotFoundError,
    ToolValidationError,
)
from world.tools.models import WagePromise
from world.tools.place_aliases import resolve_business, satisfies_gate
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState


def _business_at_hand(
    world: WorldState, tool_name: str, agent: AgentLifeRecord, business_id: str, *, owner_only: bool
):
    business = resolve_business(world, business_id)
    if business is None:
        raise NotFoundError("business", business_id, sorted(world.businesses))

    is_owner = agent.agent_id == business.owner_agent_id
    is_staff = agent.agent_id in business.employee_agent_ids
    if owner_only and not is_owner:
        raise ActorRequirementError(tool_name, [f"you do not own {business.name}."])
    if not owner_only and not (is_owner or is_staff):
        raise ActorRequirementError(
            tool_name,
            [
                f"you do not own or work for {business.name}. "
                "`transfer` pays anyone, from your own hand."
            ],
        )

    here = world.location_of(agent.agent_id)
    if not satisfies_gate(business.location_id, here or ""):
        raise LocationGateError(tool_name, business.location_id, here)
    return business


class SetWageParams(BaseModel):
    business_id: str = Field(description="A business you own.")
    employee_id: str = Field(description="Someone you have hired with hire_employee.")
    wage_sim: int = Field(ge=0, description="Sim per period.")
    every_days: int = Field(default=1, ge=1, description="Period in game-days. Default 1.")


class PayWageParams(BaseModel):
    business_id: str
    employee_id: str
    amount_sim: int = Field(gt=0)


class CheckDebtsParams(BaseModel):
    pass


async def set_wage_handler(
    world: WorldState, agent: AgentLifeRecord, params: SetWageParams
) -> ToolOutcome:
    """Record a promise. Nothing is paid until `pay_wage`.

    A promise and a grant are deliberately two acts. `hire_employee` says
    who may work the business's tools; this says what they were told they
    would be paid for it. Keeping them apart is what lets the trace
    distinguish an employer who never promised anything from one who
    promised and did not pay - and only the second is a broken promise.
    """
    business = _business_at_hand(
        world, "set_wage", agent, params.business_id, owner_only=True
    )
    if params.employee_id not in world.agents:
        raise NotFoundError("agent", params.employee_id, sorted(world.agents))
    if params.employee_id not in business.employee_agent_ids:
        name = world.agent_names.get(params.employee_id, params.employee_id)
        raise ActorRequirementError(
            "set_wage",
            [
                f"{name} is not an employee of {business.name}. "
                "`hire_employee` grants that first."
            ],
        )

    key = f"{business.business_id}|{params.employee_id}"
    previous = world.wage_promises.get(key)
    promise = WagePromise(
        business_id=business.business_id,
        employee_id=params.employee_id,
        wage_sim=params.wage_sim,
        every_days=params.every_days,
        promised_tick=world.tick(),
        last_paid_tick=previous.last_paid_tick if previous else None,
        paid_sim=previous.paid_sim if previous else 0,
        accrued_sim=previous.accrued_sim if previous else 0,
    )
    world.wage_promises[key] = promise

    event = WorldEvent(
        type="business.wage.promised",
        tick=world.tick(),
        payload={
            "business_id": business.business_id,
            "employer_id": agent.agent_id,
            "agent_id": agent.agent_id,
            "employee_id": params.employee_id,
            "wage_sim": params.wage_sim,
            "every_days": params.every_days,
            "previous_wage_sim": previous.wage_sim if previous else None,
            # The employee reads it on its next turn: being told what you
            # were promised is the only way the promise exists for them.
            "involved_agent_ids": [agent.agent_id, params.employee_id],
        },
    )
    return ToolOutcome(
        result={
            "business": business.business_id,
            "employee": params.employee_id,
            "wage_sim": params.wage_sim,
            "every_days": params.every_days,
            "first_due_tick": world.tick() + params.every_days * TICKS_PER_DAY,
            "replaced": previous.wage_sim if previous else None,
        },
        events=[event],
    )


async def pay_wage_handler(
    world: WorldState, agent: AgentLifeRecord, params: PayWageParams
) -> ToolOutcome:
    """Hand over the money, by hand, from the business account.

    The employee need not be present - wages go to their account - but the
    payer has to be at the premises, because a business account is a till
    and somebody has to be standing at it.
    """
    business = _business_at_hand(
        world, "pay_wage", agent, params.business_id, owner_only=False
    )
    if params.employee_id not in business.employee_agent_ids:
        name = world.agent_names.get(params.employee_id, params.employee_id)
        raise ActorRequirementError(
            "pay_wage",
            [f"{name} is not an employee of {business.name}. `transfer` pays anyone."],
        )

    held = world.ledger.sim_balance(business.business_id)
    if held < params.amount_sim:
        raise ToolValidationError(
            "pay_wage",
            [
                f"{business.name} holds {held} sim, not {params.amount_sim}. "
                "`transfer` from your own hand is the other way to pay."
            ],
        )

    key = f"{business.business_id}|{params.employee_id}"
    promise = world.wage_promises.get(key)
    owed_before = promise.owed_sim if promise else 0
    days_overdue_before = promise.days_overdue(world.tick()) if promise else 0

    await world.ledger.move(
        from_account_id=business.business_id,
        to_account_id=params.employee_id,
        amount=params.amount_sim,
        currency=Currency.SIM,
        reason=TransactionReason.WAGE,
        tick=world.tick(),
        memo=f"wage to {params.employee_id}",
    )

    gift = 0
    if promise is not None:
        # Paying more than owed clears the debt and records the rest as a
        # gift rather than as credit against future wages: a generous
        # employer this week is not thereby excused next week, and the
        # trace should be able to see the generosity as generosity.
        applied = min(params.amount_sim, promise.accrued_sim - promise.paid_sim)
        gift = params.amount_sim - max(applied, 0)
        promise.paid_sim += max(applied, 0)
        promise.last_paid_tick = world.tick()

    event = WorldEvent(
        type="business.wage.paid",
        tick=world.tick(),
        payload={
            "business_id": business.business_id,
            "payer_id": agent.agent_id,
            "agent_id": agent.agent_id,
            "employee_id": params.employee_id,
            "amount_sim": params.amount_sim,
            "promised_sim": promise.wage_sim if promise else None,
            "owed_before_sim": owed_before,
            "owed_after_sim": promise.owed_sim if promise else 0,
            "days_since_last_pay": days_overdue_before,
            "shortfall_sim": max(0, promise.owed_sim if promise else 0),
            "gift_sim": gift,
            "involved_agent_ids": [agent.agent_id, params.employee_id],
        },
    )
    return ToolOutcome(
        result={
            "paid": params.amount_sim,
            "to": params.employee_id,
            "owed_before": owed_before,
            "owed_after": promise.owed_sim if promise else 0,
            "days_overdue_before": days_overdue_before,
            "gift_sim": gift,
            "business_sim_after": world.ledger.sim_balance(business.business_id),
        },
        events=[event],
    )


async def check_debts_handler(
    world: WorldState, agent: AgentLifeRecord, params: CheckDebtsParams
) -> ToolOutcome:
    """What you owe and what you are owed. Read-only, and free.

    C2. The 400-sim story - a debt carried unspoken for five weeks, then
    named, acknowledged and repaid - had to be reconstructed by a human
    reading transcripts, because nothing in the world recorded that the
    transfer was a loan. `transfer` now takes a `reason`, and this reads it
    back.
    """
    return ToolOutcome(result=world.debts_of(agent.agent_id), events=[])


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="set_wage",
            description=(
                # Addendum section 5, 77 words.
                "Promise an employee of a business you own a wage: so much sim "
                "every so many game-days, payable at the premises. This records the "
                "promise only; nothing is paid until you call `pay_wage`. The world "
                "tells the employee what you promised and, each day, whether it was "
                "paid. To grant someone access to work your business's tools use "
                "`hire_employee`; a promise without a grant is refused. Cost: 0."
            ),
            params_model=SetWageParams,
            handler=set_wage_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,  # resolved against the named business, in the handler
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="pay_wage",
            description=(
                "Pay sim from a business you own or work for into an employee's "
                "hand, standing at the premises. The employee need not be present. "
                "This is the act the promise made by `set_wage` waits for; the "
                "world records how much was owed, how much was paid, and how late. "
                "To give sim to anyone for any other reason use `transfer`. Cost: 0."
            ),
            params_model=PayWageParams,
            handler=pay_wage_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="check_debts",
            description=(
                # Addendum section 8.
                # ECON-27 cut this back to what the town could actually
                # do, because it had promised two things it could not:
                # upkeep arrears nothing recorded (ECON-15) and a treasury
                # debt payable by `transfer`, which refuses every non-agent
                # recipient (ECON-14). A resident that follows a
                # description into a refusal has been misled by its own
                # toolbox.
                #
                # Both are now closed - `record_upkeep_arrears` writes the
                # shortfall and `pay_tax` settles the treasury - so the
                # sentence may name the route again. It names `pay_tax`
                # and NOT `transfer` for the treasury: that was the exact
                # wrong turn ECON-14 is about.
                "List what you owe and what is owed to you: head tax owed, unpaid "
                "upkeep, wages promised to you or by you, and loans recorded with "
                "`transfer`. Amounts and who, nothing else. Free to call. Head tax is "
                "paid with `pay_tax`; wages you owe with `pay_wage`; a debt to another "
                "resident with `transfer`."
            ),
            params_model=CheckDebtsParams,
            handler=check_debts_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
