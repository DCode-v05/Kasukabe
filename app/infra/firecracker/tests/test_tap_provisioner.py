from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import FirecrackerSettings  # noqa: E402
from resources import ResourcePool  # noqa: E402
from tap_provisioner import LinuxTapProvisioner, NullNetworkProvisioner  # noqa: E402


class FakeIpRunner:
    def __init__(self, link_show_exit_code: int = 1) -> None:
        self.calls: list[list[str]] = []
        self.link_show_exit_code = link_show_exit_code

    async def run(self, args: list[str], check: bool) -> int:
        self.calls.append(args)
        if args[:2] == ["link", "show"]:
            return self.link_show_exit_code
        return 0


@pytest.mark.asyncio
async def test_null_provisioner_touches_nothing() -> None:
    settings = FirecrackerSettings()
    pool = ResourcePool(settings)
    resources = pool.allocate("agent-0001")
    provisioner = NullNetworkProvisioner()

    await provisioner.create_tap(resources, "fcbr0")
    await provisioner.remove_tap(resources)


@pytest.mark.asyncio
async def test_create_tap_adds_device_when_absent() -> None:
    settings = FirecrackerSettings()
    pool = ResourcePool(settings)
    resources = pool.allocate("agent-0001")
    runner = FakeIpRunner(link_show_exit_code=1)
    provisioner = LinuxTapProvisioner(ip_runner=runner)

    await provisioner.create_tap(resources, "fcbr0")

    tap = resources.tap_device
    assert ["link", "show", tap] in runner.calls
    assert ["tuntap", "add", "dev", tap, "mode", "tap"] in runner.calls
    assert ["link", "set", tap, "master", "fcbr0"] in runner.calls
    assert ["link", "set", tap, "up"] in runner.calls


@pytest.mark.asyncio
async def test_create_tap_is_idempotent_when_device_already_exists() -> None:
    settings = FirecrackerSettings()
    pool = ResourcePool(settings)
    resources = pool.allocate("agent-0002")
    runner = FakeIpRunner(link_show_exit_code=0)
    provisioner = LinuxTapProvisioner(ip_runner=runner)

    await provisioner.create_tap(resources, "fcbr0")

    tap = resources.tap_device
    assert ["tuntap", "add", "dev", tap, "mode", "tap"] not in runner.calls
    assert ["link", "set", tap, "master", "fcbr0"] in runner.calls


@pytest.mark.asyncio
async def test_remove_tap_deletes_the_device() -> None:
    settings = FirecrackerSettings()
    pool = ResourcePool(settings)
    resources = pool.allocate("agent-0003")
    runner = FakeIpRunner()
    provisioner = LinuxTapProvisioner(ip_runner=runner)

    await provisioner.remove_tap(resources)

    assert ["link", "delete", resources.tap_device] in runner.calls
