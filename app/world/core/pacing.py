"""Pacing modes - DESIGN.md section 9: "Checkpoint... burst or continuous
pacing."

Two ways to drive a tick loop:

- continuous: real-time paced, the live sim. One tick per
  continuous_interval_s of wall clock (default 0: no artificial pacing
  at all - DESIGN.md's ~40s/tick figure already comes from broker RPM
  limits and LLM call latency, not from anything this controller adds;
  it exists for callers that DO want an explicit cadence, e.g. driving
  the tick loop no faster than the game app can render it).
- burst: no wall-clock pacing - advance N ticks back to back as fast as
  the event loop allows. DESIGN.md section 1's night ticks (agents
  asleep, "no interactive LLM calls") are the canonical use case: there
  is nothing to wait on, so burst through them; likewise fast-forwarding
  a fresh checkout up to the live tick, or driving tests.

PacingController is deliberately generic over a bare async `tick_fn`
rather than importing TickEngine, so it has zero dependency on
tick/scheduler/perception internals and is unit-testable with a stub.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable


class PacingMode(str, Enum):
    CONTINUOUS = "continuous"
    BURST = "burst"


@dataclass
class PacingConfig:
    mode: PacingMode = PacingMode.CONTINUOUS
    continuous_interval_s: float = 0.0

    def __post_init__(self) -> None:
        if self.continuous_interval_s < 0:
            raise ValueError(
                f"continuous_interval_s must be >= 0, got {self.continuous_interval_s}"
            )


TickFn = Callable[[], Awaitable[Any]]


class PacingController:
    """Drives a tick callable under one of the two pacing modes above."""

    def __init__(self, tick_fn: TickFn, config: PacingConfig | None = None) -> None:
        self.tick_fn = tick_fn
        self.config = config or PacingConfig()
        self.stop_requested = False

    def request_stop(self) -> None:
        """Signals run_continuous()/run_burst() to stop after the tick
        currently in flight (if any) rather than starting another."""
        self.stop_requested = True

    async def run(self, num_ticks: int | None = None) -> list[Any] | None:
        """Dispatches to run_continuous or run_burst per self.config.mode -
        the single entry point for a caller that just wants to honor
        whatever pacing mode is configured. Returns the per-tick result
        list for burst mode, None for continuous (which only returns
        once stopped)."""
        if self.config.mode is PacingMode.BURST:
            if num_ticks is None:
                raise ValueError("run() in burst mode requires num_ticks")
            return await self.run_burst(num_ticks)
        await self.run_continuous()
        return None

    async def run_continuous(self) -> None:
        """Runs until request_stop() is called. Intended to be launched via
        asyncio.create_task by the caller - awaiting it directly blocks
        forever unless something else calls request_stop() concurrently."""
        self.stop_requested = False
        while not self.stop_requested:
            await self.tick_fn()
            if self.stop_requested:
                break
            if self.config.continuous_interval_s > 0:
                await asyncio.sleep(self.config.continuous_interval_s)
            else:
                await asyncio.sleep(0)  # yield control even at max speed

    async def run_burst(self, num_ticks: int) -> list[Any]:
        """Runs up to num_ticks ticks back to back, stopping early if
        request_stop() is called mid-run. Returns the results in order."""
        if num_ticks < 0:
            raise ValueError(f"num_ticks must be >= 0, got {num_ticks}")
        self.stop_requested = False
        results: list[Any] = []
        for _ in range(num_ticks):
            if self.stop_requested:
                break
            results.append(await self.tick_fn())
            await asyncio.sleep(0)
        return results
