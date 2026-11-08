"""/api/engine: the one poll behind the viewer's Real sim readout.

"Real sim" is a claim about the world, and three separate things have to be
true before a single resident is thinking for itself: a world server is up
and its ids match the cast, its agents are engine-backed rather than
MockAgentGateway, and the broker is up with credentials. Each fails
independently and silently, and conflating them is what makes a live run
impossible to debug - so these check that the endpoint keeps them apart and
never guesses.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from game.contracts import AgentPosition, AgentSummary, Snapshot
from game.server import create_app
from game.world_client import WorldClientError


class FakeWorld:
    """A world whose agents ARE engine-backed, at real township locations."""

    cognition: str | None = "engine"
    fail = False

    def __init__(self, n: int = 6) -> None:
        self.n = n

    async def get_snapshot(self) -> Snapshot:
        if self.fail:
            raise WorldClientError("world is down")
        agents = [
            AgentSummary(
                agent_id=f"agent-{i}",
                display_name=f"Agent {i}",
                sprite_key="Isabella Rodriguez",
                # One unmappable location, so `bound` and `reported` cannot
                # be the same number by accident.
                # One agent with no location at all, so `bound` and
                # `reported` cannot be the same number by accident. An
                # unrecognised location name is NOT this case: the server
                # draws that one at the square (see _drawable_zone).
                position=AgentPosition(location_id="hospital" if i else ""),
                current_action=None if i == 1 else "walking to the market",
                is_alive=i != 2,
                is_dormant=i == 3,
            )
            for i in range(self.n)
        ]
        return Snapshot(tick=99, map_tiled_json={}, agents=agents)

    async def get_provenance(self) -> dict[str, str | None]:
        # Both axes: cognition alone cannot say whether anything is
        # actually thinking - real engines can be fed canned text.
        return {"cognition": self.cognition,
                "inference": "bedrock" if self.cognition == "engine" else "mock"}

    async def get_agent_detail(self, agent_id: str):  # pragma: no cover - unused
        return None

    async def get_places(self):  # pragma: no cover - unused
        return []

    # The relay pump and the app's shutdown reach for these on any world
    # client, engine-backed or not.
    async def stream_events(self):  # pragma: no cover - the relay is not under test
        if False:
            yield None
        return

    async def close(self) -> None:
        return None


def engine(world) -> dict:
    with TestClient(create_app(world_client=world)) as client:
        res = client.get("/api/engine")
        assert res.status_code == 200
        return res.json()


def test_counts_are_each_a_different_question() -> None:
    """reported / bound / alive / dormant / acting must not collapse into
    one number. A readout where every row says 30 hides exactly the case
    it exists to show."""
    body = engine(FakeWorld(n=6))["world"]
    assert body["connected"] is True
    assert body["reported"] == 6
    # Every agent the world reports at a location is drawable - an
    # unrecognised place falls back to the square rather than dropping that
    # resident. `bound` therefore separates from `reported` on agents with
    # NO location at all, which is the case that genuinely cannot be drawn.
    assert body["bound"] == 5, "the agent with no location at all is not on the map"
    assert body["alive"] == 5
    assert body["dormant"] == 1
    assert body["acting"] == 5
    assert body["tick"] == 99


def test_engine_backed_is_reported_as_engine() -> None:
    assert engine(FakeWorld())["world"]["cognition"] == "engine"


def test_a_mock_cognition_is_never_dressed_up_as_real() -> None:
    """A mock world can report ids that look real. Only `cognition` says
    whether anything is actually thinking, and it must pass through."""
    world = FakeWorld()
    world.cognition = "mock"
    assert engine(world)["world"]["cognition"] == "mock"


def test_a_world_that_cannot_say_reports_none_not_a_guess() -> None:
    world = FakeWorld()
    world.cognition = None
    assert engine(world)["world"]["cognition"] is None


def test_a_dead_world_is_a_stated_reason_not_an_exception() -> None:
    world = FakeWorld()
    world.fail = True
    body = engine(world)["world"]
    assert body["connected"] is False
    assert "world is down" in (body["reason"] or "")
    assert body["bound"] == 0


def test_a_missing_broker_is_normal_and_says_so(monkeypatch) -> None:
    """No broker is the ordinary state during a mock run. It must read as a
    reason, not as an error and not as zero spend.

    BROKER_BASE_URL is pointed at a closed port rather than relying on
    nothing listening on the default 8200: this failed the moment a real
    broker was started on the same machine, which is a test depending on
    ambient state, not on the code it is testing.
    """
    monkeypatch.setenv("BROKER_BASE_URL", "http://127.0.0.1:9")  # discard port
    body = engine(FakeWorld())["broker"]
    assert body["live"] is False
    assert "not reachable" in (body["reason"] or "")
    assert body["spent_micros"] is None, "unknown spend is not zero spend"
    assert body["lanes"] == []


def test_model_names_match_the_broker() -> None:
    """game/ copies these two ids rather than importing broker/ (game/ is
    presentation only, app/CONVENTIONS.md). A copy needs a parity test or
    the readout eventually names a model nobody is calling."""
    from broker.config import DEEPSEEK_MODEL_ID, KIMI_MODEL_ID, MODEL_ALLOWLIST

    models = engine(FakeWorld())["models"]
    assert models["cognition"] == KIMI_MODEL_ID
    assert models["background"] == DEEPSEEK_MODEL_ID
    assert set(models.values()) <= set(MODEL_ALLOWLIST)


@pytest.mark.parametrize("key", ["world", "broker", "models"])
def test_the_three_sections_are_always_present(key: str) -> None:
    """The viewer reads these unconditionally. A section that vanishes when
    something is down turns a readable "unknown" into a TypeError."""
    world = FakeWorld()
    world.fail = True
    assert key in engine(world)
