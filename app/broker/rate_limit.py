"""Per-model token buckets, 100 requests/min, fair FIFO queueing.

DESIGN.md section 9: "100 RPM per model (200 combined) is the binding
constraint" and the TASK spec: "Per-model token buckets at 100
requests/min with a fair async queue."

TokenBucket.acquire() holds its internal asyncio.Lock for the ENTIRE wait,
not just while checking the counter. That is the fairness mechanism, not
an accident: asyncio.Lock wakes waiters in strict FIFO arrival order, so
whichever coroutine called acquire() first is the one holding the lock
(and therefore the one that gets the next token) first. A design that
released the lock while sleeping and re-acquired it afterward would let a
coroutine that arrived later, but happened to get rescheduled sooner,
jump the queue - exactly the unfairness DESIGN.md is asking this module
to avoid across 250 agents contending for the same 100 RPM.

`clock` and `sleep` are injectable so tests exercise real wait-and-refill
behavior without a real 60-second sleep - see broker/tests/test_rate_limit.py.

BrokerService no longer routes every model through RateLimiterRegistry's
lazy per-model-id dict: broker/lanes.py builds exactly six TokenBuckets
directly (one per (model, region) lane, a fixed known set at boot, not a
dynamic set discovered per request), so it has no need for this class's
lazy-creation machinery. RateLimiterRegistry stays here, still exercised
by its own tests below, as a generic "N independent buckets keyed by
string id" utility - lanes.py just happens to build its fixed six buckets
directly with TokenBucket instead of through it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class TokenBucket:
    """One model's rate limit: `capacity` tokens, refilled continuously
    at `capacity` per `period_s`, never exceeding `capacity`."""

    def __init__(
        self,
        capacity: int,
        period_s: float = 60.0,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if capacity <= 0:
            raise ValueError(f"capacity={capacity} - must be greater than 0")
        if period_s <= 0:
            raise ValueError(f"period_s={period_s} - must be greater than 0")
        self.capacity = capacity
        self.refill_rate_per_s = capacity / period_s
        self.tokens = float(capacity)
        self.clock = clock
        self.sleep = sleep
        self.updated_at = clock()
        self.lock = asyncio.Lock()
        # Observability only (broker/trace.py's broker.rpm.throttled event):
        # counts calls that had to wait at all, distinct from the total
        # call count the caller already tracks.
        self.throttled_count = 0

    def refill(self) -> None:
        now = self.clock()
        elapsed = now - self.updated_at
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate_per_s)
            self.updated_at = now

    def peek_available(self) -> bool:
        """Non-blocking: would `acquire()` return immediately right now?

        Refills first (so a bucket idle since its last acquire reports its
        TRUE current balance, not a stale one) then checks without
        consuming a token. No lock needed: this method never awaits, and
        asyncio's cooperative scheduling means nothing else can run
        between the refill and the check - see broker/lanes.py's
        LanePool.select, which peeks up to six buckets to find one with
        instant capacity before falling back to a blocking acquire() on
        whichever lane is next in preference order.
        """
        self.refill()
        return self.tokens >= 1.0

    async def acquire(self) -> float:
        """Block until one token is available, then consume it.

        Returns the number of seconds this call waited (0.0 if a token
        was immediately available) - service.py folds this into the
        trace record's latency breakdown and emits broker.rpm.throttled
        when it's nonzero.
        """
        waited = 0.0
        async with self.lock:
            while True:
                self.refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    if waited > 0.0:
                        self.throttled_count += 1
                    return waited
                deficit = 1.0 - self.tokens
                wait_s = deficit / self.refill_rate_per_s
                await self.sleep(wait_s)
                waited += wait_s


# INFRA-24. `RateLimiterRegistry` was deleted here on 2026-09-05.
#
# Its own docstring above argued for keeping it as a generic "N
# independent buckets keyed by string id" utility. That argument is
# recorded and overruled: `lanes.py` builds exactly six TokenBuckets
# directly, it had no other caller, and a utility with no user is a
# test suite that passes forever about code nobody runs - which is the
# finding. `TokenBucket`, which lanes.py DOES use, stays.
#
# `git show` it if a dynamic per-model-id set ever comes back.
