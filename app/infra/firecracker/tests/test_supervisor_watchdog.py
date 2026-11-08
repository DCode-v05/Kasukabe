"""B1: the watchdog that restarts engines nobody is watching.

The 250 section 33: "nothing in the codebase calls .restart() automatically;
a FAILED unit stays failed until someone notices." These cover the loop that
closes that, and - just as importantly - the cases where it must NOT act.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supervisor import (  # noqa: E402
    RestartPolicy,
    SupervisorWatchdog,
    UnitSpec,
    UnitState,
    UnitStatus,
)


class FakeSupervisor:
    """An in-memory Supervisor. Records restarts; lets a test drive state."""

    def __init__(self) -> None:
        self.units: dict[str, UnitStatus] = {}
        self.restarts: list[str] = []
        self.restart_raises: str | None = None

    def put(self, unit_id: str, state: UnitState, last_health_check_at: float | None) -> None:
        self.units[unit_id] = UnitStatus(
            unit_id=unit_id, state=state, backend_handle="h",
            started_at=0.0, last_health_check_at=last_health_check_at,
        )

    async def list(self) -> list[UnitStatus]:
        return list(self.units.values())

    async def restart(self, unit_id: str) -> UnitStatus:
        if self.restart_raises:
            raise RuntimeError(self.restart_raises)
        self.restarts.append(unit_id)
        self.put(unit_id, UnitState.RUNNING, last_health_check_at=NOW)
        return self.units[unit_id]

    async def start(self, spec: UnitSpec) -> UnitStatus: ...
    async def stop(self, unit_id: str, timeout_s: float = 10.0) -> UnitStatus: ...
    async def status(self, unit_id: str) -> UnitStatus: return self.units[unit_id]
    async def health(self, unit_id: str) -> bool: return True
    async def snapshot(self, unit_id: str, tick: int): ...
    async def restore(self, unit_id: str, handle): ...
    async def shutdown_all(self, timeout_s: float = 30.0) -> None: ...


NOW = 1_000_000.0


def watchdog(sup: FakeSupervisor, **kw) -> SupervisorWatchdog:
    kw.setdefault("clock", lambda: NOW)
    kw.setdefault("silence_timeout_s", 90.0)
    return SupervisorWatchdog(sup, **kw)


@pytest.mark.asyncio
async def test_a_failed_unit_is_restarted():
    sup = FakeSupervisor()
    sup.put("agent-0001", UnitState.FAILED, NOW)
    assert await watchdog(sup).check_once() == ["agent-0001"]
    assert sup.restarts == ["agent-0001"]


@pytest.mark.asyncio
async def test_a_unit_that_went_silent_is_restarted_even_though_it_says_running():
    """The one that matters: a wedged engine looks perfectly healthy to
    anything that only reads `state`."""
    sup = FakeSupervisor()
    sup.put("agent-0002", UnitState.RUNNING, last_health_check_at=NOW - 600)
    assert await watchdog(sup).check_once() == ["agent-0002"]


@pytest.mark.asyncio
async def test_the_same_unit_id_comes_back(): 
    """The protocol's own promise: "an agent always lands on the same
    resources across a restart"."""
    sup = FakeSupervisor()
    sup.put("agent-0003", UnitState.FAILED, NOW)
    await watchdog(sup).check_once()
    assert sup.restarts == ["agent-0003"]
    assert sup.units["agent-0003"].unit_id == "agent-0003"


@pytest.mark.asyncio
async def test_a_healthy_unit_is_left_alone():
    sup = FakeSupervisor()
    sup.put("agent-0004", UnitState.RUNNING, NOW - 5)
    assert await watchdog(sup).check_once() == []
    assert sup.restarts == []


@pytest.mark.asyncio
async def test_a_deliberately_stopped_unit_is_not_resurrected():
    """Otherwise stop() would be a suggestion rather than a command."""
    sup = FakeSupervisor()
    sup.put("agent-0005", UnitState.STOPPED, NOW)
    sup.put("agent-0006", UnitState.STOPPING, NOW)
    sup.put("agent-0007", UnitState.PENDING, None)
    assert await watchdog(sup).check_once() == []


@pytest.mark.asyncio
async def test_unhealthy_but_recent_is_given_a_chance_to_recover():
    """UNHEALTHY is what a unit looks like mid-boot or during a slow broker
    call. Restarting on sight would kill units about to recover."""
    sup = FakeSupervisor()
    sup.put("agent-0008", UnitState.UNHEALTHY, NOW - 10)
    assert await watchdog(sup).check_once() == []


@pytest.mark.asyncio
async def test_but_an_unhealthy_unit_that_stays_silent_is_eventually_restarted():
    sup = FakeSupervisor()
    sup.put("agent-0009", UnitState.UNHEALTHY, NOW - 600)
    assert await watchdog(sup).check_once() == ["agent-0009"]


@pytest.mark.asyncio
async def test_a_brand_new_unit_with_no_heartbeat_yet_is_not_restarted():
    """started_at is not a heartbeat; treating it as one would restart every
    unit during a slow genesis boot."""
    sup = FakeSupervisor()
    sup.put("agent-0010", UnitState.STARTING, last_health_check_at=None)
    assert await watchdog(sup).check_once() == []


