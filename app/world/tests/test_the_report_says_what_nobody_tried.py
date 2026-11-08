"""Run 19, issues 8 and 10: two report numbers that flattered the town.

**Issue 8.** "What the town could not do" listed exactly one row -
`location:sawmill`, one agent, one attempt (report L212-216) - while the
same document shows residents unable to sell, unable to find a buyer and
unable to buy food. The table is built from REFUSALS, and everything that
actually went wrong was a tool nobody ever called. A run in which nobody
tries anything scores a clean table.

The measure that would have caught it is exact, not fuzzy: a tool that
exists, is ungated, was visible to everyone, and was called zero times all
run. `trade` is exactly that - it does what Corwin and Nell agreed out
loud, it needs no shop, and it was never once invoked.

**Issue 10.** The headline read "Dispatch success 100.0%" (L70) - true of
the calls that were made, and silent about the turns that made none.
Ainsley Holloway and Tobias Holloway each took twelve turns and called
nothing (L271, L288), and 387 calls ended without a tool call at $1.95,
48% of the run's spend.
"""

from __future__ import annotations

from world.run_report import acting_turn_rate, untouched_tools


def test_a_tool_nobody_called_is_reported() -> None:
    served = [
        {"name": "trade", "gated": False},
        {"name": "say", "gated": False},
        {"name": "vote", "gated": True},
    ]
    calls = {"say": 41, "move": 86}

    missed = untouched_tools(served, calls)
    names = [t["name"] for t in missed]
    assert "trade" in names, "the tool that would have moved the planks is not reported"
    assert "say" not in names, "a tool that was used is not a gap"


def test_the_gate_map_comes_from_the_specs_not_the_served_schema() -> None:
    """Run 20 listed `vote`, `pray`, `petition` and `define_tool` as tools
    nobody reached for. All four are gated - you vote at the town hall and
    pray at the temple - and they were listed because the gate map was read
    off `/tools/registry`, which serves the model's schema and does not
    carry `location_gate` at all. Every tool read as ungated.

    The same shape of bug as reading `action["name"]` when the field is
    `tool`, and the fix is the same: read it from the thing that has it."""
    from world.run_report import gated_tool_names

    gated = gated_tool_names()
    assert "vote" in gated and "pray" in gated and "define_tool" in gated
    assert "say" not in gated and "trade" not in gated


def test_a_gated_tool_nobody_called_is_not_reported_as_a_gap() -> None:
    """`vote` goes uncalled when there is no election, and reporting it
    would bury the one row that matters under thirty that do not."""
    served = [{"name": "vote", "gated": True}, {"name": "trade", "gated": False}]
    missed = [t["name"] for t in untouched_tools(served, {})]
    assert missed == ["trade"]


def test_a_tool_somebody_tried_and_was_refused_is_not_untouched() -> None:
    """Run 27's report, two sections apart: "What the town could not do" -
    `pay_tax`, 4 residents, 5 attempts, refused "you owe no head tax" - and
    then `pay_tax` in the list of tools "never called once" by anyone.

    Both read the same run. The never-called list counted only calls that
    LANDED, and a refused call never lands, so the one tool the town kept
    reaching for was reported as the one nobody tried."""
    served = [{"name": "pay_tax", "gated": False}, {"name": "trade", "gated": False}]

    missed = [t["name"] for t in untouched_tools(served, {}, tried={"pay_tax"})]

    assert missed == ["trade"]


def test_the_acting_rate_counts_turns_not_calls() -> None:
    """Run 19's real shape: twelve turns, no tool calls."""
    turns = [
        {"agent_id": "n02", "tool_calls": 0},
        {"agent_id": "n02", "tool_calls": 0},
        {"agent_id": "n01", "tool_calls": 2},
        {"agent_id": "n01", "tool_calls": 1},
    ]
    rate = acting_turn_rate(turns)
    assert rate["turns"] == 4
    assert rate["acting"] == 2
    assert rate["percent"] == 50.0
    assert rate["silent_residents"] == ["n02"], (
        "a resident who never touched the world in any turn is the fact the "
        "headline was hiding"
    )


def test_a_town_that_acted_every_turn_reports_a_hundred_percent() -> None:
    rate = acting_turn_rate([{"agent_id": "n01", "tool_calls": 1}])
    assert rate["percent"] == 100.0
    assert rate["silent_residents"] == []


def test_no_turns_at_all_is_not_a_division_by_zero() -> None:
    rate = acting_turn_rate([])
    assert rate["turns"] == 0 and rate["percent"] == 0.0
