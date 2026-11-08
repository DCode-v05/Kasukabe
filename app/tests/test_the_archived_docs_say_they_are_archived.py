"""A stale document says so on its first line.

`EVAL-26`. Two documents in `docs/` read as current and are not:

  * `docs/RUN_REPORT.md` is 57,865 lines of GENERATED output.
    `run_report.py` refreshes it in place while a run is going and stops
    when the run does, so the file is one run caught mid-flight in August
    2026 and left there.
  * `docs/TEST_RUN.md` is hand-written and carries figures - notably "98%
    of turns sent with no tools" - measured before the tool-surface and
    prompt work of runs 19-24, and contradicted by later runs without the
    document ever saying so.

Neither was deleted. `RUN_REPORT.md` is the only surviving record that a
run ever crossed a day boundary (`docs/review/14` cites tick 1830 / day
12 from it), and `TEST_RUN.md` is the hand-written history of what was
tried and what broke - which is worth more than its stale numbers cost,
as long as a reader is told which is which.

That "as long as" is what this file protects. A banner is only worth
anything while it is there, and nothing else in this repo would notice it
going.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"

ARCHIVED = {
    "RUN_REPORT.md": "the only surviving record of a run crossing a day boundary",
    "TEST_RUN.md": "the hand-written history of what was tried and what broke",
}


@pytest.mark.parametrize("name", sorted(ARCHIVED))
def test_the_first_line_is_the_warning(name: str) -> None:
    """First line, not somewhere in the middle: a reader who opens this
    file and starts reading must be told before they read anything else."""
    path = DOCS / name
    if not path.exists():
        pytest.skip(f"{name} is not in this checkout")
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert "ARCHIVED" in first, (
        f"EVAL-26: {name} reads as current and is not. Its figures predate "
        "the runs that contradict them, and nothing on the page says so"
    )
    assert "EVAL-26" in first, (
        "the banner does not name the finding, so a reader who wonders why "
        "it is there has nowhere to look"
    )


@pytest.mark.parametrize("name", sorted(ARCHIVED))
def test_it_says_when_it_was_true(name: str) -> None:
    """"This is old" without a date is not much better than nothing - the
    question a reader has is "older than what"."""
    path = DOCS / name
    if not path.exists():
        pytest.skip(f"{name} is not in this checkout")
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:20])
    assert "2026" in head


@pytest.mark.parametrize("name,why", sorted(ARCHIVED.items()))
def test_it_says_why_it_was_kept(name: str, why: str) -> None:
    """A stale document with no stated reason to exist gets deleted by the
    next person to notice it - and both of these are the only copy of
    something."""
    path = DOCS / name
    if not path.exists():
        pytest.skip(f"{name} is not in this checkout")
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:24]).lower()
    assert any(word in head for word in ("kept", "history", "surviving", "record")), (
        f"EVAL-26: {name} is labelled stale and does not say why it is "
        f"still here ({why})"
    )


def test_the_contradicted_figure_is_named() -> None:
    """Generic caveats get skimmed. The one number a reader is most likely
    to quote is the one to name."""
    path = DOCS / "TEST_RUN.md"
    if not path.exists():
        pytest.skip("TEST_RUN.md is not in this checkout")
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:12])
    assert "98%" in head, (
        "EVAL-26: the banner warns in general terms and leaves the specific "
        "contradicted figure to be discovered further down the page"
    )
