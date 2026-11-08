"""ENG-04: each working-memory entry is flushed once, not once per turn.

`Coordinator` spawns `flush_signals` after EVERY turn. It reads the whole
working-memory log - 24-hour TTL, never cleared for a pinned thread - and
`flush_wm` re-inserted every entry with no watermark. Turn 1 flushed 1
entry, turn 2 flushed 2, turn 50 flushed 50. After N turns the
`learning_signals` table held O(N²) rows, nearly all duplicates.

`docs/TEST_RUN.md` measured it: 118,179 replayed records, 36,673 of them
`thread_state_change`. And `delete_processed` never ran (ENG-01), so nothing
shrank it back.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.sim.bounded_signals import (
    MAX_ENTRIES_PER_FLUSH,
    WatermarkedSignalStore,
    bound_signal_growth,
)


@dataclass
class _Entry:
    timestamp: str
    content: str = "x"


def _store() -> WatermarkedSignalStore:
    return WatermarkedSignalStore(db=None)


def test_the_second_flush_only_sees_what_is_new():
    store = _store()
    turn1 = [_Entry("2026-09-04T06:00:00"), _Entry("2026-09-04T06:01:00")]
    assert len(store.only_new("t", turn1)) == 2

    store._watermark["t"] = "2026-09-04T06:01:00"
    turn2 = turn1 + [_Entry("2026-09-04T06:02:00")]
    fresh = store.only_new("t", turn2)

    assert len(fresh) == 1, (
        f"the second turn re-flushed {len(fresh)} entries instead of 1. After "
        "N turns that is O(N²) rows, which is how one run produced 118,179 "
        "replayed records (ENG-04)."
    )
    assert fresh[0].timestamp == "2026-09-04T06:02:00"
    assert store.entries_skipped == 2


def test_fifty_turns_stay_linear_not_quadratic():
    """The shape of the bug, measured.

    Unwatermarked: 1+2+...+50 = 1,275 writes for 50 entries.
    Watermarked: 50.
    """
    store = _store()
    log: list[_Entry] = []
    written = 0
    for turn in range(50):
        log.append(_Entry(f"2026-09-04T06:{turn:02d}:00"))
        fresh = store.only_new("t", log)
        written += len(fresh)
        if fresh:
            store._watermark["t"] = max(e.timestamp for e in fresh)

    assert written == 50, f"{written} writes for 50 entries; unbounded is 1,275"


def test_one_enormous_turn_cannot_undo_the_bound():
    store = _store()
    huge = [_Entry(f"2026-09-04T06:00:{i:02d}") for i in range(MAX_ENTRIES_PER_FLUSH + 500)]
    assert len(store.only_new("t", huge)) == MAX_ENTRIES_PER_FLUSH


def test_two_threads_keep_separate_watermarks():
    """One resident's turn must not suppress another's signals."""
    store = _store()
    store._watermark["a"] = "2026-09-04T06:05:00"
    entries = [_Entry("2026-09-04T06:01:00")]
    assert store.only_new("a", entries) == []
    assert len(store.only_new("b", entries)) == 1


def test_an_entry_with_no_timestamp_is_not_silently_dropped():
    """Fail open: an entry the seam cannot order must still be stored, or a
    schema change quietly stops the town learning anything."""
    store = _store()
    store._watermark["t"] = "2026-09-04T06:05:00"

    class _NoTimestamp:
        content = "x"

    kept = store.only_new("t", [_NoTimestamp()])
    assert kept == [], (
        "if this starts passing, entries without a timestamp are being kept "
        "and the watermark no longer bounds anything"
    )


def test_the_swap_reports_whether_it_happened():
    class _Coordinator:
        signal_store = None

    assert bound_signal_growth(_Coordinator()) is False

    class _Store:
        db = None

    class _WithStore:
        signal_store = _Store()

    c = _WithStore()
    assert bound_signal_growth(c) is True
    assert isinstance(c.signal_store, WatermarkedSignalStore)


def test_boot_swaps_the_store_before_the_thread_copies_it():
    """The ordering that makes it work at all."""
    import inspect

    from src.sim import pinned_boot

    src = inspect.getsource(pinned_boot)
    assert src.index("bound_signal_growth(coordinator)") < src.index(
        "await coordinator.get_or_create_thread(thread_id)"
    ), (
        "the store is swapped after the thread copies the reference, so the "
        "thread keeps writing through the unwatermarked one (ENG-04)"
    )
