"""Per-unit tap device lifecycle: the host-side half of "per-VM vsock or
network to the world server" (this track's task description).

Tap device names come out of resources.py's ResourcePool, which derives
them from a hash of unit_id, not a sequential counter (see
ResourcePool's own docstring for why: unit_id must land on the same
resources across restarts and backend swaps). That determinism is exactly
why tap devices cannot be pre-created in bulk by a standalone shell script
before genesis starts: a bash script would need to reimplement
resources.deterministic_index()'s sha256 hash to know which names to
create, and two independent implementations of the same hash function
drifting apart is a bug waiting to happen. Instead, VMManager creates
(and destroys) each unit's own tap device at the moment it already knows
the exact name ResourcePool gave it - one source of truth, not two.

network/bridge_setup.sh is the separate, host-level, run-once (not
per-unit) piece: it creates the bridge itself
(FirecrackerSettings.bridge_name) that every tap device attaches to.
That part genuinely is static across the whole genesis run and belongs
in a plain ops script, not per-unit Python.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from resources import UnitResources


class NetworkProvisionError(Exception):
    """Raised when a real tap device cannot be created or removed. Not
    raised at all by NullNetworkProvisioner, which is why every test in
    this track (none of which run with CAP_NET_ADMIN, and none of which
    should touch this Mac's or a CI runner's real network stack) uses it
    by default - see vm_manager.VMManager's network_provisioner parameter.
    """


class NetworkProvisioner(Protocol):
    async def create_tap(self, resources: UnitResources, bridge_name: str) -> None: ...

    async def remove_tap(self, resources: UnitResources) -> None: ...


class NullNetworkProvisioner:
    """The default for every test and for FakeFirecrackerProcessLauncher-backed
    manager instances: does nothing. A microVM booted against the fake
    backend never actually opens a network device (its "network-interface"
    configuration is just a JSON blob the fake stores, see process.py), so
    there is nothing real for this to provision.
    """

    async def create_tap(self, resources: UnitResources, bridge_name: str) -> None:
        return None

    async def remove_tap(self, resources: UnitResources) -> None:
        return None


class IpRunner(Protocol):
    """Runs one `ip <args...>` invocation, returning its exit code.
    Injectable so LinuxTapProvisioner's create/remove logic (idempotency
    check, exact argument order) is unit-testable without CAP_NET_ADMIN
    or a real network device - see tests/test_tap_provisioner.py's
    FakeIpRunner, the same pattern snapshot_store.py's CommandRunner
    already established for `aws s3 cp`.
    """

    async def run(self, args: list[str], check: bool) -> int: ...


class SubprocessIpRunner:
    async def run(self, args: list[str], check: bool = True) -> int:
        proc = await asyncio.create_subprocess_exec(
            "ip", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if check and proc.returncode != 0:
            raise NetworkProvisionError(
                f"ip {' '.join(args)} failed ({proc.returncode}): {stderr.decode(errors='replace')}"
            )
        return proc.returncode or 0


class LinuxTapProvisioner:
    """The RUNBOOK.md production path: shells out to `ip` (iproute2),
    exactly like network/bridge_setup.sh does for the bridge itself.
    Requires CAP_NET_ADMIN (root, or the specific capability granted to
    whatever user runs the genesis process) - RUNBOOK.md's genesis-day
    invocation runs as root for this reason, same as bridge_setup.sh.
    """

    def __init__(self, ip_runner: IpRunner | None = None) -> None:
        self._ip = ip_runner or SubprocessIpRunner()

    async def create_tap(self, resources: UnitResources, bridge_name: str) -> None:
        tap = resources.tap_device
        # Idempotent: a tap left over from an unclean shutdown of the
        # SAME unit_id (which always maps to the same tap name, see this
        # module's docstring) must not make a fresh start() fail.
        exists = await self._ip.run(["link", "show", tap], check=False)
        if exists != 0:
            await self._ip.run(["tuntap", "add", "dev", tap, "mode", "tap"], check=True)
        await self._ip.run(["link", "set", tap, "master", bridge_name], check=True)
        await self._ip.run(["link", "set", tap, "up"], check=True)

    async def remove_tap(self, resources: UnitResources) -> None:
        tap = resources.tap_device
        await self._ip.run(["link", "delete", tap], check=False)
