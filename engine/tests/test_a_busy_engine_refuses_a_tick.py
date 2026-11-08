"""/tick refuses a second turn instead of queueing it.

`CORE-11`, engine half. The runner's `asyncio.wait_for` cancels the
coroutine awaiting the response; it does not stop the turn. uvicorn does
not cancel a handler when its client disconnects either. So a tick that
arrived while a turn was running used to block on `SimEngineState`'s
`_turn_lock` until that turn finished, and then run against a perception
several ticks stale - having been paid for twice.

The review's fix names this file: "the engine answers 409 if a tick
arrives while one is running (seam change in `agent_adapter.py`, allowed
under Law 1)". `src/sim/` is the seam ROADMAP Law 1 leaves writable, so
this is a change this project is allowed to make - which is the part I
had wrong when I filed CORE-11 as blocked.

The app side of the contract is tested in
`app/world/tests/test_a_busy_engine_refuses_a_tick.py`; that suite reads
this file as text, because the two packages have separate venvs. The
BEHAVIOUR is here, where the imports resolve.

Every request below has a deadline. Reverting the fix makes the second
POST block on `_turn_lock` for ever rather than fail, and a test that
hangs the suite on regression is worse than no test at all - nobody reads
a run that never finishes.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.sim.agent_adapter import build_sim_agent_app


class _Profile:
    agent_id = "a0001"


class _Gate:
    async def on_tick(self, sleeping: bool):
        return None


class _Engine:
    """The smallest thing `build_sim_agent_app` will accept, plus a real
    lock - the lock is the object under test."""

    def __init__(self) -> None:
        self.profile = _Profile()
        self.thread_id = "t1"
        self.learning_gate = _Gate()
        self.last_turn_steps = 0
        self.last_turn_hit_budget = False
        self._turn_lock = asyncio.Lock()
        self.release = asyncio.Event()
        self.turns_started = 0

    @property
    def turn_in_flight(self) -> bool:
        return self._turn_lock.locked()

    async def send_tick(self, perception, call_budget=None) -> str:
        async with self._turn_lock:
            self.turns_started += 1
            await self.release.wait()
            return "done"


def _tick_body(tick: int) -> dict:
    return {
        "tick": tick,
        "is_sleeping": False,
        "perception": "you are in the square",
        "call_budget": 4,
    }


def test_an_idle_engine_accepts_a_tick() -> None:
    """The control. A 409 that fired when nothing was running would stop
    the town thinking altogether."""
    engine = _Engine()
    engine.release.set()
    with TestClient(build_sim_agent_app(engine)) as client:
        resp = client.post("/tick", json=_tick_body(1))
    assert resp.status_code == 200
    assert resp.json()["reply"] == "done"
    assert engine.turns_started == 1


def test_a_second_tick_is_refused_while_the_first_runs() -> None:
    """The finding. Before this, the second POST blocked on `_turn_lock`
    and then ran against a stale perception."""
    engine = _Engine()

    async def drive() -> tuple[int, dict]:
        app = build_sim_agent_app(engine)
        # Start a turn and leave it running.
        first = asyncio.create_task(engine.send_tick("first"))
        await asyncio.sleep(0)          # let it take the lock
        assert engine.turn_in_flight, "the first turn did not start"

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://engine"
        ) as client:
            resp = await asyncio.wait_for(
                client.post("/tick", json=_tick_body(2)), timeout=5.0
            )
        engine.release.set()
        await first
        return resp.status_code, resp.json()

    status, body = asyncio.run(drive())

    assert status == 409, (
        "CORE-11: the second tick was not refused. It queues on the "
        "engine's own _turn_lock, and when the first turn finally finishes "
        "it runs against a perception several ticks old - having paid for "
        "both."
    )
    detail = body["detail"]
    assert detail["error"] == "turn_in_flight"
    assert detail["agent_id"] == "a0001"
    assert detail["tick"] == 2
    assert engine.turns_started == 1, (
        "CORE-11: the refused tick still started a turn"
    )


def test_the_refusal_says_which_agent_and_which_tick() -> None:
    """A 409 with no body is a 409 nobody can attribute. Run 23's ten lost
    turns were unattributable for exactly this reason - the runner knew a
    timeout had happened and nothing about whose late tool calls were
    whose."""
    engine = _Engine()

    async def drive() -> dict:
        app = build_sim_agent_app(engine)
        first = asyncio.create_task(engine.send_tick("first"))
        await asyncio.sleep(0)

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://engine"
        ) as client:
            resp = await asyncio.wait_for(
                client.post("/tick", json=_tick_body(7)), timeout=5.0
            )
        engine.release.set()
        await first
        return resp.json()["detail"]

    detail = asyncio.run(drive())
    assert set(detail) >= {"error", "agent_id", "tick", "message"}
    assert "stale perception" in detail["message"]


def test_the_engine_is_free_again_afterwards() -> None:
    """A refusal must not latch. An engine that answered 409 forever after
    one slow turn would be a resident that never thinks again."""
    engine = _Engine()
    engine.release.set()
    with TestClient(build_sim_agent_app(engine)) as client:
        assert client.post("/tick", json=_tick_body(1)).status_code == 200
        assert engine.turn_in_flight is False
        assert client.post("/tick", json=_tick_body(2)).status_code == 200
    assert engine.turns_started == 2


def test_a_sleeping_tick_is_refused_too_while_a_turn_runs() -> None:
    """The sleeping branch returns before `send_tick`, so it would not
    have deadlocked - but it writes through `learning_gate` on the same
    engine whose turn is mid-flight, which is the state `_turn_lock`
    exists to protect."""
    engine = _Engine()

    async def drive() -> int:
        app = build_sim_agent_app(engine)
        first = asyncio.create_task(engine.send_tick("first"))
        await asyncio.sleep(0)

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://engine"
        ) as client:
            body = _tick_body(3) | {"is_sleeping": True}
            resp = await asyncio.wait_for(
                client.post("/tick", json=body), timeout=5.0
            )
        engine.release.set()
        await first
        return resp.status_code

    assert asyncio.run(drive()) == 409
