"""SSE wire contract for /execute and /execute/replay — the v2 block envelope.

Every /execute frame is `event: block` with `seq:` framing; the turn ends with
a synthesized `done` block. /execute/replay is keyed by thread_id so a client
only ever replays its own thread's blocks.
"""

import asyncio
import json

import pytest
from httpx import AsyncClient, ASGITransport

import src.server as server
from src.engine_core.blocks import Block, Gates, make_done_block
from src.engine_core.events import TextDelta, ToolExecution, ExecutionSummary, EventEmitter


class StubCoordinator:
    """Minimal coordinator: emits a few events through the real emitter."""

    def __init__(self, emitter):
        self.emitter = emitter
        self.pending_inputs = {}

    async def handle_input(
        self,
        message,
        media=None,
        thread_id=None,
        options=None,
        thread_id_box=None,
        craft=False,
        craft_wait=True,
        citations=False,
        **gates,
    ):
        if thread_id_box is not None:
            thread_id_box[0] = "t1"
        await self.emitter.emit(TextDelta(thread_id="t1", text="Hello"))
        await self.emitter.emit(
            ToolExecution(
                thread_id="t1",
                tool_name="bash",
                source="platform",
                phase="call",
            )
        )
        await self.emitter.emit(ExecutionSummary(thread_id="t1", status="completed"))
        return "t1"


def make_state(emitter):
    from src.bootstrap import EngineState
    from src.config import Settings

    state = EngineState(settings=Settings(), emitter=emitter)
    emitter.on(server.block_fan_sink)
    return state


def parse_blocks(body: str) -> list[dict]:
    """Parse the SSE body into block payloads (skips ping/replay_done frames)."""
    out = []
    for frame in body.split("\n\n"):
        if "event: block" not in frame:
            continue
        for line in frame.split("\n"):
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :]))
    return out


@pytest.mark.asyncio
async def test_execute_streams_blocks_ending_in_done(monkeypatch):
    """/execute streams folded blocks (event: block, seq: framing) and ends
    with a synthesized done block — no old per-event names."""
    emitter = EventEmitter()
    state = make_state(emitter)
    state.coordinator = StubCoordinator(emitter)
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")

    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/execute",
            json={"input": {"kind": "input", "text": "hi"}, "tool": True},
            headers={"x-engine-key": "ok"},
        ) as resp:
            assert resp.status_code == 200
            body = ""
            async with asyncio.timeout(20):
                async for chunk in resp.aiter_text():
                    body += chunk
                    if '"type": "done"' in chunk and '"awaiting"' not in chunk:
                        break

    assert "event: block" in body
    assert "event: text_delta" not in body
    assert "event: tool_execution" not in body
    assert body.count("seq: ") >= 3

    blocks = parse_blocks(body)
    types = [b["type"] for b in blocks]
    assert "text" in types
    assert "tool" in types
    assert types[-1] == "done"
    text_block = next(b for b in blocks if b["type"] == "text")
    assert text_block["payload"]["text"] == "Hello"
    done_block = blocks[-1]
    assert done_block["payload"]["status"] == "completed"
    assert done_block["thread_id"] == "t1"


class TrapToolCoordinator:
    """Emits a tool block whose params carry the literal {"type": "done"} — the
    nested dict serializes UNescaped, so the frame contains '"type": "done"'. The
    old byte-grep terminal detection would close the stream right here."""

    def __init__(self, emitter):
        self.emitter = emitter
        self.pending_inputs = {}

    async def handle_input(
        self,
        message,
        media=None,
        thread_id=None,
        options=None,
        thread_id_box=None,
        craft=False,
        craft_wait=True,
        citations=False,
        **gates,
    ):
        if thread_id_box is not None:
            thread_id_box[0] = "t1"
        await self.emitter.emit(TextDelta(thread_id="t1", text="run"))
        await self.emitter.emit(
            ToolExecution(
                thread_id="t1",
                tool_name="set_state",
                source="platform",
                phase="call",
                params={"type": "done"},
            )
        )
        await self.emitter.emit(ExecutionSummary(thread_id="t1", status="completed"))
        return "t1"


