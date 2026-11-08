"""A seeded job was a label. Nothing accrued against it, so nobody had income.

Genesis has always written `employee_agent_ids` and never a wage. The world
counts `accrued_sim` against a `WagePromise`, and there was no promise to
count against - so ECON-5 read `0 hire(s), 0 wage payment(s)` in every run,
and the town's only income was whatever residents could sell each other.

In the last 50-resident run a miller said it out loud:

    t334 Rosalind Vance: "No wage promise yet, Milo. If you want work, come
    to the mill and we can count a specific task against what the till can
    pay."

She wanted to pay and had nothing to pay against.

This is the same argument as opening stock. A town whose shops open empty
starves before it can trade; a town whose employees open unwaged has no
income before it can earn one.

What this does NOT do is pay anybody. `paid_sim` still moves only when
somebody calls `pay_wage` - which is the measurement ECON-5 exists to take,
and seeding the payment would make that check answer itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from world.run_genesis import OPENING_WAGE_SIM

APP = Path(__file__).resolve().parents[2]
CAST = json.loads((APP / "world" / "genesis_50_town.json").read_text())


def _seeded_employees() -> list[tuple[str, str]]:
    out = []
    for b in CAST["small_businesses"]:
        for e in b.get("employee_agent_ids") or []:
            out.append((b["business_id"], e))
    cc = CAST["construction_company"]
    for e in cc.get("hand_agent_ids") or []:
        out.append((cc["business_id"], e))
    return out


def test_the_cast_really_does_seed_employees() -> None:
    """If it ever stops, this whole fix is about nothing."""
    assert _seeded_employees(), "genesis seeds no employees at all"


def test_the_wage_covers_a_day_of_living() -> None:
    """A token wage is the same defect wearing a number. The head tax took 30
    sim a head a day and the grocery asks 12 for bread, so a wage under about
    45 buys nothing once the tax is paid."""
    head_tax_per_day = 30
    bread = next(b for b in CAST["small_businesses"] if b["business_id"] == "b_grocery")
    loaf = bread["opening_prices"]["bread"]["ask"]

    assert OPENING_WAGE_SIM > head_tax_per_day + loaf, (
        f"a wage of {OPENING_WAGE_SIM} does not cover {head_tax_per_day} tax "
        f"plus {loaf} for a loaf"
    )


def test_the_smallest_till_can_carry_its_staff_for_weeks() -> None:
    """A wage a business cannot pay is a promise that breaks on day two."""
    worst = None
    for b in CAST["small_businesses"]:
        staff = len(b.get("employee_agent_ids") or [])
        if not staff:
            continue
        days = b["starting_sim_balance"] / (OPENING_WAGE_SIM * staff)
        worst = days if worst is None else min(worst, days)

    assert worst is not None and worst >= 30, (
        f"the tightest business can only pay its staff for {worst:.0f} days"
    )


def test_seeding_promises_does_not_pay_them() -> None:
    """`paid_sim` is what ECON-5 measures. Seeding it would make the check
    answer itself, which is worse than failing honestly."""
    source = (APP / "world" / "run_genesis.py").read_text()
    block = source[source.index("def _seed_wages"):]
    block = block[: block.index("async def _seed_business")]

    assert "paid_sim=" not in block, "genesis pays a wage nobody earned"
    assert "accrued_sim=" not in block, "genesis accrues before a day has passed"
    assert "wage_sim=OPENING_WAGE_SIM" in block


def test_every_seeded_employee_gets_exactly_one_promise() -> None:
    """Keyed business|employee, so the same person employed twice keeps two
    promises and the same promise seeded twice stays one."""
    source = (APP / "world" / "run_genesis.py").read_text()
    block = source[source.index("def _seed_wages"):]
    block = block[: block.index("async def _seed_business")]

    assert 'f"{business.business_id}|{employee_id}"' in block, (
        "the promise is not keyed the way labour.py keys it"
    )
    assert "if key in self.world_state.wage_promises:" in block, (
        "re-seeding would overwrite a promise somebody had already changed"
    )
