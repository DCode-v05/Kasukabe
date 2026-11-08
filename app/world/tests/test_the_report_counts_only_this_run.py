"""Session logs accumulate; the report treated them as one run.

`SEPTEMBER_HOME/<agent>/session_log.jsonl` is APPENDED to and never
cleared, so one file holds every run that machine has ever done. When this
was found, `n02`'s log held 59 turns spanning three days, of which **15**
belonged to the run being reported.

Every tally the report read from a session log was therefore the sum of all
of them. Run 24 reported **1,211 tool calls across 34 verbs**; the run
itself made **82 across 14**. The trace-store figures (calls, spend, events,
transactions) were always correct — these were not, and they are the ones
quoted as "what the town did".

The run manifest CORE-24 writes at boot carries `started_at`, so the report
can scope itself. Where it cannot, it says so on stderr rather than
quietly over-reporting.
"""
from __future__ import annotations

import world.run_report as rr


def test_with_no_start_time_everything_counts():
    """Backwards-compatible: an old trace with no manifest still renders."""
    rr.RUN_STARTED_AT = 0.0
    assert rr.belongs_to_this_run({"recorded_at": 1}) is True


def test_an_older_turn_is_excluded():
    rr.RUN_STARTED_AT = 1000.0
    assert rr.belongs_to_this_run({"recorded_at": 999.0}) is False


def test_a_turn_from_this_run_is_kept():
    rr.RUN_STARTED_AT = 1000.0
    assert rr.belongs_to_this_run({"recorded_at": 1000.0}) is True
    assert rr.belongs_to_this_run({"recorded_at": 5000.0}) is True


def test_a_turn_with_no_timestamp_is_kept():
    """Fail open. A log line the report cannot date must not vanish from
    the record - under-reporting a run is as bad as over-reporting it."""
    rr.RUN_STARTED_AT = 1000.0
    assert rr.belongs_to_this_run({"actions": []}) is True
    assert rr.belongs_to_this_run({"recorded_at": "not-a-number"}) is True


def test_every_session_log_reader_is_scoped():
    """Three readers walk those files. One unscoped reader is enough to put
    another run's numbers back into this one's report."""
    import inspect

    src = inspect.getsource(rr)
    readers = src.count("session_log.jsonl")
    guards = src.count("belongs_to_this_run(")
    assert guards >= readers, (
        f"{readers} session-log readers and only {guards - 1} guards; an "
        "unscoped one puts other runs back into this report"
    )
