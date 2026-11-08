"""TOOL-18: `check_debts` could not see a debt the persona declared.

The genesis cast is written with debts in it — `n01` owes Marta 400 sim for
an assay fee on a copper seam that came up empty, and both sides know it.
The seeder that puts those into `world.loans` matched `role == "ower"`; the
genesis file says `"owing"`. One word apart, so it ran on every boot, logged
nothing, and seeded **zero**.

The consequence was in run 23: twenty `check_debts` calls came back empty,
and one debt was paid twice by two different people, because neither could
see it had already been settled.

This is the project's signature failure — code that runs and does nothing —
so the fix is two-part: read the vocabulary the data actually uses, and
make the next mismatch loud.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from world.run_genesis import (
    DEBT_LENDER_ROLES,
    DEBT_OWER_ROLES,
    GenesisRunner,
    GenesisRunnerConfig,
)

GENESIS = Path(__file__).resolve().parents[1] / "genesis_20.json"


def test_the_cast_actually_declares_debts():
    """If this fails the test below proves nothing."""
    cast = json.loads(GENESIS.read_text())
    debts = [
        o
        for a in cast["agents"]
        for o in (a.get("persona") or {}).get("opinions") or []
        if o.get("kind") == "debt"
    ]
    assert debts, "the cast declares no debts at all"
    assert any(o.get("sim_amount") for o in debts)


def test_the_role_the_cast_uses_is_understood():
    """The exact miss: the file says "owing", the seeder wanted "ower"."""
    cast = json.loads(GENESIS.read_text())
    roles = {
        o.get("role")
        for a in cast["agents"]
        for o in (a.get("persona") or {}).get("opinions") or []
        if o.get("kind") == "debt"
    }
    assert roles, "no debt roles in the cast"
    unknown = roles - DEBT_OWER_ROLES - DEBT_LENDER_ROLES
    assert not unknown, (
        f"the cast writes debt roles {sorted(unknown)} that the seeder does "
        "not know, so those debts never reach world.loans and check_debts "
        "tells the debtor it owes nobody"
    )


@pytest.mark.asyncio
async def test_a_resident_can_see_what_it_owes(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "w.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000, genesis_path=GENESIS,
    ))
    await runner.boot()
    world = runner.world_state

    assert world.loans, (
        "not one genesis debt reached world.loans, so every `check_debts` "
        "answers 'you owe nobody' (TOOL-18)"
    )
    owing = [a for a in world.agents if world.debts_of(a).get("owed_by_you")]
    assert owing, "no resident can see a debt it owes"

    debts = world.debts_of(owing[0])["owed_by_you"]
    assert all(d.get("sim") for d in debts), "a debt with no amount is not a debt"
    assert all(d.get("to") for d in debts), "a debt owed to nobody cannot be repaid"


@pytest.mark.asyncio
async def test_the_lender_side_is_not_double_counted(tmp_path, monkeypatch):
    """Each debt is written twice in the cast - once by the ower, once by
    the lender. Seeding both sides would double the town's debt."""
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "w.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000, genesis_path=GENESIS,
    ))
    await runner.boot()

    cast = json.loads(GENESIS.read_text())
    ower_side = sum(
        1
        for a in cast["agents"]
        for o in (a.get("persona") or {}).get("opinions") or []
        if o.get("kind") == "debt" and o.get("role") in DEBT_OWER_ROLES and o.get("sim_amount")
    )
    assert len(runner.world_state.loans) == ower_side, (
        f"{len(runner.world_state.loans)} loans from {ower_side} debts the "
        "cast declares from the ower's side - the lender's copy is being "
        "seeded as a second debt"
    )
