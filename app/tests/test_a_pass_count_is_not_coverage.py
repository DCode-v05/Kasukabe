"""A run says what it could not check, not just what passed.

`INFRA-30`. "534 passed" required three undocumented preconditions: a
`/data` directory for collection, network access at first use for nine
research tests, and a checked-out `smallville/` for the whole geography
chain. None of those is a defect in the code under test. Together they
meant the pass count was a claim about the HOST as much as about the
repo, and a reader had no way to tell which parts were which.

pytest already knows every skip and prints them with `-rs`, and nobody
passes `-rs`. So the same information goes where a reader actually looks
- the bottom of the run - grouped by reason, so twelve skips for one
missing prerequisite read as one prerequisite rather than twelve
mysteries.

The engine suite on this host is the live example: 40 failures in
`test_sed_integration.py` and `test_september_home.py`, both Windows
platform semantics rather than defects, in a suite whose own docs quote a
pass count with no note of what the number assumes.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

APP = Path(__file__).resolve().parent.parent


def _run_child(body: str) -> str:
    """A pytest run under `app/`, so `app/conftest.py`'s hooks load.

    Under `app/` and not in a tmp dir: conftest collection walks the test
    file's ancestors, and a tmp path is not one of app's descendants - the
    same thing GROW-27's test had to work around.
    """
    scratch = APP / "_infra30_scratch"
    scratch.mkdir(exist_ok=True)
    test_file = scratch / "test_child.py"
    test_file.write_text(textwrap.dedent(body), encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-q",
             "-p", "no:cacheprovider"],
            cwd=APP, capture_output=True, text=True, timeout=300,
        )
        return proc.stdout
    finally:
        test_file.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)


def test_a_skip_is_reported_at_the_end() -> None:
    out = _run_child('''
        import pytest

        def test_needs_something():
            pytest.skip("this host has no widget")
    ''')
    assert "SKIPPED, AND WHY" in out, (
        "INFRA-30: a run skipped a test and reported only its pass count, "
        f"so the reader cannot tell coverage from host luck:\\n{out[-1500:]}"
    )
    assert "this host has no widget" in out


def test_skips_are_grouped_by_reason() -> None:
    """Twelve skips for one missing prerequisite are one prerequisite, and
    a flat list of twelve reads as twelve problems."""
    out = _run_child('''
        import pytest

        @pytest.mark.parametrize("n", range(5))
        def test_needs_the_same_thing(n):
            pytest.skip("the tileset is not mounted")

        def test_needs_something_else():
            pytest.skip("no network")
    ''')
    import re

    grouped = re.search(r"^\s*(\d+)\s+the tileset is not mounted", out, re.M)
    assert grouped, (
        f"INFRA-30: five skips for one prerequisite are not grouped, so they "
        "INFRA-30: five skips for one prerequisite are not grouped, so "
        "they read as five separate problems. Output: " + out[-800:]
    )
    assert grouped.group(1) == "5"
    assert "no network" in out, "the second, different reason was dropped"


def test_a_clean_run_stays_quiet() -> None:
    """A summary that prints on every run is one people stop reading."""
    out = _run_child('''
        def test_fine():
            assert True
    ''')
    assert "SKIPPED, AND WHY" not in out
    assert "NOT PROVEN IN THIS RUN" not in out


def test_the_summary_says_what_the_number_means() -> None:
    """The count alone invites the wrong reading. The sentence under it is
    the point of the whole hook."""
    out = _run_child('''
        import pytest

        def test_needs_something():
            pytest.skip("no widget here")
    ''')
    assert "host preconditions, not results" in out


def test_an_uninteresting_skip_is_not_noise() -> None:
    """A platform branch that genuinely does not apply says nothing about
    coverage. The ignore list is short on purpose - a long one is how this
    summary stops being read."""
    # By PATH, not by name. `from conftest import ...` resolves to
    # whichever module is already registered as "conftest" in sys.modules,
    # and this repo has several - running `broker/ game/ supabase/ tests/`
    # together picked up `game/console/tests/conftest.py` and this failed
    # with ImportError while passing on its own. A test that only passes
    # when run alone is a test that will be believed and should not be.
    import importlib.util

    root = Path(__file__).resolve().parents[1] / "conftest.py"
    spec = importlib.util.spec_from_file_location("_root_conftest", root)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    UNINTERESTING_SKIPS = module.UNINTERESTING_SKIPS

    assert len(UNINTERESTING_SKIPS) <= 3, (
        "INFRA-30: the ignore list is growing, and every entry is a reason "
        "a reader will not be told something went unchecked"
    )