@pytest.mark.asyncio
async def test_backoff_stops_a_restart_storm():
    """A unit that fails on startup fails again the moment it comes back."""
    clock = {"t": NOW}
    sup = FakeSupervisor()
    sup.put("agent-0011", UnitState.FAILED, NOW)
    wd = SupervisorWatchdog(
        sup, clock=lambda: clock["t"],
        policy=RestartPolicy(initial_backoff_s=10.0, backoff_factor=2.0),
    )
    assert await wd.check_once() == ["agent-0011"]
    sup.put("agent-0011", UnitState.FAILED, clock["t"])
    assert await wd.check_once() == [], "should still be backing off"
    clock["t"] += 11
    assert await wd.check_once() == ["agent-0011"]


@pytest.mark.asyncio
async def test_it_gives_up_rather_than_retrying_forever():
    clock = {"t": NOW}
    sup = FakeSupervisor()
    sup.put("agent-0012", UnitState.FAILED, NOW)
    wd = SupervisorWatchdog(
        sup, clock=lambda: clock["t"],
        policy=RestartPolicy(max_restarts=3, initial_backoff_s=1.0, backoff_factor=1.0),
    )
    for _ in range(10):
        clock["t"] += 5
        sup.put("agent-0012", UnitState.FAILED, clock["t"])
        await wd.check_once()
    assert len(sup.restarts) == 3
    assert wd.records["agent-0012"].given_up is True


@pytest.mark.asyncio
async def test_a_unit_that_recovers_gets_its_budget_back():
    """A flaky unit would otherwise exhaust its budget over a long run for
    failures hours apart."""
    clock = {"t": NOW}
    sup = FakeSupervisor()
    sup.put("agent-0013", UnitState.FAILED, NOW)
    wd = SupervisorWatchdog(sup, clock=lambda: clock["t"],
                            policy=RestartPolicy(initial_backoff_s=1.0))
    await wd.check_once()
    assert wd.records["agent-0013"].restarts == 1
    clock["t"] += 100
    sup.put("agent-0013", UnitState.RUNNING, clock["t"])
    await wd.check_once()
    assert wd.records["agent-0013"].restarts == 0


@pytest.mark.asyncio
async def test_a_failed_restart_still_counts_against_the_budget():
    """Not counting it is how an unrestartable unit becomes an infinite loop."""
    clock = {"t": NOW}
    sup = FakeSupervisor()
    sup.put("agent-0014", UnitState.FAILED, NOW)
    sup.restart_raises = "no such microVM"
    wd = SupervisorWatchdog(sup, clock=lambda: clock["t"],
                            policy=RestartPolicy(max_restarts=2, initial_backoff_s=1.0,
                                                 backoff_factor=1.0))
    for _ in range(6):
        clock["t"] += 5
        await wd.check_once()
    assert wd.records["agent-0014"].given_up is True
    assert "restart failed" in wd.records["agent-0014"].last_error


@pytest.mark.asyncio
async def test_the_watchdog_survives_a_backend_that_cannot_even_list():
    """A watchdog that dies on a backend hiccup is worse than none, because
    the system then looks supervised and is not."""
    class Broken(FakeSupervisor):
        async def list(self):
            raise RuntimeError("socket closed")

    assert await watchdog(Broken()).check_once() == []


@pytest.mark.asyncio
async def test_run_forever_polls_and_stops_cleanly():
    sup = FakeSupervisor()
    sup.put("agent-0015", UnitState.FAILED, NOW)
    wd = watchdog(sup, poll_interval_s=0.01)
    task = wd.start()
    await asyncio.sleep(0.08)
    await wd.stop()
    assert task.done()
    assert sup.restarts, "the loop should have restarted the failed unit"


@pytest.mark.asyncio
async def test_starting_twice_does_not_start_two_loops():
    wd = watchdog(FakeSupervisor(), poll_interval_s=0.01)
    assert wd.start() is wd.start()
    await wd.stop()


@pytest.mark.asyncio
async def test_events_are_reported_for_an_operator_to_see():
    seen = []
    sup = FakeSupervisor()
    sup.put("agent-0016", UnitState.FAILED, NOW)
    wd = watchdog(sup, on_event=lambda kind, status, rec: seen.append((kind, status.unit_id)))
    await wd.check_once()
    assert ("restarting", "agent-0016") in seen
    assert ("restarted", "agent-0016") in seen


def test_backoff_is_bounded():
    policy = RestartPolicy(initial_backoff_s=2.0, max_backoff_s=30.0, backoff_factor=10.0)
    assert policy.backoff_for(1) == 2.0
    assert policy.backoff_for(2) == 20.0
    assert policy.backoff_for(3) == 30.0
    assert policy.backoff_for(99) == 30.0


def test_a_nonsense_policy_is_refused_at_construction():
    with pytest.raises(ValueError):
        RestartPolicy(max_restarts=-1)
    with pytest.raises(ValueError):
        RestartPolicy(initial_backoff_s=0)
