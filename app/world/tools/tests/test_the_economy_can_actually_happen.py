"""Every economic chain the programme needs, run end to end, once.

`docs/GOALS.md` reports labour, the knowledge market and in-world
institutions as **not reached**, and the trace agrees: across runs 25-28b
`hire_employee`, `pay_wage`, `set_wage`, `teach_skill`, `license_tool`,
`sell_tool`, `register_business` and `register_org` were called **zero
times** between them, while the retail verbs ran hundreds of times.

"Never called" and "does not work" are different findings with different
fixes - one is a reach problem for the evals and the prompt, the other is
a defect - and nothing in this repository could tell them apart. On
2026-09-14 each chain below was walked by hand and every one of them
worked; this file is that walk, kept, so the distinction stays a fact
rather than a memory.

What it is NOT: a claim that residents do any of this. It is the floor
under the claim that they could. If one of these ever fails, the
programme's next stage is blocked on a defect and this says so on the
next test run rather than after a $10 run.
"""

from __future__ import annotations

import pytest

from world.tools.models import Business
from world.tools.tests.conftest import in_conversation, move_agent, spawn_agent

FIELD = "Kasukabe Farmland"
COMPLEX = "Kasukabe Government Complex"


async def _a_shop(world, owner: str, business_id: str = "b_shop") -> None:
    world.businesses[business_id] = Business(
        business_id=business_id, name="Kasukabe Grocery", business_type="grocery",
        owner_agent_id=owner, location_id=FIELD,
    )
    await world.ledger.register_business(business_id, owner, fee_sim=1, tick=world.tick())
    await world.ledger.mint_sim(business_id, 5_000, tick=world.tick())


@pytest.mark.asyncio
async def test_money_becomes_a_meal(world, registry):
    """ECON-4, the broken rung. Run 28 reported "you have no food" in 89%
    of perceptions while the bakery shelf held twenty bread, and three
    runs could not convert sim into a meal. The physics does."""
    await spawn_agent(world, "n01", FIELD)
    await spawn_agent(world, "n02", FIELD)
    await _a_shop(world, "n01")
    world.inventory.credit("b_shop", "bread", 10)
    opening = world.ledger.sim_balance("n02")

    await registry.execute(world, "n01", "post_price", {
        "business_id": "b_shop", "item": "bread", "ask_sim": 10})
    in_conversation(world, "n02", "n01")
    await registry.execute(world, "n02", "buy", {
        "seller_id": "b_shop", "item": "bread", "quantity": 1})
    eaten = await registry.execute(world, "n02", "eat", {"item": "bread"})

    assert eaten.result["ate"] == "bread"
    assert world.ledger.sim_balance("n02") < opening, "the meal was free"
    assert world.inventory.quantity("b_shop", "bread") == 9


@pytest.mark.asyncio
async def test_a_wage_is_promised_and_paid(world, registry):
    """ECON-5. An apprenticeship was agreed aloud in run 28 and never
    became an act; no run has ever employed anybody. Hiring takes the
    employee's consent, which is why it cannot happen without a
    conversation first - and that is a feature, not the blocker."""
    await spawn_agent(world, "n01", FIELD)
    await spawn_agent(world, "n02", FIELD)
    await _a_shop(world, "n01")
    opening = world.ledger.sim_balance("n02")

    await registry.execute(world, "n01", "hire_employee", {
        "business_id": "b_shop", "employee_agent_id": "n02", "employee_consent": True})
    await registry.execute(world, "n01", "set_wage", {
        "business_id": "b_shop", "employee_id": "n02", "wage_sim": 20})
    paid = await registry.execute(world, "n01", "pay_wage", {
        "business_id": "b_shop", "employee_id": "n02", "amount_sim": 20})

    assert paid.result["paid"] == 20
    assert "n02" in world.businesses["b_shop"].employee_agent_ids
    assert world.ledger.sim_balance("n02") == opening + 20


