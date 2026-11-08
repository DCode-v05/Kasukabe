"""ECON-20: today's upkeep and tax are visible in context every tick.

`format_volatile_line` has rendered `upkeep_due_sim` and `head_tax_due_sim`
since it was written (`retrieval_depth.py:305-308`). `wallet_line` never
set either, so both branches were unreachable and DESIGN section 3's
"today's upkeep and tax, who depends on them ... visible in context every
tick" was a slot with nothing in it.

A resident could read its wallet, its burn rate and its runway, and had no
way to find out what was about to be taken out of it - then went hungry at
the day boundary for a bill it had never been shown. Being told after the
fact that you failed to pay is not the same as being told what is due.

This is also `build_economic_context`'s first non-test caller (TOOL-25).
"""

from __future__ import annotations

import pytest

from world.retrieval_depth import format_volatile_line
from world.run_genesis import GenesisRunner, GenesisRunnerConfig


async def _town(tmp_path) -> GenesisRunner:
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    return runner


@pytest.mark.asyncio
async def test_the_line_says_what_is_due_today(tmp_path) -> None:
    runner = await _town(tmp_path)
    agent_id = next(iter(runner.world_state.agents))

    wallet = runner.wallet_line(agent_id, tick=1)

    assert wallet is not None
    assert wallet["upkeep_due_sim"] > 0, (
        "ECON-20: the resident is shown no upkeep, so format_volatile_line's "
        "upkeep branch is still unreachable"
    )
    line = format_volatile_line(wallet)
    assert "upkeep" in (line or ""), f"ECON-20: not in the line the resident reads: {line!r}"


@pytest.mark.asyncio
async def test_the_household_bill_matches_what_will_be_charged(tmp_path) -> None:
    """A figure shown to the resident that the biller then disagrees with
    is worse than no figure: it teaches the resident its own readouts lie.
    """
    from world.life.upkeep import compute_household_bill

    runner = await _town(tmp_path)
    agent_id = next(iter(runner.world_state.agents))
    agent = runner.world_state.agents[agent_id]
    members = [
        other
        for other in runner.world_state.agents.values()
        if other.alive and other.household_id == agent.household_id
    ]

    shown = runner.wallet_line(agent_id, tick=1)["upkeep_due_sim"]
    charged = compute_household_bill(agent.household_id, 0, members).total_sim

    assert shown == charged


@pytest.mark.asyncio
async def test_who_depends_on_you_is_named(tmp_path) -> None:
    """The third clause of the same DESIGN sentence."""
    runner = await _town(tmp_path)
    for agent_id in runner.world_state.agents:
        wallet = runner.wallet_line(agent_id, tick=1)
        assert "dependents" in wallet


@pytest.mark.asyncio
async def test_a_readout_never_ends_a_turn(tmp_path) -> None:
    """`wallet_line`'s own rule. A resident who does not exist must get
    None, not an exception that costs it the whole tick.
    """
    runner = await _town(tmp_path)
    assert runner.todays_obligations("nobody-at-all", 0) == (0, 0, None)
