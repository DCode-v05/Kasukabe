"""`GET /world/preflight` answers on an embedded world instead of raising.

Run 27's first report refresh logged:

    File "app/world/run_genesis.py", line 3091, in world_preflight
        if self.trace_store is not None:
    NameError: name 'self' is not defined

The route lives inside `build_embedded_world_app`, which is a function, not
a method - there is no `self` in scope. It has raised on every call since
175f87e1 added the recorder's "has lost nothing" check, so the report's
preflight section and anything else that asked the running world "is this run
sound?" got a 500.

This is the same shape as the WIRE-02 `/world/link` bug: a route that closed
over a name from the wrong scope, shipped because nothing requested the
ROUTE. So this test requests it, through the app the runner actually built.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from world.run_genesis import GenesisRunner, GenesisRunnerConfig
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


@pytest_asyncio.fixture
async def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp",
        world_mode="embedded", world_port=0,
    ))
    await runner.boot(resume=False)
    return runner


async def _preflight(runner):
    from httpx import ASGITransport, AsyncClient

    await runner.start_embedded_world_server()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=runner.world_app), base_url="http://world"
        ) as client:
            return await client.get("/world/preflight")
    finally:
        await runner.shutdown()


@pytest.mark.asyncio
async def test_the_route_answers_rather_than_raising(runner):
    response = await _preflight(runner)

    assert response.status_code == 200, response.text[:400]


@pytest.mark.asyncio
async def test_the_recorder_check_is_actually_in_the_answer(runner):
    """Not merely a 200: the check that used to raise is the one reported."""
    response = await _preflight(runner)

    assert "The recorder has lost nothing" in response.text, response.text[:600]
