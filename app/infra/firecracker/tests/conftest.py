from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import FirecrackerSettings  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> FirecrackerSettings:
    return FirecrackerSettings(
        run_dir=tmp_path / "run",
        network_cidr="10.200.0.0/24",
        bridge_host_ip="10.200.0.1",
        s3_bucket="kasukabe-test-bucket",
        s3_prefix="kasukabe/vm-snapshots-test",
    )
