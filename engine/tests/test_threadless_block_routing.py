"""Thread-less blocks must route by turn, not vanish into a sentinel thread.

thread_id_of fell back to the serializer's scope_id when an event carried no
thread_id, and the global sink is `BlockSerializer()` whose scope_id defaults
to "_". Three folded event classes have no thread_id field at all
(ReconnectSummaryEvent, ActivityStateChanged, SubprocessSpawnedEvent), so their
blocks carried the truthy string "_". fan_block then took its thread branch and
required "_" to be in some stream's thread_id_box, which only ever holds real
thread ids or "". The block was buffered, passed its gate, and was dropped by
the router with no log at all - and the "" turn-routing fallback the fan-out
merge added was unreachable, because the coercion had already replaced "".

These use the real BlockSerializer, the real fan_block, and real StreamReg
records, so the routing decision under test is the production one.
"""

import asyncio

import pytest

from src import server
from src.engine_core.blocks import BlockSerializer, Gates, thread_id_of
from src.engine_core.coordinator import current_turn_id_var
from src.engine_core.events import (
    ActivityStateChanged,
    ReconnectSummaryEvent,
    SubprocessSpawnedEvent,
    TextDelta,
)


THREADLESS_EVENTS = [
    pytest.param(
        SubprocessSpawnedEvent(
            spawn_site="sandbox_exec",
            mode="default",
            scrubbed_count=3,
            has_proxy_env=False,
        ),
        id="subprocess",
    ),
    pytest.param(
        ActivityStateChanged(old_state="idle", new_state="active", idle_seconds=42.0),
        id="activity",
    ),
    pytest.param(
        ReconnectSummaryEvent(
            previous_state="away",
            idle_seconds=90.0,
            tool_calls=1,
            errors=0,
            user_active_seconds=5.0,
            engine_active_seconds=5.0,
            natural_language="while you were away",
            source="deterministic",
        ),
        id="reconnect",
    ),
]


class TestThreadIdOf:
    @pytest.mark.parametrize("event", THREADLESS_EVENTS)
    def test_threadless_events_get_an_empty_thread_id(self, event):
        assert thread_id_of(event, "_") == ""
        assert thread_id_of(event, "req-abc") == ""

    def test_an_event_that_names_a_thread_keeps_it(self):
        assert thread_id_of(TextDelta(thread_id="thread-a", text="hi"), "_") == "thread-a"

    @pytest.mark.parametrize("event", THREADLESS_EVENTS)
    def test_the_folded_block_carries_no_sentinel(self, event):
        block = BlockSerializer().fold(event)
        assert block is not None
        assert block.thread_id == "", f"{block.type} folded to {block.thread_id!r}"


