"""The same boot-3/snapshot/restore/verify scenario as
test_vm_manager_lifecycle.py, but against the REAL `firecracker` binary
and REAL KVM, for the m5.metal genesis host (RUNBOOK.md).

Skipped everywhere else, including in this track's own Docker test image:
this repo's dev machine is a Mac (see app/infra/firecracker/README.md) with
no /dev/kvm, and Docker Desktop's Linux VM on macOS does not expose nested
virtualization to containers either (verified: no /dev/kvm inside the
container, no qemu-system-*, no firecracker binary, confirmed while
building this track). That is a real, structural gap in what could be
tested from this environment, not a decision to skip it out of
convenience - this file is the honest record of exactly what still needs
to run, and where, before genesis.

RUNBOOK.md's pre-genesis checklist item "run the real-hardware suite"
means: on the m5.metal host, after kernel/build_kernel.sh and
rootfs/build_rootfs.sh have produced real artifacts,
FIRECRACKER_REAL_HW=1 pytest tests/test_real_firecracker_integration.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import FirecrackerSettings  # noqa: E402
from supervisor import UnitSpec, UnitState  # noqa: E402
from vm_manager import build_real_supervisor  # noqa: E402

KVM_PRESENT = Path("/dev/kvm").exists()
REAL_HW_REQUESTED = os.environ.get("FIRECRACKER_REAL_HW") == "1"

pytestmark = pytest.mark.skipif(
    not (REAL_HW_REQUESTED and KVM_PRESENT),
    reason=(
        "requires a real Linux host with /dev/kvm and a firecracker binary "
        "(set FIRECRACKER_REAL_HW=1 to opt in); see RUNBOOK.md. "
        f"FIRECRACKER_REAL_HW={os.environ.get('FIRECRACKER_REAL_HW')!r}, "
        f"/dev/kvm present={KVM_PRESENT}"
    ),
)


@pytest.mark.real_hardware
@pytest.mark.asyncio
async def test_boot_three_real_microvms_snapshot_restore_verify(tmp_path: Path) -> None:
    settings = FirecrackerSettings.build_from_env()
    manager = build_real_supervisor(settings)

    specs = [
        UnitSpec(
            unit_id=f"agent-{i:04d}",
            engine_id=f"agent-{i:04d}",
            cdt_url=os.environ.get("BROKER_BASE_URL", "http://10.200.0.1:8200"),
            cdt_key=os.environ["BROKER_TEST_KEY"],
        )
        for i in range(3)
    ]

    try:
        for spec in specs:
            status = await manager.start(spec)
            assert status.state == UnitState.RUNNING

        for spec in specs:
            healthy = await manager.health(spec.unit_id)
            assert healthy, f"{spec.unit_id} failed a real boot-to-healthy check"

        handles = {}
        for spec in specs:
            handles[spec.unit_id] = await manager.snapshot(spec.unit_id, tick=1)

        for spec in specs:
            status = await manager.restore(spec.unit_id, handles[spec.unit_id])
            assert status.state == UnitState.RUNNING

        for spec in specs:
            assert await manager.health(spec.unit_id)
    finally:
        await manager.shutdown_all()
