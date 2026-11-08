"""The series has a result, and the report names every run in it.

`EVAL-22`. `SERIES.md` sections 5 and 6 read "(pending)" and "(filled in
when the series completes)" long after `final1`..`final6` had been run and
graded - the work was on disk and the conclusion was missing, so the file
taught a reader nothing about six real runs. Its title still promised
"then ten on the fixed build"; six ran, and the decision to stop at six
(`kasukabewhatismissing.md` section 5: "do not spend that budget on ten
more six-hour mornings") was taken and never recorded where anyone
reading the series would find it.

`series_report.py` had the matching defect in code: with no arguments it
described `short1..run6`, six of the ten diagnostic runs, chosen when six
was all there were. It also dropped any run whose store was missing
without a word, so its totals line could say "4 runs" after being asked
for sixteen.
"""
from __future__ import annotations

import io
import pathlib
import re
import sys
from contextlib import redirect_stdout

import pytest

import series_report

SERIES_MD = (
    pathlib.Path(series_report.__file__).parent.parent
    / "docs" / "runs" / "SERIES.md"
)


@pytest.fixture(scope="module")
def series_text() -> str:
    return SERIES_MD.read_text(encoding="utf-8")


def test_no_section_is_still_pending(series_text: str) -> None:
    """The two headings, not the word - section 5's prose describes the
    old "(pending)" state on purpose and must stay quotable."""
    for heading in ("## §5", "## §6"):
        start = series_text.index(heading)
        body = series_text[start : start + 400]
        assert "*(pending)*" not in body and "filled in when the series completes" not in body, (
            f"EVAL-22: {heading} is still a placeholder"
        )


def test_the_final_cohort_is_graded(series_text: str) -> None:
    """Every run that happened appears with its own verdict."""
    for run in ("final1", "final2", "final3", "final4", "final5", "final6"):
        assert run in series_text, f"EVAL-22: {run} ran and is not in SERIES.md"


def test_the_two_runs_that_do_not_count_say_why(series_text: str) -> None:
    tail = series_text[series_text.index("## §5") :]
    assert "disputed" in tail and "no data" in tail
    assert "VOID.txt" in tail, (
        "EVAL-22: final5's directory contradicts itself (VOID.txt says 0 calls, "
        "gates.txt grades 299) and the series must resolve that in the open"
    )


def test_the_result_names_the_gate_that_failed(series_text: str) -> None:
    tail = series_text[series_text.index("## §6") :]
    assert "G1" in tail
    assert re.search(r"\b60%", tail), "the bar it failed against is not stated"


def test_the_title_promises_what_was_run(series_text: str) -> None:
    head = series_text[:400]
    assert "then ten on the fixed build" not in head, (
        "EVAL-22: the title still promises ten fixed-build runs; six ran"
    )


def test_the_decision_to_stop_at_six_is_recorded(series_text: str) -> None:
    tail = series_text[series_text.index("## §5") :]
    assert "kasukabewhatismissing" in tail, (
        "EVAL-22: the series stopped early on a recorded recommendation and "
        "SERIES.md never said so - the whole point of the finding"
    )


def test_the_default_is_the_whole_series() -> None:
    assert series_report.THE_SERIES[:2] == ["short1", "run2"]
    for run in ("run7", "run8", "run9", "run10"):
        assert run in series_report.THE_SERIES, (
            f"EVAL-22: {run} is part of the diagnostic ten and the default "
            "run list omits it"
        )
    for run in ("final1", "final5", "final6"):
        assert run in series_report.THE_SERIES, (
            f"EVAL-22: {run} is part of the series and the default omits it"
        )
    assert len(series_report.THE_SERIES) == 16


def test_a_missing_store_is_named_not_dropped(tmp_path, monkeypatch) -> None:
    """The silent-drop half. Asked for three, one exists: the report has to
    say which two are gone rather than print a one-row table."""
    monkeypatch.setattr(
        series_report,
        "one",
        lambda name: (
            {
                "name": name, "calls": 10, "failed": 0, "spend": 1.0, "utterances": 5,
                "directed": 4, "reply_rate": 0.5, "deep": 1, "gatherings": 1,
                "settlements": 0, "alive": "10/10", "effect": 0.6, "turn_effect": 0.5,
                "biggest": 3, "tools": {"world_say": 5},
            }
            if name == "final1"
            else None
        ),
    )
    out = io.StringIO()
    with redirect_stdout(out):
        series_report.main(["final1", "final5", "final6"])
    printed = out.getvalue()
    assert "final5" in printed and "final6" in printed, (
        "EVAL-22: two runs were dropped from the table without a word, so the "
        "totals line describes a series smaller than the one asked for"
    )
    assert "Asked for 3 runs, found 1" in printed


def test_nothing_found_still_says_which(monkeypatch) -> None:
    monkeypatch.setattr(series_report, "one", lambda name: None)
    out = io.StringIO()
    with redirect_stdout(out):
        code = series_report.main(["final1", "final2"])
    assert code == 1
    assert "final1" in out.getvalue()
