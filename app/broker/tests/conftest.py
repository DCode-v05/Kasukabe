"""Test-session defaults for the broker suite.

`EVAL-17` / `INFRA-11`. `BrokerSettings.trace_sqlite_path` defaults to
`/data/kasukabe.db`. A test that builds a real app therefore either fails
(no `/data` on this machine, or any developer's) or - once the path is
relative - writes `app/data/` into the working tree just by being
collected.

Neither is the test's fault and neither should be its problem, so the
whole suite gets a temporary path. One per session rather than per test:
several tests build an app, and a shared file is closer to how the broker
actually runs than a fresh database per assertion would be.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _trace_store_in_a_temp_dir(tmp_path_factory) -> None:
    import os

    directory = tmp_path_factory.mktemp("broker-trace")
    os.environ.setdefault("TRACE_SQLITE_PATH", str(directory / "trace.db"))
