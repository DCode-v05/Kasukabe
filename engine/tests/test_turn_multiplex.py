"""One input, several threads, one stream - and what a client does when it drops.

These drive the real fan/buffer/replay path in server.py: real Block objects,
the real sink, the real StreamReg routing, the real replay route over ASGI.
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from src import server
from src.bootstrap import EngineState
from src.engine_core.blocks import (
    Block,
    Gates,
    TurnBlockBuffers,
    make_aggregate_done_block,
    make_done_block,
    sse_frame,
)
from src.engine_core.coordinator import current_turn_id_var
from src.engine_core.events import EventEmitter
from src.config import Settings


@pytest.fixture
def wired(monkeypatch):
    """A real EngineState with the module-level stream registry cleared."""
    state = EngineState(settings=Settings(), emitter=EventEmitter())
    monkeypatch.setattr(server, "state", state)
    server.active_streams.clear()
    yield state
    server.active_streams.clear()


def register(queue, threads, turn_id="turn-1", telemetry="none"):
    class FakeTask:
        pass

    task = FakeTask()
    server.active_streams[task] = server.StreamReg(
        queue=queue,
        thread_id_box=list(threads),
        gates=Gates(telemetry=telemetry),
        turn_id=turn_id,
    )
    return task


def frames(queue):
    out = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, str):
            out.append(json.loads(item.split("data: ", 1)[1].strip()))
    return out


class TestOriginRule:
    def test_engine_origin_serializes_thread_id_null(self):
        block = Block(type="routing", payload={"segments": []}, thread_id="", id="r1")
        data = json.loads(sse_frame(block, 1).split("data: ", 1)[1].strip())
        assert data["thread_id"] is None

    def test_thread_block_keeps_its_id(self):
        block = Block(type="text", payload={"text": "hi"}, thread_id="thread-a", id="t1")
        data = json.loads(sse_frame(block, 1).split("data: ", 1)[1].strip())
        assert data["thread_id"] == "thread-a"

    def test_aggregate_done_is_engine_origin(self):
        block = make_aggregate_done_block("r1", [{"thread_id": "thread-a"}])
        assert block.thread_id == ""
        assert block.payload["segments"] == [{"thread_id": "thread-a"}]


class TestRouting:
    @pytest.mark.asyncio
    async def test_stream_receives_blocks_from_every_bound_thread(self, wired):
        import asyncio

        q = asyncio.Queue()
        register(q, ["thread-a", "thread-b"])
        current_turn_id_var.set("turn-1")

        server.fan_block(Block(type="text", payload={"text": "A"}, thread_id="thread-a"))
        server.fan_block(Block(type="text", payload={"text": "B"}, thread_id="thread-b"))

        texts = [f["payload"]["text"] for f in frames(q)]
        assert texts == ["A", "B"]

    @pytest.mark.asyncio
    async def test_unbound_thread_is_not_delivered(self, wired):
        import asyncio

        q = asyncio.Queue()
        register(q, ["thread-a"])
        current_turn_id_var.set("turn-1")

        server.fan_block(Block(type="text", payload={"text": "mine"}, thread_id="thread-a"))
        server.fan_block(Block(type="text", payload={"text": "theirs"}, thread_id="thread-z"))

        assert [f["payload"]["text"] for f in frames(q)] == ["mine"]

    @pytest.mark.asyncio
    async def test_engine_origin_block_routes_by_turn(self, wired):
        import asyncio

        mine, other = asyncio.Queue(), asyncio.Queue()
        register(mine, ["thread-a"], turn_id="turn-1")
        register(other, ["thread-b"], turn_id="turn-2")
        current_turn_id_var.set("turn-1")

        server.fan_block(Block(type="routing", payload={"segments": []}, thread_id=""))

        assert len(frames(mine)) == 1
        assert frames(other) == []

    @pytest.mark.asyncio
    async def test_dispatch_reaches_a_default_stream(self, wired):
        """The dispatch is always-on: a client that asked for no telemetry
        still learns what its input became."""
        import asyncio

        q = asyncio.Queue()
        register(q, ["thread-a"], telemetry="none")
        current_turn_id_var.set("turn-1")

        server.fan_block(
            Block(
                type="routing",
                payload={"segments": [{"verdict": "open"}]},
                thread_id="",
            )
        )
        assert len(frames(q)) == 1


class TestTerminalDone:
    @pytest.mark.asyncio
    async def test_single_thread_turn_closes_on_its_done(self, wired):
        import asyncio

        q = asyncio.Queue()
        register(q, ["thread-a"])
        current_turn_id_var.set("turn-1")

        server.fan_block(make_done_block("r1", {"status": "completed"}, "thread-a"))

        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert any(
            isinstance(i, tuple) and i[0] == server.DONE_SENTINEL for i in items
        ), "single-thread turn must still close on its own done"

    @pytest.mark.asyncio
    async def test_per_segment_done_does_not_close_a_fanned_turn(self, wired):
        import asyncio

        q = asyncio.Queue()
        register(q, ["thread-a", "thread-b"])
        current_turn_id_var.set("turn-1")

        server.fan_block(
            make_done_block("r1", {"status": "completed"}, "thread-a", terminal=False)
        )

        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert not any(isinstance(i, tuple) and i[0] == server.DONE_SENTINEL for i in items), (
            "a sibling segment was still running - the stream must stay open"
        )

    @pytest.mark.asyncio
    async def test_aggregate_done_closes_the_turn(self, wired):
        import asyncio

        q = asyncio.Queue()
        register(q, ["thread-a", "thread-b"])
        current_turn_id_var.set("turn-1")

        server.fan_block(
            make_aggregate_done_block(
                "r1",
                [
                    {"thread_id": "thread-a", "status": "completed"},
                    {"thread_id": "thread-b", "status": "failed"},
                ],
            )
        )

        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert any(isinstance(i, tuple) and i[0] == server.DONE_SENTINEL for i in items)
        payload = [
            json.loads(i.split("data: ", 1)[1].strip()) for i in items if isinstance(i, str)
        ][0]["payload"]
        assert [s["status"] for s in payload["segments"]] == ["completed", "failed"]


class TestTurnBuffer:
    def test_merged_interleave_is_kept_in_delivery_order(self):
        buf = TurnBlockBuffers()
        buf.append("turn-1", Block(type="routing", payload={}, thread_id=""))
        buf.append("turn-1", Block(type="text", payload={"text": "A"}, thread_id="thread-a"))
        buf.append("turn-1", Block(type="text", payload={"text": "B"}, thread_id="thread-b"))
        buf.append("turn-2", Block(type="text", payload={"text": "other"}, thread_id="thread-c"))

        replayed = buf.replay("turn-1")
        assert [b.type for _, b in replayed] == ["routing", "text", "text"]
        assert [s for s, _ in replayed] == [1, 2, 3]

    def test_after_cursor_resumes_midstream(self):
        buf = TurnBlockBuffers()
        for i in range(5):
            buf.append("turn-1", Block(type="text", payload={"i": i}, thread_id="thread-a"))
        assert [b.payload["i"] for _, b in buf.replay("turn-1", after=2)] == [2, 3, 4]

    def test_turns_do_not_leak_into_each_other(self):
        buf = TurnBlockBuffers()
        buf.append("turn-1", Block(type="text", payload={}, thread_id="thread-a"))
        buf.append("turn-2", Block(type="text", payload={}, thread_id="thread-a"))
        assert len(buf.replay("turn-1")) == 1
        assert len(buf.replay("turn-2")) == 1

    def test_unknown_turn_is_empty(self):
        assert TurnBlockBuffers().replay("nope") == []

    def test_untracked_turn_is_not_buffered(self):
        buf = TurnBlockBuffers()
        assert buf.append("", Block(type="text", payload={}, thread_id="t")) == 0
        assert buf.turns() == []


class TestReplayRoute:
    @pytest.mark.asyncio
    async def test_turn_replay_returns_the_whole_interleave(self, wired, monkeypatch):
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        wired.turn_buffers.append(
            "turn-1", Block(type="routing", payload={"segments": []}, thread_id="")
        )
        wired.turn_buffers.append(
            "turn-1", Block(type="text", payload={"text": "A"}, thread_id="thread-a")
        )
        wired.turn_buffers.append(
            "turn-1", Block(type="text", payload={"text": "B"}, thread_id="thread-b")
        )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/execute/replay?turn_id=turn-1", headers={"x-engine-key": "ok"}
            )
        body = resp.text
        assert body.count("event: block") == 3
        assert '"replayed": 3' in body
        assert '"thread_id": null' in body

    @pytest.mark.asyncio
    async def test_turn_replay_honours_after(self, wired, monkeypatch):
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        for i in range(4):
            wired.turn_buffers.append(
                "turn-1", Block(type="text", payload={"i": i}, thread_id="thread-a")
            )

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/execute/replay?turn_id=turn-1&after=2", headers={"x-engine-key": "ok"}
            )
        assert resp.text.count("event: block") == 2

    @pytest.mark.asyncio
    async def test_thread_replay_still_works(self, wired, monkeypatch):
        """Per-thread replay is unchanged for consumers following one thread."""
        monkeypatch.setattr(server, "validate_key", lambda *a, **k: "ok")
        wired.block_buffers.append(Block(type="text", payload={"text": "A"}, thread_id="thread-a"))
        wired.block_buffers.append(Block(type="text", payload={"text": "B"}, thread_id="thread-b"))

        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/execute/replay?thread_id=thread-a", headers={"x-engine-key": "ok"}
            )
        assert resp.text.count("event: block") == 1
        assert '"text": "A"' in resp.text


class TestEndOfTurnReviewIsHonest:
    """A turn that fans out keeps going when one segment fails - that is the
    point. What it must not do is finish and then report the failure as a
    success, which is the one outcome worse than the failure itself."""

    def setup_method(self):
        server.last_summary_by_thread.clear()
        server.failed_threads.clear()

    def test_a_completed_segment_reports_its_summary(self):
        server.last_summary_by_thread["thread-a"] = {"status": "completed", "num_turns": 3}
        assert server.segment_outcome("thread-a")["status"] == "completed"

    def test_a_failed_segment_reports_failed(self):
        server.record_thread_failure("thread-b")
        assert server.segment_outcome("thread-b")["status"] == "failed"

    def test_a_failed_segment_is_not_defaulted_to_completed(self):
        """The regression: no ExecutionSummary is emitted on failure, so a
        naive default would call it completed."""
        server.record_thread_failure("thread-b")
        assert server.segment_outcome("thread-b") != {"status": "completed"}

    def test_outcome_is_consumed_once(self):
        server.record_thread_failure("thread-b")
        assert server.segment_outcome("thread-b")["status"] == "failed"
        assert server.segment_outcome("thread-b")["status"] == "completed"

    def test_a_thread_with_no_signal_reports_completed(self):
        assert server.segment_outcome("thread-quiet")["status"] == "completed"

    @pytest.mark.asyncio
    async def test_failure_is_recorded_from_the_live_event(self, wired):
        from src.engine_core.events import ThreadLifecycle

        await server.block_fan_sink(ThreadLifecycle(thread_id="thread-c", status="failed"))
        assert server.segment_outcome("thread-c")["status"] == "failed"

    @pytest.mark.asyncio
    async def test_a_started_lifecycle_is_not_mistaken_for_failure(self, wired):
        from src.engine_core.events import ThreadLifecycle

        await server.block_fan_sink(ThreadLifecycle(thread_id="thread-d", status="started"))
        assert server.segment_outcome("thread-d")["status"] == "completed"

    @pytest.mark.asyncio
    async def test_mixed_turn_reports_each_segment_truthfully(self, wired):
        from src.engine_core.events import ThreadLifecycle

        await server.block_fan_sink(ThreadLifecycle(thread_id="thread-b", status="failed"))
        server.last_summary_by_thread["thread-a"] = {"status": "completed"}

        outcomes = [
            {"thread_id": tid, **server.segment_outcome(tid)}
            for tid in ["thread-a", "thread-b", "thread-c"]
        ]
        assert [o["status"] for o in outcomes] == ["completed", "failed", "completed"]

        agg = make_aggregate_done_block("r1", outcomes)
        assert agg.thread_id == ""
        assert agg.payload["segments"][1]["status"] == "failed"


class TestTerminalFlag:
    """Which done ends the turn is stated by the engine, not inferred: a reader
    would have to know how many segments were dispatched to work it out."""

    def test_single_turn_done_is_terminal(self):
        b = make_done_block("r1", {"status": "completed"}, "thread-a")
        assert "terminal" not in b.payload

    def test_per_segment_done_is_marked_non_terminal(self):
        b = make_done_block("r1", {"status": "completed"}, "thread-a", terminal=False)
        assert b.payload["terminal"] is False

    def test_aggregate_done_is_terminal(self):
        b = make_aggregate_done_block("r1", [{"thread_id": "thread-a"}])
        assert b.payload.get("terminal", True) is True

    @pytest.mark.asyncio
    async def test_a_marked_done_closes_even_with_siblings_bound(self, wired):
        import asyncio

        q = asyncio.Queue()
        register(q, ["thread-a", "thread-b"])
        current_turn_id_var.set("turn-1")

        server.fan_block(make_aggregate_done_block("r1", [{"thread_id": "thread-a"}]))
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert any(isinstance(i, tuple) and i[0] == server.DONE_SENTINEL for i in items)


class TestAwaitingAttribution:
    """The awaiting done must name the thread that raised the question. In a
    fanned-out turn that is often not the primary."""

    @pytest.mark.asyncio
    async def test_sentinel_carries_the_asking_thread(self, wired):
        import asyncio

        q = asyncio.Queue()
        register(q, ["thread-a", "thread-b"])
        current_turn_id_var.set("turn-1")

        craft = Block(
            type="craft",
            payload={"wait": True, "question": "which vendor?"},
            thread_id="thread-b",
            id="c1",
        )
        server.fan_block(craft)

        sentinels = []
        while not q.empty():
            item = q.get_nowait()
            if isinstance(item, tuple) and item[0] == server.AWAIT_SENTINEL:
                sentinels.append(item)
        assert sentinels, "a waiting craft must raise the awaiting sentinel"
        assert sentinels[0][2] == "thread-b", "the asking thread, not slot 0"


class TestFailureWindowIsBounded:
    def setup_method(self):
        server.failed_threads.clear()

    def test_records_and_consumes(self):
        server.record_thread_failure("thread-a")
        assert server.segment_outcome("thread-a")["status"] == "failed"
        assert server.segment_outcome("thread-a")["status"] == "completed"

    def test_empty_thread_id_is_ignored(self):
        server.record_thread_failure("")
        assert "" not in server.failed_threads

    def test_window_evicts_oldest_rather_than_growing(self):
        for i in range(server.FAILED_THREAD_WINDOW + 50):
            server.record_thread_failure(f"thread-{i}")
        assert len(server.failed_threads) <= server.FAILED_THREAD_WINDOW
        assert f"thread-{server.FAILED_THREAD_WINDOW + 49}" in server.failed_threads


class TestTurnBufferIsBounded:
    def test_oldest_turn_is_evicted_once_the_window_is_full(self):
        buf = TurnBlockBuffers(max_turns=3)
        for i in range(5):
            buf.append(f"turn-{i}", Block(type="text", payload={}, thread_id="t"))
        assert len(buf.turns()) == 3
        assert "turn-0" not in buf.turns()
        assert "turn-4" in buf.turns()

    def test_eviction_drops_the_seq_counter_too(self):
        buf = TurnBlockBuffers(max_turns=2)
        for i in range(4):
            buf.append(f"turn-{i}", Block(type="text", payload={}, thread_id="t"))
        assert set(buf.seq) == set(buf.turns())

    def test_a_live_turn_keeps_its_blocks_while_in_the_window(self):
        buf = TurnBlockBuffers(max_turns=3)
        for i in range(3):
            buf.append("turn-live", Block(type="text", payload={"i": i}, thread_id="t"))
        assert len(buf.replay("turn-live")) == 3
