"""game/server.py: the browser-facing FastAPI surface (health, snapshot,
map, agent detail, the WS relay) - wiring tests only, matching broker's
own test_broker_app.py's "these tests only check that app.py wires a
Service into FastAPI correctly" scope. The pieces being wired (relay
fan-out logic, WorldClient contract parsing) have their own focused
tests in test_game_relay.py and test_game_world_client.py."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from game.config import GameSettings
from game.contracts import AgentDetail, Snapshot, WireEvent
from game.server import app, build_world_client, create_app
from game.world_client import HttpWorldClient
from game.world_client import GENESIS_PLACES, SpendStatus, WorldClientError


# These tests exercise the MOCK town. The module-level `app` boots real
# now (game/config.py: mock_mode defaults False and MOCK_MODE is no
# longer an env var), so anything wanting the fake 12-agent world has
# to construct it explicitly - which is the only way left to ask.
def mock_app():
    return create_app(settings=GameSettings(mock_mode=True))


def test_health_reports_ok() -> None:
    client = TestClient(mock_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "game"}


def test_default_app_boots_against_a_real_world() -> None:
    """One runtime mode. See build_world_client's sibling test."""
    assert app.state.settings.mock_mode is False


def test_build_world_client_picks_the_real_world_by_default() -> None:
    """The default is a REAL world server now.

    This used to assert the opposite. A map that can quietly serve 12
    invented residents is a map you cannot trust while watching a real
    run - and the invented ids match no genesis agent, so it looks
    identical to a live feed that has silently stopped binding.
    """
    from game.mock_world import MockWorldClient

    assert isinstance(build_world_client(GameSettings()), HttpWorldClient)
    # Tests may still ask for the fake town, in code, explicitly.
    assert isinstance(
        build_world_client(GameSettings(mock_mode=True)), MockWorldClient
    )


def test_build_world_client_picks_http_when_not_mock() -> None:
    from game.world_client import HttpWorldClient

    client = build_world_client(GameSettings(mock_mode=False))
    assert isinstance(client, HttpWorldClient)


def test_snapshot_endpoint_returns_contract_shaped_body() -> None:
    settings = GameSettings(mock_mode=True, mock_agent_count=5, mock_seed=1)
    test_app = create_app(settings=settings)
    with TestClient(test_app) as client:
        response = client.get("/api/snapshot")
        assert response.status_code == 200
        body = response.json()
        snapshot = Snapshot.model_validate(body)
        assert snapshot.tick == 0
        assert len(snapshot.agents) == 5


def test_map_endpoint_returns_raw_tiled_json_not_wrapped() -> None:
    settings = GameSettings(mock_mode=True, mock_agent_count=2, mock_seed=1)
    test_app = create_app(settings=settings)
    with TestClient(test_app) as client:
        response = client.get("/api/map")
        assert response.status_code == 200
        body = response.json()
        assert body["width"] == 140
        assert "map_tiled_json" not in body  # raw map, not the Snapshot envelope


def test_places_endpoint_returns_the_genesis_catalog_in_mock_mode() -> None:
    settings = GameSettings(mock_mode=True, mock_agent_count=2, mock_seed=1)
    test_app = create_app(settings=settings)
    with TestClient(test_app) as client:
        response = client.get("/api/places")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == len(GENESIS_PLACES)
        assert {p["name"] for p in body} >= {"Kasukabe Temple", "Kasukabe Government Complex"}


def test_spend_endpoint_returns_available_status_in_mock_mode() -> None:
    settings = GameSettings(mock_mode=True, mock_agent_count=2, mock_seed=1)
    test_app = create_app(settings=settings)
    with TestClient(test_app) as client:
        response = client.get("/api/spend")
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is True
        assert body["total_usd_cents"] == 100_000


