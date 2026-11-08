"""CORE-02: charter events must reach the event bus on the host that runs.

`build_world()` constructs every charter sub-engine with no event sink, so
each one publishes into `noop_sink` until something rewires it.
`build_world_server()` does that at wiring.py step 4. `GenesisRunner` -
the host all 23 runs actually used - did not.

The cost was silent and total: an election could be scheduled, opened,
voted and tallied and not one `politics.*` row would exist. Nothing failed;
the government simply never appeared in any record.

This test fires a real charter sub-engine through the runner's booted world
and asserts the event lands on the bus. Remove `wire_the_charter_to_the_bus()`
from `boot()` and it fails.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _runner(tmp_path: Path) -> GenesisRunner:
    return GenesisRunner(
        GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=tmp_path / "checkpoints",
            world_mode="embedded",
            world_port=0,
            budget_cap_cents=40_000,
        )
    )


@pytest.mark.asyncio
async def test_a_charter_sub_engine_publishes_onto_the_bus(tmp_path):
    runner = _runner(tmp_path)
    await runner.boot()
    world = runner.world_state

    for name in ("elections", "amendments", "government", "land", "justice"):
        sub = getattr(world.charter, name, None)
        if sub is None:
            continue
        sink = getattr(sub, "event_sink", None)
        assert sink is not None, f"{name} has no event_sink attribute at all"
        assert getattr(sink, "__name__", "") != "noop_sink", (
            f"charter.{name} is still publishing into noop_sink - the seam "
            "build_world() leaves open was never closed on this host, so "
            "nothing it emits can ever be recorded (CORE-02)."
        )


@pytest.mark.asyncio
async def test_boot_records_that_it_wired_the_charter(tmp_path):
    """A flag, so a run can assert its own wiring instead of hoping."""
    runner = _runner(tmp_path)
    assert getattr(runner, "charter_sink_wired", False) is False
    await runner.boot()
    assert runner.charter_sink_wired is True
