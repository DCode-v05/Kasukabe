"""A5 and C2: a promise, a payment, and the gap between them.

`hire_employee` grants access to a business's tools and pays nobody. A
student was told "your work is at Oak Hill College" and nothing there
earned; jobs were labels. So promise-keeping under scarcity - standing
question 23, and Charness & Dufwenberg's finding that a bare promise
raises follow-through to a majority - had nothing to keep and nothing to
break.

The world never pays on its own. It counts. That is why `set_wage` and
`pay_wage` are two acts rather than one automatic payroll: the trace can
now tell "hired and paid" from "hired and never promised" from "promised
and never paid", and only the last of those is a broken promise.
"""

from __future__ import annotations

import pytest

from world.core.clock import TICKS_PER_DAY
from world.tools.errors import (
    ActorRequirementError,
    LocationGateError,
    ToolValidationError,
)
from world.tools.models import Business
from world.tools.state import WorldState

from .conftest import spawn_agent

SHOP = "Kasukabe Town Square"
ELSEWHERE = "Kasukabe Farmland"


async def _shop_with_staff(
    world: WorldState, *, hire: bool = True, till_sim: int = 5_000
) -> Business:
    await spawn_agent(world, "n05", SHOP)
    world.agent_names["n05"] = "Marta Thorne"
    await spawn_agent(world, "n14", SHOP, starting_sim=100)
    world.agent_names["n14"] = "Aldous Vance"
    await world.ledger.register_business("b_bakery", "n05", fee_sim=1, tick=world.tick())
    if till_sim:
        await world.ledger.mint_sim("b_bakery", till_sim, tick=world.tick())
    business = Business(
        business_id="b_bakery", name="Thorne's Bakery", business_type="bakery",
        owner_agent_id="n05", location_id=SHOP,
    )
    if hire:
        business.employee_agent_ids.add("n14")
    world.businesses["b_bakery"] = business
    return business


@pytest.mark.asyncio
async def test_a_promise_is_not_a_payment(world: WorldState, registry) -> None:
    """The spec's first named test, and the design in one assertion."""
    await _shop_with_staff(world)
    before = world.ledger.sim_balance("n14")

    await registry.execute(world, "n05", "set_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
    })

    assert world.ledger.sim_balance("n14") == before, "the world paid on its own"
    promise = world.wage_promises["b_bakery|n14"]
    assert promise.wage_sim == 40
    assert promise.paid_sim == 0
    assert promise.owed_sim == 0, "nothing is owed until a period comes round"


@pytest.mark.asyncio
async def test_a_promise_needs_a_grant_first(world: WorldState, registry) -> None:
    """`hire_employee` stays an access grant and a wage is a second act, so
    the trace can tell "hired and never promised" from "never hired"."""
    await _shop_with_staff(world, hire=False)

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "n05", "set_wage", {
            "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
        })

    assert "hire_employee" in str(exc.value)


@pytest.mark.asyncio
async def test_only_the_owner_promises_a_wage(world: WorldState, registry) -> None:
    await _shop_with_staff(world)

    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "n14", "set_wage", {
            "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 4_000,
        })


@pytest.mark.asyncio
async def test_promises_are_made_on_the_premises(world: WorldState, registry) -> None:
    await _shop_with_staff(world)
    await world.event_bus.emit(
        "agent.moved", tick=1, payload={"agent_id": "n05", "location_id": ELSEWHERE}
    )

    with pytest.raises(LocationGateError):
        await registry.execute(world, "n05", "set_wage", {
            "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
        })


@pytest.mark.asyncio
async def test_a_missed_payday_shows_up_as_a_debt_on_both_sides(
    world: WorldState, registry
) -> None:
    """The spec's second named test. The employer sees "you owe", the
    employee sees "you are owed", and neither had to be told by the other."""
    await _shop_with_staff(world)
    await registry.execute(world, "n05", "set_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
    })

    world.current_tick = TICKS_PER_DAY * 2
    world.accrue_wages(game_day=2, now_tick=world.current_tick)

    employee = await registry.execute(world, "n14", "check_debts", {})
    owed_to_them = [r for r in employee.result["owed_to_you"] if r["kind"] == "wage"]
    assert owed_to_them and owed_to_them[0]["sim"] == 80

    employer = await registry.execute(world, "n05", "check_debts", {})
    owed_by_them = [r for r in employer.result["owed_by_you"] if r["kind"] == "wage"]
    assert owed_by_them and owed_by_them[0]["sim"] == 80
    assert owed_by_them[0]["to"] == "n14"