class TestFanBlockDelivery:
    """The routing decision itself, through the real fan_block."""

    def register(self, monkeypatch, turn_id: str, thread_ids: list) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        reg = server.StreamReg(
            queue=queue,
            thread_id_box=thread_ids,
            gates=Gates(telemetry="full"),
            turn_id=turn_id,
        )
        streams = {object(): reg}
        monkeypatch.setattr(server, "active_streams", streams)

        class FakeBuffers:
            def append(self, *a):
                return 1

        class FakeState:
            block_buffers = FakeBuffers()
            turn_buffers = FakeBuffers()

        monkeypatch.setattr(server, "state", FakeState())
        return queue

    @pytest.mark.parametrize("event", THREADLESS_EVENTS)
    @pytest.mark.asyncio
    async def test_a_threadless_block_reaches_the_turns_stream(self, monkeypatch, event):
        queue = self.register(monkeypatch, "turn-1", [""])
        block = BlockSerializer().fold(event)

        token = current_turn_id_var.set("turn-1")
        try:
            server.fan_block(block)
        finally:
            current_turn_id_var.reset(token)

        assert not queue.empty(), f"{block.type} block was dropped by the router"

    @pytest.mark.parametrize("event", THREADLESS_EVENTS)
    @pytest.mark.asyncio
    async def test_it_does_not_leak_into_another_turns_stream(self, monkeypatch, event):
        queue = self.register(monkeypatch, "turn-1", [""])
        block = BlockSerializer().fold(event)

        token = current_turn_id_var.set("turn-2")
        try:
            server.fan_block(block)
        finally:
            current_turn_id_var.reset(token)

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_a_thread_block_still_routes_by_thread(self, monkeypatch):
        queue = self.register(monkeypatch, "turn-1", ["thread-a"])
        block = BlockSerializer().fold(TextDelta(thread_id="thread-a", text="hi"))

        token = current_turn_id_var.set("turn-1")
        try:
            server.fan_block(block)
        finally:
            current_turn_id_var.reset(token)

        assert not queue.empty()

    @pytest.mark.asyncio
    async def test_a_thread_block_for_a_different_thread_is_still_filtered(
        self, monkeypatch
    ):
        queue = self.register(monkeypatch, "turn-1", ["thread-a"])
        block = BlockSerializer().fold(TextDelta(thread_id="thread-b", text="hi"))

        token = current_turn_id_var.set("turn-1")
        try:
            server.fan_block(block)
        finally:
            current_turn_id_var.reset(token)

        assert queue.empty()


class TestReconnectSummaryCarriesTheTurn:
    """The reconnect card was blocked twice over: the "_" sentinel AND an empty
    turn id, because its task is created before run_and_finish sets the
    contextvar. Fixing only the sentinel leaves it undelivered."""

    @pytest.mark.asyncio
    async def test_emit_reconnect_summary_sets_the_turn_it_was_given(self, monkeypatch):
        seen: list = []

        class CapturingEmitter:
            async def emit(self, event):
                seen.append((event, current_turn_id_var.get()))

        class FakeState:
            emitter = CapturingEmitter()
            light_adapter = None
            adapter = None

        monkeypatch.setattr(server, "state", FakeState())

        class Stats:
            tool_calls = 1
            errors = 0
            user_active_seconds = 5.0
            engine_active_seconds = 5.0

        class Summary:
            class previous_state:
                value = "away"

            idle_seconds = 90.0
            stats = Stats()

            def format_recap(self):
                return "you were away"

        await asyncio.create_task(server.emit_reconnect_summary(Summary(), "turn-9"))

        assert seen, "no event was emitted"
        event, turn_at_emit = seen[0]
        assert isinstance(event, ReconnectSummaryEvent)
        assert turn_at_emit == "turn-9"

    @pytest.mark.asyncio
    async def test_the_card_reaches_a_stream_end_to_end(self, monkeypatch):
        """Fold + route the event the real emitter would have published."""
        queue: asyncio.Queue = asyncio.Queue()
        reg = server.StreamReg(
            queue=queue,
            thread_id_box=[""],
            gates=Gates(telemetry="full"),
            turn_id="turn-9",
        )
        streams = {object(): reg}
        monkeypatch.setattr(server, "active_streams", streams)

        class FakeBuffers:
            def append(self, *a):
                return 1

        class FakeState:
            block_buffers = FakeBuffers()
            turn_buffers = FakeBuffers()

        monkeypatch.setattr(server, "state", FakeState())

        event = ReconnectSummaryEvent(
            previous_state="away",
            idle_seconds=90.0,
            tool_calls=1,
            errors=0,
            user_active_seconds=5.0,
            engine_active_seconds=5.0,
            natural_language="while you were away",
            source="deterministic",
        )
        block = BlockSerializer().fold(event)

        async def emit_in_its_own_task():
            token = current_turn_id_var.set("turn-9")
            try:
                server.fan_block(block)
            finally:
                current_turn_id_var.reset(token)

        await asyncio.create_task(emit_in_its_own_task())
        assert not queue.empty(), "the reconnect card never reached the stream"
