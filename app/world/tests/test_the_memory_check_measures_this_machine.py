"""The memory check has never measured anything on Windows.

`memory_budget.measured_mb_per_engine()` shells out to `ps`. Windows has
no `ps`, so it returns None, and `preflight` falls back to
`MB_PER_ENGINE_PROCESS = 550` - a figure taken on a Linux box under load.
The constant's own comment already says what that means:

    "The constant is not wrong; it is wrong HERE, and the machine already
    knows better."

It said that about a Mac, where the same ten engines measured 33 MB each
against the 550 the check assumed. The identical hole exists on Windows
and has never been closed, so every capacity decision made on this machine
has been made against a number from a different one.

MEASURED, on the live engines of the validation series:

    a cluster of ten engines at 81-94 MB each
    total 1,069 MB across 26 python processes

About 82 MB, not 550. The constant is 6.7x too high here, which is the
difference between "fifty engines need 27 GB and this box has 23" and
"fifty engines need 4 GB" - and that is the decision in front of us.

WHY THIS IS NOT SKIPPING THE CHECK. The check is right to exist and right
to refuse a run that cannot fit; it has been refusing on the wrong
evidence. Making it measure is the fix. Raising the constant, lowering it,
or passing a flag to bypass it would each replace one guess with another.

NO NEW DEPENDENCY. Same rule the module already keeps: Win32 through
ctypes, `ps` everywhere else. `world/resource_log.py` reads exactly these
numbers per agent and this reuses its reader rather than writing a second
one that could disagree with it.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from world.memory_budget import (
    MB_PER_ENGINE_PROCESS,
    measured_mb_per_engine,
)


def test_a_machine_with_no_engines_running_measures_nothing() -> None:
    """The normal pre-boot case, and the reason the constant still
    exists. A marker nothing matches must answer None, not zero."""
    assert measured_mb_per_engine(marker="a-marker-nothing-will-ever-match") is None


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows hole")
def test_windows_can_now_measure_at_all() -> None:
    """The whole point. Before this, every call on Windows returned None
    because `ps` is not there - so the check silently used a Linux
    constant and nobody could tell from the output that it had.

    Measured against this very interpreter, so the reading cannot be a
    fixture agreeing with itself.
    """
    seen = measured_mb_per_engine(marker="python")
    assert seen is not None, "Windows still cannot measure its own processes"
    count, mean_mb = seen
    assert count >= 1
    assert mean_mb > 1, "a live Python process uses more than a megabyte"


def test_the_measurement_and_the_constant_disagree_and_that_is_the_finding() -> None:
    """Not a test of a number, a test that the two are compared at all.

    If a future change made `measured_mb_per_engine` return the constant,
    every one of these tests would pass while the check went back to
    guessing.
    """
    seen = measured_mb_per_engine(marker="python")
    if seen is None:
        pytest.skip("nothing running to measure")
    _, mean_mb = seen
    assert mean_mb != MB_PER_ENGINE_PROCESS, (
        "the measurement is exactly the fallback constant, which means it "
        "is not a measurement"
    )


def test_measuring_never_raises() -> None:
    """It runs inside preflight, before a run. A capacity check that
    crashes has stopped the run it was checking."""
    assert measured_mb_per_engine(marker="\x00 not a real marker \x00") is None


def test_it_takes_no_new_dependency() -> None:
    """memory_budget has a standing no-psutil rule, stated in
    `available_mb`: a preflight check that requires a package installed
    before it can run is a check that does not run on the machine you
    most need it on."""
    import pathlib

    import world.memory_budget as budget

    source = pathlib.Path(budget.__file__).read_text(encoding="utf-8")
    assert "import psutil" not in source


def test_it_reuses_the_resource_logs_reader() -> None:
    """One reader, so the per-agent log and the capacity check can never
    disagree about what a process costs. Two implementations of the same
    measurement is how a report and a gate end up telling different
    stories about one run."""
    import pathlib

    import world.memory_budget as budget

    source = pathlib.Path(budget.__file__).read_text(encoding="utf-8")
    assert "resource_log" in source


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX path")
def test_posix_still_uses_ps() -> None:
    """The platform that already worked must keep working, unchanged."""
    assert subprocess.run(["which", "ps"], capture_output=True).returncode == 0
    assert measured_mb_per_engine(marker="python") is not None
