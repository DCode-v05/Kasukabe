"""Durable storage for microVM snapshot pairs (vmstate + guest memory file),
"AgentEnv style": snapshot is a resumable cache keyed by unit + tick, not
the system of record. DESIGN.md section 9's SQLite/Litestream trace store
stays the source of truth for what happened in the sim; this module only
answers "how do I get unit X's guest memory back to exactly tick T,"
mirroring the distinction the Space runtime decision already drew for
this project's other snapshot layer.

Two implementations:

- LocalSnapshotStore: plain filesystem copy, no subprocess, no network.
  Used by every test in this track (test_vm_manager_lifecycle.py's boot/
  snapshot/restore/verify run entirely against this) and available as a
  genuine single-host fallback - not just a test double - since a single
  m5.metal host with local disk does not strictly need S3 to survive its
  own process crashing, only to survive the *host* being reclaimed
  (DESIGN.md section 9's spot-interruption safety net).

- S3SnapshotStore: shells out to the `aws` CLI rather than importing
  boto3. This is a deliberate module-boundary choice, not a missing
  dependency: app/CONVENTIONS.md restricts boto3 to broker/ ("the only
  package allowed to import boto3"), and app/tests/test_module_boundaries.py
  enforces it repo-wide by scanning every .py file's imports - importing
  boto3 here would trip that shared, cross-track invariant test for
  every other track's `docker compose run --rm test`, which is exactly
  the kind of collateral damage "stay in your lane" exists to prevent.
  Shelling out to awscli sidesteps the whole question and matches how
  Litestream (this repo's other S3 sync path) already works: an external
  binary owns the transfer, Python only orchestrates it.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Protocol

from config import FirecrackerSettings
from supervisor import SnapshotHandle


class SnapshotStore(Protocol):
    async def upload(
        self, unit_id: str, tick: int, snapshot_path: Path, mem_file_path: Path
    ) -> SnapshotHandle: ...

    async def download(self, handle: SnapshotHandle, dest_dir: Path) -> tuple[Path, Path]: ...


class CommandRunner(Protocol):
    async def run(self, args: list[str]) -> None: ...


class SubprocessCommandRunner:
    """Runs a command for real, raising with captured stderr on a nonzero
    exit so an upload failure during genesis names the exact aws CLI
    invocation that failed rather than surfacing as a bare CalledProcessError.
    """

    async def run(self, args: list[str]) -> None:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"command failed ({proc.returncode}): {' '.join(args)}\n"
                f"stderr: {stderr.decode(errors='replace')}"
            )


def snapshot_key(prefix: str, unit_id: str, tick: int, filename: str) -> str:
    return f"{prefix}/{unit_id}/{tick}/{filename}"


class LocalSnapshotStore:
    """Filesystem-backed SnapshotStore. root_dir stands in for the S3
    bucket: same key layout (prefix/unit_id/tick/filename), just a
    directory tree instead of object keys, so switching a VMManager from
    this to S3SnapshotStore later changes only which store gets
    constructed, never vm_manager.py's own snapshot()/restore() code.
    """

    def __init__(self, root_dir: Path, prefix: str = "kasukabe/vm-snapshots") -> None:
        self.root_dir = root_dir
        self.prefix = prefix

    def snapshot_paths(self, unit_id: str, tick: int) -> tuple[Path, Path]:
        base = self.root_dir / snapshot_key(self.prefix, unit_id, tick, "")
        return base / "vmstate.bin", base / "memfile.bin"

    async def upload(
        self, unit_id: str, tick: int, snapshot_path: Path, mem_file_path: Path
    ) -> SnapshotHandle:
        snap_dest, mem_dest = self.snapshot_paths(unit_id, tick)
        snap_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snapshot_path, snap_dest)
        shutil.copyfile(mem_file_path, mem_dest)
        return SnapshotHandle(
            unit_id=unit_id,
            snapshot_uri=f"file://{snap_dest}",
            memfile_uri=f"file://{mem_dest}",
            taken_at_tick=tick,
        )

    async def download(self, handle: SnapshotHandle, dest_dir: Path) -> tuple[Path, Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        snap_dest = dest_dir / f"restored-vmstate-{handle.taken_at_tick}.bin"
        mem_dest = dest_dir / f"restored-memfile-{handle.taken_at_tick}.bin"
        shutil.copyfile(strip_file_uri(handle.snapshot_uri), snap_dest)
        if handle.memfile_uri:
            shutil.copyfile(strip_file_uri(handle.memfile_uri), mem_dest)
        return snap_dest, mem_dest


def strip_file_uri(uri: str) -> str:
    return uri[len("file://") :] if uri.startswith("file://") else uri


class S3SnapshotStore:
    """The RUNBOOK.md production path. Every call goes through a
    CommandRunner so tests can assert on the exact `aws s3 cp` invocations
    without a network call or AWS credentials - see
    tests/test_snapshot_store.py's FakeCommandRunner.
    """

    def __init__(
        self, settings: FirecrackerSettings, command_runner: CommandRunner | None = None
    ) -> None:
        self._settings = settings
        self._runner = command_runner or SubprocessCommandRunner()

    def build_uri(self, unit_id: str, tick: int, filename: str) -> str:
        key = snapshot_key(self._settings.s3_prefix, unit_id, tick, filename)
        return f"s3://{self._settings.s3_bucket}/{key}"

    async def upload(
        self, unit_id: str, tick: int, snapshot_path: Path, mem_file_path: Path
    ) -> SnapshotHandle:
        snap_uri = self.build_uri(unit_id, tick, "vmstate.bin")
        mem_uri = self.build_uri(unit_id, tick, "memfile.bin")
        await self._runner.run(
            ["aws", "s3", "cp", str(snapshot_path), snap_uri, "--region", self._settings.aws_region]
        )
        await self._runner.run(
            ["aws", "s3", "cp", str(mem_file_path), mem_uri, "--region", self._settings.aws_region]
        )
        return SnapshotHandle(
            unit_id=unit_id, snapshot_uri=snap_uri, memfile_uri=mem_uri, taken_at_tick=tick
        )

    async def download(self, handle: SnapshotHandle, dest_dir: Path) -> tuple[Path, Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        snap_dest = dest_dir / f"restored-vmstate-{handle.taken_at_tick}.bin"
        mem_dest = dest_dir / f"restored-memfile-{handle.taken_at_tick}.bin"
        await self._runner.run(
            ["aws", "s3", "cp", handle.snapshot_uri, str(snap_dest), "--region", self._settings.aws_region]
        )
        if handle.memfile_uri:
            await self._runner.run(
                [
                    "aws",
                    "s3",
                    "cp",
                    handle.memfile_uri,
                    str(mem_dest),
                    "--region",
                    self._settings.aws_region,
                ]
            )
        return snap_dest, mem_dest
