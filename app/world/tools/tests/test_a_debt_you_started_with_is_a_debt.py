"""TOOL-18: the debt reader itself is sound; the store was empty.

The finding is that `check_debts` "cannot know a genesis debt": twenty
calls in run 23 returned nothing while a 400-sim debt sat in persona prose
only, and it was paid twice by two different people because neither the
debtor nor the creditor could look it up. The review pins that on
`debts_of` (state.py) reading wage promises, `self.loans` and `arrears`
and nothing else - and the missing half is the SEEDING: `world.loans` is
never populated at genesis, which run_genesis.py owns.

Read closely, `debts_of` has no defect of its own. It iterates
`self.loans.values()` with no test of where a row came from, so a
`LoanRecord` seeded straight into `world.loans` reads back for the
borrower and the lender exactly as one created by `transfer(reason=
"loan")` does; `loans` is in `OWNED_STATE_FIELDS`, so a seeded debt
survives a checkpoint resume; and `record_repayment` clears a seeded row
on the same path a transferred one takes. Verified on this checkout
before writing anything: a hand-seeded `n02|n01` loan of 400 came back
from `check_debts` on both sides, and repaying it through the real
`transfer` cleared it.

So this file is not a fix. It is the handshake with the seeding half:
every existing loan test in `test_wages_and_debts.py` creates its loan by
calling `transfer`, so all of them would still pass if `debts_of` were
narrowed to loans it could prove a transfer had produced - and the genesis
debt would silently vanish again. These pin the provenance-blind
behaviour the seeder depends on, and the exact key format it has to write
under for the debt to be repayable.
"""

from __future__ import annotations

import pytest

from world.tools.models import LoanRecord
from world.tools.state import WorldState

from .conftest import spawn_agent

SHOP = "Kasukabe Government Complex"


def _seed_genesis_loan(
    world: WorldState, lender: str, borrower: str, principal: int
) -> None:
    """What run_genesis.py's genesis `loans:` block writes.

    The key is `"{lender}|{borrower}"` because that is what
    `WorldState.record_loan` builds and what `record_repayment` looks up -
    a row filed under any other key would still be READ back by
    `debts_of`, which iterates values, and would then be impossible to
    repay, because repayment is a keyed lookup.
    """
    world.loans[f"{lender}|{borrower}"] = LoanRecord(
        lender_id=lender, borrower_id=borrower, principal_sim=principal, since_tick=0
    )


@pytest.mark.asyncio
async def test_a_debt_nobody_transferred_still_reaches_both_parties(
    world: WorldState, registry
) -> None:
    """Through `check_debts`, the tool - not `debts_of` directly - because
    the tool is the only way a resident ever asks."""
    await spawn_agent(world, "n01", SHOP, starting_sim=2_000)
    await spawn_agent(world, "n03", SHOP, starting_sim=0)
    _seed_genesis_loan(world, lender="n01", borrower="n03", principal=400)

    lender = await registry.execute(world, "n01", "check_debts", {})
    borrower = await registry.execute(world, "n03", "check_debts", {})

    assert lender.result["owed_to_you"] == [
        {"kind": "loan", "from": "n03", "sim": 400, "since_tick": 0}
    ], "the creditor cannot see the debt it is owed"
    assert borrower.result["owed_by_you"] == [
        {"kind": "loan", "to": "n01", "sim": 400, "since_tick": 0}
    ], "the debtor cannot see the debt it carries - run 23's twenty empty calls"
    assert borrower.result["due_today_sim"] == 400


@pytest.mark.asyncio
async def test_a_genesis_debt_can_actually_be_paid_off(
    world: WorldState, registry
) -> None:
    """And paid ONCE. The 400-sim debt was settled twice by two people
    because the world held no row either of them could watch go to zero.
    """
    await spawn_agent(world, "n01", SHOP, starting_sim=0)
    await spawn_agent(world, "n03", SHOP, starting_sim=1_000)
    _seed_genesis_loan(world, lender="n01", borrower="n03", principal=400)
    assert world.debts_of("n03")["due_today_sim"] == 400, (
        "the debt was never visible in the first place; this test would "
        "then prove nothing by watching it disappear"
    )

    await registry.execute(world, "n03", "transfer", {
        "to_agent_id": "n01", "amount_sim": 400, "reason": "payment",
    })

    assert world.loans["n01|n03"].repaid_sim == 400, (
        "the payment did not land against the seeded row; a genesis debt "
        "filed under a key `record_repayment` cannot look up is a debt "
        "nobody can ever clear"
    )
    assert world.debts_of("n03")["owed_by_you"] == []
    assert world.debts_of("n01")["owed_to_you"] == []


@pytest.mark.asyncio
async def test_a_genesis_debt_survives_a_resume(world: WorldState, registry) -> None:
    """`loans` is owned state. A resumed run that dropped it would clear
    the town's opening debts by restarting it."""
    await spawn_agent(world, "n01", SHOP)
    await spawn_agent(world, "n03", SHOP)
    _seed_genesis_loan(world, lender="n01", borrower="n03", principal=400)

    saved = world.snapshot()
    world.loans.clear()
    world.restore(saved)

    outcome = await registry.execute(world, "n03", "check_debts", {})
    assert outcome.result["owed_by_you"][0]["sim"] == 400
