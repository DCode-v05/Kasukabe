"""Keep the test suite out of the town's real trace store.

GenesisRunner.attach_persistence() points its recorder at
TRACE_SQLITE_PATH (default "data/kasukabe.db") - the same file a live run
writes and a run report reads. Without this, every test that boots the
runner seeds its ten genesis agents straight into that file, so a report
written afterwards would describe a cast that came from a test rather
than from the town.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def trace_store_is_disposable(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
