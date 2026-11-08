"""250 streams do not queue behind 32 threads.

`INFRA-15`. `converse_stream` pulled each Bedrock event with
`asyncio.to_thread`, which runs on the interpreter's DEFAULT executor:
`min(32, cpu_count + 4)` workers. Every one of those workers is blocked
on a socket read for as long as the model takes to produce the next
delta, so the pool is not doing work - it is holding places in a queue.

At 20 residents that is invisible. At the 250-resident design target, 250
concurrent streams each pulling hundreds of deltas contend for at most 32
slots, and the wait shows up in NO metric: not `rate_limit_wait_ms`
(nothing throttled), not Bedrock's reported latency (Bedrock answered) -
just turns quietly crossing the 120-second dispatch limit. Run 23 already
discarded 10 turns that way, and nobody could say why.
"""
from __future__ import annotations

import asyncio
import os
import threading

import pytest

from broker.bedrock import STREAM_POOL_WORKERS, Boto3BedrockGateway


def test_the_pool_is_sized_for_the_town_not_the_cpu() -> None:
    """These threads wait on sockets; they do not compute. The usual
    "don't exceed core count" rule is the wrong one here."""
    default_executor_size = min(32, (os.cpu_count() or 1) + 4)
    assert STREAM_POOL_WORKERS > default_executor_size, (
        f"INFRA-15: the stream pool allows {STREAM_POOL_WORKERS} concurrent "
        f"reads and the interpreter's default already allowed "
        f"{default_executor_size} - nothing has changed"
    )
    assert STREAM_POOL_WORKERS >= 250, (
        "the design target is 250 residents, so a pool below that puts the "
        "last of them back in the queue this exists to empty"
    )


def test_it_is_one_pool_for_the_whole_process() -> None:
    """A pool per gateway would be six pools for six lanes, which is six
    times the threads for the same work."""
    a = Boto3BedrockGateway.__new__(Boto3BedrockGateway)
    b = Boto3BedrockGateway.__new__(Boto3BedrockGateway)
    assert a.stream_pool is b.stream_pool


def test_the_pool_is_built_once() -> None:
    gateway = Boto3BedrockGateway.__new__(Boto3BedrockGateway)
    assert gateway.stream_pool is gateway.stream_pool


@pytest.mark.asyncio
async def test_more_streams_run_at_once_than_the_default_would_allow() -> None:
    """The finding, measured rather than asserted about a constant.

    Forty readers that each block until all forty have arrived. On the
    default executor - 32 at most on any machine this runs on - the
    fortieth never arrives and this deadlocks, which is exactly the
    queueing the finding describes, at a scale a test can reach.
    """
    concurrent = 40
    arrived = threading.Barrier(concurrent, timeout=20)
    pool = Boto3BedrockGateway.__new__(Boto3BedrockGateway).stream_pool
    loop = asyncio.get_running_loop()

    def wait_for_everyone() -> int:
        return arrived.wait()

    try:
        await asyncio.wait_for(
            asyncio.gather(*[
                loop.run_in_executor(pool, wait_for_everyone)
                for _ in range(concurrent)
            ]),
            timeout=25,
        )
    except (asyncio.TimeoutError, threading.BrokenBarrierError):
        pytest.fail(
            f"INFRA-15: {concurrent} concurrent stream reads could not all "
            "start, so at 250 residents the pool is the ceiling and the wait "
            "appears in no metric"
        )


def test_the_size_can_be_moved_without_a_code_change() -> None:
    """A host with less memory, or a run with a much larger cast, needs to
    be able to say so - and the number should be in the run manifest
    (INFRA-09) rather than baked in."""
    import inspect

    from broker import bedrock

    source = inspect.getsource(bedrock)
    assert "KASUKABE_STREAM_POOL_WORKERS" in source


@pytest.mark.asyncio
async def test_streaming_still_yields_every_event() -> None:
    """Changing which executor reads the stream must not change what the
    stream produces."""

    class _Client:
        def converse_stream(self, **kwargs):
            return {"stream": iter([{"a": 1}, {"b": 2}, {"c": 3}])}

    gateway = Boto3BedrockGateway.__new__(Boto3BedrockGateway)
    gateway.client = _Client()
    gateway.resolved_request = lambda request: request

    seen = [event async for event in gateway.converse_stream({})]
    assert seen == [{"a": 1}, {"b": 2}, {"c": 3}]
