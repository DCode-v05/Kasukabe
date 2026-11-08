"""`direct` must reach the routing decision, not just the request log.

ExecuteRequest.direct was documented as "bypass the router", validated, logged
once - and then dropped. It was absent from Gates, from RequestOptions, and
from the handle_input call, and Coordinator.handle_input had no such parameter.
The request parsed, the turn ran, an answer came back: the caller paid the full
triage hop it asked to skip, and on an unlucky triage got no turn at all.

These drive the real Coordinator with a real ThreadRouter double, so the router
is observed to be called or not called, and the /execute route is exercised over
ASGI so the field is proven to survive the whole server -> coordinator path.
"""

import asyncio

import pytest

from src.engine_core.coordinator import Coordinator
from src.engine_core.schema import Dispatch, DispatchSegment, ThreadIndexEntry


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


class CountingRouter:
    """Records every triage hop and always claims the input for a live thread."""

    def __init__(self):
        self.calls = 0

    async def dispatch(self, index_text, message, known_ids):
        self.calls += 1
        return Dispatch(
            segments=[
                DispatchSegment(
                    extract=message, verdict="continue", target=sorted(known_ids)[0]
                )
            ],
            reasoning="triaged",
        )


class RecordingThread:
    def __init__(self, thread_id, log):
        self.thread_id = thread_id
        self.log = log
        self.cancel_requested = False

    async def execute(self, message, thread_id=None, **kwargs):
        self.log.append((thread_id, message))
        return f"done:{thread_id}"


def make_coordinator(monkeypatch):
    coord = Coordinator(adapter=None, emitter=FakeEmitter(), memory=FakeMemory(), db=None)
    log: list[tuple[str, str]] = []
    coord.execution_log = log
    coord.router = CountingRouter()

    for tid in ("thread-a", "thread-b"):
        coord.threads[tid] = RecordingThread(tid, log)
        coord.thread_index[tid] = ThreadIndexEntry(thread_id=tid, summary=f"{tid} work")

    async def get_or_create(thread_id):
        if thread_id not in coord.threads:
            coord.threads[thread_id] = RecordingThread(thread_id, log)
        return coord.threads[thread_id]

    monkeypatch.setattr(coord, "get_or_create_thread", get_or_create)

    async def noop_index(*a, **k):
        pass

    monkeypatch.setattr(coord, "update_thread_index", noop_index)
    return coord


class TestBuildDispatch:
    @pytest.mark.asyncio
    async def test_direct_skips_the_router_entirely(self, monkeypatch):
        coord = make_coordinator(monkeypatch)
        dispatch = await coord.build_dispatch("ship the fix", thread_id=None, direct=True)
        assert coord.router.calls == 0
        assert dispatch.reasoning == "direct"
        assert len(dispatch.segments) == 1
        assert dispatch.segments[0].extract == "ship the fix"
        assert dispatch.segments[0].target is None

    @pytest.mark.asyncio
    async def test_direct_segment_is_executable(self, monkeypatch):
        """The bypass must produce work, not the 'awaiting your decision' dead
        end a claimed/contested verdict produces."""
        coord = make_coordinator(monkeypatch)
        dispatch = await coord.build_dispatch("ship the fix", thread_id=None, direct=True)
        assert dispatch.executable

    @pytest.mark.asyncio
    async def test_explicit_thread_id_still_wins_over_direct(self, monkeypatch):
        coord = make_coordinator(monkeypatch)
        dispatch = await coord.build_dispatch("ship it", thread_id="thread-b", direct=True)
        assert coord.router.calls == 0
        assert dispatch.segments[0].target == "thread-b"
        assert dispatch.reasoning == "explicit thread_id"


class TestHandleInput:
    @pytest.mark.asyncio
    async def test_direct_runs_a_turn_without_triage(self, monkeypatch):
        coord = make_coordinator(monkeypatch)
        await coord.handle_input("ship the fix", direct=True)
        assert coord.router.calls == 0
        assert len(coord.execution_log) == 1
        assert coord.execution_log[0][1] == "ship the fix"
        # No target on the segment, so a NEW thread was opened rather than the
        # existing one triage would have picked.
        assert coord.execution_log[0][0] not in ("thread-a", "thread-b")

class TestOverTheRoute:
    """The field must survive ExecuteRequest -> handle_input over real HTTP.

    Before the fix server.py logged req.direct at line 965 and that was its
    last appearance: it reached neither Gates, nor RequestOptions, nor the
    coordinator call.
    """

    async def post_execute(self, monkeypatch, body: dict) -> list:
        from httpx import ASGITransport, AsyncClient

        from src import server
        from src.config import Settings
        from src.engine_core.events import EventEmitter

        seen: list = []

        class SpyCoordinator:
            def __init__(self):
                self.emitter = EventEmitter()

            def on(self, cb):
                pass

            async def handle_input(self, message, **kwargs):
                seen.append(kwargs.get("direct"))
                return "ok"

        engine_state = server.EngineState(settings=Settings(), emitter=EventEmitter())
        engine_state.coordinator = SpyCoordinator()
        engine_state.restored_thread_ids = set()
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        monkeypatch.setattr(server, "state", engine_state)
        monkeypatch.setattr(server, "is_shutting_down", lambda: False)

        called = asyncio.Event()

        async def drive():
            transport = ASGITransport(app=server.app, raise_app_exceptions=False)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                async with client.stream(
                    "POST", "/execute", json=body, headers={"x-engine-key": "k"}
                ) as resp:
                    async for _ in resp.aiter_lines():
                        if called.is_set():
                            break

        # The spy coordinator produces no blocks, so the SSE stream has nothing
        # to close it. Drive it until handle_input has been observed, then stop:
        # what is under test is the argument, not the stream's lifecycle.
        task = asyncio.create_task(drive())
        for _ in range(200):
            if seen:
                called.set()
                break
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return seen

    @pytest.mark.asyncio
    async def test_direct_true_reaches_handle_input(self, monkeypatch):
        seen = await self.post_execute(
            monkeypatch, {"input": {"kind": "input", "text": "hi"}, "direct": True}
        )
        assert seen == [True]

    @pytest.mark.asyncio
    async def test_direct_defaults_to_false(self, monkeypatch):
        seen = await self.post_execute(
            monkeypatch, {"input": {"kind": "input", "text": "hi"}}
        )
        assert seen == [False]
