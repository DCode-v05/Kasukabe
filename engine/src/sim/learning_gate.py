"""Sleep-hours gate for the learning centre: kimi-k2.5, never while awake.

DESIGN.md section 4: "Agents sleep ~8 game-hours (no interactive LLM calls;
learning pipeline runs then)." src/learning_centre/scheduler.py's LCScheduler
runs on a WALL-CLOCK interval (default 6 real hours, see
LCScheduler.interval_seconds) and has no concept of "asleep" at all - the
wrong clock for a sim whose time is ticks (10 game-minutes each, DESIGN.md
section 1). This module replaces the timer with an edge-triggered gate
driven by the world server's own per-tick ``is_sleeping`` flag (see
agent_adapter.py's tick payload): the learning batch runs once at the moment
an agent falls asleep, and not at all while awake - not "less often," never.
Restricting the batch to sleep-onset also keeps this agent's own kimi-k2.5
RPM usage from ever overlapping between its interactive turns and its
learning batch, even though (single-model era) both draw from the exact
same allowlisted model and the exact same broker bucket.

The adapter driving the pipeline here is always kimi-k2.5
(src.sim.model_profile.MODEL_ID - the ONLY model for every herald consumer
in this sim, learning centre included), and pinned_boot.py builds it
through guarded_create_llm so it can never silently resolve to
src/learning_centre/pipeline.py's own module-docstring default ("cheap
model (GPT-4o-mini)") or src/herald/defaults.py's MODEL_DEFAULTS.lc - the
task is explicit that GPT-4o-mini, or any other model, "must never be
used" here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.learning_centre.pipeline import LCPipeline, LCPipelineResult

logger = logging.getLogger(__name__)


@dataclass
class SimLearningGate:
    """Runs one LC batch per sleep-onset edge; never while the agent is awake."""

    pipeline: "LCPipeline"
    was_sleeping: bool = False
    ran_this_window: bool = False
    runs_triggered: int = 0
    last_result: "LCPipelineResult | None" = field(default=None, repr=False)
    awake_interval_ticks: int = 0
    ticks_since_batch: int = 0
    learns: bool = True
    suppressed: int = 0

    async def on_tick(self, is_sleeping: bool) -> "LCPipelineResult | None":
        """Call once per tick with the world server's is_sleeping flag.

        Returns the LCPipelineResult when a batch ran on this call, else
        None. Awake ticks never reach ``self.pipeline.run_batch()``. A long
        sleep window still runs the batch exactly once, at the wake->asleep
        edge - not once per tick spent asleep. A caller that wants a second
        run mid-sleep can call ``trigger_extra()`` explicitly; on_tick alone
        never does that on its own.
        """
        if not self.learns:
            self.suppressed += 1
            self.was_sleeping = is_sleeping
            return None

        fell_asleep = is_sleeping and not self.was_sleeping
        self.was_sleeping = is_sleeping

        if not is_sleeping:
            self.ran_this_window = False
            self.ticks_since_batch += 1
            if (
                self.awake_interval_ticks
                and self.ticks_since_batch >= self.awake_interval_ticks
            ):
                return await self._run("waking interval")
            return None

        if not fell_asleep or self.ran_this_window:
            return None

        return await self._run("sleep onset")

    async def trigger_extra(self) -> "LCPipelineResult | None":  # noqa: D401
        """Run a batch right now, unconditionally. Used by on_tick for the
        sleep-onset edge, by the adapter's POST /learn for the end-of-run
        drain, and available directly for a caller (or a test) that wants an
        additional run within the same sleep window.

        Returns None when the batch failed - `_run` swallows a pipeline
        error on purpose (see its docstring) and the old annotation here
        promised a result it could not deliver.
        """
        if not self.learns:
            self.suppressed += 1
            return None
        return await self._run("explicit trigger")

    async def _run(self, why: str) -> "LCPipelineResult | None":
        """One batch, and never a reason a tick fails.

        Consolidation is bookkeeping about turns that have already
        happened. A resident whose tick dies because the learning model
        was briefly unavailable has been punished for the pipeline's
        problem - the opposite of the trace store, where losing an
        observation must halt the run because there the record IS the
        product.
        """
        self.ran_this_window = True
        self.ticks_since_batch = 0
        self.runs_triggered += 1
        logger.info(
            "sim learning gate: running LC batch on kimi-k2.5 (run #%d, %s)",
            self.runs_triggered, why,
        )
        try:
            result = await self.pipeline.run_batch()
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning("learning batch failed (%s); the turn carries on: %s", why, exc)
            return None
        self.last_result = result
        return result
