from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import FirecrackerSettings  # noqa: E402
from snapshot_store import LocalSnapshotStore, S3SnapshotStore  # noqa: E402


@pytest.mark.asyncio
async def test_local_store_round_trips_file_content(tmp_path: Path) -> None:
    store = LocalSnapshotStore(root_dir=tmp_path / "store")

    src_snap = tmp_path / "src-snap.bin"
    src_mem = tmp_path / "src-mem.bin"
    src_snap.write_bytes(b"vmstate-content")
    src_mem.write_bytes(b"memory-content")

    handle = await store.upload("agent-0001", tick=42, snapshot_path=src_snap, mem_file_path=src_mem)
    assert handle.taken_at_tick == 42
    assert handle.unit_id == "agent-0001"

    restored_snap, restored_mem = await store.download(handle, dest_dir=tmp_path / "restored")
    assert restored_snap.read_bytes() == b"vmstate-content"
    assert restored_mem.read_bytes() == b"memory-content"


@pytest.mark.asyncio
async def test_local_store_keys_by_unit_and_tick_independently(tmp_path: Path) -> None:
    store = LocalSnapshotStore(root_dir=tmp_path / "store")
    snap = tmp_path / "snap.bin"
    mem = tmp_path / "mem.bin"
    snap.write_bytes(b"a")
    mem.write_bytes(b"b")

    h1 = await store.upload("agent-0001", tick=1, snapshot_path=snap, mem_file_path=mem)
    h2 = await store.upload("agent-0001", tick=2, snapshot_path=snap, mem_file_path=mem)
    h3 = await store.upload("agent-0002", tick=1, snapshot_path=snap, mem_file_path=mem)

    assert h1.snapshot_uri != h2.snapshot_uri
    assert h1.snapshot_uri != h3.snapshot_uri


class FakeCommandRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._files: dict[str, bytes] = {}

    async def run(self, args: list[str]) -> None:
        self.calls.append(args)
        assert args[:3] == ["aws", "s3", "cp"]
        src, dst = args[3], args[4]
        if src.startswith("s3://"):
            Path(dst).write_bytes(self._files[src])
        else:
            self._files[dst] = Path(src).read_bytes()


@pytest.mark.asyncio
async def test_s3_store_shells_out_to_aws_cli_with_correct_uris(tmp_path: Path) -> None:
    settings = FirecrackerSettings(s3_bucket="my-bucket", s3_prefix="kasukabe/vm-snapshots")
    runner = FakeCommandRunner()
    store = S3SnapshotStore(settings, command_runner=runner)

    snap = tmp_path / "snap.bin"
    mem = tmp_path / "mem.bin"
    snap.write_bytes(b"vmstate")
    mem.write_bytes(b"memory")

    handle = await store.upload("agent-0173", tick=7, snapshot_path=snap, mem_file_path=mem)

    assert handle.snapshot_uri == "s3://my-bucket/kasukabe/vm-snapshots/agent-0173/7/vmstate.bin"
    assert handle.memfile_uri == "s3://my-bucket/kasukabe/vm-snapshots/agent-0173/7/memfile.bin"
    assert len(runner.calls) == 2
    for call in runner.calls:
        assert "--region" in call

    restored_snap, restored_mem = await store.download(handle, dest_dir=tmp_path / "restored")
    assert restored_snap.read_bytes() == b"vmstate"
    assert restored_mem.read_bytes() == b"memory"


@pytest.mark.asyncio
async def test_s3_store_upload_failure_surfaces_the_exact_command(tmp_path: Path) -> None:
    class FailingRunner:
        async def run(self, args: list[str]) -> None:
            raise RuntimeError(f"command failed (1): {' '.join(args)}\nstderr: access denied")

    settings = FirecrackerSettings(s3_bucket="my-bucket")
    store = S3SnapshotStore(settings, command_runner=FailingRunner())

    snap = tmp_path / "snap.bin"
    mem = tmp_path / "mem.bin"
    snap.write_bytes(b"x")
    mem.write_bytes(b"y")

    with pytest.raises(RuntimeError, match="access denied"):
        await store.upload("agent-0001", tick=1, snapshot_path=snap, mem_file_path=mem)