def test_agent_detail_known_and_unknown() -> None:
    settings = GameSettings(mock_mode=True, mock_agent_count=3, mock_seed=2)
    test_app = create_app(settings=settings)
    with TestClient(test_app) as client:
        snapshot = client.get("/api/snapshot").json()
        agent_id = snapshot["agents"][0]["agent_id"]

        response = client.get(f"/api/agents/{agent_id}")
        assert response.status_code == 200
        detail = AgentDetail.model_validate(response.json())
        assert detail.agent_id == agent_id

        missing = client.get("/api/agents/does-not-exist")
        assert missing.status_code == 404


class FailingWorldClient:
    """A WorldClient whose every call raises WorldClientError - exercises
    game/server.py's 502 handling without depending on MockWorldClient's
    internals."""

    async def get_snapshot(self):
        raise WorldClientError("world/ is unreachable")

    async def get_agent_detail(self, agent_id: str):
        raise WorldClientError("world/ is unreachable")

    async def get_places(self):
        raise WorldClientError("world/ is unreachable")

    async def get_spend_status(self):
        raise WorldClientError("world/ is unreachable")

    async def stream_events(self):
        return
        yield  # pragma: no cover - makes this an async generator

    async def close(self) -> None:
        pass


def test_places_returns_502_when_world_client_fails() -> None:
    test_app = create_app(settings=GameSettings(), world_client=FailingWorldClient())
    with TestClient(test_app) as client:
        response = client.get("/api/places")
        assert response.status_code == 502


def test_spend_returns_502_when_world_client_fails() -> None:
    test_app = create_app(settings=GameSettings(), world_client=FailingWorldClient())
    with TestClient(test_app) as client:
        response = client.get("/api/spend")
        assert response.status_code == 502


def test_snapshot_returns_502_when_world_client_fails() -> None:
    test_app = create_app(settings=GameSettings(), world_client=FailingWorldClient())
    with TestClient(test_app) as client:
        response = client.get("/api/snapshot")
        assert response.status_code == 502


def test_agent_detail_returns_502_when_world_client_fails() -> None:
    test_app = create_app(settings=GameSettings(), world_client=FailingWorldClient())
    with TestClient(test_app) as client:
        response = client.get("/api/agents/a-1")
        assert response.status_code == 502


def test_map_returns_502_when_world_client_fails() -> None:
    test_app = create_app(settings=GameSettings(), world_client=FailingWorldClient())
    with TestClient(test_app) as client:
        response = client.get("/api/map")
        assert response.status_code == 502


class ScriptedWorldClient:
    """Yields a fixed list of WireEvents once, then idles - enough to
    prove the WS route delivers whatever the relay hub forwards, without
    depending on MockWorldClient's wall-clock ticking."""

    def __init__(self, events: list[WireEvent]) -> None:
        self._events = events

    async def get_snapshot(self) -> Snapshot:
        return Snapshot(tick=0, map_tiled_json={"width": 140, "height": 100, "layers": []}, agents=[])

    async def get_agent_detail(self, agent_id: str):
        return None

    async def get_places(self):
        return list(GENESIS_PLACES)

    async def get_spend_status(self) -> SpendStatus:
        return SpendStatus(burned_usd_cents=0, total_usd_cents=100_000, available=True)

    async def stream_events(self):
        for event in self._events:
            yield event
        import asyncio

        await asyncio.get_event_loop().create_future()  # idle forever

    async def close(self) -> None:
        pass


def test_websocket_relay_delivers_events() -> None:
    events = [
        WireEvent(type="world.tick.advanced", tick=1, payload={}, event_id="e1", published_at=0.0)
    ]
    test_app = create_app(settings=GameSettings(), world_client=ScriptedWorldClient(events))
    with TestClient(test_app) as client, client.websocket_connect("/ws") as websocket:
        raw = websocket.receive_text()
        data = json.loads(raw)
        assert data["type"] == "world.tick.advanced"
        assert data["tick"] == 1


def test_index_html_is_served_at_root() -> None:
    client = TestClient(mock_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "Kasukabe" in response.text


def test_static_assets_are_served() -> None:
    client = TestClient(mock_app())
    response = client.get("/js/main.js")
    assert response.status_code == 200
    response = client.get("/assets/speech_bubble/v3.png")
    assert response.status_code == 200
