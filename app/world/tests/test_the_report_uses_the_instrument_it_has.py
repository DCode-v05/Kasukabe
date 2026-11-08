"""Every reader `stakes_report` provides ends up in a report.

`EVAL-05`. `world/stakes_report.py` is the evaluation spec's section 6,
written against the event stream a completed run produces, and only its
own test file imported it. `run_report.py` re-implemented three of its
six sections - wrongly, which is EVAL-01, where five of six event names
were wrong and the report said "Nobody posted a price" over twelve real
posts - and never implemented Time or Diffusion at all. A grep for
`time_spent` or `diffusion` in `run_report.py` returned nothing.

So T4 (the share of intentions actually fulfilled, which is also
business-readiness figure R4) and T5 ("a tool held by three residents")
have never been read off a run. Not "were low"; never computed.

This is the seventh complete-and-unwired mechanism in this review, and
the one where it stings most: the instrument that would have shown the
other six going unmeasured was itself unmeasured.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from world import run_report, stakes_report

APP = Path(__file__).resolve().parents[2]

READERS = ["prices", "stakes", "wages", "time_spent", "diffusion", "learning"]


@pytest.mark.parametrize("reader", READERS)
def test_every_reader_has_a_caller(reader: str) -> None:
    """The finding, stated once per reader."""
    source = (APP / "world" / "run_report.py").read_text(encoding="utf-8")
    assert reader in source, (
        f"EVAL-05: stakes_report.{reader} computes a section of the "
        "evaluation spec and nothing in run_report.py calls it, so that "
        "section has never appeared in a report"
    )


def test_the_readers_are_all_still_there() -> None:
    """The other direction: a caller for a reader that has been renamed
    away is a test passing over nothing."""
    for reader in READERS:
        assert callable(getattr(stakes_report, reader)), reader


def test_time_says_so_when_nobody_committed_to_anything() -> None:
    """An empty section must read as a finding about the town, not as a
    hole in the instrument - the confusion EVAL-01 was made of."""
    lines = "".join(run_report.time_section([]))
    assert "## Time" in lines
    assert "one tick at a time" in lines
    assert "gap in the record" in lines


def test_time_reports_the_fulfilled_share() -> None:
    """T4, and business-readiness figure R4. Never computed on any run."""
    events = [
        {"type": "agent.intention.set", "tick": 1, "payload": {}},
        {"type": "agent.intention.set", "tick": 2, "payload": {}},
        {"type": "agent.intention.dropped", "tick": 9,
         "payload": {"reason": "fulfilled"}},
    ]
    lines = "".join(run_report.time_section(events))
    assert "T4: 50%" in lines, lines


def test_time_reports_the_cheap_work() -> None:
    """T2: repetitions carried out without a fresh decision are the least
    expensive thing the town does, and nothing counted them."""
    events = [
        {"type": "agent.continuing.started", "tick": 1, "payload": {}},
        {"type": "world.tick.advanced", "tick": 2, "payload": {"continuing": True}},
        {"type": "world.tick.advanced", "tick": 3, "payload": {"continuing": True}},
        {"type": "agent.continuing.ended", "tick": 4, "payload": {"reason": "spoken to"}},
    ]
    lines = "".join(run_report.time_section(events))
    assert "1 started" in lines
    assert "2 repetitions" in lines
    assert "spoken to" in lines


def test_diffusion_says_so_when_nothing_moved() -> None:
    lines = "".join(run_report.diffusion_section([]))
    assert "## Diffusion" in lines
    assert "1 by construction" in lines, (
        "an empty diffusion section must say WHY the number is 1, or a "
        "reader takes it for a measurement"
    )


def test_diffusion_reports_the_widest_reach() -> None:
    """T5 asks for a tool in three pairs of hands."""
    events = [
        {"type": "business.tool.licensed", "tick": 1,
         "payload": {"tool_name": "world_press", "owner_id": "a1", "licensee_id": "a2"}},
        {"type": "business.tool.taught", "tick": 2,
         "payload": {"tool_name": "world_press", "teacher_id": "a1", "student_id": "a3"}},
    ]
    lines = "".join(run_report.diffusion_section(events))
    assert "**3** pairs of hands" in lines, lines
    assert "T5 asks for three" in lines
    assert "world_press" in lines


def test_the_report_actually_calls_them() -> None:
    """A section function nobody calls is the same defect one level up."""
    source = (APP / "world" / "run_report.py").read_text(encoding="utf-8")
    assert "for line in time_section(" in source
    assert "for line in diffusion_section(" in source


def test_the_readers_get_the_whole_stream() -> None:
    """Both select several event types of their own. Handing them a list
    assembled type-by-type at the call site would put those names in two
    places, which is exactly how EVAL-01's five wrong names happened."""
    source = (APP / "world" / "run_report.py").read_text(encoding="utf-8")
    assert "time_section(_all_events())" in source
    assert "diffusion_section(_all_events())" in source


def test_the_stream_carries_the_type_where_the_readers_look() -> None:
    """`stakes_report` documents `{"type", "tick", "payload"}` and the
    events table keeps `type` in a column, not in the payload. A reader
    handed the payload alone matches nothing and reports a quiet zero."""
    source = inspect.getsource(run_report.build)
    helper = source[source.index("def _all_events"):]
    helper = helper[: helper.index("def _transactions")]
    assert '"type": kind' in helper, (
        "EVAL-05: the events handed to the readers carry no `type`, so "
        "every one of them selects nothing and reports zero"
    )
