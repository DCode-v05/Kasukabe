"""ENG-04: stop re-flushing the whole working memory after every turn.

`Coordinator` spawns `flush_signals` after **every** turn. That reads
`self.memory.read(thread_id)` - the WHOLE working-memory log, which has a
24-hour TTL and is never cleared for a pinned thread - and `flush_wm`
re-inserts every entry it finds, with no watermark.

So turn 1 flushes 1 entry, turn 2 flushes 2, turn 50 flushes 50. After N
turns the `learning_signals` table holds O(N²) rows, almost all of them
duplicates of rows already there. `docs/TEST_RUN.md` measured the result:
**118,179 replayed records**, 36,673 of them `thread_state_change`.

Three costs, all silent:

  * the table, and every query over it, grows quadratically in turn count;
  * the learning pipeline reads that table, so its batches get larger and
    more repetitive the longer a resident lives - which is the opposite of
    what consolidation is for;
  * `delete_processed` never runs (ENG-01), so nothing ever shrank it back.

The fix is a watermark per thread: remember the timestamp of the last entry
flushed, and skip anything at or before it. Plus a hard cap, so one enormous
turn cannot undo the bound.

`src/learning_centre/` is frozen under ROADMAP Law 1, so this subclasses the
store and is swapped in at boot - the same technique `bounded_history` uses
for the conversation store.
"""

from __future__ import annotations

import logging
import os

from src.learning_centre.signals import SignalStore

logger = logging.getLogger(__name__)

# The most WM entries one flush may write. A turn that produces more than
# this is not a turn, it is a loop, and flushing all of it would put the
# quadratic growth back by another route.
MAX_ENTRIES_PER_FLUSH = int(os.environ.get("KASUKABE_MAX_SIGNAL_FLUSH", "200"))


class WatermarkedSignalStore(SignalStore):
    """A SignalStore that flushes each WM entry once.

    Everything else - `write`, `write_batch`, the credential guard, the
    queries - is the parent's, unchanged. Only `flush_wm` narrows, because
    that is the only method that re-reads history it has already stored.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # thread_id -> the timestamp of the last entry flushed for it.
        self._watermark: dict[str, str] = {}
        # Counted, so a run can say how much duplication it avoided rather
        # than leaving it to be inferred from a table size.
        self.entries_skipped = 0

    async def flush_wm(self, thread_id: str, wm_items: list, status: str = "completed") -> int:
        fresh = self.only_new(thread_id, wm_items)
        if not fresh:
            return 0
        stored = await super().flush_wm(thread_id, fresh, status=status)
        # Advance only after the write succeeded: a flush that raised must
        # be retried, not silently skipped on the next turn.
        self._watermark[thread_id] = max(
            (getattr(i, "timestamp", "") or "") for i in fresh
        ) or self._watermark.get(thread_id, "")
        return stored

    def only_new(self, thread_id: str, wm_items: list) -> list:
        """The entries this thread has not already flushed (ENG-04)."""
        mark = self._watermark.get(thread_id)
        if mark is not None:
            before = len(wm_items)
            wm_items = [
                i for i in wm_items
                if (getattr(i, "timestamp", "") or "") > mark
            ]
            self.entries_skipped += before - len(wm_items)

        if len(wm_items) > MAX_ENTRIES_PER_FLUSH:
            logger.warning(
                "%s produced %d working-memory entries in one turn; flushing "
                "the most recent %d. A turn this size is a loop, and flushing "
                "all of it would restore the quadratic growth by another "
                "route (ENG-04).",
                thread_id, len(wm_items), MAX_ENTRIES_PER_FLUSH,
            )
            wm_items = wm_items[-MAX_ENTRIES_PER_FLUSH:]
        return wm_items


def bound_signal_growth(coordinator) -> bool:
    """Swap the coordinator's signal store for a watermarked one.

    Must run BEFORE `get_or_create_thread`: the thread copies the reference,
    so a swap afterwards leaves the thread writing through the old store.
    Returns whether a store was actually replaced.
    """
    existing = getattr(coordinator, "signal_store", None)
    if existing is None:
        return False
    bounded = WatermarkedSignalStore(existing.db)
    coordinator.signal_store = bounded
    return True
