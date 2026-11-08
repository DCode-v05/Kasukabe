"""VMManager: the Firecracker-backed implementation of supervisor.Supervisor.

This is the one class in the track that ties everything else together -
ResourcePool for addressing, a ProcessLauncher for getting an API socket
answering requests (real firecracker or, in every test, the fake), and a
SnapshotStore for durability. It never imports RealFirecrackerProcessLauncher
directly into its own logic; build_real_supervisor() at the bottom of this
file is the only place that decision gets made, so
test_vm_manager_lifecycle.py can construct a VMManager against
FakeFirecrackerProcessLauncher and exercise the exact same start/stop/
snapshot/restore code the real backend runs.
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
import urllib.parse
from dataclasses import dataclass, replace

from api_client import FirecrackerApiClient
from config import FirecrackerSettings
from process import (
    ProcessHandle,
    ProcessLauncher,
    RealFirecrackerProcessLauncher,
)
from resources import ResourcePool, UnitResources
from snapshot_store import SnapshotStore
from supervisor import (
    SnapshotHandle,
    UnitAlreadyExistsError,
    UnitSpec,
    UnitState,
    UnitStatus,
    UnknownUnitError,
)
from tap_provisioner import NetworkProvisioner, NullNetworkProvisioner


@dataclass
class UnitRuntime:
    resources: UnitResources
    handle: ProcessHandle
    client: FirecrackerApiClient
    status: UnitStatus
    spec: UnitSpec | None  # None after a cold restore() with no prior start()


def netmask_for_cidr(cidr: str) -> str:
    return str(ipaddress.ip_network(cidr, strict=True).netmask)


def build_boot_args(spec: UnitSpec, resources: UnitResources, settings: FirecrackerSettings) -> str:
    """Kernel command line the engine_init script (rootfs/engine_init)
    parses at boot. Identity fields (engine_id/cdt_url/cdt_key/unit_id)
    are exactly what engine/.env.example documents the engine needing
    (ENGINE_ID, CDT_URL, CDT_KEY) plus this track's own unit_id.

    extra_env rides along as extra__<KEY>=<url-quoted value> tokens
    rather than a separate config-drive image: the Linux kernel command
    line has no working quoting for embedded spaces short of a second
    block device, and DESIGN.md's engines need only the fields above, so
    that complexity is not worth carrying for a field this track's own
    UnitSpec docstring already calls "rare." A value that needs
    whitespace should not go through extra_env; this is a real
    constraint of the delivery mechanism, not an oversight.
    """
    netmask = netmask_for_cidr(settings.network_cidr)
    parts = [
        "console=ttyS0",
        "reboot=k",
        "panic=1",
        "pci=off",
        f"ip={resources.guest_ip}::{settings.bridge_host_ip}:{netmask}::eth0:off",
        "init=/usr/local/sbin/engine-init",
        f"unit_id={spec.unit_id}",
        f"engine_id={spec.engine_id}",
        f"cdt_url={urllib.parse.quote(spec.cdt_url, safe='')}",
        f"cdt_key={urllib.parse.quote(spec.cdt_key, safe='')}",
    ]
    for key, value in spec.extra_env.items():
        parts.append(f"extra__{key}={urllib.parse.quote(value, safe='')}")
    return " ".join(parts)


class VMManager:
    def __init__(
        self,
        settings: FirecrackerSettings,
        launcher: ProcessLauncher,
        snapshot_store: SnapshotStore,
        resource_pool: ResourcePool | None = None,
        network_provisioner: NetworkProvisioner | None = None,
    ) -> None:
        self._settings = settings
        self._launcher = launcher
        self._snapshot_store = snapshot_store
        self._resources = resource_pool or ResourcePool(settings)
        # Defaults to a no-op: every test in this track (and every
        # FakeFirecrackerProcessLauncher-backed manager, which never opens
        # a real network device to begin with, see process.py) must never
        # touch this host's or a CI runner's real network stack or need
        # CAP_NET_ADMIN just to run `pytest`. build_real_supervisor()
        # below is the only place a real one gets wired in.
        self._network = network_provisioner or NullNetworkProvisioner()
        self._units: dict[str, UnitRuntime] = {}

    def require_unit(self, unit_id: str) -> UnitRuntime:
        rt = self._units.get(unit_id)
        if rt is None:
            raise UnknownUnitError(f"no unit {unit_id!r} has ever been started on this manager")
        return rt

    async def start(self, spec: UnitSpec) -> UnitStatus:
        existing = self._units.get(spec.unit_id)
        if existing is not None and existing.status.is_alive:
            raise UnitAlreadyExistsError(
                f"unit {spec.unit_id!r} is already {existing.status.state.value}; "
                "call restart() or stop() first"
            )

        resources = self._resources.allocate(spec.unit_id)
        # Tap device must exist before configure_network_interface() below
        # asks Firecracker to attach to it by name - Firecracker itself
        # never creates the host-side device, only attaches to one that
        # already exists (tap_provisioner.py's module docstring).
        await self._network.create_tap(resources, self._settings.bridge_name)
        handle = await self._launcher.launch(
            spec.unit_id, resources.api_socket_path, resources.run_dir
        )
        client = FirecrackerApiClient(
            resources.api_socket_path, self._settings.api_request_timeout_s
        )

        try:
            boot_args = build_boot_args(spec, resources, self._settings)
            await client.configure_boot_source(str(self._settings.kernel_image), boot_args)
            await client.configure_drive(
                "rootfs",
                str(self._settings.rootfs_image),
                is_root_device=True,
                is_read_only=True,
            )
            await client.configure_network_interface("eth0", resources.tap_device)
            await client.configure_vsock("vsock0", resources.vsock_cid, resources.vsock_uds_path)
            await client.configure_machine(
                vcpu_count=self._settings.default_vcpu_count, mem_size_mib=spec.memory_mb
            )
            await client.start_instance()
        except Exception as exc:
            await client.close()
            await handle.terminate()
            await self._network.remove_tap(resources)
            self._resources.release(spec.unit_id)
            failed = UnitStatus(
                unit_id=spec.unit_id,
                state=UnitState.FAILED,
                backend_handle=resources.api_socket_path,
                started_at=None,
                last_health_check_at=None,
                detail=str(exc),
            )
            self._units[spec.unit_id] = UnitRuntime(
                resources=resources, handle=handle, client=client, status=failed, spec=spec
            )
            raise

        status = UnitStatus(
            unit_id=spec.unit_id,
            state=UnitState.RUNNING,
            backend_handle=resources.api_socket_path,
            started_at=time.time(),
            last_health_check_at=None,
            detail="",
        )
        self._units[spec.unit_id] = UnitRuntime(
            resources=resources, handle=handle, client=client, status=status, spec=spec
        )
        return status

    async def stop(self, unit_id: str, timeout_s: float = 10.0) -> UnitStatus:
        rt = self.require_unit(unit_id)
        rt.status = replace(rt.status, state=UnitState.STOPPING)
        await rt.client.close()
        await rt.handle.terminate(timeout_s)
        await self._network.remove_tap(rt.resources)
        rt.status = replace(rt.status, state=UnitState.STOPPED, backend_handle="")
        self._resources.release(unit_id)
        return rt.status

    async def restart(self, unit_id: str) -> UnitStatus:
        rt = self.require_unit(unit_id)
        if rt.spec is None:
            raise UnknownUnitError(
                f"unit {unit_id!r} was restored from a snapshot with no cached UnitSpec; "
                "call start(spec) instead of restart() for a unit that was never start()ed "
                "on this manager instance"
            )
        spec = rt.spec
        if rt.status.is_alive:
            await self.stop(unit_id)
        return await self.start(spec)

    async def status(self, unit_id: str) -> UnitStatus:
        return self.require_unit(unit_id).status

    async def list(self) -> list[UnitStatus]:
        return [rt.status for rt in self._units.values()]

    async def health(self, unit_id: str) -> bool:
        rt = self.require_unit(unit_id)
        healthy = False
        detail = ""
        try:
            info = await rt.client.get_instance_info()
            state = str(info.get("state", "")).lower()
            healthy = rt.handle.is_running and state in ("running",)
            if not healthy:
                detail = f"instance state reported as {state!r}"
        except Exception as exc:
            detail = str(exc)

        new_state = rt.status.state
        if healthy:
            if rt.status.state == UnitState.UNHEALTHY:
                new_state = UnitState.RUNNING
        else:
            if rt.status.state in (UnitState.RUNNING, UnitState.UNHEALTHY, UnitState.STARTING):
                new_state = UnitState.UNHEALTHY

        rt.status = replace(
            rt.status, state=new_state, last_health_check_at=time.time(), detail=detail
        )
        return healthy

    async def snapshot(self, unit_id: str, tick: int) -> SnapshotHandle:
        rt = self.require_unit(unit_id)
        if rt.status.state != UnitState.RUNNING:
            raise UnknownUnitError(
                f"cannot snapshot unit {unit_id!r} in state {rt.status.state.value}; "
                "only a RUNNING unit can be paused and snapshotted"
            )

        local_snapshot = rt.resources.run_dir / f"snapshot-{tick}.bin"
        local_memfile = rt.resources.run_dir / f"memfile-{tick}.bin"

        await rt.client.pause()
        try:
            await rt.client.create_snapshot(str(local_snapshot), str(local_memfile))
        finally:
            # Resume even if create_snapshot raised: a paused unit that
            # silently stays paused after a failed snapshot attempt is a
            # worse failure mode during a live genesis run than a
            # snapshot we simply retry.
            await rt.client.resume()

        return await self._snapshot_store.upload(
            unit_id=unit_id, tick=tick, snapshot_path=local_snapshot, mem_file_path=local_memfile
        )

    async def restore(self, unit_id: str, handle: SnapshotHandle) -> UnitStatus:
        existing = self._units.get(unit_id)
        if existing is not None and existing.status.is_alive:
            await self.stop(unit_id)

        resources = self._resources.allocate(unit_id)
        # Same tap name as the original start() (resources.py derives it
        # deterministically from unit_id, not a counter) - re-created here
        # because self.stop() above (if this unit was live) already tore
        # the previous one down, and a cold restore after a manager
        # restart never had one at all.
        await self._network.create_tap(resources, self._settings.bridge_name)
        local_snapshot, local_memfile = await self._snapshot_store.download(
            handle, dest_dir=resources.run_dir
        )

        launch_handle = await self._launcher.launch(
            unit_id, resources.api_socket_path, resources.run_dir
        )
        client = FirecrackerApiClient(
            resources.api_socket_path, self._settings.api_request_timeout_s
        )
        try:
            await client.load_snapshot(str(local_snapshot), str(local_memfile), resume_vm=True)
        except Exception as exc:
            await client.close()
            await launch_handle.terminate()
            await self._network.remove_tap(resources)
            self._resources.release(unit_id)
            failed = UnitStatus(
                unit_id=unit_id,
                state=UnitState.FAILED,
                backend_handle=resources.api_socket_path,
                started_at=None,
                last_health_check_at=None,
                detail=str(exc),
            )
            self._units[unit_id] = UnitRuntime(
                resources=resources,
                handle=launch_handle,
                client=client,
                status=failed,
                spec=existing.spec if existing else None,
            )
            raise

        status = UnitStatus(
            unit_id=unit_id,
            state=UnitState.RUNNING,
            backend_handle=resources.api_socket_path,
            started_at=time.time(),
            last_health_check_at=None,
            detail=f"restored from tick {handle.taken_at_tick}",
        )
        self._units[unit_id] = UnitRuntime(
            resources=resources,
            handle=launch_handle,
            client=client,
            status=status,
            spec=existing.spec if existing else None,
        )
        return status

    async def shutdown_all(self, timeout_s: float = 30.0) -> None:
        unit_ids = [uid for uid, rt in self._units.items() if rt.status.is_alive]
        results = await asyncio.gather(
            *(self.stop(uid, timeout_s) for uid in unit_ids), return_exceptions=True
        )
        errors = [(uid, r) for uid, r in zip(unit_ids, results) if isinstance(r, Exception)]
        if errors:
            # Every other unit still got a stop() attempt (gather ran them
            # all concurrently, not sequentially) - one unit's teardown
            # failure never blocks the other 249's, but it must not be
            # swallowed silently during a live genesis run either.
            raise RuntimeError(
                "shutdown_all: "
                + "; ".join(f"{uid} failed to stop: {err}" for uid, err in errors)
            )


def build_real_supervisor(settings: FirecrackerSettings) -> VMManager:
    """The one call site that selects RealFirecrackerProcessLauncher.
    Every test constructs VMManager directly with
    process.FakeFirecrackerProcessLauncher instead of calling this, so a
    test can never accidentally boot a real microVM and this function can
    never accidentally be exercised by fakes.
    """
    problems = settings.validate_for_real_boot()
    if problems:
        raise RuntimeError(
            "cannot build a real Firecracker supervisor:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )
    launcher = RealFirecrackerProcessLauncher(
        firecracker_binary=settings.firecracker_binary,
        jailer_binary=settings.jailer_binary,
        use_jailer=settings.use_jailer,
    )
    from snapshot_store import S3SnapshotStore
    from tap_provisioner import LinuxTapProvisioner

    store = S3SnapshotStore(settings)
    return VMManager(
        settings=settings,
        launcher=launcher,
        snapshot_store=store,
        network_provisioner=LinuxTapProvisioner(),
    )
