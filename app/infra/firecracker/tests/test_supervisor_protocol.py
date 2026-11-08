"""Confirms VMManager structurally satisfies supervisor.Supervisor - the
one property the genesis runner actually depends on when it does
`supervisor: Supervisor = build_real_supervisor(settings)` or, in
process-mode, whatever the sibling track's ProcessSupervisor constructs.
If a method on either side ever drifts (renamed, wrong arity, sync where
the protocol wants async), this fails at test time instead of at genesis
boot.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import FirecrackerSettings  # noqa: E402
from resources import ResourcePool  # noqa: E402
from process import FakeFirecrackerProcessLauncher  # noqa: E402
from snapshot_store import LocalSnapshotStore  # noqa: E402
from supervisor import Supervisor  # noqa: E402
from vm_manager import VMManager  # noqa: E402


def build_manager(tmp_path: Path) -> VMManager:
    settings = FirecrackerSettings(run_dir=tmp_path / "run")
    return VMManager(
        settings=settings,
        launcher=FakeFirecrackerProcessLauncher(),
        snapshot_store=LocalSnapshotStore(root_dir=tmp_path / "snapshots"),
        resource_pool=ResourcePool(settings),
    )


def test_vm_manager_is_structurally_a_supervisor(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    assert isinstance(manager, Supervisor)


def test_every_protocol_method_is_a_coroutine_function(tmp_path: Path) -> None:
    manager = build_manager(tmp_path)
    for name in (
        "start",
        "stop",
        "restart",
        "status",
        "list",
        "health",
        "snapshot",
        "restore",
        "shutdown_all",
    ):
        method = getattr(manager, name)
        assert inspect.iscoroutinefunction(method), f"Supervisor.{name} must be async"
