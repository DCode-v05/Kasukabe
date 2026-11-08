"""ConnectionHub: the fan-out between ONE upstream WorldClient event
stream and MANY connected browser tabs.

This is the "WebSocket endpoint that relays world events" half of the
task spec, kept separate from server.py's FastAPI route wiring so it can
be unit-tested with a fake browser socket (game/tests/test_relay.py)
instead of a real ASGI TestClient WebSocket, and separate from
world_client.py so relaying doesn't need to know whether events are
coming from a mocked town or a real world/ WebSocket - it only depends on
world_client.WorldClient's stream_events() shape.

One ConnectionHub per game server process, constructed once in
server.py's lifespan and shared by every browser connection - NOT one
hub per browser tab, which is what makes this a fan-out relay instead of
N independent proxies each opening their own connection to world/ (world/
only has to serve one WS client: game/, no matter how many browser tabs
are open).
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Protocol

from game.contracts import WireEvent
from game.world_client import WorldClient

logger = logging.getLogger("game.relay")


class BrowserSocket(Protocol):
    """The minimal shape ConnectionHub needs from a browser connection -
    satisfied by fastapi.WebSocket and by test doubles
    (game/tests/test_relay.py) with none of FastAPI's machinery."""

    async def send_text(self, data: str) -> None: ...


class ConnectionHub:
    def __init__(self, world_client: WorldClient, replay_buffer_size: int = 200) -> None:
        self.world_client = world_client
        self.clients: set[BrowserSocket] = set()
        self.buffer: deque[WireEvent] = deque(maxlen=replay_buffer_size)
        self._pump_task: asyncio.Task | None = None

    def start(self) -> None:
        if self._pump_task is None:
            self._pump_task = asyncio.ensure_future(self._pump())

    async def _pump(self) -> None:
        try:
            async for event in self.world_client.stream_events():
                self.buffer.append(event)
                await self.broadcast(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            # world_client.stream_events() is documented to reconnect on
            # its own (world_client.py's HttpWorldClient.stream_events
            # docstring) and never raise except on close(); reaching here
            # means that contract was broken somewhere upstream. Log and
            # stop pumping rather than silently going dark with no error
            # anywhere - a hung relay with no browser updates and no log
            # line is the worst failure mode for a live operator console.
            logger.exception("game.relay: event pump crashed, no more events will be relayed")

    async def broadcast(self, event: WireEvent) -> None:
        if not self.clients:
            return
        payload = event.model_dump_json()
        dead: list[BrowserSocket] = []
        results = await asyncio.gather(
            *(client.send_text(payload) for client in self.clients), return_exceptions=True
        )
        for client, result in zip(list(self.clients), results):
            if isinstance(result, Exception):
                dead.append(client)
        for client in dead:
            self.clients.discard(client)

    async def register(self, client: BrowserSocket) -> None:
        """Adds a browser connection and replays the recent-event buffer
        to it, so a tab that reconnects after a brief drop (see
        config.GameSettings.replay_buffer_size's docstring) sees what it
        missed instead of a gap - it already has a snapshot from its
        initial GET /api/snapshot, so replaying is strictly additive."""
        self.clients.add(client)
        for event in list(self.buffer):
            await client.send_text(event.model_dump_json())

    def unregister(self, client: BrowserSocket) -> None:
        self.clients.discard(client)

    async def stop(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            try:
                await self._pump_task
            except asyncio.CancelledError:
                pass
