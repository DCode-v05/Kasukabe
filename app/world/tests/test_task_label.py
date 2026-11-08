"""Evaluation spec section 4: every turn carries what it was FOR.

The spec's own words: "C1 cannot be measured without one, and no turn
carries one today." C1 is the roadmap's first compounding quantity -
median tokens per turn, per task, per resident, day 5 against day 1 - and
without a label there is nothing to group by, so the whole of section 2.4
is unmeasurable rather than merely unmeasured.

Assigned deterministically FROM THE TRACE, never by a model, in the
spec's own order:

  1. ran under a `keep_doing`        -> that tool
  2. an intention with done_when_tool -> that tool
  3. first LABOUR or TRADE tool called -> that tool
  4. only say / start_conversation    -> talk
  5. only observation tools           -> look
  6. otherwise                        -> idle

Deterministic matters here beyond cost: a label a judge assigned would
make C1 a measurement of the judge.
"""

from __future__ import annotations

from world.task_label import label_for_turn


def test_a_continuing_action_names_the_turn() -> None:
    """Rule 1. Twenty repetitions of one job are twenty turns of that job,
    whatever else happened in them."""
    assert label_for_turn(tools_called=["harvest"], continuing_tool="harvest") == "harvest"
    assert label_for_turn(
        tools_called=["look_around", "say"], continuing_tool="fell_timber"
    ) == "fell_timber", "the continuing action outranks whatever else the turn did"


def test_an_intention_names_the_turn_when_nothing_is_continuing() -> None:
    assert label_for_turn(
        tools_called=["move", "look_around"], intention_tool="craft"
    ) == "craft"


def test_a_continuing_action_outranks_an_intention() -> None:
    """The spec's order is an order, not a set: what a resident IS doing
    beats what it said it would do."""
    assert label_for_turn(
        tools_called=["harvest"], continuing_tool="harvest", intention_tool="craft"
    ) == "harvest"


def test_the_first_work_or_trade_tool_names_the_turn() -> None:
    """Rule 3. First, not last: a turn that harvests and then sells is a
    harvesting turn that ended in a sale, and grouping it under `sell`
    would hide the work."""
    assert label_for_turn(tools_called=["look_around", "harvest", "sell"]) == "harvest"
    assert label_for_turn(tools_called=["move", "trade"]) == "trade"


def test_a_turn_that_only_talks_is_talk() -> None:
    assert label_for_turn(tools_called=["say"]) == "talk"
    assert label_for_turn(tools_called=["start_conversation", "say"]) == "talk"


def test_a_turn_that_only_looks_is_look() -> None:
    assert label_for_turn(tools_called=["look_around", "list_places"]) == "look"
    assert label_for_turn(tools_called=["describe_place"]) == "look"


def test_a_turn_that_did_nothing_is_idle() -> None:
    """Half of run 19's spend went on these, and they need a name of their
    own or they hide inside `look`."""
    assert label_for_turn(tools_called=[]) == "idle"


def test_looking_before_working_is_still_a_working_turn() -> None:
    """The caution the town wants must not rename the turn."""
    assert label_for_turn(tools_called=["look_around", "describe_place", "mine"]) == "mine"


def test_moving_alone_is_not_work() -> None:
    """`move` is TRAVEL, not LABOUR or TRADE. A morning of walking is not
    a morning of mining, and C1 would flatter itself if it were."""
    assert label_for_turn(tools_called=["move", "move"]) == "look"


def test_the_label_is_written_on_the_call_row() -> None:
    """A label the trace does not carry is a label C1 cannot group by."""
    from pathlib import Path

    schema = (Path(__file__).resolve().parent.parent.parent
              / "supabase" / "sqlite_schema.sql").read_text(encoding="utf-8")
    assert "task_label" in schema, "llm_calls has nowhere to put it"
