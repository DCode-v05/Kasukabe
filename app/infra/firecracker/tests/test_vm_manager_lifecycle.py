"""The track's required test: boot 3 microVMs, snapshot, restore, verify state.

Runs against process.FakeFirecrackerProcessLauncher (a real FastAPI app on
a real Unix socket implementing Firecracker's actual call-ordering rules,
see process.py's module docstring for exactly what that does and does not
prove) because this track was built on a Mac dev host with no /dev/kvm -
see app/infra/firecracker/README.md for why a real boot cannot happen
here. tests/test_real_firecracker_integration.py is the same scenario
against the real `firecracker` binary, gated on real hardware and skipped
in this environment.

"Verify state" here means something stronger than "the calls succeeded":
each unit's guest-state is set to a distinct value, snapshotted, mutated
again post-snapshot, and restore must bring back the PRE-mutation
snapshotted value, not the current one - proving the restore path
actually moved data through the snapshot files rather than restore()
being a no-op that happens to match already-current state.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import FirecrackerSettings  # noqa: E402
from process import FakeFirecrackerProcessLauncher  # noqa: E402
from resources import ResourcePool  # noqa: E402
from snapshot_store import LocalSnapshotStore  # noqa: E402
from supervisor import (  # noqa: E402
    UnitAlreadyExistsError,
    UnitSpec,
    UnitState,
    UnknownUnitError,
)
from vm_manager import VMManager  # noqa: E402


def make_manager(tmp_path: Path) -> VMManager:
    settings = FirecrackerSettings(run_dir=tmp_path / "run", network_cidr="10.200.0.0/24")
    return VMManager(
        settings=settings,
        launcher=FakeFirecrackerProcessLauncher(),
        snapshot_store=LocalSnapshotStore(root_dir=tmp_path / "snapshots"),
        resource_pool=ResourcePool(settings),
    )


def make_spec(index: int) -> UnitSpec:
    unit_id = f"agent-{index:04d}"
    return UnitSpec(
        unit_id=unit_id,
        engine_id=unit_id,
        cdt_url="http://10.200.0.1:8200",
        cdt_key="test-broker-key",
    )


async def get_guest_state(api_socket_path: str) -> dict:
    transport = httpx.AsyncHTTPTransport(uds=api_socket_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://fc") as client:
        resp = await client.get("/_test/guest-state")
        resp.raise_for_status()
        return resp.json()


async def set_guest_state(api_socket_path: str, state: dict) -> None:
    transport = httpx.AsyncHTTPTransport(uds=api_socket_path)
    async with httpx.AsyncClient(transport=transport, base_url="http://fc") as client:
        resp = await client.post("/_test/guest-state", json=state)
        resp.raise_for_status()


@pytest.mark.asyncio
async def test_boot_three_snapshot_restore_verify_state(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    specs = [make_spec(i) for i in range(3)]

    # --- boot 3 microVMs ---
    statuses = [await manager.start(spec) for spec in specs]
    assert all(s.state == UnitState.RUNNING for s in statuses)

    listed = await manager.list()
    assert len(listed) == 3
    assert {s.unit_id for s in listed} == {spec.unit_id for spec in specs}
    assert all(s.is_alive for s in listed)

    for spec in specs:
        assert await manager.health(spec.unit_id) is True

    # --- give each unit distinct, verifiable guest content ---
    resources_by_unit = {spec.unit_id: manager.require_unit(spec.unit_id).resources for spec in specs}
    pre_snapshot_state = {}
    for spec in specs:
        content = {"processed_ticks": 100 + int(spec.unit_id[-4:]), "last_action": "farm.harvest"}
        pre_snapshot_state[spec.unit_id] = content
        await set_guest_state(resources_by_unit[spec.unit_id].api_socket_path, content)

    # --- snapshot every unit at the same world tick ---
    handles = {}
    for spec in specs:
        handle = await manager.snapshot(spec.unit_id, tick=42)
        handles[spec.unit_id] = handle
        assert handle.taken_at_tick == 42
        assert handle.unit_id == spec.unit_id
        assert handle.memfile_uri is not None

    # snapshot must leave the unit running again, not paused
    for spec in specs:
        status = await manager.status(spec.unit_id)
        assert status.state == UnitState.RUNNING

    # --- mutate guest state AFTER the snapshot, to prove restore reverts it ---
    for spec in specs:
        await set_guest_state(
            resources_by_unit[spec.unit_id].api_socket_path,
            {"processed_ticks": 999999, "last_action": "should-not-survive-restore"},
        )
        mutated = await get_guest_state(resources_by_unit[spec.unit_id].api_socket_path)
        assert mutated["processed_ticks"] == 999999

    # --- restore every unit from its tick-42 snapshot ---
    for spec in specs:
        status = await manager.restore(spec.unit_id, handles[spec.unit_id])
        assert status.state == UnitState.RUNNING
        assert "restored from tick 42" in status.detail

    # --- verify state: restored guest content matches pre-snapshot, not the mutation ---
    for spec in specs:
        restored_resources = manager.require_unit(spec.unit_id).resources
        restored_state = await get_guest_state(restored_resources.api_socket_path)
        assert restored_state == pre_snapshot_state[spec.unit_id], (
            f"{spec.unit_id} restored to the wrong guest state: "
            f"got {restored_state}, expected {pre_snapshot_state[spec.unit_id]}"
        )

    # --- clean teardown of all 3 ---
    await manager.shutdown_all()
    final = await manager.list()
    assert all(s.state == UnitState.STOPPED for s in final)
    assert all(not s.is_alive for s in final)


@pytest.mark.asyncio
async def test_double_start_of_a_live_unit_is_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    spec = make_spec(0)
    await manager.start(spec)
    with pytest.raises(UnitAlreadyExistsError):
        await manager.start(spec)
    await manager.shutdown_all()


@pytest.mark.asyncio
async def test_operations_on_an_unknown_unit_raise(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    with pytest.raises(UnknownUnitError):
        await manager.status("never-started")
    with pytest.raises(UnknownUnitError):
        await manager.stop("never-started")
    with pytest.raises(UnknownUnitError):
        await manager.health("never-started")


@pytest.mark.asyncio
async def test_restart_preserves_unit_identity_and_resources(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    spec = make_spec(1)
    first = await manager.start(spec)
    resources_before = manager.require_unit(spec.unit_id).resources

    second = await manager.restart(spec.unit_id)

    assert second.state == UnitState.RUNNING
    assert second.unit_id == first.unit_id
    resources_after = manager.require_unit(spec.unit_id).resources
    assert resources_before.vsock_cid == resources_after.vsock_cid
    assert resources_before.guest_ip == resources_after.guest_ip
    await manager.shutdown_all()


@pytest.mark.asyncio
async def test_health_detects_a_unit_that_died_out_from_under_the_manager(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    spec = make_spec(2)
    await manager.start(spec)
    assert await manager.health(spec.unit_id) is True

    # Simulate the microVM process crashing without going through
    # manager.stop() - the fake handle's terminate() stands in for the
    # firecracker process dying (kill -9, host OOM, etc.).
    rt = manager.require_unit(spec.unit_id)
    await rt.handle.terminate()

    assert await manager.health(spec.unit_id) is False
    status = await manager.status(spec.unit_id)
    assert status.state == UnitState.UNHEALTHY


@pytest.mark.asyncio
async def test_snapshotting_a_non_running_unit_is_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    spec = make_spec(3)
    await manager.start(spec)
    await manager.stop(spec.unit_id)

    with pytest.raises(UnknownUnitError):
        await manager.snapshot(spec.unit_id, tick=1)
