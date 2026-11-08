from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import FirecrackerSettings  # noqa: E402
from resources import ResourceError, ResourcePool  # noqa: E402


def test_allocation_is_deterministic_across_pool_instances(tmp_path: Path) -> None:
    settings = FirecrackerSettings(run_dir=tmp_path / "run", network_cidr="10.200.0.0/20")
    pool_a = ResourcePool(settings)
    pool_b = ResourcePool(settings)

    a = pool_a.allocate("agent-0173")
    b = pool_b.allocate("agent-0173")

    assert a.vsock_cid == b.vsock_cid
    assert a.guest_ip == b.guest_ip
    assert a.tap_device == b.tap_device


def test_allocation_survives_release_and_reallocate(tmp_path: Path) -> None:
    settings = FirecrackerSettings(run_dir=tmp_path / "run")
    pool = ResourcePool(settings)

    first = pool.allocate("agent-0001")
    pool.release("agent-0001")
    second = pool.allocate("agent-0001")

    assert first == second


def test_allocate_is_idempotent_while_live(tmp_path: Path) -> None:
    settings = FirecrackerSettings(run_dir=tmp_path / "run")
    pool = ResourcePool(settings)

    first = pool.allocate("agent-0001")
    second = pool.allocate("agent-0001")

    assert first == second


def test_distinct_units_get_distinct_resources(tmp_path: Path) -> None:
    settings = FirecrackerSettings(run_dir=tmp_path / "run", network_cidr="10.200.0.0/16")
    pool = ResourcePool(settings, cid_pool_size=100_000)

    allocated = [pool.allocate(f"agent-{i:04d}") for i in range(250)]

    cids = {r.vsock_cid for r in allocated}
    ips = {r.guest_ip for r in allocated}
    assert len(cids) == 250, "250 genesis agents must not share a vsock CID"
    assert len(ips) == 250, "250 genesis agents must not share a guest IP"


def test_vsock_cid_collision_between_live_units_raises(tmp_path: Path) -> None:
    settings = FirecrackerSettings(run_dir=tmp_path / "run", network_cidr="10.200.0.0/16")
    pool = ResourcePool(settings, cid_pool_size=1)

    pool.allocate("agent-a")
    with pytest.raises(ResourceError, match="vsock CID"):
        pool.allocate("agent-b")


def test_guest_ip_collision_between_live_units_raises(tmp_path: Path) -> None:
    settings = FirecrackerSettings(run_dir=tmp_path / "run", network_cidr="10.200.0.0/30")
    pool = ResourcePool(settings, cid_pool_size=100_000)

    pool.allocate("agent-a")
    with pytest.raises(ResourceError, match="guest IP"):
        pool.allocate("agent-b")


def test_ip_pool_exhaustion_is_rejected_at_construction(tmp_path: Path) -> None:
    settings = FirecrackerSettings(run_dir=tmp_path / "run", network_cidr="10.200.0.0/31")
    with pytest.raises(ResourceError):
        ResourcePool(settings)
