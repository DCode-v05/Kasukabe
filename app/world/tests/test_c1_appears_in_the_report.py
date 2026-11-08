"""EVAL-03: C1 - calls per resident-day - must appear in a run report.

Three stacked reasons it never has:

  1. `llm_calls.tick` was NULL on every row, so nothing could be bucketed
     into a game day at all (INFRA-02).
  2. `stakes_report.label_calls` - which computes `task_label`, the spec's
     x-axis for the learning curve - had four tests and NO production
     caller.
  3. `run_report` did not import `stakes_report` at all. The whole
     evaluation module was orphaned.

This pins the wiring end of it: the label is computed, the section renders,
and an empty result says so out loud rather than printing a confident zero.
"""
from __future__ import annotations

from world.run_report import learning_section


def test_a_labelled_call_produces_a_c1_row():
    calls = [
        {"agent_id": "n01", "tick": 40, "task_label": "farming", "input_tokens": 900},
        {"agent_id": "n01", "tick": 44, "task_label": "farming", "input_tokens": 950},
        {"agent_id": "n02", "tick": 45, "task_label": "farming", "input_tokens": 800},
    ]
    out = "\n".join(learning_section(calls))
    assert "C1" in out
    assert "farming" in out
    # 3 calls across 2 residents on day 0 = 1.5 per resident.
    assert "1.5" in out, f"expected calls-per-resident in the table:\n{out}"


def test_two_days_are_separate_rows():
    calls = [
        {"agent_id": "n01", "tick": 10, "task_label": "baking"},
        {"agent_id": "n01", "tick": 150, "task_label": "baking"},
    ]
    out = "\n".join(learning_section(calls))
    assert "| 0 | baking" in out
    assert "| 1 | baking" in out, "day 1 (tick 150) was not bucketed separately"


def test_no_calls_is_reported_as_a_defect_not_a_quiet_town():
    out = "\n".join(learning_section([]))
    assert "cannot be computed" in out
    assert "defect in the record" in out, (
        "an absent C1 must read as a broken record, not as a town that did "
        "no thinking - that confusion is the whole EVAL family"
    )


def test_calls_without_a_tick_say_so_rather_than_counting_as_day_zero():
    """The INFRA-02 failure mode, guarded.

    A NULL tick must not silently bucket every call into day 0 and produce
    a confident, wrong C1.
    """
    out = "\n".join(learning_section([{"agent_id": "n01", "tick": None}]))
    assert "uncomputable" in out or "cannot be computed" in out
    assert "INFRA-02" in out


def test_the_report_actually_calls_the_labeller():
    """`label_calls` had tests and no caller; this is the caller."""
    from world import run_report

    assert hasattr(run_report, "_labelled_calls")
    import inspect

    src = inspect.getsource(run_report._labelled_calls)
    assert "label_calls" in src
