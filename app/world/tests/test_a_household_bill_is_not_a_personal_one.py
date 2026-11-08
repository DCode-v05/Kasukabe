"""A household's bill is shown as the household's, not as the reader's own.

Run 28, Perrin Holloway (n04), sixteen, a student, 400 sim. His first
perception of the run:

    Energy: 6,000. How long that lasts is not yet known. Money: 400 sim.
    Owed today: upkeep 450, head tax 150.

450 is the upkeep of all five Holloways and 150 their head tax - neither is
his. Upkeep is taken from a household's EARNERS in order
(`collect_household_upkeep`), and a minor is a dependent; head tax is taken
from whichever member holds the most sim (`who_pays_for`). Three earning
adults stood in the room with him. The line said "Owed today" and nothing
else, and he read it the only way it could be read:

    "I have four hundred sim. Our bill today is six hundred. Even if I give
    everything I have, we're still two hundred short."

He transferred 395 sim to Bram on his first turn, before any bill was due,
and spent the rest of the run unable to afford `say` (1 sim) to ask for
help. Liora (17) and Aldous (15) read the same line.

`test_the_household_bill_matches_what_will_be_charged` keeps the FIGURE the
household total, which is right - it is what will be charged. What was
missing is whose bill it is and who it is taken from. Facts only, as the
volatile line's own rule says: no advice about whether to pay.
"""

from __future__ import annotations

import pytest

from world.retrieval_depth import format_volatile_line
from world.run_genesis import GenesisRunner, GenesisRunnerConfig

PERRINS_WALLET = {
    "usd_wallet_cents": 600, "sim_balance": 400,
    "last_turn_usd_cents": 0.0, "runway_game_days": None,
    "upkeep_due_sim": 450, "head_tax_due_sim": 150,
    "household_size": 5,
}


def test_the_line_says_the_bill_is_the_households() -> None:
    line = format_volatile_line(PERRINS_WALLET)

    assert "household of 5" in line, line
    assert "upkeep 450" in line and "head tax 150" in line


def test_the_line_says_who_the_bill_is_taken_from() -> None:
    """The fact Perrin did not have: a 400-sim student is not charged 600
    while earners sit beside him."""
    line = format_volatile_line(PERRINS_WALLET).lower()

    assert "earners" in line, line
    assert "most sim" in line, line


def test_it_still_gives_no_advice() -> None:
    line = format_volatile_line(PERRINS_WALLET).lower()

    for word in ("should", "must", "try to", "consider", "careful", "save", "don't", "do not"):
        assert word not in line, f"the owed line advises: {word!r} in {line!r}"


def test_a_household_of_one_reads_as_before() -> None:
    """Ulrike, alone in h0146, owes her own bill - and the run-27 wording
    was right for her."""
    line = format_volatile_line({**PERRINS_WALLET, "upkeep_due_sim": 90,
                                 "head_tax_due_sim": 30, "household_size": 1})

    assert "Owed today: upkeep 90, head tax 30." in line
    assert "household" not in line


@pytest.mark.asyncio
async def test_the_runner_says_how_big_the_household_is(tmp_path) -> None:
    """The formatter is only half: `wallet_line` has to produce the size,
    or the new branch is the dead one ECON-20 already found once."""
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded", world_port=0,
    ))
    await runner.boot()
    world = runner.world_state
    sizes: dict[str, int] = {}
    for agent in world.agents.values():
        if agent.alive and agent.household_id:
            sizes[agent.household_id] = sizes.get(agent.household_id, 0) + 1
    shared = next(
        (a for a in world.agents.values() if a.alive and sizes.get(a.household_id, 0) > 1),
        None,
    )
    assert shared is not None, "the default cast has no shared household to test against"

    wallet = runner.wallet_line(shared.agent_id, tick=1)

    assert wallet["household_size"] == sizes[shared.household_id]
    assert f"household of {sizes[shared.household_id]}" in format_volatile_line(wallet)
