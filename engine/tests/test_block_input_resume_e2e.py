"""block_input over the REAL /execute SSE: the answer's stream must carry the
resumed turn's continuation + terminal done — and close on that done.

This is the path the unit tests miss: test_awaiting_no_orphan drives fan_block
by hand, and test_resume_block_lockfree stops at the coordinator. Neither runs
``run_and_finish``, where the stream used to be deregistered the instant
resume_block returned — so the resumed turn fanned its continuation to a stream
that was already gone and the answering SSE hung on pings forever.

A two-request HTTP round-trip can't be driven (the parked task is pinned to the
first request's loop), so the original turn is a bare task parked in the test
loop; the block_input answer goes through the real server stack via
ASGITransport — which is exactly where the bug lived.
"""

import asyncio
import json

import pytest
from httpx import AsyncClient, ASGITransport

import src.server as server
from src.engine_core.coordinator import Coordinator, current_thread_id_var
from src.engine_core.blocks import Gates, make_done_block
from src.engine_core.events import TextDelta, EventEmitter


def make_state(emitter):
    from src.bootstrap import EngineState
    from src.config import Settings

    state = EngineState(settings=Settings(), emitter=emitter)
    # Mirror server.lifespan(): wire the one global event→block fan sink.
    # ASGITransport bypasses lifespan, so do it explicitly.
    emitter.on(server.block_fan_sink)
    return state


def parse_blocks(body: str) -> list[dict]:
    out = []
    for frame in body.split("\n\n"):
        if "event: block" not in frame:
            continue
        for line in frame.split("\n"):
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :]))
    return out


async def collect_until_done(resp, timeout_s: float = 10.0) -> str:
    """Read the SSE body until the terminal done frame, bounded by a timeout so
    a routing/registration regression names itself instead of hanging the suite.
    """
    body = ""
    async with asyncio.timeout(timeout_s):
        async for chunk in resp.aiter_text():
            body += chunk
            if '"type": "done"' in chunk and '"awaiting"' not in chunk:
                break
    return body


@pytest.mark.asyncio
async def test_block_input_resume_streams_continuation_and_done(monkeypatch):
    """Answering a live waiter: the continuation + terminal done arrive on the
    block_input request's own SSE, and the stream closes on the done."""
    emitter = EventEmitter()
    coord = Coordinator(adapter=None, emitter=emitter, memory=None, db=None)
    state = make_state(emitter)
    state.coordinator = coord
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")

    tid = "thr_resume"

    # The ORIGINAL turn: park on a waiting block, then — once answered — emit a
    # continuation and its OWN terminal done, exactly as the original request's
    # run_and_finish (emit_terminal=True) does. A bare task in this loop.
    async def original_turn():
        current_thread_id_var.set(tid)
        await coord.await_block_input(
            {
                "question": "continue?",
                "format": "confirm",
                "options": [{"label": "Yes", "value": "yes"}],
            }
        )
        await emitter.emit(TextDelta(thread_id=tid, text="continuation-after-resume"))
        server.fan_block(make_done_block("rid_orig", {"status": "completed"}, tid))

    # Stream 1: the ORIGINAL request's stream. The awaiting prompt + its
    # AWAIT_SENTINEL route HERE (two different requests in production), never to
    # the block_input answer stream — so the answer stream stays clean.
    q1: asyncio.Queue = asyncio.Queue()
    server.active_streams[object()] = server.StreamReg(
        queue=q1,
        thread_id_box=[tid],
        gates=Gates(craft=True),
    )

    task_a = asyncio.create_task(original_turn())
    try:
        # Pump until the turn is FULLY parked: the AWAIT_SENTINEL has reached
        # stream 1 (proves the prompt was emitted and the turn is on its future).
        drained1 = []
        for _ in range(200):
            await asyncio.sleep(0)
            while not q1.empty():
                drained1.append(q1.get_nowait())
            if any(isinstance(x, tuple) and x and x[0] == server.AWAIT_SENTINEL for x in drained1):
                break
        assert coord.pending_inputs, "the turn must be parked with a registered waiter"
        assert any(
            isinstance(x, tuple) and x and x[0] == server.AWAIT_SENTINEL for x in drained1
        ), "the awaiting sentinel must reach the original stream before we answer"
        block_id = next(iter(coord.pending_inputs))

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/execute",
                json={
                    "input": {"kind": "block_input", "block_id": block_id, "text": "yes"},
                    "thread_id": tid,
                    "tool": True,
                },
                headers={"x-engine-key": "ok"},
            ) as resp:
                assert resp.status_code == 200
                body = await collect_until_done(resp)

        blocks = parse_blocks(body)
        types = [b["type"] for b in blocks]
        # The resumed turn's continuation reached THIS stream (not just /replay).
        text = next(b for b in blocks if b["type"] == "text")
        assert text["payload"]["text"] == "continuation-after-resume"
        # ...and the stream closed on the resumed turn's own terminal done — and
        # there is exactly one done (this request stayed silent, no duplicate).
        assert types[-1] == "done"
        assert blocks[-1]["thread_id"] == tid
        assert types.count("done") == 1

        await task_a
    finally:
        if not task_a.done():
            task_a.cancel()
        server.active_streams.clear()


@pytest.mark.asyncio
async def test_block_input_no_waiter_runs_fresh_turn_and_emits_done(monkeypatch):
    """No live waiter (route:engine / craft-edit on a completed thread): a fresh
    turn runs here and THIS request owns the terminal done — it must still be
    emitted (the bug suppressed it, hanging the stream)."""
    emitter = EventEmitter()
    coord = Coordinator(adapter=None, emitter=emitter, memory=None, db=None)
    state = make_state(emitter)
    state.coordinator = coord
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")

    tid = "thr_fresh"

    async def fresh_handle_input(
        message,
        media=None,
        super_thinking=False,
        thread_id=None,
        options=None,
        thread_id_box=None,
        craft=False,
        **gates,  # output-gate kwargs resume_block forwards (voice, image, ...)
    ):
        # The stream's box is pre-bound to req.thread_id for block_input, so
        # blocks emitted on tid route to it. Emit content, return the thread.
        await emitter.emit(TextDelta(thread_id=tid, text="fresh-turn-output"))
        return tid

    coord.handle_input = fresh_handle_input

    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/execute",
            json={
                # A block_id with no registered waiter → fresh-turn fallback.
                "input": {"kind": "block_input", "block_id": "craft:thr_fresh:gone", "text": "hi"},
                "thread_id": tid,
                "tool": True,
            },
            headers={"x-engine-key": "ok"},
        ) as resp:
            assert resp.status_code == 200
            body = await collect_until_done(resp)

    blocks = parse_blocks(body)
    types = [b["type"] for b in blocks]
    text = next(b for b in blocks if b["type"] == "text")
    assert text["payload"]["text"] == "fresh-turn-output"
    assert types[-1] == "done"
    assert blocks[-1]["payload"]["status"] == "completed"
    assert blocks[-1]["thread_id"] == tid

    server.active_streams.clear()
