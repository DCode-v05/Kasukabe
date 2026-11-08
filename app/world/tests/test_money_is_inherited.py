"""A dead resident's money goes somewhere.

`ECON-07`. `apply_death` settled the land (`settle_estate_land`) and the
tools (`settle_estate_tools`) and stopped. `Ledger.settle_inheritance`
was written, documented and tested, and had no caller anywhere in the
tree - `Will`'s own docstring names it as the rule that applies when a
will does not override the default.

So the land and the tools were inherited and the MONEY was not. Three
consequences, and the third is the one that matters most:

  - a dead resident's sim left circulation entirely;
  - every will naming a money bequest was silently unexecuted;
  - the deceased's USD account was never closed, so it kept counting as
    a live wallet in `Ledger.usd_total`. That is the invariant the halt
    gate reads, so every death quietly moved the number the run stops on.

Heirs come from the will's MONEY bequests where it names any - a will
leaving only tiles or tools is not a statement about money - otherwise
from `living_relatives`, the same section 31.3 order (spouse, children,
parents) the land already follows. No living heir is not an error:
`distribute_estate` escheats the estate to treasury, which is the right
answer for a resident who leaves nobody.

`apply_death` became a coroutine to do this. That is a real API change
and every caller in the tree was updated - four in `run_genesis.py` and
seven across three test files.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


@pytest_asyncio.fixture
async def runner(tmp_path):
    built = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await built.boot()
    return built


def _living_pair(runner):
    """A resident with a living relative, so the default rule has an heir."""
    for agent in runner.world_state.agents.values():
        if not agent.alive:
            continue
        heirs = runner.living_relatives(agent)
        if heirs:
            return agent, heirs
    return None, []


@pytest.mark.asyncio
async def test_the_estate_reaches_a_living_relative(runner) -> None:
    """The finding. Before this the money simply stayed in a closed book."""
    deceased, heirs = _living_pair(runner)
    assert deceased is not None, "this genesis seeds nobody with a living relative"

    ledger = runner.ledger
    ledger.get_account(deceased.agent_id).sim_units = 900
    before = sum(ledger.get_account(h).sim_units for h in heirs)

    await runner.apply_death(deceased, "terminal", 10)

    after = sum(ledger.get_account(h).sim_units for h in heirs)
    assert after > before, (
        "ECON-07: the deceased's sim reached nobody - it left circulation"
    )


@pytest.mark.asyncio
async def test_the_account_is_closed(runner) -> None:
    """The consequence that reaches the halt gate. An open account with a
    balance keeps counting toward `usd_total`, which is the invariant the
    run stops on."""
    deceased, heirs = _living_pair(runner)
    ledger = runner.ledger
    ledger.get_account(deceased.agent_id).sim_units = 400

    await runner.apply_death(deceased, "terminal", 10)

    assert ledger.get_account(deceased.agent_id).closed is True, (
        "ECON-07: a dead resident's account is still open, so their wallet "
        "keeps counting in usd_total and the halt gate reads a number that "
        "includes money nobody can spend"
    )


@pytest.mark.asyncio
async def test_the_money_is_conserved(runner) -> None:
    """Nothing is created or destroyed - it moves. If the estate vanished
    the ledger invariant would break in the other direction."""
    deceased, heirs = _living_pair(runner)
    ledger = runner.ledger
    ledger.get_account(deceased.agent_id).sim_units = 600

    watched = [deceased.agent_id, *heirs, "treasury"]

    def total() -> int:
        out = 0
        for account_id in watched:
            try:
                out += ledger.get_account(account_id).sim_units
            except Exception:  # noqa: BLE001 - treasury may be named otherwise
                pass
        return out

    before = total()
    await runner.apply_death(deceased, "terminal", 10)
    assert total() == before, (
        "ECON-07: the estate did not balance - money was created or lost "
        "settling it"
    )


@pytest.mark.asyncio
async def test_a_will_naming_money_beats_the_default_order(runner) -> None:
    """`Will`'s whole purpose. Before this, a will that left money to a
    friend was read for its tiles and tools and ignored for its cash."""
    from world.tools.models import Bequest, Will

    deceased, relatives = _living_pair(runner)
    friend = next(
        a.agent_id for a in runner.world_state.agents.values()
        if a.alive and a.agent_id != deceased.agent_id
        and a.agent_id not in relatives
    )
    runner.world_state.wills[deceased.agent_id] = Will(
        agent_id=deceased.agent_id,
        bequests=[Bequest(heir_id=friend, sim_share=1)],
    )
    ledger = runner.ledger
    ledger.get_account(deceased.agent_id).sim_units = 500
    friend_before = ledger.get_account(friend).sim_units
    relatives_before = sum(ledger.get_account(r).sim_units for r in relatives)

    await runner.apply_death(deceased, "terminal", 10)

    assert ledger.get_account(friend).sim_units > friend_before, (
        "ECON-07: the will named a money heir and the money went elsewhere"
    )
    assert sum(
        ledger.get_account(r).sim_units for r in relatives
    ) == relatives_before, "the default order overrode an explicit will"


@pytest.mark.asyncio
async def test_a_will_with_no_money_line_falls_back(runner) -> None:
    """A will that leaves only tools is not a statement about money, so
    the section 31.3 order still applies. Reading `bequests` as
    'money heirs' regardless would disinherit every relative the moment
    somebody bequeathed a chisel."""
    from world.tools.models import Bequest, Will

    deceased, relatives = _living_pair(runner)
    friend = next(
        a.agent_id for a in runner.world_state.agents.values()
        if a.alive and a.agent_id != deceased.agent_id
        and a.agent_id not in relatives
    )
    runner.world_state.wills[deceased.agent_id] = Will(
        agent_id=deceased.agent_id,
        bequests=[Bequest(heir_id=friend, sim_share=0, tools=["chisel"])],
    )
    ledger = runner.ledger
    ledger.get_account(deceased.agent_id).sim_units = 500
    before = sum(ledger.get_account(r).sim_units for r in relatives)

    await runner.apply_death(deceased, "terminal", 10)

    assert sum(ledger.get_account(r).sim_units for r in relatives) > before, (
        "ECON-07: a tools-only will was read as naming a money heir, so "
        "the family was disinherited by a bequeathed chisel"
    )


@pytest.mark.asyncio
async def test_a_resident_with_no_heir_does_not_break_the_run(runner) -> None:
    """`distribute_estate` escheats to treasury on an empty heir list.
    `apply_death` runs inside the tick loop, so a raise here takes the
    whole town down."""
    hermit = next(
        a for a in runner.world_state.agents.values()
        if a.alive and not runner.living_relatives(a)
    )
    runner.ledger.get_account(hermit.agent_id).sim_units = 300

    await runner.apply_death(hermit, "terminal", 10)

    assert runner.ledger.get_account(hermit.agent_id).closed is True


@pytest.mark.asyncio
async def test_settling_twice_does_not_raise(runner) -> None:
    """`settle_inheritance` refuses a closed account. A second death event
    for the same resident - a resume replaying the tick, say - must be a
    logged no-op, not the end of the run."""
    deceased, _ = _living_pair(runner)
    await runner.apply_death(deceased, "terminal", 10)
    await runner.apply_death(deceased, "terminal", 11)


def test_every_caller_awaits_it() -> None:
    """`apply_death` became a coroutine. A caller that forgot the await
    would get a coroutine object, drop the death events on the floor, and
    never settle anything - silently."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    missed: list[str] = []
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
        ):
            if not re.search(r"\.apply_death\(", line):
                continue
            if "await " in line or "def apply_death" in line:
                continue
            missed.append(f"{path.name}:{number}")
    assert not missed, (
        f"ECON-07: apply_death is a coroutine and these do not await it, so "
        f"the death never happens: {missed}"
    )
