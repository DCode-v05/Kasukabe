"""A tool that breaks must leave a mark on the run's own account of itself.

`record_refusal` asks `unmet_requirement_for` whether a failure is a gap worth
counting, and drops anything it cannot name. So a handler raising a plain
exception was refunded, re-raised, and left no trace: absent from the refusal
table, absent from "What the town could not do", absent from the report.

That is how a fifty-resident run came to report

    GOV-2  an ordinary resident votes   0 vote(s)          ✗ not held
    `location:town_hall`                12 agents, 13 attempts

while 1,233 ballots were in fact being rejected by a TypeError inside
`cast_vote`. A reader of that report would conclude thirteen residents stood
in the wrong place. The real cause appeared nowhere, and the report named
something else instead.
"""

from __future__ import annotations

import pytest

from world.tools.errors import LocationGateError
from world.tools.registry import record_broken_tool, record_refusal


def test_an_unclassifiable_failure_is_still_recorded(world) -> None:
    record_broken_tool(world, "n01", "vote", TypeError(
        "cast_vote() got an unexpected keyword argument 'because'"))

    assert len(world.tool_refusals) == 1
    r = world.tool_refusals[0]
    assert r.tool_name == "vote"
    assert r.unmet_requirement == "broken:TypeError"
    assert "unexpected keyword" in r.detail


def test_it_reads_as_the_tool_s_fault_not_the_resident_s(world) -> None:
    """`broken:` rather than a requirement the resident failed to meet. The
    refusal table is otherwise a list of things residents could not do; a
    broken tool is a different kind of entry and should sort apart."""
    record_broken_tool(world, "n01", "vote", RuntimeError("boom"))

    assert world.tool_refusals[0].unmet_requirement.startswith("broken:")


def test_a_classifiable_refusal_keeps_its_own_name(world) -> None:
    """A location gate is the town working as designed. It must not be
    relabelled as a broken tool just because it arrived by this path."""
    record_broken_tool(world, "n01", "vote", LocationGateError("vote", "town_hall", "b_yard"))

    assert world.tool_refusals
    assert not world.tool_refusals[0].unmet_requirement.startswith("broken:")


def test_the_dispatcher_files_it_before_re_raising() -> None:
    """The fix is worthless if the call site does not use it."""
    import inspect

    from world.tools import registry

    src = inspect.getsource(registry)
    i = src.index("outcome = await spec.handler(world, agent, params)\n        except Exception")
    block = src[i: i + 2200]

    assert "record_broken_tool(world, actor_id, tool_name, exc)" in block
    # the bare `raise` statement, not the word "raising" in a comment
    bare_raise = block.index("\n            raise\n")
    assert block.index("record_broken_tool") < bare_raise, (
        "the failure is re-raised before it is recorded"
    )
