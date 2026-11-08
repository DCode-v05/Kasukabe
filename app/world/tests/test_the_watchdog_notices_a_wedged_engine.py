"""A wedged engine is the case the watchdog exists for, and it was blind.

`CORE-18`. `SupervisorWatchdog.needs_restart` has two arms: a unit that
reported FAILED, and a unit that is nominally RUNNING but has gone silent
for longer than `silence_timeout_s`. The second arm is the interesting one
- a process that exits is noticed by anything, a process that stops
answering while still alive is not - and it is dead code:

    if status.last_health_check_at is None:
        return None                      # never health-checked yet

`ProcessSupervisor.start` sets `last_health_check_at=None` and NOTHING
ever updates it. `status()` does not, `health()` does not (it returns
`state is RUNNING`, which is what the caller already knew), and nothing in
the runner calls `health()` at all. So the field is None for the whole
life of every unit and the silence arm can never fire.

These tests hold the watchdog to the behaviour, not to the field: a unit
that answers stays up, a unit that stops answering gets restarted.
"""
from __future__ import annotations

import dataclasses

import pytest

from world.run_genesis import (
    SupervisorWatchdog,
    UnitState,
    UnitStatus,
)


class FakeSupervisor:
    """A supervisor whose units can be told to stop answering."""

    def __init__(self) -> None:
        self.answers = True
        self.restarted: list[str] = []
        self.health_calls: list[str] = []
        self._status = UnitStatus(
            unit_id="n01",
            state=UnitState.RUNNING,
            backend_handle="fake",
            started_at=0.0,
            last_health_check_at=None,
            detail="",
        )

    async def list(self) -> list[UnitStatus]:
        return [self._status]

    async def status(self, unit_id: str) -> UnitStatus:
        return self._status

    async def health(self, unit_id: str) -> bool:
        self.health_calls.append(unit_id)
        if self.answers:
            self._status = dataclasses.replace(
                self._status, last_health_check_at=self.now
            )
        return self.answers

    async def restart(self, unit_id: str) -> None:
        self.restarted.append(unit_id)

    now = 0.0


@pytest.mark.asyncio
async def test_the_watchdog_probes_running_units() -> None:
    """Without a probe the silence arm has no input and cannot fire."""
    supervisor = FakeSupervisor()
    clock = [1000.0]
    supervisor.now = clock[0]
    watchdog = SupervisorWatchdog(supervisor, clock=lambda: clock[0])

    await watchdog.check_once()

    assert supervisor.health_calls == ["n01"], (
        "CORE-18: the watchdog never asked the unit whether it was answering, "
        "so last_health_check_at stayed None and the silence arm was dead"
    )


@pytest.mark.asyncio
async def test_an_engine_that_stops_answering_is_restarted() -> None:
    supervisor = FakeSupervisor()
    clock = [1000.0]
    supervisor.now = clock[0]
    watchdog = SupervisorWatchdog(supervisor, clock=lambda: clock[0])

    await watchdog.check_once()
    assert not supervisor.restarted

    supervisor.answers = False
    clock[0] += 10_000.0
    await watchdog.check_once()

    assert supervisor.restarted == ["n01"], (
        "CORE-18: a RUNNING unit that stopped answering was never restarted"
    )


@pytest.mark.asyncio
async def test_an_engine_that_keeps_answering_is_left_alone() -> None:
    """The other half. A watchdog that restarts healthy engines is worse
    than one that restarts nothing."""
    supervisor = FakeSupervisor()
    clock = [1000.0]
    watchdog = SupervisorWatchdog(supervisor, clock=lambda: clock[0])

    for _ in range(5):
        clock[0] += 10_000.0
        supervisor.now = clock[0]
        await watchdog.check_once()

    assert not supervisor.restarted
