"""TokenBucket: rate limiting + FIFO fairness,
tested with an injected fake clock and sleep so nothing here waits on
real time (CONVENTIONS.md: tests must be fast, deterministic)."""

from __future__ import annotations

import asyncio

import pytest

from broker.rate_limit import TokenBucket


class FakeClock:
    """Monotonic clock under test control: advances only when told to."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_fake_sleep(clock: FakeClock):
    """A `sleep` that advances the fake clock by exactly the requested
    duration instead of actually waiting - acquire()'s wait-then-refill
    logic becomes purely a function of the clock, not wall time. Still
    does one real `asyncio.sleep(0)` cooperative yield (zero wall-clock
    cost) so concurrent acquire() calls genuinely interleave through the
    event loop - required for test_acquire_grants_tokens_in_strict_fifo_
    arrival_order to exercise real lock contention instead of every
    waiter completing synchronously before the next one is even created.
    """

    async def fake_sleep(seconds: float) -> None:
        clock.advance(seconds)
        await asyncio.sleep(0)

    return fake_sleep


@pytest.mark.asyncio
async def test_acquire_does_not_wait_when_tokens_available() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=100, period_s=60.0, clock=clock, sleep=make_fake_sleep(clock))

    waited = await bucket.acquire()

    assert waited == 0.0
    assert bucket.tokens == pytest.approx(99.0)


@pytest.mark.asyncio
async def test_acquire_drains_capacity_then_waits() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=3, period_s=60.0, clock=clock, sleep=make_fake_sleep(clock))

    for _ in range(3):
        waited = await bucket.acquire()
        assert waited == 0.0

    # 4th request: bucket is empty, refill rate is 3/60 = 0.05 tokens/s,
    # so exactly 20s must pass before one token is available.
    waited = await bucket.acquire()
    assert waited == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_refill_is_continuous_not_step() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, period_s=60.0, clock=clock, sleep=make_fake_sleep(clock))

    await bucket.acquire()  # drains the single token
    clock.advance(30.0)  # half the refill period -> half a token
    bucket.refill()
    assert bucket.tokens == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_tokens_never_exceed_capacity() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=5, period_s=60.0, clock=clock, sleep=make_fake_sleep(clock))

    clock.advance(10_000.0)  # far more than enough to refill many times over
    bucket.refill()

    assert bucket.tokens == 5.0


@pytest.mark.asyncio
async def test_throttled_count_only_increments_on_a_real_wait() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, period_s=60.0, clock=clock, sleep=make_fake_sleep(clock))

    await bucket.acquire()  # immediate, not throttled
    assert bucket.throttled_count == 0

    await bucket.acquire()  # must wait for refill
    assert bucket.throttled_count == 1


@pytest.mark.asyncio
async def test_acquire_grants_tokens_in_strict_fifo_arrival_order() -> None:
    """The fairness requirement: 250 agents contending for one model's
    100 RPM bucket must be served in the order they asked, not in
    whatever order the event loop happens to wake coroutines."""
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, period_s=60.0, clock=clock, sleep=make_fake_sleep(clock))

    order: list[int] = []

    async def waiter(n: int) -> None:
        await bucket.acquire()
        order.append(n)

    # Drain the single token, then immediately start waiter 0 so it's the
    # one holding bucket.lock (across its own await inside fake_sleep)
    # when waiters 1-4 arrive and are forced to genuinely queue on it.
    await bucket.acquire()
    tasks = [asyncio.ensure_future(waiter(0))]
    await asyncio.sleep(0)  # let waiter 0 run up to its suspension inside fake_sleep

    for n in range(1, 5):
        tasks.append(asyncio.ensure_future(waiter(n)))
        await asyncio.sleep(0)  # let each subsequent waiter reach the lock and queue on it

    await asyncio.gather(*tasks)

    assert order == [0, 1, 2, 3, 4]



# INFRA-24: the RateLimiterRegistry tests were removed with the class on
# 2026-09-05. `lanes.py` builds its six TokenBuckets directly, so the
# registry had no caller and these passed forever about nothing that runs.
