"""relay.ConnectionHub: fan-out from one fake WorldClient event stream to
N fake browser sockets. Neither fake touches asyncio.sleep or a real
network socket, so these tests are fast and deterministic
(app/CONVENTIONS.md's test conventions)."""

from __future__ import annotations

import asyncio

import pytest

from game.contracts import WireEvent
from game.relay import ConnectionHub


def make_event(event_type: str = "world.tick.advanced", tick: int = 1) -> WireEvent:
    return WireEvent(type=event_type, tick=tick, payload={}, event_id=f"e-{tick}", published_at=0.0)


class FakeWorldClient:
    """A WorldClient whose stream_events() yields a fixed list of events
    then waits forever - matches the real contract's "yields
    indefinitely" shape without an actual network connection."""

    def __init__(self, events: list[WireEvent]) -> None:
        self._events = events
        self.closed = False

    async def get_snapshot(self):
        raise NotImplementedError("not exercised by these tests")

    async def get_agent_detail(self, agent_id: str):
        raise NotImplementedError("not exercised by these tests")

    async def stream_events(self):
        for event in self._events:
            yield event
        forever: asyncio.Future = asyncio.get_event_loop().create_future()
        await forever

    async def close(self) -> None:
        self.closed = True


class FakeBrowserSocket:
    def __init__(self, fail: bool = False) -> None:
        self.received: list[str] = []
        self.fail = fail

    async def send_text(self, data: str) -> None:
        if self.fail:
            raise ConnectionError("simulated disconnect")
        self.received.append(data)


async def wait_until(predicate, timeout: float = 1.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_hub_replays_buffer_to_a_newly_registered_client() -> None:
    world = FakeWorldClient([make_event(tick=1), make_event(tick=2)])
    hub = ConnectionHub(world, replay_buffer_size=10)
    hub.start()

    await wait_until(lambda: len(hub.buffer) == 2)

    client = FakeBrowserSocket()
    await hub.register(client)

    assert len(client.received) == 2
    assert '"tick":1' in client.received[0]
    assert '"tick":2' in client.received[1]

    await hub.stop()


@pytest.mark.asyncio
async def test_hub_broadcasts_new_events_to_registered_clients() -> None:
    world = FakeWorldClient([make_event(tick=1)])
    hub = ConnectionHub(world, replay_buffer_size=10)
    client = FakeBrowserSocket()
    hub.clients.add(client)

    hub.start()
    await wait_until(lambda: len(client.received) == 1)

    assert '"tick":1' in client.received[0]
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_drops_a_client_whose_send_fails() -> None:
    world = FakeWorldClient([make_event(tick=1)])
    hub = ConnectionHub(world, replay_buffer_size=10)
    dead_client = FakeBrowserSocket(fail=True)
    hub.clients.add(dead_client)

    hub.start()
    await wait_until(lambda: dead_client not in hub.clients)

    assert dead_client not in hub.clients
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_broadcast_is_a_noop_with_no_clients() -> None:
    world = FakeWorldClient([])
    hub = ConnectionHub(world, replay_buffer_size=10)
    await hub.broadcast(make_event())
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_unregister_removes_client() -> None:
    world = FakeWorldClient([])
    hub = ConnectionHub(world, replay_buffer_size=10)
    client = FakeBrowserSocket()
    hub.clients.add(client)
    hub.unregister(client)
    assert client not in hub.clients
    await hub.stop()


@pytest.mark.asyncio
async def test_hub_replay_buffer_respects_max_size() -> None:
    events = [make_event(tick=i) for i in range(5)]
    world = FakeWorldClient(events)
    hub = ConnectionHub(world, replay_buffer_size=3)
    hub.start()

    await wait_until(lambda: len(hub.buffer) == 3)

    assert [e.tick for e in hub.buffer] == [2, 3, 4]
    await hub.stop()
