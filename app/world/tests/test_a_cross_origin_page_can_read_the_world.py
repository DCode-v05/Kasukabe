"""Every read-only route answers a browser on another port.

`UI-22`. `views.html` and `agents.html` are served by the game server on
:8400 and read the world on :8100, so every call they make is
cross-origin. Six of world's routers carry `add_cors_headers` -
governance, social, agent-viewer, family, skills, workshop - and
`agents_api`, `location_api` and the embedded app's own `/health` did
not, for no reason anyone recorded.

`/health` is the one that actually bit: it is the FIRST call those pages
make, the reachability check. Without the header the check fails and the
page reports "could not reach world API", which a reader takes as a dead
world rather than a missing response header.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from world.admin_api import AdminExtras
from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    build_embedded_world_app,
)

CROSS_ORIGIN_GETS = [
    "/health",
    "/agents",
    "/places",
    "/world/summary",
    "/governance/charter",
    "/agent-viewer/agents",
]


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import asyncio

    async def build():
        runner = GenesisRunner(GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=tmp_path_factory.mktemp("cors"),
            world_mode="embedded",
            world_port=0,
        ))
        await runner.boot()
        return runner

    runner = asyncio.run(build())
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        tick_engine=runner.tick_engine, genesis=runner.genesis,
        map_service=runner.map_service,
        admin_extras=AdminExtras(operator_token="t"),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("path", CROSS_ORIGIN_GETS)
def test_a_browser_on_another_port_is_allowed_to_read(client, path: str) -> None:
    response = client.get(path, headers={"Origin": "http://localhost:8400"})
    assert response.headers.get("Access-Control-Allow-Origin") == "*", (
        f"UI-22: {path} sends no Access-Control-Allow-Origin, so a page "
        "served from the game server on :8400 cannot read it and reports "
        f"the world unreachable. Status was {response.status_code}"
    )


def test_health_is_the_one_that_gates_the_page(client) -> None:
    """Called first, as a reachability check, so its absence is not one
    missing panel - it is the whole page giving up."""
    response = client.get("/health", headers={"Origin": "http://localhost:8400"})
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"


def test_the_admin_console_is_deliberately_not_open(client) -> None:
    """CORS is for the read-only, credential-less GETs. The operator
    console authenticates with a token and must NOT be readable from any
    origin that asks."""
    response = client.get("/admin/alerts", headers={"Origin": "http://evil.test"})
    assert response.headers.get("Access-Control-Allow-Origin") is None, (
        "the admin API is now open to any origin - that is a wider hole "
        "than the one UI-22 was about"
    )
