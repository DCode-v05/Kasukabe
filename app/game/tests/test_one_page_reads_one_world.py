"""Every read route answers from the same world.

`UI-20`. `live_world_client()` - which prefers the world a Real-sim run
brought up - was used by the township routes only. `/api/snapshot`,
`/api/map`, `/api/places`, `/api/spend` and `/api/agents/{id}` kept the
client the process launched with.

The stated reasoning was "starting a run cannot disturb a page that is
not watching it". The effect was the opposite: ONE page mixing two
worlds. The township map drew the real-sim town while the agent inspector
on that same page answered from the other one, so clicking a resident
could show a stranger's wallet, or a 404 for somebody visibly standing
in front of you.

Two worlds is a legitimate thing to want. Two worlds inside one page,
with nothing saying which is which, is not.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from game.config import GameSettings
from game.contracts import AgentDetail, AgentPosition, Snapshot, Wallets
from game.world_client import Place, SpendStatus
from game.server import create_app


class _World:
    """A world that says which one it is in every answer."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def get_snapshot(self) -> Snapshot:
        return Snapshot(tick=1, map_tiled_json={"which": self.name}, agents=[])

    async def get_agent_detail(self, agent_id: str) -> AgentDetail | None:
        return AgentDetail(
            agent_id=agent_id,
            display_name=self.name,
            sprite_key="a",
            bio="",
            wallets=Wallets(usd_wallet_cents=0, sim_balance=0),
            position=AgentPosition(location_id=None, tile_x=0, tile_y=0),
            current_action="idle",
            is_alive=True,
            is_dormant=False,
            recent_utterances=[],
            relationships=[],
        )

    async def get_places(self) -> list[Place]:
        return [Place(place_id=self.name, name=self.name,
                      category="park", tile_x=0, tile_y=0)]

    async def get_spend_status(self) -> SpendStatus:
        return SpendStatus(available=True, burned_usd_cents=len(self.name),
                           total_usd_cents=100_000)

    async def stream_events(self):
        """The relay's half of the Protocol. Yields nothing: this file is
        about which world the READ routes answer from, and the hub is the
        documented exception to that."""
        if False:  # pragma: no cover - a generator that never yields
            yield None

    async def close(self) -> None:
        return None


@pytest.fixture
def app_with_a_run():
    """The state after the Real-sim button has started a run: the process
    world is one town, the run's world is another."""
    app = create_app(
        settings=GameSettings(mock_mode=False, world_base_url="http://process.test"),
        world_client=_World("process-world"),
    )
    return app


ROUTES = [
    ("/api/snapshot", lambda body: body["map_tiled_json"]["which"]),
    ("/api/map", lambda body: body["which"]),
    ("/api/places", lambda body: body[0]["place_id"]),
    ("/api/agents/a-anya", lambda body: body["display_name"]),
]


@pytest.mark.parametrize("path,extract", ROUTES)
def test_a_read_route_follows_the_run(app_with_a_run, path, extract) -> None:
    """The finding itself, one route at a time."""
    with TestClient(app_with_a_run) as client:
        client.app.state.realsim.world_client = _World("run-world")
        body = client.get(path).json()

    assert extract(body) == "run-world", (
        f"UI-20: {path} answered from the world the process started with "
        "while the township map was drawing the run's - one page, two towns"
    )


def test_every_route_agrees_with_every_other(app_with_a_run) -> None:
    """The property that actually matters. Any one route being right is
    not the point; them all naming the SAME town is."""
    with TestClient(app_with_a_run) as client:
        client.app.state.realsim.world_client = _World("run-world")
        answers = {path: extract(client.get(path).json()) for path, extract in ROUTES}
        answers["/api/township/live"] = client.get("/api/township/live").json()["source"]

    towns = set(answers.values()) - {"world", "mock"}
    assert len(towns) == 1, f"UI-20: these routes disagree about the town: {answers}"


def test_with_no_run_the_process_world_still_answers(app_with_a_run) -> None:
    """Nothing changes when there is no run - the fix must not make the
    ordinary case depend on the real-sim controller."""
    with TestClient(app_with_a_run) as client:
        assert client.get("/api/snapshot").json()["map_tiled_json"]["which"] == "process-world"


def test_the_relay_is_the_known_exception(app_with_a_run) -> None:
    """`ConnectionHub` holds one long-lived connection opened at process
    start; re-pointing it means tearing down every browser socket. A real
    limitation, pinned here as KNOWN rather than left to be rediscovered
    as a bug - and the reason `/api/township/live` reports `source`."""
    with TestClient(app_with_a_run) as client:
        body = client.get("/api/township/live").json()
    assert "source" in body, (
        "the relay can lag the rest of the app, so the page must at least "
        "be able to say which world it is being told about"
    )
