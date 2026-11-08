"""The end-of-run drain must not report a consolidation that could not happen.

Run 22's log, 235 milliseconds apart:

    12:52:06,674 ERROR stopping: the broker has halted dispatch ($2.00
                 spent against a $2.00 cap). Every further call would be
                 refused
    12:52:06,909 INFO  end-of-run learning drain: 20 of 20 resident(s)
                 consolidated

Twenty of twenty, after the line saying every further call would be
refused. The trace agrees with the pessimistic reading: **7,138 signals
still unconsolidated and zero knowledge rows written.** The drain counted
residents it POSTed to, not signals anything encoded, so a batch that
could not make a single model call reported complete success.

This is the failure this project keeps finding, in code written to fix a
different instance of it: `batches_failed: 0` over a deleted inode,
`[✓] Memory fits` one second before the runner refused, `Dispatch success
100%` over turns that called nothing. A green line over work that did not
happen is worse than a red one, because nobody investigates it.

Two things follow, and this file asserts both: the drain must know when
the broker has halted and say so, and its report must count what was
consolidated rather than who was asked.
"""

from __future__ import annotations

from world.run_genesis import describe_drain


def test_a_drain_that_encoded_nothing_does_not_claim_success() -> None:
    """Run 22's exact shape: every resident answered, nothing was
    learned."""
    line = describe_drain({f"n{i:02d}": False for i in range(1, 21)}, halted=True)
    assert "20 of 20" not in line, "the misleading count survives"
    assert "0" in line
    assert "halt" in line.lower() or "refused" in line.lower(), (
        "the reason the drain could do nothing is not in the line a reader sees"
    )


def test_a_real_consolidation_is_reported_as_one() -> None:
    line = describe_drain({"n01": True, "n02": True, "n03": False}, halted=False)
    assert "2 of 3" in line


def test_a_halted_broker_is_named_even_when_some_engines_answered() -> None:
    """Partial success against a halted broker is still a run whose last
    stretch was thrown away, and the line has to say which."""
    line = describe_drain({"n01": True, "n02": False}, halted=True)
    assert "halt" in line.lower() or "refused" in line.lower()


def test_nothing_to_drain_is_not_reported_as_a_failure() -> None:
    """A run with no engines left is not a learning failure, and dressing
    it as one would train a reader to ignore the line."""
    line = describe_drain({}, halted=False)
    assert "no engines" in line.lower() or "nothing" in line.lower()


def test_the_drain_is_attempted_before_the_cap_halts_dispatch() -> None:
    """The fix that matters, exercised rather than grepped.

    CORE-23. This asserted `"drain_learning" in inspect.getsource(...)` -
    which a COMMENT satisfies, and which passed happily through the entire
    period CORE-05 was broken. It now replays run 22's arithmetic: a cap of
    1,000 cents and ticks costing 250, which under the old flat 10-cent
    reserve gave the drain no window at all.

    The full behavioural version lives in
    `test_the_drain_fires_before_the_halt.py`; this keeps the honesty tests
    and the firing test in the same suite.
    """
    import asyncio
    import tempfile
    from pathlib import Path

    from world.run_genesis import GenesisRunner, GenesisRunnerConfig

    async def run() -> list[int]:
        tmp = Path(tempfile.mkdtemp())
        runner = GenesisRunner(GenesisRunnerConfig(
            mock_mode=True, checkpoint_dir=tmp / "c", world_mode="embedded",
            world_port=0, budget_cap_cents=1_000,
        ))
        drained_at: list[int] = []
        spent = [0]

        class Gateway:
            ports_by_agent = {"n01": 9100}

            async def drain_learning(self):
                drained_at.append(spent[0])
                return {"n01": True}

        class Governor:
            budget_cap_cents = 1_000
            tripped = False

        runner.gateway = Gateway()
        runner.governor = Governor()
        runner.ledger = type("L", (), {"usd_balance": staticmethod(lambda _a: spent[0])})()

        for _ in range(4):
            spent[0] += 250
            runner.note_tick_spend(250)
            if spent[0] >= Governor.budget_cap_cents:
                Governor.tripped = True
            await runner.drain_before_halt()
        return drained_at

    drained_at = asyncio.run(run())
    assert drained_at, (
        "nothing triggers a drain while the broker will still answer, so the "
        "only drain that ever runs is the one that cannot work"
    )
    assert drained_at[0] <= 750, (
        f"the drain fired at {drained_at[0]} of a 1,000 cap with 250-cent "
        "ticks - less than one tick of headroom"
    )
