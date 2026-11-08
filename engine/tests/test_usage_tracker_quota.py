"""usage_tracker quota snapshot round-trip — the contextvar sink that the
relay uses to carry citadel's pre-computed ``quota`` dict from the provider
out to the ExecutionSummary.

The provider reads ``usage.quota`` off citadel's response and calls
``record_quota_snapshot(q)``; thread.execute() later reads it back with
``tracker.latest_quota()``. These are real objects (no mocks) — the
accumulator mirrors how ``record_usage`` / ``total`` already work.
"""

from __future__ import annotations

from src.herald.usage_tracker import (
    UsageAccumulator,
    record_quota_snapshot,
    usage_tracker_var,
)


def test_accumulator_quota_round_trip():
    """record_quota stores the dict; latest_quota returns it verbatim."""
    acc = UsageAccumulator()
    assert acc.latest_quota() is None

    snapshot = {
        "subscription": {
            "session": {"used": 1000, "limit": 5000, "remaining": 4000, "reset": 18000},
            "weekly": {"used": 1000, "limit": 50000, "remaining": 49000, "reset": 604800},
        },
    }
    acc.record_quota(snapshot)
    assert acc.latest_quota() == snapshot


def test_accumulator_quota_latest_wins():
    """A later snapshot overwrites the earlier one (latest-quota semantics)."""
    acc = UsageAccumulator()
    acc.record_quota({"metered": {"spent_usd": 1.0}})
    acc.record_quota({"metered": {"spent_usd": 2.5}})
    assert acc.latest_quota() == {"metered": {"spent_usd": 2.5}}


def test_accumulator_quota_none_does_not_clear():
    """record_quota(None) is a no-op — a missing snapshot must not wipe a
    previously captured one (citadel may omit quota on some turns)."""
    acc = UsageAccumulator()
    acc.record_quota({"metered": {"spent_usd": 3.0}})
    acc.record_quota(None)
    assert acc.latest_quota() == {"metered": {"spent_usd": 3.0}}


def test_module_record_quota_snapshot_writes_to_active_tracker():
    """The module-level record_quota_snapshot writes to the tracker bound on
    the contextvar — the exact path the provider uses."""
    acc = UsageAccumulator()
    token = usage_tracker_var.set(acc)
    try:
        q = {
            "metered": {
                "spent_usd": 4.25,
                "budget_usd": 10.0,
                "remaining_usd": 5.75,
                "reset": 2592000,
            },
        }
        record_quota_snapshot(q)
        assert acc.latest_quota() == q
    finally:
        usage_tracker_var.reset(token)


def test_module_record_quota_snapshot_noop_without_tracker():
    """No active tracker on the contextvar → record_quota_snapshot is a
    silent no-op (must not raise)."""
    assert usage_tracker_var.get(None) is None
    record_quota_snapshot({"metered": {"spent_usd": 1.0}})
