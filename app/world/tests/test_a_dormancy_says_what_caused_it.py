"""Thirty-seven of fifty residents went dormant and the report says why
for none of them.

`teambox-50-100usd-2026-09-16`, the Stakes section: a table of residents
and ticks with a `Cause` column reading `None` on every row. Not a
rendering bug - `health.agent.dormant` carries `{"at_hours": ...}` and
nothing else, so there is no cause to print. And there are three
different ways in: a debit that leaves a wallet at $0 (`wiring`), the
tick-loop sweep over everybody already below the line (`run_genesis`),
and the day-jobs sweep that stops a worker who cannot pay to think
(`life/day_jobs`). Those are three different findings about a town, and
the run recorded them as one blank.

The 50-agent run is the reason it matters: $100.40 over 50 residents is
$2.01 each, against a $2 starting wallet. Whether the town spent itself
dormant, or was swept dormant while holding sim it never converted, is
the difference between an economy that is too expensive and one nobody
used - and the report could not tell those apart.
"""

from __future__ import annotations

import inspect

from world.life.death import DORMANCY_CAUSES, mark_dormant
from world.life.clock import GameClock
from world.life.models import DormancyState


def _clock(hours: float = 12.0) -> GameClock:
    return GameClock(total_hours=hours)


def test_a_dormancy_carries_its_cause() -> None:
    dormancy, events = mark_dormant(
        DormancyState(), _clock(), cause=DORMANCY_CAUSES["spent"])

    assert dormancy.is_dormant
    assert events[0].payload["reason"] == DORMANCY_CAUSES["spent"]


def test_the_cause_is_a_sentence_a_reader_understands() -> None:
    """The report prints this verbatim in a table column."""
    for key, cause in DORMANCY_CAUSES.items():
        assert len(cause) > 12 and cause == cause.strip()
        if key == "unstated":
            continue
        assert "$" in cause or "thinking" in cause


def test_an_unnamed_cause_is_still_recorded_as_one() -> None:
    """A caller that says nothing must not put the report back where it
    was; "not stated" is a worse answer than a real cause and a better one
    than None."""
    _dormancy, events = mark_dormant(DormancyState(), _clock())

    assert events[0].payload.get("reason")


def test_every_way_into_dormancy_names_itself() -> None:
    """Three call sites, three different facts about the town."""
    from world import wiring
    from world.life import day_jobs
    from world import run_genesis

    for module in (wiring, day_jobs, run_genesis):
        source = inspect.getsource(module)
        for call in source.split("mark_dormant(")[1:]:
            head = call[:200]
            assert "cause=" in head, (
                f"{module.__name__} marks a resident dormant without saying why; "
                "that is the blank column in the 50-agent report"
            )
