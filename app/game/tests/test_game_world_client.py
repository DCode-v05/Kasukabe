"""HttpWorldClient against an in-process ASGI stub built from world/'s
REAL, live HTTP surface - not the assumed `/api/game/snapshot` contract
contracts.py's CONTRACT docstring used to describe (world/main.py never
implemented that; see world_client.py's module docstring for exactly
what was curled against a real booted `world` container and what came
back). `build_real_world_stub()` below reproduces that real shape route
for route: `/state/summary`, `/agents`, `/agents/{id}`, `/ledger/
invariant`, `/ledger/balance/{account_id}` all present; `/world/state`
and `/places` both absent by default (world/main.py has neither today),
with an opt-in flag on each so a test can assert this client picks up
either the moment world/ ships it.

The WebSocket half (stream_events) is tested separately by monkeypatching
`websockets.connect` with a fake async context manager, since httpx's
ASGITransport does not carry WebSocket traffic - no live socket, no real
network, per app/CONVENTIONS.md's test rules either way.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from game.config import GameSettings
from game.contracts import WireEvent
from game.position import fallback_tile_for, sprite_key_for
from game.world_client import (
    BURNED_ACCOUNT_ID,
    GENESIS_PLACES,
    HttpWorldClient,
    WorldClientError,
)

REAL_AGENT_A = {
    "agent_id": "a0039",
    "alive": True,
    "age_years": 41.0,
    "is_minor": False,
    "location_id": "home:h0043",
    "health": {"current_ailment": None, "is_sick": False},
    "dormancy": {"is_dormant": False, "dormant_since_hours": None},
    "usd_wallet_cents": 200,
    "sim_balance": 5000,
}

REAL_AGENT_DORMANT = {
    "agent_id": "a0100",
    "alive": True,
    "age_years": 55.0,
    "is_minor": False,
    "location_id": "home:h0077",
    "health": {
        "current_ailment": {"kind": "short", "onset_day": 3, "duration_days": 1, "treated": False},
        "is_sick": True,
    },
    "dormancy": {"is_dormant": True, "dormant_since_hours": 12.0},
    "usd_wallet_cents": 0,
    "sim_balance": 100,
}


def build_real_world_stub(
    *, with_world_state: bool = False, with_places: bool = False, with_ledger: bool = True
) -> FastAPI:
    """Reproduces world/main.py's real, live route surface (see module
    docstring). Every route below is shaped exactly like a real curl
    against a booted `world` container returned - see world_client.py's
    module docstring for the transcript."""
    app = FastAPI()

    @app.get("/state/summary")
    async def state_summary() -> dict:
        return {
            "tick": 42,
            "game_hour": 7.0,
            "population": 250,
            "dormant": 1,
            "scheduled_agents": 250,
            "ledger": {"halted": False, "halt_reason": None, "usd_total_cents": 100000, "expected_cents": 100000},
            "population_dials": {
                "target_band_min": 250,
                "target_band_max": 300,
                "death_hazard_multiplier": 1.0,
                "blessing_throttle": 1.0,
            },
        }

    if with_world_state:

        @app.get("/world/state")
        async def world_state() -> dict:
            return {"tick": 999}

    @app.get("/agents")
    async def list_agents() -> list[dict]:
        return [REAL_AGENT_A, REAL_AGENT_DORMANT]

    @app.get("/agents/{agent_id}")
    async def agent_detail(agent_id: str) -> JSONResponse:
        if agent_id == REAL_AGENT_A["agent_id"]:
            return JSONResponse(REAL_AGENT_A)
        return JSONResponse({"detail": "not found"}, status_code=404)

    if with_places:

        @app.get("/places")
        async def places() -> list[dict]:
            return [
                {"place_id": "town-hall", "name": "Town Hall", "category": "government", "tile_x": 10, "tile_y": 20},
                {"name": "Broken Place", "category": "office", "tile_x": 5},
            ]

    if with_ledger:

        @app.get("/ledger/invariant")
        async def ledger_invariant() -> dict:
            return {"halted": False, "halt_reason": None, "usd_total_cents": 100000, "expected_cents": 100000}

        @app.get("/ledger/balance/{account_id}")
        async def ledger_balance(account_id: str) -> dict:
            assert account_id == BURNED_ACCOUNT_ID
            return {"account_id": account_id, "usd_cents": 3720, "sim_units": 0}

    return app


def build_client(app: FastAPI) -> HttpWorldClient:
    """An HttpWorldClient whose httpx.AsyncClient is wired to an
    in-process ASGI app instead of a real socket."""
    client = HttpWorldClient(GameSettings(mock_mode=False, world_base_url="http://world.test"))
    client.http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://world.test"
    )
    return client


@pytest.mark.asyncio
async def test_get_snapshot_against_the_real_route_shape() -> None:
    client = build_client(build_real_world_stub())
    snap = await client.get_snapshot()
    assert snap.tick == 42
    assert {a.agent_id for a in snap.agents} == {"a0039", "a0100"}
    assert snap.map_tiled_json["width"] == 140
    assert snap.map_tiled_json["height"] == 100
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_snapshot_prefers_world_state_over_state_summary() -> None:
    client = build_client(build_real_world_stub(with_world_state=True))
    snap = await client.get_snapshot()
    assert snap.tick == 999
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_snapshot_maps_real_agent_fields_honestly() -> None:
    client = build_client(build_real_world_stub())
    snap = await client.get_snapshot()
    by_id = {a.agent_id: a for a in snap.agents}

    normal = by_id["a0039"]
    assert normal.display_name == "a0039"
    assert normal.sprite_key == sprite_key_for("a0039")
    assert normal.position.location_id == "home:h0043"
    assert normal.position.tile_x, normal.position.tile_y == fallback_tile_for("home:h0043")
    assert normal.is_alive is True
    assert normal.is_dormant is False
    assert normal.current_action is None

    dormant = by_id["a0100"]
    assert dormant.is_dormant is True
    assert dormant.current_action == "dormant (cognitive poverty)"
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_snapshot_skips_one_malformed_agent_without_failing() -> None:
    app = FastAPI()

    @app.get("/state/summary")
    async def state_summary() -> dict:
        return {"tick": 1}

    @app.get("/agents")
    async def list_agents() -> list[dict]:
        return [REAL_AGENT_A, {"unexpected": "shape, no agent_id at all"}]

    client = build_client(app)
    snap = await client.get_snapshot()
    assert [a.agent_id for a in snap.agents] == ["a0039"]
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_snapshot_raises_when_neither_tick_route_exists() -> None:
    app = FastAPI()

    @app.get("/agents")
    async def list_agents() -> list[dict]:
        return []

    client = build_client(app)
    with pytest.raises(WorldClientError, match="exposes neither"):
        await client.get_snapshot()
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_snapshot_raises_on_agents_non_200() -> None:
    app = FastAPI()

    @app.get("/state/summary")
    async def state_summary() -> dict:
        return {"tick": 1}

    @app.get("/agents")
    async def list_agents() -> JSONResponse:
        return JSONResponse({"error": "boom"}, status_code=500)

    client = build_client(app)
    with pytest.raises(WorldClientError):
        await client.get_snapshot()
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_snapshot_raises_when_world_is_unreachable() -> None:
    """A real connection failure (world/ process down, DNS failure,
    timeout) - not a 404, not a 500, no HTTP response at all - is exactly
    the "world unreachable" case main.js's banner exists for. Simulated
    by making the transport itself raise, since ASGITransport has no
    real socket to refuse a connection on."""

    async def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    client = HttpWorldClient(GameSettings(mock_mode=False, world_base_url="http://world.test"))
    client.http.get = raise_connect_error  # type: ignore[method-assign]
    with pytest.raises(WorldClientError, match="unreachable"):
        await client.get_snapshot()
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_agent_detail_against_the_real_route_shape() -> None:
    client = build_client(build_real_world_stub())
    detail = await client.get_agent_detail("a0039")
    assert detail is not None
    assert detail.agent_id == "a0039"
    assert detail.display_name == "a0039"
    assert detail.bio == ""
    assert detail.wallets.usd_wallet_cents == 200
    assert detail.wallets.sim_balance == 5000
    assert detail.recent_utterances == []
    assert detail.relationships == []
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_agent_detail_returns_none_on_404() -> None:
    client = build_client(build_real_world_stub())
    result = await client.get_agent_detail("does-not-exist")
    assert result is None
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_agent_detail_raises_on_other_error_status() -> None:
    app = FastAPI()

    @app.get("/agents/{agent_id}")
    async def detail(agent_id: str) -> JSONResponse:
        return JSONResponse({"detail": "broken"}, status_code=503)

    client = build_client(app)
    with pytest.raises(WorldClientError):
        await client.get_agent_detail("a-1")
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_places_falls_back_to_genesis_catalog_on_404() -> None:
    client = build_client(build_real_world_stub(with_places=False))
    places = await client.get_places()
    assert places == list(GENESIS_PLACES)
    assert len(places) > 0
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_places_uses_the_real_route_when_present() -> None:
    client = build_client(build_real_world_stub(with_places=True))
    places = await client.get_places()
    assert len(places) == 1
    assert places[0].name == "Town Hall"
    assert places[0].category == "government"
    assert places[0].tile_x == 10
    assert places[0].tile_y == 20
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_places_raises_on_non_404_non_200() -> None:
    app = FastAPI()

    @app.get("/places")
    async def places() -> JSONResponse:
        return JSONResponse({"error": "boom"}, status_code=500)

    client = build_client(app)
    with pytest.raises(WorldClientError):
        await client.get_places()
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_spend_status_against_the_real_route_shape() -> None:
    client = build_client(build_real_world_stub())
    status = await client.get_spend_status()
    assert status.available is True
    assert status.total_usd_cents == 100000
    assert status.burned_usd_cents == 3720
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_spend_status_unavailable_when_ledger_routes_are_missing() -> None:
    client = build_client(build_real_world_stub(with_ledger=False))
    status = await client.get_spend_status()
    assert status.available is False
    assert status.burned_usd_cents is None
    assert status.total_usd_cents is None
    await client.http.aclose()


@pytest.mark.asyncio
async def test_get_spend_status_raises_on_non_404_non_200() -> None:
    app = FastAPI()

    @app.get("/ledger/invariant")
    async def ledger_invariant() -> JSONResponse:
        return JSONResponse({"error": "boom"}, status_code=500)

    client = build_client(app)
    with pytest.raises(WorldClientError):
        await client.get_spend_status()
    await client.http.aclose()


class FakeSocket:
    """Minimal async context manager + async iterator standing in for a
    `websockets.connect(...)` result, so stream_events() can be tested
    with zero real network I/O."""

    def __init__(self, frames: list[str], raise_after: Exception | None = None) -> None:
        self.frames = frames
        self.raise_after = raise_after

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for frame in self.frames:
            yield frame
        if self.raise_after is not None:
            raise self.raise_after


@pytest.mark.asyncio
async def test_stream_events_yields_parsed_wire_events(monkeypatch: pytest.MonkeyPatch) -> None:
    from game import world_client as world_client_module

    event = WireEvent(
        type="world.tick.advanced", tick=1, payload={}, event_id="e1", published_at=0.0
    )
    frames = [event.model_dump_json()]

    def fake_connect(url: str) -> FakeSocket:
        assert url == "ws://world.test/ws/events"
        return FakeSocket(frames)

    monkeypatch.setattr(world_client_module.websockets, "connect", fake_connect)

    settings = GameSettings(mock_mode=False, world_ws_url="ws://world.test/ws/events")
    client = HttpWorldClient(settings)

    received = []
    async for received_event in client.stream_events():
        received.append(received_event)
        await client.close()

    assert len(received) == 1
    assert received[0].type == "world.tick.advanced"
    await client.http.aclose()


@pytest.mark.asyncio
async def test_stream_events_skips_unparseable_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    from game import world_client as world_client_module

    good = WireEvent(
        type="agent.moved", tick=2, payload={}, event_id="e2", published_at=0.0
    ).model_dump_json()
    frames = ["not valid json{{{", good]

    def fake_connect(url: str) -> FakeSocket:
        return FakeSocket(frames)

    monkeypatch.setattr(world_client_module.websockets, "connect", fake_connect)

    settings = GameSettings(mock_mode=False)
    client = HttpWorldClient(settings)

    received = []
    async for received_event in client.stream_events():
        received.append(received_event)
        if len(received) == 1:
            await client.close()

    assert len(received) == 1
    assert received[0].type == "agent.moved"
    await client.http.aclose()


@pytest.mark.asyncio
async def test_stream_events_synthesizes_lost_and_restored_around_a_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The "if the world is unreachable show an explicit banner" half of
    this task: a real WS drop/reconnect must be visible to the browser as
    ordinary WireEvents, since relay.py fans out whatever this yields
    with no special-casing."""
    from game import world_client as world_client_module

    good = WireEvent(
        type="world.tick.advanced", tick=1, payload={}, event_id="e1", published_at=0.0
    ).model_dump_json()

    call_count = {"n": 0}

    def fake_connect(url: str) -> FakeSocket:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return FakeSocket([], raise_after=ConnectionRefusedError("simulated drop"))
        return FakeSocket([good])

    monkeypatch.setattr(world_client_module.websockets, "connect", fake_connect)
    original_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: original_sleep(0))

    settings = GameSettings(mock_mode=False, world_ws_reconnect_delay_s=0.01)
    client = HttpWorldClient(settings)

    received = []
    async for event in client.stream_events():
        received.append(event)
        if len(received) >= 3:
            await client.close()

    assert call_count["n"] == 2
    assert [e.type for e in received] == [
        "world.connection.lost",
        "world.connection.restored",
        "world.tick.advanced",
    ]
    await client.http.aclose()


@pytest.mark.asyncio
async def test_stream_events_first_connection_ever_failing_still_announces_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """world/ never having been reachable at all (not merely dropping
    after a successful connect) must still surface as a banner-worthy
    event - a fresh page load pointed at a dead world/ is exactly this
    case."""
    from game import world_client as world_client_module

    def fake_connect(url: str) -> FakeSocket:
        return FakeSocket([], raise_after=ConnectionRefusedError("never up"))

    monkeypatch.setattr(world_client_module.websockets, "connect", fake_connect)
    original_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _s: original_sleep(0))

    settings = GameSettings(mock_mode=False, world_ws_reconnect_delay_s=0.01)
    client = HttpWorldClient(settings)

    received = []
    async for event in client.stream_events():
        received.append(event)
        await client.close()

    assert received[0].type == "world.connection.lost"
    await client.http.aclose()
