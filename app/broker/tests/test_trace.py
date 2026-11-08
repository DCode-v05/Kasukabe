"""BatchWriter: size-triggered and timer-triggered flushing, graceful
close, and never letting a sink exception escape into the caller."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from broker.trace import (
    DEFAULT_HALT_AFTER_CONSECUTIVE_FAILURES,
    BatchWriter,
    BrokerEvent,
    InMemorySink,
    TraceRecord,
    broker_event_to_row,
    trace_record_to_row,
)


class FailingSink:
    def __init__(self) -> None:
        self.attempts = 0
        self.fail = True

    async def write_batch(self, rows: list) -> None:
        self.attempts += 1
        if self.fail:
            raise RuntimeError("sink is down")


@pytest.mark.asyncio
async def test_flush_is_a_no_op_on_an_empty_buffer() -> None:
    sink = InMemorySink()
    writer = BatchWriter(sink=sink, batch_size=10, flush_interval_s=1000.0)

    await writer.flush()

    assert sink.batches == []
    await writer.close()


@pytest.mark.asyncio
async def test_record_below_batch_size_does_not_auto_flush() -> None:
    sink = InMemorySink()
    writer = BatchWriter(sink=sink, batch_size=10, flush_interval_s=1000.0)

    writer.record("a")
    writer.record("b")
    await asyncio.sleep(0)  # let any scheduled background task get a turn

    assert sink.batches == []
    assert writer.buffer == ["a", "b"]
    await writer.close()


@pytest.mark.asyncio
async def test_record_reaching_batch_size_triggers_an_async_flush() -> None:
    sink = InMemorySink()
    writer = BatchWriter(sink=sink, batch_size=2, flush_interval_s=1000.0)

    writer.record("a")
    writer.record("b")  # buffer now at batch_size -> schedules a flush
    await asyncio.sleep(0)  # let the scheduled flush() task actually run

    assert sink.rows == ["a", "b"]
    assert writer.buffer == []
    await writer.close()


@pytest.mark.asyncio
async def test_explicit_flush_drains_whatever_is_buffered() -> None:
    sink = InMemorySink()
    writer = BatchWriter(sink=sink, batch_size=100, flush_interval_s=1000.0)

    writer.record("a")
    writer.record("b")
    writer.record("c")
    await writer.flush()

    assert sink.rows == ["a", "b", "c"]
    assert writer.buffer == []
    await writer.close()


@pytest.mark.asyncio
async def test_periodic_flush_fires_on_its_own_without_hitting_batch_size() -> None:
    tick = asyncio.Event()

    async def fast_sleep(_seconds: float) -> None:
        await tick.wait()
        tick.clear()

    sink = InMemorySink()
    writer = BatchWriter(sink=sink, batch_size=1000, flush_interval_s=0.001, sleep=fast_sleep)

    writer.record("a")  # starts the background periodic-flush task
    await asyncio.sleep(0)  # let the background task reach `await self.sleep(...)`
    tick.set()  # release the fake sleep -> triggers one periodic flush
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert sink.rows == ["a"]
    await writer.close()


@pytest.mark.asyncio
async def test_sink_exception_is_swallowed_not_raised() -> None:
    sink = FailingSink()
    writer = BatchWriter(sink=sink, batch_size=100, flush_interval_s=1000.0)

    writer.record("a")
    await writer.flush()  # must not raise despite the sink always raising

    assert sink.attempts == 1
    await writer.close()


@pytest.mark.asyncio
async def test_close_flushes_remaining_buffer_and_stops_accepting() -> None:
    sink = InMemorySink()
    writer = BatchWriter(sink=sink, batch_size=100, flush_interval_s=1000.0)

    writer.record("a")
    await writer.close()

    assert sink.rows == ["a"]
    assert writer.closed is True

    writer.record("b")  # dropped: writer is closed
    assert writer.buffer == []


@pytest.mark.asyncio
async def test_close_is_safe_to_call_twice() -> None:
    writer = BatchWriter(sink=InMemorySink(), batch_size=10, flush_interval_s=1000.0)
    writer.record("a")
    await writer.close()
    await writer.close()  # must not raise


# INFRA-24: the SupabaseRestSink tests were removed with the class
# on 2026-09-05. They exercised a sink no code path builds, so they
# passed forever and covered nothing that runs.
# ── failure tracking / is_halted (TASK spec: "count failures ... halt
# dispatch when writes fail persistently") ──────────────────────────────


@pytest.mark.asyncio
async def test_stats_start_clean_and_not_halted() -> None:
    writer = BatchWriter(sink=InMemorySink(), batch_size=100, flush_interval_s=1000.0)

    stats = writer.stats()

    assert stats == {
        "batches_attempted": 0,
        "batches_failed": 0,
        "rows_dropped": 0,
        "consecutive_failures": 0,
        "halt_after_consecutive_failures": DEFAULT_HALT_AFTER_CONSECUTIVE_FAILURES,
        "halted": False,
        "last_failure_message": None,
        "last_failure_at": None,
    }
    assert writer.is_halted is False
    await writer.close()


@pytest.mark.asyncio
async def test_a_failed_flush_is_counted_but_does_not_halt_below_threshold() -> None:
    sink = FailingSink()
    writer = BatchWriter(sink=sink, batch_size=100, flush_interval_s=1000.0, halt_after_consecutive_failures=3)

    writer.record("a")
    await writer.flush()

    assert writer.stats()["batches_attempted"] == 1
    assert writer.stats()["batches_failed"] == 1
    assert writer.stats()["rows_dropped"] == 1
    assert writer.consecutive_failures == 1
    assert writer.last_failure_message == "sink is down"
    assert writer.is_halted is False
    await writer.close()


@pytest.mark.asyncio
async def test_reaching_the_threshold_of_consecutive_failures_halts() -> None:
    sink = FailingSink()
    writer = BatchWriter(sink=sink, batch_size=1, flush_interval_s=1000.0, halt_after_consecutive_failures=3)

    for row in ("a", "b", "c"):
        writer.record(row)
        await writer.flush()

    assert writer.consecutive_failures == 3
    assert writer.is_halted is True
    assert writer.stats()["rows_dropped"] == 3
    await writer.close()


@pytest.mark.asyncio
async def test_a_successful_flush_resets_consecutive_failures_and_unhalts() -> None:
    sink = FailingSink()
    writer = BatchWriter(sink=sink, batch_size=1, flush_interval_s=1000.0, halt_after_consecutive_failures=2)

    writer.record("a")
    await writer.flush()
    writer.record("b")
    await writer.flush()
    assert writer.is_halted is True

    sink.fail = False  # the sink recovers
    writer.record("c")
    await writer.flush()

    assert writer.consecutive_failures == 0
    assert writer.is_halted is False
    # The historical totals are not erased by recovering - only the
    # consecutive-failure streak that gates dispatch resets.
    assert writer.stats()["batches_failed"] == 2
    assert writer.stats()["batches_attempted"] == 3
    await writer.close()


@pytest.mark.asyncio
async def test_a_successful_flush_never_counted_as_a_failure() -> None:
    writer = BatchWriter(sink=InMemorySink(), batch_size=1, flush_interval_s=1000.0)

    writer.record("a")
    await writer.flush()

    assert writer.stats()["batches_attempted"] == 1
    assert writer.stats()["batches_failed"] == 0
    assert writer.is_halted is False
    await writer.close()


# ── tick field: additive, backward compatible with every existing
# keyword-argument construction site (broker/service.py) ────────────────


def test_trace_record_tick_defaults_to_none_and_is_additive() -> None:
    record = TraceRecord(
        id="trc_1",
        agent_id="agent-1",
        payer_agent_id="agent-1",
        model="moonshotai.kimi-k2.5",
        requested_model="moonshotai.kimi-k2.5",
        region="ap-south-1",
        call_class="cognition",
        purpose="agent_turn",
        request_body={},
        response_body=None,
        stop_reason=None,
        input_tokens=1,
        output_tokens=1,
        cost_nano_dollars=1,
        cents_debited=0,
        latency_ms=1,
        rate_limit_wait_ms=0,
        success=True,
        error_type=None,
        error_message=None,
        created_at=1.0,
    )

    assert record.tick is None
    row = trace_record_to_row(record)
    assert row["tick"] is None
    assert row["success"] is True


def test_broker_event_tick_defaults_to_none_and_is_additive() -> None:
    event = BrokerEvent(
        id="evt_1", type="broker.call.completed", agent_id="agent-1", model="moonshotai.kimi-k2.5",
        payload={}, created_at=1.0,
    )

    assert event.tick is None
    row = broker_event_to_row(event)
    assert row["tick"] is None


# ── the body cap ────────────────────────────────────────────────────────
#
# A 30-agent, six-hour run wrote a 5.9 GB trace onto a laptop with 1.2 GB
# free. The average request body was 630 KB; the largest was 36.5 MB. The
# cause of prompts that size is upstream of broker/ (see docs/TEST_RUN.md);
# what these tests pin is that the trace store can no longer take the
# machine down with it, and that it says so when it truncates.

def test_a_normal_body_is_stored_untouched():
    """The cap must be invisible under a healthy run - a stored prompt that
    is silently not the prompt would poison every trace read after it."""
    from broker.trace import trace_record_to_row

    body = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
    row = trace_record_to_row(_record(request_body=body))
    assert row["request_body"] == body
    assert "_truncated" not in row["request_body"]


def test_a_pathological_body_is_truncated_and_says_so():
    from broker.trace import TRACE_BODY_LIMIT, trace_record_to_row

    body = {"messages": [{"role": "user", "content": "x" * (TRACE_BODY_LIMIT * 3)}]}
    stored = trace_record_to_row(_record(request_body=body))["request_body"]

    assert stored["_truncated"] is True
    assert stored["_original_bytes"] > TRACE_BODY_LIMIT
    # Announced, not silent. A reader who finds a fragment must be able to
    # tell it IS a fragment and how much is missing.
    assert stored["_limit_bytes"] == TRACE_BODY_LIMIT
    # It used to keep `_head`: the first 64 KB of the serialised JSON, cut
    # mid-token, which is a string and not JSON. Measured on data/loop5.db,
    # that shape made 342 of 566 successful calls unreadable to every tool
    # that reads this store. What is stored now is a smaller VALID OBJECT -
    # see broker/tests/test_a_capped_body_is_still_readable.py.
    assert "_head" not in stored
    assert json.loads(json.dumps(stored, default=str)), "must still parse"
    assert len(json.dumps(stored, default=str)) <= TRACE_BODY_LIMIT


def test_the_cap_can_be_lifted_for_a_deliberate_deep_trace(monkeypatch):
    """A machine with the disk to afford it may keep everything."""
    import importlib

    monkeypatch.setenv("KASUKABE_TRACE_BODY_LIMIT", "0")
    import broker.trace as trace_module

    reloaded = importlib.reload(trace_module)
    try:
        body = {"messages": ["y" * 500_000]}
        row = reloaded.trace_record_to_row(_record(request_body=body))
        assert row["request_body"] == body
    finally:
        monkeypatch.delenv("KASUKABE_TRACE_BODY_LIMIT", raising=False)
        importlib.reload(trace_module)


def _record(**over):
    from broker.trace import TraceRecord

    base = dict(
        id="trc_1", agent_id="a0001", payer_agent_id="a0001", model="m",
        requested_model="m", region=None, call_class="cognition",
        purpose="test", request_body={}, response_body=None, stop_reason=None,
        input_tokens=1, output_tokens=1, cost_nano_dollars=0, cents_debited=0,
        latency_ms=1, rate_limit_wait_ms=0, success=True, error_type=None,
        error_message=None, created_at=0.0, tick=0,
    )
    base.update(over)
    return TraceRecord(**base)