@pytest.mark.asyncio
async def test_tool_frame_carrying_type_done_does_not_truncate_stream(monkeypatch):
    """A non-terminal frame that literally contains '"type": "done"' (here a tool
    call whose params nest that key) must NOT be mistaken for the terminal frame.
    The stream is closed by a typed DONE_SENTINEL after the real done, so the tool
    block AND the real done both arrive."""
    emitter = EventEmitter()
    state = make_state(emitter)
    state.coordinator = TrapToolCoordinator(emitter)
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")

    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/execute",
            json={"input": {"kind": "input", "text": "hi"}, "tool": True},
            headers={"x-engine-key": "ok"},
        ) as resp:
            assert resp.status_code == 200
            body = ""
            async with asyncio.timeout(20):
                async for chunk in resp.aiter_text():
                    body += chunk

    assert '"type": "done"' in body
    blocks = parse_blocks(body)
    types = [b["type"] for b in blocks]
    assert "tool" in types
    assert types[-1] == "done"
    assert blocks[-1]["payload"]["status"] == "completed"
    tool_block = next(b for b in blocks if b["type"] == "tool")
    assert tool_block["payload"]["params"] == {"type": "done"}


@pytest.mark.asyncio
async def test_replay_is_thread_scoped(monkeypatch):
    """/execute/replay returns only the asked thread's blocks."""
    emitter = EventEmitter()
    state = make_state(emitter)
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")

    await emitter.emit(TextDelta(thread_id="t1", text="one"))
    await emitter.emit(TextDelta(thread_id="t1", text="two"))
    await emitter.emit(TextDelta(thread_id="t2", text="other"))

    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/execute/replay?thread_id=t1&after=0", headers={"x-engine-key": "ok"}
        )
        assert resp.status_code == 200
        assert "replay_done" in resp.text
        blocks = parse_blocks(resp.text)
        assert blocks, "expected t1 blocks"
        assert all(b["thread_id"] == "t1" for b in blocks)


@pytest.mark.asyncio
async def test_replay_unknown_thread_is_empty(monkeypatch):
    emitter = EventEmitter()
    state = make_state(emitter)
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")

    await emitter.emit(TextDelta(thread_id="t1", text="one"))

    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/execute/replay?thread_id=ghost&after=0", headers={"x-engine-key": "ok"}
        )
        assert resp.status_code == 200
        assert "replay_done" in resp.text
        assert parse_blocks(resp.text) == []


def _frame_seq_cursor(frame: str) -> tuple[int, int | None]:
    """Pull (seq, cursor) off one SSE block frame. cursor is None when absent."""
    seq = cursor = None
    for line in frame.split("\n"):
        if line.startswith("seq: "):
            seq = int(line[len("seq: ") :])
        elif line.startswith("cursor: "):
            cursor = int(line[len("cursor: ") :])
    return seq, cursor


def _drain(q: asyncio.Queue) -> list:
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


def test_gated_telemetry_yields_contiguous_per_stream_seq_with_gappy_cursor(monkeypatch):
    """Approach A wire contract, unit level.

    A telemetry:"off" subscriber gets the gated trace blocks (thread/summary)
    DROPPED, but the buffer (cursor space) still advances for them. The frames
    actually delivered to this stream must carry a CONTIGUOUS per-stream seq
    (1,2,3,...) — that is what the SDK gap-checks — while cursor reflects the
    larger, gappy per-thread buffer indices. Drives server.fan_block directly
    with a real ThreadBlockBuffers (same harness family as test_awaiting_no_orphan).
    """
    from src.bootstrap import EngineState
    from src.config import Settings
    from src.engine_core.events import EventEmitter

    state = EngineState(settings=Settings(), emitter=EventEmitter())
    monkeypatch.setattr(server, "state", state)
    server.active_streams.clear()

    tid = "t1"
    q: asyncio.Queue = asyncio.Queue()
    server.active_streams[object()] = server.StreamReg(
        queue=q, thread_id_box=[tid], gates=Gates(telemetry="off", tool=True)
    )

    def text(n):
        return Block(type="text", payload={"text": n}, thread_id=tid, id=f"text:{tid}")

    def thread_blk():
        return Block(type="thread", payload={"phase": "created"}, thread_id=tid, id=f"thread:{tid}")

    def summary_blk():
        return Block(type="summary", payload={"k": 1}, thread_id=tid, id=f"summary:{tid}")

    server.fan_block(text("a"))
    server.fan_block(thread_blk())
    server.fan_block(text("b"))
    server.fan_block(summary_blk())
    server.fan_block(thread_blk())
    server.fan_block(text("c"))
    server.fan_block(make_done_block("r1", {"status": "completed"}, tid))

    frames = [x for x in _drain(q) if isinstance(x, str)]
    parsed = [_frame_seq_cursor(f) for f in frames]
    seqs = [s for s, _ in parsed]
    cursors = [c for _, c in parsed]

    assert seqs == [1, 2, 3, 4]
    assert cursors == [1, 3, 6, 7]
    last_block = json.loads(frames[-1].split("data: ", 1)[1].strip())
    assert last_block["type"] == "done"

    server.active_streams.clear()