@pytest.mark.asyncio
async def test_knowledge_changes_hands_for_money(world, registry):
    """ECON-7. Every authored artifact in every run is held by exactly one
    resident, and the two verbs that would move one have never been
    called. Both work, both charge, and both need the other party's
    consent or fee."""
    await spawn_agent(world, "n01", FIELD)
    await spawn_agent(world, "n02", FIELD)
    teacher_opening = world.ledger.sim_balance("n01")

    student_opening = world.ledger.sim_balance("n02")

    await registry.execute(world, "n01", "skillify", {
        "name": "mill_round", "preconditions": "at the mill",
        "steps": [{"order": 1, "kind": "tool", "ref": "look_around", "note": "see the mill"}]})
    taught = await registry.execute(world, "n01", "teach_skill", {
        "student_agent_id": "n02", "skill": "mill_round",
        "student_consent": True, "price_sim": 5})

    await registry.execute(world, "n01", "toolify", {
        "name": "look_twice", "description": "look, then look again",
        "steps": [{"tool_name": "look_around", "params": {}}]})
    licensed = await registry.execute(world, "n01", "license_tool", {
        "tool_name": "look_twice", "licensee_id": "n02", "fee_sim": 5})

    assert taught.result["skill"] == "mill_round"
    assert "mill_round" in world.skills.get("n02", {}), "the student learned nothing"
    assert licensed.result["fee_paid"] == 5
    assert world.ledger.sim_balance("n02") == student_opening - 10, (
        "the money for the lesson and the licence did not move"
    )
    assert world.ledger.sim_balance("n01") < teacher_opening, (
        "teaching at 5 sim is no longer loss-making - the price table moved, "
        "and ECON-7's floor moved with it"
    )


@pytest.mark.asyncio
async def test_a_resident_can_found_an_institution(world, registry):
    """ECON-6 and GOV-9. Every business in the town is one genesis seeded:
    `register_business` and `register_org` have never been called. Both
    work, at the government complex, for a fee."""
    await spawn_agent(world, "n01", FIELD)
    await move_agent(world, "n01", COMPLEX)

    business = await registry.execute(world, "n01", "register_business", {
        "business_id": "b_second", "name": "Second Shop", "business_type": "grocery"})
    org = await registry.execute(world, "n01", "register_org", {
        "kind": "society", "name": "The Millers", "charter_text": "we mill, together"})

    assert business.result["business_id"] == "b_second"
    assert world.businesses["b_second"].owner_agent_id == "n01"
    assert org.result, "no organisation came back"


@pytest.mark.asyncio
async def test_a_theft_accuses_nobody_until_somebody_names_a_name(world, registry):
    """GOV-8. The governance review found a theft case recorded AGAINST
    THE VICTIM, so an arrest could only ever reach the person who was
    robbed. That is fixed, and this is the guard: a fresh theft names no
    accused, and `arrest` refuses a case that names nobody."""
    from world.tools.errors import ToolError

    await spawn_agent(world, "n01", FIELD)
    await spawn_agent(world, "n02", FIELD)
    from world.tools.models import Storage

    world.storages["st_n02"] = Storage(
        storage_id="st_n02", owner_id="n02", location_id=FIELD)
    world.inventory.credit("st_n02", "bread", 3)
    await move_agent(world, "n02", COMPLEX)
    storage = world.storages["st_n02"]

    await registry.execute(world, "n01", "steal", {
        "storage_id": storage.storage_id, "item": "bread", "quantity": 1})
    case = next(c for c in world.crime_cases if c.kind == "theft")

    assert case.against_agent_id == "", "a fresh theft accuses somebody"
    assert case.victim_agent_id == "n02"
    assert case.ground_truth_perpetrator_id == "n01"
    with pytest.raises(ToolError):
        await registry.execute(world, "n01", "arrest", {
            "case_id": case.case_id, "agent_id": "n02"})


