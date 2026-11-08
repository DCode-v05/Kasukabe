"""Many pollers, one upstream fan-out.

`UI-08`. `/api/township/live` costs `/health` + `/world/state` +
`/agents` + `/world/activity` upstream, and the `/agents` row for 20-50
residents carries skills and workflow documents. `/api/engine` repeats
three of those and adds two broker calls.

Both are polled twice over: `township-v2.js` every 2 s and `live-cast.js`
every 10 s hit the live feed, and `/api/engine` runs on its own 2 s
timer. That is about 2.5 requests a second to the game server PER OPEN
TAB, and roughly 8 upstream requests a second to a world process that is
simultaneously serving every engine's tool calls - a world where 10 turns
per run were already being discarded at the 120-second dispatch limit.

The browser's cadence is deliberately left alone. What changes is that
two pollers and ten tabs collapse into one fetch per window.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from game.config import GameSettings
from game.contracts import Snapshot
from game.server import create_app


class _CountingWorld:
    """Counts how often the server actually goes upstream."""

    def __init__(self) -> None:
        self.snapshots = 0

    async def get_snapshot(self) -> Snapshot:
        self.snapshots += 1
        # Slow enough that a second caller arrives while this is in
        # flight - the case a plain TTL cache does not cover.
        await asyncio.sleep(0.05)
        return Snapshot(tick=self.snapshots, map_tiled_json={}, agents=[])

    async def stream_events(self):
        if False:  # pragma: no cover - never yields
            yield None

    async def close(self) -> None:
        return None


@pytest.fixture
def counted():
    world = _CountingWorld()
    app = create_app(
        settings=GameSettings(mock_mode=False, world_base_url="http://w.test"),
        world_client=world,
    )
    return world, app


def test_repeated_polls_do_not_repeat_the_fan_out(counted) -> None:
    """The finding: two pollers on one route, ten tabs behind them."""
    world, app = counted
    with TestClient(app) as client:
        for _ in range(10):
            assert client.get("/api/township/live").status_code == 200

    assert world.snapshots == 1, (
        f"UI-08: ten polls cost {world.snapshots} upstream fan-outs; a world "
        "already dropping turns at the dispatch limit is serving the same "
        "answer over and over"
    )


def test_the_answer_is_still_fresh_after_the_window(counted) -> None:
    """A cache that never expires is a viewer showing a frozen town."""
    import time

    world, app = counted
    with TestClient(app) as client:
        first = client.get("/api/township/live").json()
        time.sleep(1.1)
        second = client.get("/api/township/live").json()

    assert world.snapshots == 2
    assert first["tick"] != second["tick"]


def test_a_cold_cache_does_not_start_two_fetches(counted) -> None:
    """Single-flight, not just cached. Without the lock, every caller
    arriving before the first fetch returns starts its own - which is
    precisely the burst this exists to prevent."""
    world, app = counted

    async def hammer():
        from httpx import ASGITransport, AsyncClient

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            await asyncio.gather(*[client.get("/api/township/live") for _ in range(8)])

    asyncio.run(hammer())
    assert world.snapshots == 1, (
        f"UI-08: eight simultaneous callers on a cold cache produced "
        f"{world.snapshots} fan-outs - the cache helps only after the first "
        "one lands, and the first one is the stampede"
    )


def test_switching_worlds_is_visible_immediately(counted) -> None:
    """The cache must not outlive the thing it is caching. The Real-sim
    button swaps the world mid-process, and a key that ignored that would
    serve the previous town for a whole window - UI-20's bug arriving
    through a different door. A test caught exactly this."""
    world, app = counted
    with TestClient(app) as client:
        before = client.get("/api/township/live").json()
        client.app.state.realsim.world_client = _CountingWorld()
        after = client.get("/api/township/live").json()

    assert before["tick"] == 1 and after["tick"] == 1
    assert world.snapshots == 1, "the old world was polled again after the switch"
