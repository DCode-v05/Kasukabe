"""M6 — tui end-to-end against the REAL engine wire.

Points tui's own `Wire` at the real `server.app` (via httpx ASGITransport) with a
stub coordinator that emits real events. So tui parses genuine SSE frames, the
real block envelope, the real per-thread sink + gate filter — the independent-
consumer confirmation, minus only the live LLM (citadel). A live-citadel run of
the §10 craft sequence is the one piece this can't cover headlessly.
"""

import asyncio

import pytest
from httpx import ASGITransport

import src.server as server
from src.engine_core.blocks import BLOCK_TYPES
from src.engine_core.events import (
    TextDelta,
    ToolExecution,
    CraftAuthored,
    ExecutionSummary,
    EventEmitter,
)
from tui import EngineApp, Wire


class StubCoordinator:
    """Emits a realistic turn (text + tool + craft + summary) through the real emitter."""

    def __init__(self, emitter):
        self.emitter = emitter
        self.pending_inputs = {}
        self.thread_index = {}

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
        **gates,  # output-gate kwargs the server forwards (voice, image, ...)
    ):
        if thread_id_box is not None:
            thread_id_box[0] = "t1"
        await self.emitter.emit(TextDelta(thread_id="t1", text="Hello\n"))
        await self.emitter.emit(
            ToolExecution(thread_id="t1", tool_name="bash", source="platform", phase="call")
        )
        await self.emitter.emit(
            CraftAuthored(
                thread_id="t1",
                block_id="craft:1",
                key="k1",
                surface="inline",
                format="markdown",
                title="Result",
                content="# done",
            )
        )
        await self.emitter.emit(ExecutionSummary(thread_id="t1", status="completed"))
        return "t1"


def make_state(emitter):
    from src.bootstrap import EngineState
    from src.config import Settings

    state = EngineState(settings=Settings(), emitter=emitter)
    emitter.on(server.block_fan_sink)  # ASGITransport bypasses lifespan; wire the sink explicitly
    return state


def real_wire(monkeypatch):
    emitter = EventEmitter()
    state = make_state(emitter)
    state.coordinator = StubCoordinator(emitter)
    monkeypatch.setattr(server, "state", state)
    monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
    return Wire("http://test", "ok", transport=ASGITransport(app=server.app))


@pytest.mark.asyncio
async def test_tui_wire_parses_real_blocks(monkeypatch):
    """tui's Wire consumes the real /execute SSE stream: real envelope, real gate
    filter (FULL_GATES → craft+tool pass), real terminal done."""
    wire = real_wire(monkeypatch)
    blocks = []
    async with asyncio.timeout(20):
        done = await wire.execute(text="hi", on_block=blocks.append)

    types = [b["type"] for b in blocks]
    assert "text" in types and "tool" in types and "craft" in types
    assert types[-1] == "done"
    assert done["status"] == "completed"
    # the block envelope is the real one
    text_block = next(b for b in blocks if b["type"] == "text")
    assert text_block["payload"]["text"] == "Hello\n"
    assert text_block["thread_id"] == "t1"
    assert all(set(b) >= {"id", "type", "thread_id", "payload"} for b in blocks)


@pytest.mark.asyncio
async def test_tui_app_drives_real_server(monkeypatch):
    """The full EngineApp, headless, driving a turn against the real server and
    rendering the real block stream — then adopting the driven thread."""
    wire = real_wire(monkeypatch)
    app = EngineApp(wire)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.send_message("hi engine")
        async with asyncio.timeout(20):
            await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.selected_thread_id == "t1"  # adopted the real done.thread_id


@pytest.mark.asyncio
async def test_tui_render_block_handles_every_type(monkeypatch):
    """render_block must not crash on any wire block type (a client that chokes
    on an unexpected type is a broken consumer). The vocabulary is read from
    ``BLOCK_TYPES`` rather than copied here, so a type added to the wire is
    covered by this test the moment it is added."""
    wire = real_wire(monkeypatch)
    app = EngineApp(wire)
    async with app.run_test() as pilot:
        await pilot.pause()
        for t in BLOCK_TYPES:
            app.stream.render_block({"id": f"{t}:1", "type": t, "thread_id": "t1", "payload": {}})
        await pilot.pause()  # no exception = every type renders