@pytest.mark.asyncio
async def test_a_completed_day_takes_its_cut(tmp_path):
    """ECON-11, the rung no run has ever reached.

    Upkeep, head tax and arrears are charged at the day boundary, which is
    tick 144. Run 29 stopped at tick 77 of 144, having traded 12 times,
    stocked 18 shelves and fed 20 residents - so the busiest economy this
    project has produced still paid nothing for existing, and the whole
    day-boundary half of `run_genesis` has never executed in a live run.
    At run 29's rate a game-day costs about $70, so finding out the hard
    way is a $70 question and this is the cheap answer to it.

    What is NEW here rather than already covered:
    `test_the_tax_is_real_and_the_gate_knows_it` charges the tax on a town
    where everybody can pay, and `life/tests/test_upkeep.py` charges
    upkeep with no tax in sight. Neither runs the two IN ORDER, which is
    the thing `day_jobs.run_day_boundary_jobs` says out loud is load
    bearing: "upkeep first, so a household spends its money on food
    before the government takes its cut and a shortfall shows up as
    hunger rather than as tax debt." And nothing anywhere has ever made
    arrears HAPPEN - `world_state.arrears` is written by hand in the one
    test that reads it, so the path from "cannot pay" to a recorded debt
    has never run.
    """
    from world.core.clock import TICKS_PER_DAY, timestamp_for
    from world.ledger.head_tax import HEAD_TAX_BASE_SIM, head_tax_sim_per_head
    from world.ledger.models import Currency, TransactionReason
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    state, ledger = runner.world_state, runner.ledger
    assert state is not None and ledger is not None

    stamp = timestamp_for(TICKS_PER_DAY)
    assert stamp.game_day == 1 and stamp.day_tick == 0, (
        "tick 144 is the first day boundary; if this moved, the run length "
        "every one of these findings is measured against moved with it"
    )

    policy = await state.charter.store.get_policy()
    owed_per_head = head_tax_sim_per_head(policy.tax_rate_bps)
    assert owed_per_head > 0, "a town at 0 bps cannot put anybody in arrears"

    households = {
        agent.household_id: [
            a.agent_id for a in state.agents.values()
            if a.alive and a.household_id == agent.household_id
        ]
        for agent in state.agents.values()
        if agent.alive and agent.household_id
    }
    broke_household, broke_members = next(iter(sorted(households.items())))
    for member in broke_members:
        held = ledger.sim_balance(member)
        if held > 0:
            await ledger.move(
                from_account_id=member, to_account_id="government:treasury",
                amount=held, currency=Currency.SIM,
                reason=TransactionReason.TRANSFER, tick=stamp.tick_index,
                memo="emptied by the test, so the day finds somebody short",
            )
        assert ledger.sim_balance(member) == 0

    solvent = [
        a.agent_id for a in state.agents.values()
        if a.alive and a.agent_id not in broke_members
    ]
    assert solvent, "the town has to have somebody who CAN pay"
    paid_before = {who: ledger.sim_balance(who) for who in solvent}
    treasury_before = ledger.sim_balance("government:treasury")

    await runner.run_daily_upkeep(stamp, runner.population_dials)
    await runner.charge_the_day_tax(stamp)

    assert any(
        ledger.sim_balance(who) < paid_before[who] for who in solvent
    ), "a completed game-day took nothing from anybody who could pay"

    record = runner.day_jobs_ran[stamp.game_day]
    assert record["households"] > 0
    assert record["head_tax"] == record["households"], (
        f"head tax reached {record['head_tax']} of {record['households']} "
        "household(s) - S1 halts a run on exactly this"
    )
    assert record["head_tax_sim"] > 0
    assert ledger.sim_balance("government:treasury") > treasury_before

    debtors = {
        who: owed["head_tax"]
        for who, owed in state.arrears.items()
        if owed.get("head_tax")
    }
    assert debtors, (
        "an emptied household paid its head tax out of nothing; the debt "
        "path from `charge_daily_head_tax`'s new_debts into "
        "`world_state.arrears` has never run in any test or any run"
    )
    debtor = next(who for who in debtors if who in broke_members)
    assert debtors[debtor] >= owed_per_head
    for member in broke_members:
        assert ledger.sim_balance(member) >= 0, (
            "a resident who could not pay went NEGATIVE; unpaid tax is a "
            "recorded debt, never a silent overdraft"
        )

    runner.assert_the_day_was_charged(stamp)
    assert runner.halt_reason is None, runner.halt_reason

    assert owed_per_head == HEAD_TAX_BASE_SIM * policy.tax_rate_bps // 10_000