@pytest.mark.asyncio
async def test_paying_clears_what_was_owed(world: WorldState, registry) -> None:
    await _shop_with_staff(world)
    await registry.execute(world, "n05", "set_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
    })
    world.current_tick = TICKS_PER_DAY
    world.accrue_wages(game_day=1, now_tick=world.current_tick)
    before = world.ledger.sim_balance("n14")

    outcome = await registry.execute(world, "n05", "pay_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "amount_sim": 40,
    })

    assert world.ledger.sim_balance("n14") == before + 40
    assert outcome.result["owed_before"] == 40
    assert outcome.result["owed_after"] == 0


@pytest.mark.asyncio
async def test_paying_more_than_owed_clears_the_debt_and_records_the_gift(
    world: WorldState, registry
) -> None:
    """The spec's third named test. Generosity this week does not buy an
    excuse next week, so the excess is a gift rather than credit."""
    await _shop_with_staff(world)
    await registry.execute(world, "n05", "set_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
    })
    world.current_tick = TICKS_PER_DAY
    world.accrue_wages(game_day=1, now_tick=world.current_tick)

    outcome = await registry.execute(world, "n05", "pay_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "amount_sim": 100,
    })

    assert outcome.result["owed_after"] == 0
    assert outcome.result["gift_sim"] == 60
    world.accrue_wages(game_day=2, now_tick=TICKS_PER_DAY * 2)
    assert world.wage_promises["b_bakery|n14"].owed_sim == 40


@pytest.mark.asyncio
async def test_a_stranger_cannot_pay_wages_from_a_business_they_do_not_own(
    world: WorldState, registry
) -> None:
    """The spec's fourth named test."""
    await _shop_with_staff(world)
    await spawn_agent(world, "n09", SHOP)

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "n09", "pay_wage", {
            "business_id": "b_bakery", "employee_id": "n14", "amount_sim": 40,
        })

    assert "transfer" in str(exc.value)


@pytest.mark.asyncio
async def test_a_till_that_cannot_cover_it_names_the_other_way_to_pay(
    world: WorldState, registry
) -> None:
    await _shop_with_staff(world, till_sim=0)
    await registry.execute(world, "n05", "set_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
    })

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n05", "pay_wage", {
            "business_id": "b_bakery", "employee_id": "n14", "amount_sim": 40,
        })

    assert "transfer" in str(exc.value)


@pytest.mark.asyncio
async def test_an_employee_can_pay_the_wages_but_not_promise_them(
    world: WorldState, registry
) -> None:
    """Paying is a shop errand; promising is the owner's word."""
    business = await _shop_with_staff(world)
    business.employee_agent_ids.add("n09")
    await spawn_agent(world, "n09", SHOP)
    await registry.execute(world, "n05", "set_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
    })
    world.accrue_wages(game_day=1, now_tick=TICKS_PER_DAY)

    outcome = await registry.execute(world, "n09", "pay_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "amount_sim": 40,
    })
    assert outcome.result["paid"] == 40

    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "n09", "set_wage", {
            "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 1,
        })


@pytest.mark.asyncio
async def test_accruing_twice_for_the_same_day_does_not_bill_twice(
    world: WorldState, registry
) -> None:
    """The reconciliation runs at a day boundary and the runner is
    restartable, so a second call for the same day must be a no-op."""
    await _shop_with_staff(world)
    await registry.execute(world, "n05", "set_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
    })

    world.accrue_wages(game_day=1, now_tick=TICKS_PER_DAY)
    world.accrue_wages(game_day=1, now_tick=TICKS_PER_DAY)

    assert world.wage_promises["b_bakery|n14"].owed_sim == 40


