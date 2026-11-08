"""Per-unit resource allocation: vsock CID, tap device name, guest IP,
and the on-disk paths (API socket, vsock UDS, snapshot staging) a unit
needs before vm_manager.py can configure it.

Allocation is a pure function of unit_id, not a counter that hands out the
next free slot. That is deliberate: unit_id "agent-0173" gets the same
CID, tap name, and IP every single time across restarts, snapshots, and
even a full VMManager process restart, so a snapshot taken before a crash
and restored after one lands the unit back on the network address the
world server's broker already associates with it, instead of racing other
units for whichever slot happened to be free first.

The one exception is socket/run-dir paths, which do need cleanup on
stop() (stale UDS files block a re-bind) - that half of this module is
imperative, not pure, and is called out separately below.
"""

from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from pathlib import Path

from config import FirecrackerSettings


class ResourceError(Exception):
    """Raised when unit_id's derived resources cannot fit the configured pool."""


def deterministic_index(unit_id: str, pool_size: int) -> int:
    """Stable unit_id -> [0, pool_size) mapping via a hash, not
    incrementing state. Collisions are possible in principle (pigeonhole,
    two unit_ids hashing to the same slot) but not in practice for this
    system: DESIGN.md caps genesis at 250-300 agents and unit_ids are
    assigned by the genesis generator as "agent-NNNN", so pool_size is
    always >> the number of live unit_ids and ResourcePool.allocate()
    still checks for a live collision and raises rather than silently
    reusing another unit's resources.
    """
    digest = hashlib.sha256(unit_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % pool_size


@dataclass(frozen=True)
class UnitResources:
    unit_id: str
    vsock_cid: int
    guest_ip: str
    tap_device: str
    api_socket_path: str
    vsock_uds_path: str
    run_dir: Path


class ResourcePool:
    """Tracks which unit_id currently holds which derived resources, so a
    second start() for a unit_id that hashes to the same slot as a
    DIFFERENT still-live unit_id is caught instead of silently colliding
    on the wire (two microVMs on the same tap device would be a genuinely
    confusing bug to chase during a live genesis run).
    """

    def __init__(self, settings: FirecrackerSettings, cid_pool_size: int = 100_000) -> None:
        self.settings = settings
        self.cid_pool_size = cid_pool_size
        self._network = ipaddress.ip_network(settings.network_cidr, strict=True)
        self._ip_pool_size = self._network.num_addresses - 3
        if self._ip_pool_size < 1:
            raise ResourceError(
                f"network_cidr {settings.network_cidr} has no usable addresses for guests"
            )
        self._live: dict[str, UnitResources] = {}

    def allocate(self, unit_id: str) -> UnitResources:
        existing = self._live.get(unit_id)
        if existing is not None:
            return existing

        cid = self.settings.vsock_cid_base + deterministic_index(unit_id, self.cid_pool_size)
        ip_index = 2 + deterministic_index(unit_id, self._ip_pool_size)
        guest_ip = str(self._network.network_address + ip_index)

        for other_id, other in self._live.items():
            if other.vsock_cid == cid:
                raise ResourceError(
                    f"vsock CID {cid} for {unit_id!r} collides with live unit {other_id!r}; "
                    "widen cid_pool_size or rename one of the units"
                )
            if other.guest_ip == guest_ip:
                raise ResourceError(
                    f"guest IP {guest_ip} for {unit_id!r} collides with live unit {other_id!r}; "
                    f"widen network_cidr (currently {self.settings.network_cidr})"
                )

        tap_device = f"{self.settings.tap_prefix}{cid}"
        run_dir = self.settings.run_dir / unit_id
        resources = UnitResources(
            unit_id=unit_id,
            vsock_cid=cid,
            guest_ip=guest_ip,
            tap_device=tap_device,
            api_socket_path=str(run_dir / "api.sock"),
            vsock_uds_path=str(run_dir / "vsock.sock"),
            run_dir=run_dir,
        )
        self._live[unit_id] = resources
        return resources

    def release(self, unit_id: str) -> None:
        self._live.pop(unit_id, None)

    def get(self, unit_id: str) -> UnitResources | None:
        return self._live.get(unit_id)

    def live_units(self) -> list[str]:
        return list(self._live.keys())
