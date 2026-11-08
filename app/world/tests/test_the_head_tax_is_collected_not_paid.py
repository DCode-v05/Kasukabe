"""The head tax is collected, not paid - and the town now says so.

The 50-resident run (2026-09-16): `pay_tax` was refused 18 times with "you
owe no head tax", each time by a resident who had just read "Owed today:
... head tax 30". Hendrik Whitlock, at tick 114: "Someone told me I owe no
head tax, yet my notice says thirty sim due today." Petra Hartigan: "I tried
to pay the head tax but the ledger says I owe none."

Both statements were true. Today's head tax is TAKEN at the day boundary
from whoever in the household holds the most sim; `pay_tax` clears only
arrears. Nothing said which, so a refusal was met eighteen times without a
change in behaviour - which the checklist (X10) calls a tool-text defect.
"""

from __future__ import annotations

import pytest

from world.retrieval_depth import format_volatile_line
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX
from world.tools.tests.conftest import (  # noqa: F401 - fixtures
    registry,
    spawn_agent,
    world,
    world_and_registry,
)

WALLET = {
    "usd_wallet_cents": 600, "sim_balance": 400, "last_turn_usd_cents": 0.0,
    "runway_game_days": None, "upkeep_due_sim": 90, "head_tax_due_sim": 30,
    "household_size": 1,
}


def test_the_owed_line_says_the_bills_are_collected() -> None:
    line = format_volatile_line(WALLET)

    assert "Owed today: upkeep 90, head tax 30." in line
    assert "taken automatically at the day boundary" in line


def test_a_household_line_says_it_too() -> None:
    line = format_volatile_line({**WALLET, "household_size": 4})

    assert "taken automatically at the day boundary" in line


def test_nothing_owed_says_nothing() -> None:
    line = format_volatile_line({**WALLET, "upkeep_due_sim": 0, "head_tax_due_sim": 0})

    assert "day boundary" not in line


@pytest.mark.asyncio
async def test_the_refusal_says_why_there_is_nothing_to_pay(world, registry) -> None:
    await spawn_agent(world, "hendrik", LOCATION_GOVERNMENT_COMPLEX)

    with pytest.raises(Exception) as refused:
        await registry.execute(world, "hendrik", "pay_tax", {"amount_sim": 30})

    why = str(refused.value)
    assert "arrears" in why
    assert "taken automatically at the day boundary" in why
