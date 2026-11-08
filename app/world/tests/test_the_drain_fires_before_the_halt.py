"""CORE-05: consolidation must happen while the broker still answers.

Run 22's pre-halt drain fired 235 ms AFTER the governor halted dispatch. It
then reported "20 of 20" - because it counts batches attempted, and every
one of those calls was refused. A whole morning of signals was thrown away
by the mechanism built to stop exactly that.

Two causes, both here:

  1. The reserve was a flat 10 cents. One tick of 20 residents spends far
     more, so spend crossed from under the line to over the cap inside a
     single tick and the drain's own `governor.tripped` guard sent it home.
  2. `drain_before_halt` was called from `run_burst` and NOT from
     `run_continuous` - which is the loop every real run uses.
"""
from __future__ import annotations

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _runner(tmp_path, **kw) -> GenesisRunner:
    cfg = dict(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
        budget_cap_cents=40_000,
    )
    cfg.update(kw)
    return GenesisRunner(GenesisRunnerConfig(**cfg))


def test_the_reserve_starts_at_the_floor(tmp_path):
    runner = _runner(tmp_path)
    assert runner.drain_reserve_cents() == runner.DRAIN_RESERVE_CENTS


def test_the_reserve_grows_to_cover_the_priciest_tick(tmp_path):
    """The whole point: reserve at least one tick's worth of headroom.

    A flat floor cannot do this. If a tick costs 250 cents and the reserve
    is 10, spend goes from cap-11 to cap+239 with no observation in
    between, and there is no moment at which the drain can legally run.
    """
    runner = _runner(tmp_path)
    runner.note_tick_spend(250)
    assert runner.drain_reserve_cents() >= 250, (
        "the reserve does not cover a single tick, so spend can cross the "
        "cap between two observations and the drain can never fire (CORE-05)"
    )


def test_a_cheaper_tick_does_not_shrink_the_reserve(tmp_path):
    """Max, not last. One quiet tick must not undo the headroom."""
    runner = _runner(tmp_path)
    runner.note_tick_spend(250)
    runner.note_tick_spend(3)
    assert runner.drain_reserve_cents() >= 250


@pytest.mark.asyncio
async def test_the_drain_fires_with_a_whole_tick_of_headroom(tmp_path):
    """Replay the run-22 failure and show it cannot happen again.

    A cap of 1,000 cents and ticks that cost 250. Under the old flat
    10-cent reserve the drain's window was `spent >= 990`, and spend went
    750 -> 1000 with no observation inside it: the governor tripped and the
    drain returned at its own `tripped` guard, having done nothing.

    This drives the same arithmetic through the real methods.
    """
    runner = _runner(tmp_path, budget_cap_cents=1_000)

    drained_at: list[int] = []

    class Gateway:
        ports_by_agent = {"n01": 9100}

        async def drain_learning(self):
            drained_at.append(spent[0])
            return {"n01": True}

    class Governor:
        budget_cap_cents = 1_000
        tripped = False

    spent = [0]
    runner.gateway = Gateway()
    runner.governor = Governor()
    runner.ledger = type("L", (), {"usd_balance": staticmethod(lambda _acct: spent[0])})()

    for _ in range(4):
        cost = 250
        spent[0] += cost
        runner.note_tick_spend(cost)
        if spent[0] >= Governor.budget_cap_cents:
            Governor.tripped = True
        await runner.drain_before_halt()

    assert drained_at, (
        "the drain never fired before the cap - every signal the residents "
        "produced was thrown away, which is exactly what run 22 did (CORE-05)"
    )
    assert drained_at[0] <= Governor.budget_cap_cents - 250, (
        f"the drain fired at {drained_at[0]} cents of a 1000 cap with 250-cent "
        "ticks - that is less than one tick of headroom, so on a slightly "
        "more expensive tick it would have fired after the halt again"
    )
    assert not Governor.tripped or drained_at[0] < 1000, "drained only after the halt"


def test_the_continuous_loop_drains_and_measures(tmp_path):
    """`run_continuous` is the loop every real run uses; `run_burst` is not.

    Read from the compiled function rather than the file, so a moved or
    renamed loop cannot make this pass by accident.
    """
    import inspect

    body = inspect.getsource(GenesisRunner.run_continuous)
    assert "drain_before_halt" in body, (
        "run_continuous never calls the pre-halt drain - it was only ever "
        "wired into run_burst, which no real run uses (CORE-05)"
    )
    assert "note_tick_spend" in body, (
        "run_continuous does not measure what a tick costs, so the reserve "
        "can never become proportional"
    )


def test_the_reserve_covers_what_the_drain_will_spend(tmp_path):
    """Run 24: the drain fired at exactly the right moment and still
    consolidated 0 of 20.

    The reserve was 10 cents. The drain then had to make twenty learning
    calls at a few cents each against a broker that had just halted, so
    every one was refused. A drain that fires in time and cannot afford
    itself is the same as no drain - and reports the same "0 of 20".
    """
    runner = _runner(tmp_path)

    runner.world_state = type("W", (), {"agents": {f"n{i:02d}": None for i in range(20)}})()
    reserve = runner.drain_reserve_cents()
    needed = 20 * runner.DRAIN_COST_PER_RESIDENT_CENTS
    assert reserve >= needed, (
        f"reserve {reserve}c against {needed}c of consolidation calls for 20 "
        "residents; the drain cannot pay for itself (run 24)"
    )


def test_the_reserve_still_covers_a_pricey_tick(tmp_path):
    """The three-way max: a tick can cost more than the whole drain."""
    runner = _runner(tmp_path)

    runner.world_state = type("W", (), {"agents": {"n01": None}})()
    runner.note_tick_spend(250)
    assert runner.drain_reserve_cents() >= 250


def test_with_no_engines_the_floor_still_applies(tmp_path):
    runner = _runner(tmp_path)
    assert runner.drain_reserve_cents() == runner.DRAIN_RESERVE_CENTS
