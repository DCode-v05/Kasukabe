"""M1 — the awaiting flow: a pause ends the SSE without orphaning the turn,
and a block_input resumes the parked turn whose continuation routes by thread.

The full HTTP round-trip can't be driven through two streamed ASGITransport
requests (the parked asyncio task is pinned to the first request's loop), so
this drives the same machinery directly: the real server.fan_block sink, a real
StreamReg, and the real coordinator await_block_input / resume_block — which is
exactly what /execute wires together.
"""

import asyncio
import json

import pytest

import src.server as server
from src.engine_core.coordinator import Coordinator, current_thread_id_var
from src.engine_core.blocks import Gates, make_done_block
from src.engine_core.events import TextDelta, EventEmitter


def frames_to_blocks(frames: list[str]) -> list[dict]:
    out = []
    for f in frames:
        if "event: block" not in f:
            continue
        for line in f.split("\n"):
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :]))
    return out


def make_state(emitter, coordinator):
    from src.bootstrap import EngineState
    from src.config import Settings

    state = EngineState(settings=Settings(), emitter=emitter)
    state.coordinator = coordinator
    return state


@pytest.mark.asyncio
async def test_pause_routes_awaiting_then_resume_continues(monkeypatch):
    emitter = EventEmitter()
    coord = Coordinator(adapter=None, emitter=emitter, memory=None, db=None)
    state = make_state(emitter, coord)
    monkeypatch.setattr(server, "state", state)
    emitter.on(server.block_fan_sink)

    tid = "thr_await"

    q1: asyncio.Queue = asyncio.Queue()
    server.active_streams[object()] = server.StreamReg(
        queue=q1,
        thread_id_box=[tid],
        gates=Gates(craft=True),
    )

    current_thread_id_var.set(tid)
    await emitter.emit(TextDelta(thread_id=tid, text="working"))
    pause = asyncio.create_task(
        coord.await_block_input(
            {
                "question": "continue?",
                "format": "confirm",
                "options": [{"label": "Yes", "value": "yes"}],
            }
        )
    )
    drained = []
    for _ in range(20):
        await asyncio.sleep(0)
        while not q1.empty():
            drained.append(q1.get_nowait())
        if any(isinstance(x, tuple) and x[0] == server.AWAIT_SENTINEL for x in drained):
            break
    assert coord.pending_inputs, "turn must be parked with a registered waiter"
    block_id = next(iter(coord.pending_inputs))

    assert any(isinstance(x, str) and '"type": "text"' in x for x in drained)
    assert any(isinstance(x, str) and '"type": "craft"' in x for x in drained)
    assert any(isinstance(x, tuple) and x[0] == server.AWAIT_SENTINEL for x in drained)
    assert not pause.done(), "turn must stay parked, not orphaned/cancelled"

    q2: asyncio.Queue = asyncio.Queue()
    server.active_streams[object()] = server.StreamReg(
        queue=q2,
        thread_id_box=[tid],
        gates=Gates(craft=True),
    )

    owner, resumed = await coord.resume_block(tid, block_id, value="yes", text="")
    assert owner == tid
    assert resumed is True
    reply = await pause
    assert reply == "yes"

    server.fan_block(make_done_block("rid2", {"status": "completed"}, tid))
    got = []
    while not q2.empty():
        got.append(q2.get_nowait())
    blks = frames_to_blocks([x for x in got if isinstance(x, str)])
    assert blks and blks[-1]["type"] == "done"
    assert blks[-1]["payload"]["status"] == "completed"
    assert blks[-1]["thread_id"] == tid

    server.active_streams.clear()


@pytest.mark.asyncio
async def test_block_only_routes_to_its_own_thread(monkeypatch):
    """A second thread's stream never receives thread-1's blocks."""
    emitter = EventEmitter()
    state = make_state(emitter, Coordinator(adapter=None, emitter=emitter, memory=None, db=None))
    monkeypatch.setattr(server, "state", state)
    emitter.on(server.block_fan_sink)

    qa: asyncio.Queue = asyncio.Queue()
    qb: asyncio.Queue = asyncio.Queue()
    server.active_streams[object()] = server.StreamReg(qa, ["thr_a"], Gates())
    server.active_streams[object()] = server.StreamReg(qb, ["thr_b"], Gates())

    await emitter.emit(TextDelta(thread_id="thr_a", text="for A"))

    a_items, b_items = [], []
    while not qa.empty():
        a_items.append(qa.get_nowait())
    while not qb.empty():
        b_items.append(qb.get_nowait())
    assert any('"type": "text"' in x for x in a_items if isinstance(x, str))
    assert b_items == []

    server.active_streams.clear()
