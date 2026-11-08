"""SG7: a run that is killed keeps its residents' own records.

`archive_engine_homes` packs every session log and `brain.sqlite` into the
checkpoint directory - on a clean stop, after the engines have closed their
files. A killed run never gets there, and killed is how runs end. Run 29's
135 MB of homes were gone from /tmp within a day.

`snapshot_engine_homes` is taken with the periodic reading while the
engines are still running. A live database is copied through SQLite's own
backup, so the copy is consistent even while it is being written to.
"""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

import pytest

from world import run_genesis
from world.run_genesis import restore_engine_homes, snapshot_engine_homes


def _a_live_home(root: Path) -> sqlite3.Connection:
    home = root / "a0042"
    home.mkdir(parents=True)
    (home / "session.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    brain = sqlite3.connect(str(home / "brain.sqlite"))
    brain.execute("pragma journal_mode=wal")
    brain.execute("create table memories (text)")
    brain.execute("insert into memories values ('Hendrik paid 5000 sim to wake my brother')")
    brain.commit()
    return brain            # left OPEN, as a running engine holds it


def test_a_running_home_is_snapshotted_whole(tmp_path) -> None:
    brain = _a_live_home(tmp_path / "homes")
    brain.execute("insert into memories values ('written after the commit, in the wal')")
    brain.commit()

    archive = snapshot_engine_homes(tmp_path / "homes", tmp_path / "cp")

    assert archive == tmp_path / "cp" / "engine-homes.tar.gz"
    out = tmp_path / "out"
    with tarfile.open(archive) as tar:
        tar.extractall(out, filter="data")
        names = tar.getnames()
    assert not any(n.endswith(("-wal", "-shm")) for n in names)
    copy = sqlite3.connect(str(out / "homes" / "a0042" / "brain.sqlite"))
    assert copy.execute("select count(*) from memories").fetchone()[0] == 2
    assert (out / "homes" / "a0042" / "session.jsonl").read_text().startswith('{"turn"')
    brain.close()


def test_a_continuation_restores_from_the_snapshot(tmp_path) -> None:
    brain = _a_live_home(tmp_path / "homes")
    snapshot_engine_homes(tmp_path / "homes", tmp_path / "cp")
    brain.close()

    restored = restore_engine_homes(tmp_path / "fresh", tmp_path / "cp")

    assert restored is not None
    assert (tmp_path / "fresh" / "a0042" / "brain.sqlite").is_file()


def test_nothing_to_keep_writes_nothing(tmp_path) -> None:
    (tmp_path / "homes").mkdir()

    assert snapshot_engine_homes(tmp_path / "homes", tmp_path / "cp") is None
    assert not (tmp_path / "cp" / "engine-homes.tar.gz").exists()


def test_a_failed_snapshot_leaves_the_last_good_one(tmp_path, monkeypatch) -> None:
    brain = _a_live_home(tmp_path / "homes")
    good = snapshot_engine_homes(tmp_path / "homes", tmp_path / "cp")
    size = good.stat().st_size

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(tarfile, "open", broken)
    assert snapshot_engine_homes(tmp_path / "homes", tmp_path / "cp") is None
    assert good.stat().st_size == size
    assert not (tmp_path / "cp" / "engine-homes.tar.gz.part").exists()
    brain.close()


@pytest.mark.asyncio
async def test_the_periodic_reading_takes_it(tmp_path, monkeypatch) -> None:
    taken: list[tuple] = []

    async def reading(**kwargs):
        return None

    monkeypatch.setattr(run_genesis, "capture_final_reading", reading)
    monkeypatch.setattr(run_genesis, "snapshot_engine_homes", lambda *a: taken.append(a))
    monkeypatch.setattr(run_genesis, "READING_EVERY_TICKS", 144)
    runner = run_genesis.GenesisRunner(run_genesis.GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp", home_root=str(tmp_path / "homes"),
        world_mode="embedded", world_port=0,
    ))

    await runner.capture_reading_if_due(143)
    assert taken == []
    await runner.capture_reading_if_due(144)
    assert len(taken) == 1
