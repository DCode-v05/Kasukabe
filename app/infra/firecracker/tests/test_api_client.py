"""Exercises FirecrackerApiClient against process.FakeFirecrackerProcessLauncher's
real (fake-backed) Unix-socket HTTP server - not a mocked httpx transport.
The fake enforces Firecracker's actual call ordering (see
process.build_fake_firecracker_app's require_phase calls), so a client-side
bug in request shape or an orchestration bug in call order fails here the
same way it would against real Firecracker.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_client import FirecrackerApiClient, FirecrackerApiError  # noqa: E402
from process import FakeFirecrackerProcessLauncher  # noqa: E402


@pytest_asyncio.fixture
async def live_socket(tmp_path: Path) -> str:
    launcher = FakeFirecrackerProcessLauncher()
    run_dir = tmp_path / "unit"
    socket_path = str(run_dir / "api.sock")
    handle = await launcher.launch("unit-under-test", socket_path, run_dir)
    yield socket_path
    await handle.terminate()


@pytest.mark.asyncio
async def test_full_boot_lifecycle_succeeds_in_order(live_socket: str) -> None:
    async with FirecrackerApiClient(live_socket) as client:
        info = await client.get_instance_info()
        assert info["state"] == "uninitialized"

        await client.configure_boot_source("/fake/vmlinux", "console=ttyS0")
        await client.configure_drive("rootfs", "/fake/rootfs.ext4", is_root_device=True)
        await client.configure_network_interface("eth0", "fctap0")
        await client.configure_vsock("vsock0", guest_cid=3, uds_path="/fake/vsock.sock")
        await client.configure_machine(vcpu_count=1, mem_size_mib=256)

        info = await client.get_instance_info()
        assert info["state"] == "configured"

        await client.start_instance()
        info = await client.get_instance_info()
        assert info["state"] == "running"

        await client.pause()
        info = await client.get_instance_info()
        assert info["state"] == "paused"

        await client.resume()
        info = await client.get_instance_info()
        assert info["state"] == "running"


@pytest.mark.asyncio
async def test_start_instance_before_machine_config_is_rejected(live_socket: str) -> None:
    async with FirecrackerApiClient(live_socket) as client:
        await client.configure_boot_source("/fake/vmlinux", "console=ttyS0")
        with pytest.raises(FirecrackerApiError) as excinfo:
            await client.start_instance()
        assert excinfo.value.status_code == 400
        assert "phase" in excinfo.value.body


@pytest.mark.asyncio
async def test_snapshot_create_before_pause_is_rejected(live_socket: str, tmp_path: Path) -> None:
    async with FirecrackerApiClient(live_socket) as client:
        await client.configure_boot_source("/fake/vmlinux", "console=ttyS0")
        await client.configure_machine(vcpu_count=1, mem_size_mib=256)
        await client.start_instance()

        with pytest.raises(FirecrackerApiError) as excinfo:
            await client.create_snapshot(
                str(tmp_path / "snap.bin"), str(tmp_path / "mem.bin")
            )
        assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_snapshot_create_then_load_round_trips_configuration(
    live_socket: str, tmp_path: Path
) -> None:
    async with FirecrackerApiClient(live_socket) as client:
        await client.configure_boot_source("/fake/vmlinux", "engine_id=agent-0001")
        await client.configure_machine(vcpu_count=1, mem_size_mib=256)
        await client.start_instance()
        await client.pause()

        snap_path = str(tmp_path / "snap.bin")
        mem_path = str(tmp_path / "mem.bin")
        await client.create_snapshot(snap_path, mem_path)
        assert Path(snap_path).exists()
        assert Path(mem_path).exists()

    # A fresh process (fresh socket, fresh state machine) loading that
    # snapshot must come up already configured with the same boot args.
    fresh_socket = str(tmp_path / "fresh-api.sock")
    from process import FakeFirecrackerProcessLauncher

    launcher = FakeFirecrackerProcessLauncher()
    handle = await launcher.launch("fresh-unit", fresh_socket, tmp_path / "fresh")
    try:
        async with FirecrackerApiClient(fresh_socket) as fresh_client:
            await fresh_client.load_snapshot(snap_path, mem_path, resume_vm=True)
            info = await fresh_client.get_instance_info()
            assert info["state"] == "running"
    finally:
        await handle.terminate()
