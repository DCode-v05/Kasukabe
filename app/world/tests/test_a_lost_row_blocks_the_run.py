"""`14`.7: the recorder's drop counter blocks a run at preflight.

A recorder that has already lost a row does not deserve three more chances.
Run 16 dropped 125 records - one resident's entire existence - and the run
finished, reported, and was graded, with a WARNING buried in a 4,000-line
log as the only sign. Everything the town did after the first drop was being
measured against a record already known to be incomplete.

The existing `halt_after_consecutive_failures = 3` stops a run mid-flight.
This makes the same fact visible BEFORE the run is trusted.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

from world.preflight import Check
from world.run_genesis import GenesisRunner  # noqa: F401


def test_a_clean_recorder_passes():
    assert Check("The recorder has lost nothing", True, "0 row(s) dropped").status == "PASS"


def test_a_dropped_row_is_a_blocking_failure_not_a_warning():
    """`critical` defaults True, so this FAILs rather than WARNs - which is
    the whole point of moving it to preflight."""
    check = Check("The recorder has lost nothing", False, "125 row(s) dropped")
    assert check.critical is True
    assert check.status == "FAIL", (
        "a lost row only WARNs, so a run measured against an incomplete "
        "record still counts as evidence"
    )


def test_preflight_actually_reads_the_drop_counter():
    """The call site, checked the way every predicate in this work now is."""
    from world.run_genesis import build_embedded_world_app

    # The check lives in the `/world/preflight` route, which is a nested
    # function inside the app builder - so read the builder.
    src = inspect.getsource(build_embedded_world_app)
    assert "rows_dropped" in src, (
        "preflight never asks the recorder whether it has lost anything, so "
        "a run that already dropped rows boots and is trusted (14.7)"
    )
    assert "The recorder has lost nothing" in src


def test_the_mid_flight_halt_is_still_there():
    """Preflight is the new gate, not a replacement for the old one: a
    recorder can start clean and break later."""
    from world.wiring import TraceStore

    src = textwrap.dedent(inspect.getsource(TraceStore))
    assert "halt_after_consecutive_failures" in src
