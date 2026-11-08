"""Shared TTL-cache utility - the one memoize implementation.

Consolidates the two byte-identical hand-rolled ``CacheEntry`` classes
(utility catalog cache, asset schema/behavior caches) into a single module.
Modeled on Claude Code's ``memoize.ts`` (studied reference), adapted to this
engine's asyncio single-process model. Monotonic time is a deliberate
improvement over the reference's wall clock; do not "fix" it.

Three invariants every call site inherits (learned the hard way upstream):

1. NEVER cache a failure for the full TTL. ``set()`` is only ever called
   with a successful result, and ``get_or_load`` stores NOTHING when the
   loader raises - the exception propagates, the cache stays empty, the
   next call retries. Caching an error (e.g. a failed credential fetch)
   would serve the failure for the whole TTL.

2. In-flight cold-miss dedup (stampede protection). A shared cache on
   asyncio means an expired hot key can be cold-missed by N concurrent
   tasks at once, each firing its own load. ``get_or_load`` keeps an
   in-flight future per key: the first caller runs the loader, concurrent
   callers await the same future. An ``invalidate()`` that lands while a
   load is in flight discards that load's result (identity guard) - the
   invalidation's intent is "what you knew is wrong", and a load that
   started before it must not resurrect stale data.

3. NO stale-while-revalidate. Serving a stale value while refreshing in the
   background is safe for catalogs but NOT for permission decisions: a
   stale 'allow' past expiry widens a security window, and
   invalidate_behavior's whole contract is "do not serve a stale decision".
   If SWR is ever wanted for a catalog cache it must be a per-instance
   opt-in flag, and the behavior cache must never opt in.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

DEFAULT_TTL_SECONDS = 900.0


class CacheEntry:
    __slots__ = ("data", "expires_at")

    def __init__(self, data: Any, ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self.data = data
        self.expires_at = time.monotonic() + ttl

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class TTLCache:
    """dict[str, CacheEntry] + the get/set/invalidate idiom, in one place.

    ``get`` returns None for missing OR expired (the two are
    indistinguishable to callers by design). ``invalidate("")`` clears
    everything; a non-empty prefix removes matching keys only.
    """

    def __init__(self, default_ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self.default_ttl = default_ttl
        self.entries: dict[str, CacheEntry] = {}
        self.in_flight: dict[str, asyncio.Future] = {}

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, key: str) -> Any | None:
        entry = self.entries.get(key)
        if entry is None or entry.expired:
            return None
        return entry.data

    def set(self, key: str, data: Any, ttl: float | None = None) -> None:
        self.entries[key] = CacheEntry(data, ttl if ttl is not None else self.default_ttl)

    def invalidate(self, prefix: str = "") -> None:
        """Remove entries (all, or by prefix) AND discard matching in-flight
        loads: a load already running when the invalidation landed must not
        store its (now presumed stale) result."""
        if not prefix:
            self.entries.clear()
            self.in_flight.clear()
            return
        for k in [k for k in self.entries if k.startswith(prefix)]:
            del self.entries[k]
        for k in [k for k in self.in_flight if k.startswith(prefix)]:
            del self.in_flight[k]

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[Any]],
        ttl: float | None = None,
    ) -> Any:
        """Cached read with stampede protection (invariants 1 and 2 above).

        Cold miss: the first caller registers an in-flight future and runs
        the loader; concurrent callers await that future. Success stores via
        ``set()`` only if this load is still the registered one (identity
        guard vs invalidate); failure stores nothing and propagates.
        """
        hit = self.get(key)
        if hit is not None:
            return hit

        existing = self.in_flight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self.in_flight[key] = future
        try:
            result = await loader()
        except BaseException as e:
            if self.in_flight.get(key) is future:
                del self.in_flight[key]
            if not future.done():
                future.set_exception(e)
                future.exception()
            raise
        if self.in_flight.get(key) is future:
            self.set(key, result, ttl)
            del self.in_flight[key]
        if not future.done():
            future.set_result(result)
        return result
