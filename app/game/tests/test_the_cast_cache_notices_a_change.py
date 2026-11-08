"""A stale cast, and a blip that became an empty town.

`UI-10`, two halves.

**The cache key.** `_cast_cache` was keyed on the SET OF IDS alone, so a
rename or a newborn reusing an id never invalidated it: the browser held
the same 1.7 MB document until somebody joined or left. Names are exactly
what the roster, the map labels and every panel read.

**The blip.** When `get_snapshot()` raised - a world restarting, a
momentary connection reset - the route fell through and served
`const AGENTS = []`. The browser then CACHES that as a document, so one
hiccup left the page showing a town with nobody in it until a reload. The
empty cast is the honest answer for "no world is bound" and the wrong
answer for "the world did not reply just now", and the route could not
tell those apart. 503 + `Retry-After` is the difference between "there is
nobody" and "ask me again in a second".
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from game.config import GameSettings
from game.contracts import AgentPosition, AgentSummary, Snapshot
from game.server import create_app


class _World:
    """The narrowest world the cast route will accept."""

    def __init__(self, agents):
        self.agents = agents
        self.raise_on_snapshot: Exception | None = None
        self.calls = 0

    async def get_snapshot(self):
        self.calls += 1
        if self.raise_on_snapshot:
            raise self.raise_on_snapshot
        return Snapshot(tick=1, map_tiled_json={}, agents=self.agents)

    def __getattr__(self, name):
        async def _empty(*a, **kw):
            return {}
        return _empty


def _agent(agent_id: str, name: str) -> AgentSummary:
    return AgentSummary(
        agent_id=agent_id,
        display_name=name,
        sprite_key="a",
        position=AgentPosition(location_id="Kasukabe Bakery", tile_x=1, tile_y=1),
    )


def _client(world) -> TestClient:
    return TestClient(create_app(settings=GameSettings(mock_mode=True), world_client=world))


class TestTheKey:
    def test_a_rename_reaches_the_browser(self):
        """The regression: same ids, different names, stale document."""
        world = _World([_agent("n01", "Marta Thorne")])
        with _client(world) as client:
            first = client.get("/js/township/agents.js").text
            world.agents = [_agent("n01", "Marta Vance")]
            second = client.get("/js/township/agents.js").text

        assert first != second, (
            "UI-10: the cast cache is keyed on ids alone, so a rename is "
            "never served and the roster shows the old name indefinitely"
        )
        assert "Marta Vance" in second

    def test_an_unchanged_cast_is_still_served_from_cache(self):
        """The point of the cache: 1.7 MB must not be rebuilt per request
        on the hot path just because the key got stricter."""
        world = _World([_agent("n01", "Marta Thorne")])
        with _client(world) as client:
            first = client.get("/js/township/agents.js").text
            second = client.get("/js/township/agents.js").text
        assert first == second


class TestTheBlip:
    def test_an_unreachable_world_is_a_503_not_an_empty_town(self):
        world = _World([_agent("n01", "Marta Thorne")])
        world.raise_on_snapshot = RuntimeError("connection reset")
        with _client(world) as client:
            response = client.get("/js/township/agents.js")

        assert response.status_code == 503, (
            "UI-10: a momentary world failure was served as an empty cast, "
            "which the browser then caches as a document"
        )
        assert response.headers.get("Retry-After")
        assert "no-store" in (response.headers.get("Cache-Control") or "")

    def test_a_world_reporting_nobody_is_still_an_honest_empty_cast(self):
        """The other half. A world that answers and holds no residents is
        not an error - the empty town is the true picture, and turning it
        into a 503 would hide a real state behind a retry."""
        with _client(_World([])) as client:
            response = client.get("/js/township/agents.js")

        assert response.status_code == 200
        assert "const AGENTS = [];" in response.text
