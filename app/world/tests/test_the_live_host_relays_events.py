"""The host that runs serves the websocket the front end connects to.

`UI-06`. DESIGN section 9: "the game app is fed by the world server's own
WebSocket relay (no external realtime)". `world/main.py` has served that
relay for a long time. The EMBEDDED app - the one every actual run boots
- never had the route.

So `game/relay.py` connected to `ws://<world>/ws/events`, got a 404, and
`HttpWorldClient.stream_events` emitted `world.connection.lost` every two
seconds forever. No `agent.moved` and no `world.tick.advanced` ever
arrived: on the Phaser page the sprites never moved and the clock never
advanced, for the whole life of the project.

Two composition roots with one of them wired - the root cause of most of
this review's P0s, here costing the entire live view of the town.

Every test here is SYNCHRONOUS on purpose. `TestClient` runs the app in
its own event loop, so an `event_bus.emit` awaited from a pytest-asyncio
loop reaches a subscriber that then tries to write to a socket owned by
the other loop, and the receive blocks forever. The first draft of this
file did exactly that and hung. Events that must arrive live are
therefore raised through a real HTTP route, which runs where the socket
does.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from world.admin_api import AdminExtras
from world.run_genesis import (
    WS_REPLAY_EVENTS,
    GenesisRunner,
    GenesisRunnerConfig,
    build_embedded_world_app,
)

HEADERS = {"X-Operator-Token": "t"}


def _boot(tmp_path, pre_emit: int = 0):
    """A booted runner and its embedded app, with any pre-connection
    events already in the ring - raised inside the same `asyncio.run` that
    booted, because after it returns there is no loop to emit on."""

    async def build():
        runner = GenesisRunner(GenesisRunnerConfig(
            mock_mode=True,
            checkpoint_dir=tmp_path / "checkpoints",
            world_mode="embedded",
            world_port=0,
        ))
        await runner.boot()
        for i in range(pre_emit):
            await runner.world_state.event_bus.emit(
                "agent.moved", tick=i, payload={"agent_id": f"a{i}"}
            )
        return runner

    runner = asyncio.run(build())
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        tick_engine=runner.tick_engine, genesis=runner.genesis,
        map_service=runner.map_service,
        admin_extras=AdminExtras(operator_token="t", sim_control=runner.sim_control),
    )
    return runner, app


@pytest.fixture(scope="module")
def booted(tmp_path_factory):
    return _boot(tmp_path_factory.mktemp("ws"))


def test_the_route_exists_on_the_host_that_runs(booted) -> None:
    """The finding at its simplest: `game/config.py` points at
    `/ws/events` and the live app had no such path."""
    _runner, app = booted
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/ws/events" in paths, (
        "UI-06: the embedded world app serves no websocket, so the game "
        "relay reconnects forever and no tick ever reaches the browser"
    )


def test_the_live_path_is_wired_while_a_browser_is_connected(booted) -> None:
    """Delivery of NEW events is `subscribe("*", on_event)` plus a
    `send_json`, and the subscription is the half that was missing: with
    no route there was nothing to subscribe.

    Asserted by watching the bus rather than by emitting and reading,
    because `TestClient` runs the app on its own event loop and a POST
    issued inside an open `websocket_connect` block deadlocks against the
    blocking `receive_json` - the second thing this file hung on. Paired
    with `test_a_disconnect_does_not_leave_a_subscriber_behind`, which
    covers the other end of the same lifecycle.
    """
    runner, app = booted
    bus = runner.world_state.event_bus
    before = len(bus.subscribers.get("*", []))

    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as socket:
            # Read one frame first. That the handler has sent anything
            # proves it is past `accept` and past `subscribe`, which is
            # what makes this deterministic rather than a race against the
            # app's own event loop.
            socket.receive_json()
            during = len(bus.subscribers.get("*", []))

    assert during == before + 1, (
        "UI-06: a browser is connected to the relay and nothing on the bus "
        "is listening for it, so no live event can ever reach the page"
    )


def test_a_new_tab_is_caught_up_on_what_it_missed(tmp_path) -> None:
    """A browser opening mid-run must see the town as it stands, not an
    empty map waiting for the next thing to happen."""
    _runner, app = _boot(tmp_path, pre_emit=3)
    replayed: list[dict] = []
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as socket:
            # Genesis itself emits (agent.spawned and friends), so the
            # three moved-events are in the catch-up, not at the head of
            # it. Reading a bounded window and looking for them is the
            # honest assertion; "the first frame is mine" was true only
            # of a town that had done nothing.
            for _ in range(WS_REPLAY_EVENTS):
                frame = socket.receive_json()
                replayed.append(frame)
                if frame["payload"].get("agent_id") == "a2":
                    break
    moved = [f for f in replayed if f["type"] == "agent.moved"]
    assert [f["payload"]["agent_id"] for f in moved] == ["a0", "a1", "a2"], (
        "UI-06: a tab opening mid-run was not caught up on what happened "
        "before it connected, so it draws an empty town"
    )


def test_the_catch_up_is_bounded(tmp_path) -> None:
    """CORE-09 grows the ring to `agents * 200` - up to 50,000 - and
    `main.py`'s relay replays the whole buffer. A tab opening mid-run must
    not be sent the run's entire history before its first live frame."""
    runner, app = _boot(tmp_path, pre_emit=WS_REPLAY_EVENTS + 200)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as socket:
            first = socket.receive_json()

    oldest_replayed = runner.world_state.event_bus.recent()[-WS_REPLAY_EVENTS:][0]
    assert first["event_id"] == oldest_replayed.event_id, (
        "UI-06: the relay replayed further back than the cap, so a new tab "
        "waits through the whole ring before seeing anything live"
    )


def test_a_disconnect_does_not_leave_a_subscriber_behind(booted) -> None:
    """A leaked subscriber writes into a closed socket on every event for
    the rest of the run.

    Driven against the route's own coroutine with a fake socket, on one
    event loop, rather than through `TestClient`. Through the client this
    measured the harness: `TestClient` tears its portal down without
    waiting for the handler's `finally`, so the subscriber was still there
    when the assertion ran whatever production did. A test that fails for
    a reason outside the code under test is not evidence about the code
    under test.
    """
    from fastapi import WebSocketDisconnect

    runner, app = booted
    bus = runner.world_state.event_bus
    before = len(bus.subscribers.get("*", []))

    endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, "path", "") == "/ws/events"
    )

    class _Socket:
        """Accepts, takes frames, then reports the browser has gone."""

        def __init__(self) -> None:
            self.sent: list[dict] = []
            self.subscribed_while_open = None

        async def accept(self) -> None:
            pass

        async def send_json(self, payload: dict) -> None:
            self.sent.append(payload)

        async def receive_text(self) -> str:
            # By now the catch-up is done and the subscription is live -
            # the one moment where "is the browser being listened for"
            # can be asked without a race.
            self.subscribed_while_open = len(bus.subscribers.get("*", []))
            raise WebSocketDisconnect(code=1000)

    socket = _Socket()
    asyncio.run(endpoint(socket))

    assert socket.sent, "the relay accepted a browser and sent it nothing"
    assert socket.subscribed_while_open == before + 1, (
        "UI-06: nothing on the bus was listening for this browser"
    )
    assert len(bus.subscribers.get("*", [])) == before, (
        "UI-06: a closed browser left a subscriber on the bus, which then "
        "writes into a dead socket on every event for the rest of the run"
    )
