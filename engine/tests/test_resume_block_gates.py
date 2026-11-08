"""The block_input resume path must honour the same gates as /execute.

resume_block forwarded craft/voice/voice_primary/image/audio/video but not
citations or craft_wait, and had no parameter for either - so a fresh turn on
the resume path ran with citations=False and craft_wait=True regardless of what
the request said. Both sides of the same gate then disagreed:

  - citations: the stream's Gates was built with sources=True from the real
    request, so the sink held a channel open for source blocks the engine had
    been told not to produce. Nothing raised; the client just got silence.
  - craft_wait: the stream read the real craft_wait=False and did NOT park,
    while the engine ran with craft_wait_var=True and halted the turn at the
    first waiting craft - a silently truncated answer with no pause signal.

The assertions read the contextvars the turn actually runs under, so they prove
the value reached the segment's own context copy rather than the caller's.
"""

import pytest

from src.engine_core.coordinator import (
    Coordinator,
    citations_gate_var,
    craft_wait_var,
    voice_gate_var,
)
from src.engine_core.schema import ThreadIndexEntry


class FakeEmitter:
    def __init__(self):
        self.events = []

    async def emit(self, ev):
        self.events.append(ev)


class FakeMemory:
    async def write(self, *a, **k):
        pass

    async def read(self, *a, **k):
        return []


class GateReadingThread:
    """Reads the gate contextvars from inside the running turn."""

    def __init__(self, thread_id, seen):
        self.thread_id = thread_id
        self.seen = seen
        self.cancel_requested = False

    async def execute(self, message, thread_id=None, **kwargs):
        self.seen.append(
            {
                "craft_wait": craft_wait_var.get(),
                "citations": citations_gate_var.get(),
                "voice": voice_gate_var.get(),
                "message": message,
            }
        )
        return f"done:{thread_id}"


def make_coordinator(monkeypatch):
    coord = Coordinator(adapter=None, emitter=FakeEmitter(), memory=FakeMemory(), db=None)
    seen: list[dict] = []
    coord.seen_gates = seen
    coord.threads["thread-a"] = GateReadingThread("thread-a", seen)
    coord.thread_index["thread-a"] = ThreadIndexEntry(thread_id="thread-a", summary="work")

    async def get_or_create(thread_id):
        if thread_id not in coord.threads:
            coord.threads[thread_id] = GateReadingThread(thread_id, seen)
        return coord.threads[thread_id]

    monkeypatch.setattr(coord, "get_or_create_thread", get_or_create)

    async def noop_index(*a, **k):
        pass

    monkeypatch.setattr(coord, "update_thread_index", noop_index)
    return coord


class TestFreshTurnOnResume:
    """No live waiter, so resume_block runs a fresh turn via handle_input."""

    @pytest.mark.asyncio
    async def test_citations_reaches_the_turn(self, monkeypatch):
        coord = make_coordinator(monkeypatch)
        await coord.resume_block("thread-a", "blk-1", None, "go on", citations=True)
        assert coord.seen_gates[0]["citations"] is True

    @pytest.mark.asyncio
    async def test_citations_defaults_off(self, monkeypatch):
        coord = make_coordinator(monkeypatch)
        await coord.resume_block("thread-a", "blk-1", None, "go on")
        assert coord.seen_gates[0]["citations"] is False

    @pytest.mark.asyncio
    async def test_craft_wait_false_reaches_the_turn(self, monkeypatch):
        """The caller asked never to be parked; the engine must not halt."""
        coord = make_coordinator(monkeypatch)
        await coord.resume_block("thread-a", "blk-1", None, "go on", craft_wait=False)
        assert coord.seen_gates[0]["craft_wait"] is False

    @pytest.mark.asyncio
    async def test_craft_wait_defaults_on(self, monkeypatch):
        coord = make_coordinator(monkeypatch)
        await coord.resume_block("thread-a", "blk-1", None, "go on")
        assert coord.seen_gates[0]["craft_wait"] is True

    @pytest.mark.asyncio
    async def test_craft_edit_path_carries_the_gates_too(self, monkeypatch):
        """A craft edit forces craft=True but must not reset the other gates."""
        coord = make_coordinator(monkeypatch)
        await coord.resume_block(
            "thread-a",
            "blk-1",
            {"edit": "make it shorter", "current": {"key": "k", "version": 1}},
            "",
            craft_wait=False,
            citations=True,
        )
        assert coord.seen_gates[0]["craft_wait"] is False
        assert coord.seen_gates[0]["citations"] is True

    @pytest.mark.asyncio
    async def test_existing_gates_still_work(self, monkeypatch):
        coord = make_coordinator(monkeypatch)
        await coord.resume_block("thread-a", "blk-1", None, "go on", voice=True)
        assert coord.seen_gates[0]["voice"] is True


class TestResumeMatchesExecute:
    """The two branches of /execute must forward the same parameter set."""

    def test_parameter_sets_agree(self):
        import inspect

        resume = set(inspect.signature(Coordinator.resume_block).parameters)
        handle = set(inspect.signature(Coordinator.handle_input).parameters)
        gates = {
            "craft",
            "craft_wait",
            "citations",
            "voice",
            "voice_primary",
            "image",
            "audio",
            "video",
        }
        assert gates <= handle
        assert gates <= resume, f"resume_block is missing {sorted(gates - resume)}"


class TestOverTheRoute:
    """server.py must pass req.citations / req.craft_wait on the resume branch."""

    @pytest.mark.asyncio
    async def test_block_input_forwards_both(self, monkeypatch):
        import asyncio

        from httpx import ASGITransport, AsyncClient

        from src import server
        from src.config import Settings
        from src.engine_core.events import EventEmitter

        seen: list[dict] = []

        class SpyCoordinator:
            def __init__(self):
                self.emitter = EventEmitter()

            def on(self, cb):
                pass

            async def resume_block(self, thread_id, block_id, value, text, **kwargs):
                seen.append(kwargs)
                return thread_id, True

        engine_state = server.EngineState(settings=Settings(), emitter=EventEmitter())
        engine_state.coordinator = SpyCoordinator()
        engine_state.restored_thread_ids = set()
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        monkeypatch.setattr(server, "state", engine_state)
        monkeypatch.setattr(server, "is_shutting_down", lambda: False)

        body = {
            "input": {"kind": "block_input", "block_id": "blk-1", "text": "yes"},
            "thread_id": "thread-a",
            "citations": True,
            "craft_wait": False,
        }

        async def drive():
            transport = ASGITransport(app=server.app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                async with client.stream(
                    "POST", "/execute", json=body, headers={"x-engine-key": "k"}
                ) as resp:
                    async for _ in resp.aiter_lines():
                        if seen:
                            break

        task = asyncio.create_task(drive())
        for _ in range(200):
            if seen:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except BaseException:
            pass

        assert seen, "resume_block was never called"
        assert seen[0]["citations"] is True
        assert seen[0]["craft_wait"] is False
