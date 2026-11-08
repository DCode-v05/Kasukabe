"""B2: the genesis runner actually starts B1's watchdog.

The 250 section 33 says both Supervisor protocols "already define... a
restart(unit_id) method". That was true of infra/firecracker/supervisor.py
and NOT true of this runner's own protocol - neither ProcessSupervisor nor
MockSupervisor had one, so there was nothing for a watchdog to call on the
backend the sim actually boots on. These cover both halves of closing that.
"""

from __future__ import annotations

import asyncio

import pytest

from world.run_genesis import (
    GenesisRunnerConfig,
    MockSupervisor,
    Supervisor,
    SupervisorWatchdog,
    UnitSpec,
    UnitState,
    UnitStatus,
)


def spec(unit_id: str) -> UnitSpec:
    return UnitSpec(unit_id=unit_id, engine_id="e", cdt_url="http://broker", cdt_key="k")


def test_the_runners_protocol_now_declares_restart():
    assert hasattr(Supervisor, "restart")


@pytest.mark.asyncio
async def test_mock_supervisor_restarts_the_same_unit_id():
    sup = MockSupervisor()
    await sup.start(spec("agent-0001"))
    status = await sup.restart("agent-0001")
    assert status.unit_id == "agent-0001"
    assert status.state is UnitState.RUNNING
    assert sup.restarts == ["agent-0001"]


@pytest.mark.asyncio
async def test_restarting_a_unit_that_never_existed_is_an_error():
    from world.run_genesis import UnknownUnitError

    with pytest.raises(UnknownUnitError):
        await MockSupervisor().restart("agent-nope")


@pytest.mark.asyncio
async def test_restart_works_on_a_unit_that_was_stopped():
    """start() alone would raise UnitAlreadyExistsError on a live unit, which
    is why restart() has to own the stop-then-start ordering itself."""
    sup = MockSupervisor()
    await sup.start(spec("agent-0002"))
    await sup.stop("agent-0002")
    assert (await sup.restart("agent-0002")).state is UnitState.RUNNING


@pytest.mark.asyncio
async def test_the_watchdog_compares_states_by_value_not_identity():
    """run_genesis declares its OWN UnitState rather than importing infra's
    (infra/README.md: "Never imported by world/..."). Two structurally
    identical str-Enums are never `is`-equal, so a watchdog written with
    `is` would silently never restart anything. This is that regression."""
    sup = MockSupervisor()
    await sup.start(spec("agent-0003"))
    sup.statuses["agent-0003"] = UnitStatus(
        unit_id="agent-0003", state=UnitState.FAILED, backend_handle="mock",
        started_at=0.0, last_health_check_at=0.0,
    )
    wd = SupervisorWatchdog(sup, clock=lambda: 0.0)
    assert await wd.check_once() == ["agent-0003"]
    assert sup.restarts == ["agent-0003"]


def test_the_config_carries_the_watchdog_knobs():
    cfg = GenesisRunnerConfig()
    assert cfg.watchdog_poll_interval_s > 0
    assert cfg.watchdog_silence_timeout_s > cfg.tick_timeout_s


@pytest.mark.asyncio
async def test_start_watchdog_is_a_no_op_before_any_supervisor_exists():
    """Called on a runner that never booted, it must not explode."""
    from world.run_genesis import GenesisRunner

    runner = GenesisRunner.__new__(GenesisRunner)
    runner.supervisor = None
    runner.watchdog = None
    runner.config = GenesisRunnerConfig()
    runner.start_watchdog()
    assert runner.watchdog is None


@pytest.mark.asyncio
async def test_start_watchdog_starts_a_loop_that_restarts_a_dead_unit():
    from world.run_genesis import GenesisRunner

    sup = MockSupervisor()
    await sup.start(spec("agent-0004"))
    sup.statuses["agent-0004"] = UnitStatus(
        unit_id="agent-0004", state=UnitState.FAILED, backend_handle="mock",
        started_at=0.0, last_health_check_at=0.0,
    )
    runner = GenesisRunner.__new__(GenesisRunner)
    runner.supervisor = sup
    runner.watchdog = None
    runner.config = GenesisRunnerConfig(watchdog_poll_interval_s=0.01)

    runner.start_watchdog()
    assert runner.watchdog is not None
    await asyncio.sleep(0.08)
    await runner.watchdog.stop()
    assert sup.restarts == ["agent-0004"]
