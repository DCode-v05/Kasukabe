"""SimLearningGate: runs the LC batch once per sleep onset, never while awake."""

from __future__ import annotations

import pytest

from src.sim.learning_gate import SimLearningGate


class FakePipeline:
    def __init__(self):
        self.run_count = 0

    async def run_batch(self):
        self.run_count += 1
        return f"result-{self.run_count}"


@pytest.mark.asyncio
async def test_awake_ticks_never_run_the_pipeline():
    pipeline = FakePipeline()
    gate = SimLearningGate(pipeline=pipeline)

    for _ in range(10):
        result = await gate.on_tick(is_sleeping=False)
        assert result is None

    assert pipeline.run_count == 0


@pytest.mark.asyncio
async def test_falling_asleep_runs_the_pipeline_exactly_once():
    pipeline = FakePipeline()
    gate = SimLearningGate(pipeline=pipeline)

    result_first = await gate.on_tick(is_sleeping=True)
    assert result_first == "result-1"
    assert pipeline.run_count == 1

    # Still asleep on later ticks in the same window - must not run again.
    for _ in range(5):
        result = await gate.on_tick(is_sleeping=True)
        assert result is None
    assert pipeline.run_count == 1


@pytest.mark.asyncio
async def test_a_new_sleep_window_runs_a_new_batch():
    pipeline = FakePipeline()
    gate = SimLearningGate(pipeline=pipeline)

    await gate.on_tick(is_sleeping=True)
    await gate.on_tick(is_sleeping=True)
    await gate.on_tick(is_sleeping=False)  # wakes up
    await gate.on_tick(is_sleeping=False)
    result = await gate.on_tick(is_sleeping=True)  # falls asleep again

    assert pipeline.run_count == 2
    assert result == "result-2"


@pytest.mark.asyncio
async def test_trigger_extra_runs_regardless_of_window_state():
    pipeline = FakePipeline()
    gate = SimLearningGate(pipeline=pipeline)

    result = await gate.trigger_extra()
    assert result == "result-1"
    assert gate.last_result == "result-1"
    assert gate.runs_triggered == 1


# ── the no_learning control arm (evaluation spec section 5.2) ─────────
#
# `genesis.py` has assigned every resident an arm since it was written -
# 190 full, the rest split three ways - and NOTHING HONOURED IT. A
# `no_learning` resident ran the learning gate exactly like everybody
# else, so the arm was a label on a resident that behaved identically and
# C1 was being reported against a control that did not differ from the
# treatment in any way.
#
# The spec's own words: "a learning claim without a control arm is a
# correlation."


@pytest.mark.asyncio
async def test_the_no_learning_arm_never_consolidates():
    """Not the sleep edge, not the waking interval, not the drain. A
    control that consolidated "a bit less" is not a control."""
    pipeline = FakePipeline()
    gate = SimLearningGate(pipeline=pipeline, awake_interval_ticks=2, learns=False)

    for _ in range(10):
        assert await gate.on_tick(is_sleeping=False) is None
    assert await gate.on_tick(is_sleeping=True) is None
    assert await gate.trigger_extra() is None

    assert pipeline.run_count == 0


@pytest.mark.asyncio
async def test_the_suppression_is_counted_not_silent():
    """A run has to be able to show the arm did something. "No batches
    ran" is indistinguishable from "the gate was never reached" unless
    somebody counts."""
    gate = SimLearningGate(pipeline=FakePipeline(), learns=False)

    await gate.on_tick(is_sleeping=False)
    await gate.on_tick(is_sleeping=True)
    await gate.trigger_extra()

    assert gate.suppressed == 3
    assert gate.runs_triggered == 0


@pytest.mark.asyncio
async def test_the_full_arm_is_the_default_and_still_learns():
    """A resident whose arm nobody recorded is not a control for anything,
    so the default has to be the treated one."""
    pipeline = FakePipeline()
    gate = SimLearningGate(pipeline=pipeline)

    assert gate.learns is True
    await gate.on_tick(is_sleeping=True)

    assert pipeline.run_count == 1
