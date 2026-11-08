"""ENG-19, on the host every real run actually serves.

The agent that fixed ENG-19 wired the ETag into `world/main.py` - the
STANDALONE server - and handed this half back, because `run_genesis.py`
is the embedded runner and was outside its scope. Without this the saving
would have existed only in a host no simulation uses, which is this
codebase's signature defect wearing a new hat: correct, tested, and
serving nobody.

`refresh_world_tool_registry(force=True)` pulled the whole 53 KB registry
and rebuilt 76 ToolDefs every tick, per engine. At 20 residents that is
merely wasteful; the design target is 250.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    build_embedded_world_app,
)


async def _app(tmp_path):
    """The real embedded app, built the way the runner builds it."""
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "c", world_mode="embedded",
        world_port=0, budget_cap_cents=40_000,
    ))
    await runner.boot()
    return build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics
    )


@pytest.mark.asyncio
async def test_the_registry_carries_an_etag(tmp_path) -> None:
    app = await _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/tools/registry")

    assert response.status_code == 200
    assert response.headers.get("ETag"), (
        "ENG-19: no ETag on the embedded registry, so an engine has no way "
        "to ask whether it changed and must pull all 53 KB every tick"
    )


@pytest.mark.asyncio
async def test_an_unchanged_registry_answers_304_with_no_body(tmp_path) -> None:
    app = await _app(tmp_path)
    with TestClient(app) as client:
        first = client.get("/tools/registry")
        again = client.get(
            "/tools/registry", headers={"If-None-Match": first.headers["ETag"]}
        )

    assert again.status_code == 304, (
        f"ENG-19: the town resent the whole registry unchanged ({again.status_code})"
    )
    assert not again.content


@pytest.mark.asyncio
async def test_the_body_is_still_the_registry(tmp_path) -> None:
    """The optimisation must not change what a resident is told exists.

    Switching from a list return to an explicit Response means FastAPI no
    longer serialises this - a wrong hand-rolled body would be an engine
    that boots with no tools at all.
    """
    app = await _app(tmp_path)
    with TestClient(app) as client:
        rows = client.get("/tools/registry").json()

    assert isinstance(rows, list) and rows
    assert all("name" in row for row in rows)
    assert any(row["name"] == "eat" for row in rows), "the town serves no `eat` tool"