@pytest.mark.asyncio
async def test_a_loan_appears_in_both_parties_debt_views(
    world: WorldState, registry
) -> None:
    """The spec's named test for C2.

    The 400-sim debt is the best thing the series produced - carried
    unspoken for five weeks, then named, acknowledged and paid - and a
    human had to reconstruct it from transcripts, because the world
    recorded a transfer and nothing more.
    """
    await spawn_agent(world, "n01", SHOP, starting_sim=2_000)
    await spawn_agent(world, "n03", SHOP, starting_sim=0)

    await registry.execute(world, "n01", "transfer", {
        "to_agent_id": "n03", "amount_sim": 400, "reason": "loan",
    })

    lender = await registry.execute(world, "n01", "check_debts", {})
    borrower = await registry.execute(world, "n03", "check_debts", {})

    assert lender.result["owed_to_you"] == [
        {"kind": "loan", "from": "n03", "sim": 400, "since_tick": 0}
    ]
    assert borrower.result["owed_by_you"] == [
        {"kind": "loan", "to": "n01", "sim": 400, "since_tick": 0}
    ]
    assert borrower.result["due_today_sim"] == 400


@pytest.mark.asyncio
async def test_repaying_a_loan_clears_it(world: WorldState, registry) -> None:
    await spawn_agent(world, "n01", SHOP, starting_sim=2_000)
    await spawn_agent(world, "n03", SHOP, starting_sim=1_000)
    await registry.execute(world, "n01", "transfer", {
        "to_agent_id": "n03", "amount_sim": 400, "reason": "loan",
    })

    outcome = await registry.execute(world, "n03", "transfer", {
        "to_agent_id": "n01", "amount_sim": 400, "reason": "payment",
    })

    assert outcome.result["repaid_sim"] == 400
    borrower = await registry.execute(world, "n03", "check_debts", {})
    assert borrower.result["owed_by_you"] == []


@pytest.mark.asyncio
async def test_paying_back_more_than_was_borrowed_is_not_recorded_as_debt(
    world: WorldState, registry
) -> None:
    """Anything past the principal is just a payment. A resident that
    overpays must not read as having overpaid a debt it had cleared."""
    await spawn_agent(world, "n01", SHOP, starting_sim=2_000)
    await spawn_agent(world, "n03", SHOP, starting_sim=1_000)
    await registry.execute(world, "n01", "transfer", {
        "to_agent_id": "n03", "amount_sim": 400, "reason": "loan",
    })

    outcome = await registry.execute(world, "n03", "transfer", {
        "to_agent_id": "n01", "amount_sim": 600, "reason": "payment",
    })

    assert outcome.result["repaid_sim"] == 400


@pytest.mark.asyncio
async def test_an_untagged_transfer_is_still_a_transfer(
    world: WorldState, registry
) -> None:
    """Forcing a resident to classify every gift would make the tag a
    formality rather than a statement."""
    await spawn_agent(world, "n01", SHOP, starting_sim=2_000)
    await spawn_agent(world, "n03", SHOP)

    outcome = await registry.execute(world, "n01", "transfer", {
        "to_agent_id": "n03", "amount_sim": 50,
    })

    assert outcome.result["reason"] == "other"
    assert world.loans == {}


@pytest.mark.asyncio
async def test_a_reason_nobody_recognises_is_refused_with_the_list(
    world: WorldState, registry
) -> None:
    await spawn_agent(world, "n01", SHOP, starting_sim=2_000)
    await spawn_agent(world, "n03", SHOP)

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n01", "transfer", {
            "to_agent_id": "n03", "amount_sim": 50, "reason": "tribute",
        })

    assert "gift" in str(exc.value) and "loan" in str(exc.value)


@pytest.mark.asyncio
async def test_check_debts_is_free(world: WorldState, registry) -> None:
    """An agent too poor to act must not also be too poor to find out
    why - that is exactly when borrowing, selling and asking start."""
    await spawn_agent(world, "n01", SHOP, starting_sim=0)

    outcome = await registry.execute(world, "n01", "check_debts", {})

    assert world.ledger.sim_balance("n01") == 0
    assert outcome.result["due_today_sim"] == 0


@pytest.mark.asyncio
async def test_wages_and_loans_survive_a_checkpoint(world: WorldState, registry) -> None:
    await _shop_with_staff(world)
    await registry.execute(world, "n05", "set_wage", {
        "business_id": "b_bakery", "employee_id": "n14", "wage_sim": 40,
    })
    await registry.execute(world, "n05", "transfer", {
        "to_agent_id": "n14", "amount_sim": 100, "reason": "loan",
    })

    saved = world.snapshot()
    world.wage_promises.clear()
    world.loans.clear()
    world.restore(saved)

    assert world.wage_promises["b_bakery|n14"].wage_sim == 40
    assert world.loans["n05|n14"].outstanding_sim == 100
