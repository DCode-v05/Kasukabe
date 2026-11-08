"""The /execute SSE must deliver its terminal done when handle_input answers.

``Coordinator.handle_input`` returns the assistant's ANSWER TEXT when it
returns a str — never a thread id (``ThreadResult.thread_id`` or the
``thread_id_box`` the coordinator binds mid-turn carry the id).
``run_and_finish`` used to assign that answer string to ``thread_id``, so the
terminal done was fanned under a "thread" no stream had bound — fan_block
routes by thread_id match, the DONE_SENTINEL never reached the queue, and the
SSE hung until client timeout. Observed live by the E2E wiring audit; the unit
suites missed it because their handle_input stubs returned thread ids, baking
the server's wrong assumption into the tests. These stubs are faithful to
production: bind the box, return the answer.

Same defect existed on the block_input no-waiter fallback (resume_block's
fresh-turn branch returns handle_input's result verbatim) — covered here too.

The asyncio.timeout(20) wraps the WHOLE client interaction, not just body
iteration: httpx's ASGITransport buffers the entire app call before returning
headers, so a never-delivered done hangs the header await itself — the outer
timeout is what turns a regression into a named 20s failure.
"""

import asyncio
import json

import pytest
from httpx import AsyncClient, ASGITransport

import src.server as server
from src.engine_core.coordinator import Coordinator
from src.engine_core.events import TextDelta, EventEmitter


def make_state(emitter):
    from src.bootstrap import EngineState
    from src.config import Settings

    state = EngineState(settings=Settings(), emitter=emitter)
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
    a done-routing regression names itself instead of hanging the suite."""
    body = ""
    async with asyncio.timeout(timeout_s):
        async for chunk in resp.aiter_text():
            body += chunk
            if '"type": "done"' in chunk and '"awaiting"' not in chunk:
                break
    return body


ANSWER = "The assistant's final answer — prose, not a thread id."


def answering_handle_input(emitter, tid):
    """A production-faithful handle_input: binds the stream's box to the
    resolved thread (the coordinator does this before the first block), emits
    content on that thread, and returns the ANSWER TEXT."""

    async def handle_input(
        message,
        media=None,
        super_thinking=False,
        thread_id=None,
        options=None,
        thread_id_box=None,
        craft=False,
        craft_wait=True,
        citations=False,
        **gates,
    ):
        if thread_id_box is not None:
            thread_id_box[0] = tid
        await emitter.emit(TextDelta(thread_id=tid, text="turn-output"))
        return ANSWER

    return handle_input


@pytest.mark.asyncio
async def test_fresh_input_done_arrives_when_handle_input_returns_answer_text(monkeypatch):
    """kind=input: the done must be fanned under the box-bound thread id, not
    under the answer string — the stream must close instead of hanging."""
    emitter = EventEmitter()
    coord = Coordinator(adapter=None, emitter=emitter, memory=None, db=None)
    state = make_state(emitter)
    state.coordinator = coord
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")

    tid = "thr_done_delivery"
    coord.handle_input = answering_handle_input(emitter, tid)

    transport = ASGITransport(app=server.app)
    async with asyncio.timeout(20):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/execute",
                json={"input": {"kind": "input", "text": "hi"}, "tool": True},
                headers={"x-engine-key": "ok"},
            ) as resp:
                assert resp.status_code == 200
                body = await collect_until_done(resp)

    blocks = parse_blocks(body)
    types = [b["type"] for b in blocks]
    text = next(b for b in blocks if b["type"] == "text")
    assert text["payload"]["text"] == "turn-output"
    assert types[-1] == "done"
    assert blocks[-1]["thread_id"] == tid
    server.active_streams.clear()


@pytest.mark.asyncio
async def test_no_waiter_block_input_done_arrives_when_handle_input_returns_answer_text(
    monkeypatch,
):
    """kind=block_input with no live waiter: resume_block runs a fresh turn via
    handle_input and returns its result verbatim — the same answer-text shape
    must not poison the done's thread id (the box is pre-bound to
    req.thread_id here)."""
    emitter = EventEmitter()
    coord = Coordinator(adapter=None, emitter=emitter, memory=None, db=None)
    state = make_state(emitter)
    state.coordinator = coord
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")

    tid = "thr_done_fresh_block"
    coord.handle_input = answering_handle_input(emitter, tid)

    transport = ASGITransport(app=server.app)
    async with asyncio.timeout(20):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/execute",
                json={
                    "input": {"kind": "block_input", "block_id": "craft:thr:gone", "text": "hi"},
                    "thread_id": tid,
                    "tool": True,
                },
                headers={"x-engine-key": "ok"},
            ) as resp:
                assert resp.status_code == 200
                body = await collect_until_done(resp)

    blocks = parse_blocks(body)
    types = [b["type"] for b in blocks]
    assert types[-1] == "done"
    assert blocks[-1]["thread_id"] == tid
    server.active_streams.clear()
