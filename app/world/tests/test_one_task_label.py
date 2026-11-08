"""One turn, one label.

`EVAL-04`. Two implementations of the evaluation spec's section 4 rules
existed side by side and disagreed:

  * `world/task_label.py::label_for_turn` - categories derived from
    `DEFAULT_SCHEDULE`, and its observation set includes `move`, `rest`
    and `search_memory`;
  * `world/stakes_report.py::task_label` - hard-coded `LABOUR_TOOLS` /
    `TRADE_TOOLS`, and a `LOOK_TOOLS` set with no `move` and no `rest`.

So a turn of `["move", "look_around"]` is `look` to one and `idle` to the
other, and `task_label` is the column C1 - "does a task get cheaper as a
resident gets better at it" - is grouped by. Two answers to that question
is the same as none.

`label_for_turn` is the canonical one: it derives its work set from the
schedule rather than restating it, so a tool added to the town is
classified without anybody remembering to update a literal. These tests
hold the two to the same answer, which is what makes the delegation real
rather than a comment saying it should be.
"""
from __future__ import annotations

import pytest

from world.stakes_report import task_label
from world.task_label import label_for_turn

CASES = [
    (["move", "look_around"],),
    (["look_around"],),
    (["say"],),
    (["say", "look_around"],),
    ([],),
    (["harvest", "sell"],),
    (["sell", "harvest"],),
    (["rest"],),
    (["search_memory"],),
    (["mine", "say"],),
    (["check_debts"],),
]


@pytest.mark.parametrize("tools", [c[0] for c in CASES], ids=lambda t: ",".join(t) or "empty")
def test_the_two_labellers_agree(tools: list[str]) -> None:
    assert task_label(tools) == label_for_turn(tools_called=tools), (
        f"EVAL-04: {tools} is labelled two different ways, and task_label is "
        "the column C1 groups by"
    )


def test_the_precedence_rules_agree_too() -> None:
    """Rules 1 and 2 - a continuing action and an intention beat whatever
    the turn happened to call - are the two the spec is most explicit
    about."""
    assert task_label(["say"], continuing_tool="mine") == label_for_turn(
        tools_called=["say"], continuing_tool="mine"
    )
    assert task_label(["say"], intention_tool="harvest") == label_for_turn(
        tools_called=["say"], intention_tool="harvest"
    )


def test_the_first_work_tool_wins_not_the_last() -> None:
    """A turn that harvests and then sells is a harvesting turn that ended
    in a sale. Grouping it under `sell` hides the work C1 exists to watch
    get cheaper."""
    assert task_label(["harvest", "sell"]) == "harvest"
