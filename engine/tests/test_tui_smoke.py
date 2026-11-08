"""M6 — tui.py smoke tests.

tui.py is a pure HTTP/SSE client of the block wire. These tests exercise its
client logic deterministically with a fake Wire (no live engine): the SSE frame
parser, a normal turn (adopt the driven thread), and the craft/HITL await loop
(a paused block is answered via block_input). Textual is driven headlessly via
App.run_test().
"""

import pytest

from tui import EngineApp, ExecutionStream, Wire, parse_frame


# ── SSE parsing (pure) ──


def test_parse_frame_block():
    f = parse_frame('seq: 3\nevent: block\ndata: {"type":"text"}')
    assert f["event"] == "block"
    assert f["seq"] == 3
    assert f["data"] == '{"type":"text"}'


def test_parse_frame_defaults_and_multiline_data():
    f = parse_frame('data: {"x":\ndata: 1}')
    assert f["event"] == "message"  # default when no event: line
    assert f["seq"] is None
    assert f["data"] == '{"x":1}'  # data lines concatenated, leading space stripped


# ── App flow with a fake wire ──


class FakeWire(Wire):
    """A Wire whose execute() replays a scripted list of block-lists."""

    def __init__(self, script: list[list[dict]]) -> None:
        super().__init__("http://test", "k")
        self.script = script
        self.calls: list[dict] = []

    async def execute(
        self, *, text="", thread_id=None, block_id=None, value=None, gates=None, on_block
    ):
        self.calls.append(
            {"text": text, "thread_id": thread_id, "block_id": block_id, "value": value}
        )
        blocks = (
            self.script.pop(0)
            if self.script
            else [{"type": "done", "payload": {"status": "completed", "thread_id": "t1"}}]
        )
        done: dict = {}
        for b in blocks:
            on_block(b)
            if b.get("type") == "done":
                done = b.get("payload", {})
        return done

    async def rpc(self, subsystem, op, **params):
        return {"threads": [], "count": 0}

    async def health(self):
        return {"status": "healthy", "subsystems": {"db": True, "llm": True}}

    async def channel_subscribe(self, craft_id, channels):
        return {"session_id": "", "channels": {}}


@pytest.mark.asyncio
async def test_normal_turn_adopts_thread():
    wire = FakeWire(
        [
            [
                {"type": "text", "payload": {"text": "hello there\n"}},
                {"type": "done", "payload": {"status": "completed", "thread_id": "t1"}},
            ]
        ]
    )
    app = EngineApp(wire)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.send_message("hi engine")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert wire.calls and wire.calls[0]["text"] == "hi engine"
        assert wire.calls[0]["block_id"] is None
        assert app.selected_thread_id == "t1"  # adopted the driven thread


def test_routing_block_renders_intent_and_target():
    # The routing telemetry line must read the RoutingDecision fields the engine
    # actually folds (intent / target_thread) — not the renamed-away method /
    # selected_slot_id, which rendered a permanently blank "[routing:  → ?]".
    stream = ExecutionStream()
    captured: list[str] = []
    stream.write = lambda text, *a, **k: captured.append(
        text.plain if hasattr(text, "plain") else str(text)
    )
    stream.render_block(
        {
            "type": "routing",
            "payload": {"intent": "continuation", "target_thread": "thread99"},
        }
    )
    line = next(s for s in captured if "routing:" in s)
    assert "continuation" in line  # intent rendered (was always blank)
    assert "thread99" in line  # target_thread rendered (was '?')
    assert "→ ?]" not in line  # not the stale-empty form


@pytest.mark.asyncio
async def test_craft_await_then_block_input():
    wire = FakeWire(
        [
            # turn 1: a craft that pauses (done.status=awaiting carries the block_id)
            [
                {
                    "type": "craft",
                    "id": "craft:1",
                    "payload": {
                        "key": "k1",
                        "title": "Pick",
                        "question": "Which?",
                        "options": [{"label": "A", "value": "a"}],
                    },
                },
                {"type": "done", "payload": {"status": "awaiting", "block_id": "craft:1"}},
            ],
            # turn 2: the answer resolves it
            [{"type": "done", "payload": {"status": "completed", "thread_id": "t1"}}],
        ]
    )
    app = EngineApp(wire)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.send_message("start")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.awaiting_block_id == "craft:1"  # paused, waiting for an answer

        app.send_message("answer text")
        await app.workers.wait_for_complete()
        await pilot.pause()
        # second call answered the paused block via block_input
        assert wire.calls[1]["block_id"] == "craft:1"
        assert wire.calls[1]["value"] == {"response": "answer text"}
        assert app.awaiting_block_id == ""  # cleared after answering


@pytest.mark.asyncio
async def test_unknown_command_is_handled():
    app = EngineApp(FakeWire([]))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.handle_command("/bogus")
        await pilot.pause()  # no exception = handled gracefully
