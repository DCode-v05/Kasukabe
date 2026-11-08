"""The wage notice points at verbs that work, on the staff genesis gave.

`format_standing_brief` now tells an owner with staff that "an employer may
`set_wage` and `pay_wage`, at its own premises". A notice that walks a
resident into a refusal is worse than no notice - it spends a paid turn and
teaches the resident the verb is not for it.

The doubt worth checking: `set_wage`'s own description says "a promise
without a grant is refused. To grant someone access to work your business's
tools use `hire_employee`", and election30 recorded 0 hires. If a genesis
employee needed hiring before it could be paid, every owner reading the new
line would hit that wall.

It does not. The gate is `business.employee_agent_ids`, which genesis fills
straight from the cast file, so the people the brief calls staff are exactly
the people `set_wage` accepts. This pins that the two agree.
"""

from __future__ import annotations

import pytest

from world.tools.models import Business
from world.tools.tests.conftest import spawn_agent

PREMISES = "business:b_mill"


async def _a_mill_with_staff(world, till_sim: int = 5000):
    """A business the way genesis makes one: employees named in the cast
    file, never a `hire_employee` call."""
    world.businesses["b_mill"] = Business(
        business_id="b_mill",
        name="The Mill",
        business_type="mill",
        owner_agent_id="owner",
        employee_agent_ids={"worker"},
        location_id=PREMISES,
    )
    await spawn_agent(world, "owner", PREMISES)
    await spawn_agent(world, "worker", PREMISES)
    world.ledger.open_business("b_mill", "owner")
    world.ledger.get_account("b_mill").sim_units = till_sim


@pytest.mark.asyncio
async def test_a_wage_can_be_promised_to_somebody_genesis_hired(world, registry) -> None:
    await _a_mill_with_staff(world)

    await registry.execute(world, "owner", "set_wage", {
        "business_id": "b_mill", "employee_id": "worker",
        "wage_sim": 40, "every_days": 1,
    })


@pytest.mark.asyncio
async def test_and_then_actually_paid(world, registry) -> None:
    """election30 paid 0 wages. This is the whole path it never walked."""
    await _a_mill_with_staff(world)
    before = world.ledger.sim_balance("worker")

    await registry.execute(world, "owner", "set_wage", {
        "business_id": "b_mill", "employee_id": "worker",
        "wage_sim": 40, "every_days": 1,
    })
    await registry.execute(world, "owner", "pay_wage", {
        "business_id": "b_mill", "employee_id": "worker", "amount_sim": 40,
    })

    assert world.ledger.sim_balance("worker") == before + 40


@pytest.mark.asyncio
async def test_the_brief_and_the_verb_agree_on_who_the_staff_are(world, registry) -> None:
    """The brief's `staff` list and `set_wage`'s gate read the same field.

    If these ever diverge, the notice starts naming people the verb will
    refuse - which is the failure mode this whole file exists to rule out.
    """
    await _a_mill_with_staff(world)
    business = world.businesses["b_mill"]

    for employee in business.employee_agent_ids:
        await registry.execute(world, "owner", "set_wage", {
            "business_id": "b_mill", "employee_id": employee,
            "wage_sim": 10, "every_days": 1,
        })


@pytest.mark.asyncio
async def test_somebody_who_does_not_work_there_is_still_refused(world, registry) -> None:
    """The grant rule is intact - this widens nothing."""
    from world.tools.errors import ActorRequirementError

    await _a_mill_with_staff(world)
    await spawn_agent(world, "stranger", PREMISES)

    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "owner", "set_wage", {
            "business_id": "b_mill", "employee_id": "stranger",
            "wage_sim": 40, "every_days": 1,
        })
