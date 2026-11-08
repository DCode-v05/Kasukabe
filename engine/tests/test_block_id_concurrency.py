"""M0 — concurrent same-type blocks must get distinct, stable ids so
replace-by-id reconciliation never lets one instance clobber another."""

from src.engine_core.blocks import BlockSerializer
from src.engine_core.events import (
    SubAgentStart,
    SubAgentProgress,
    SubAgentBackgrounded,
    SubAgentDone,
)


def test_two_sub_agents_get_distinct_ids():
    s = BlockSerializer("r1")
    a_start = s.fold(
        SubAgentStart(thread_id="t", agent_id="agent_A", agent_name="A", task_description="x")
    )
    b_start = s.fold(
        SubAgentStart(thread_id="t", agent_id="agent_B", agent_name="B", task_description="y")
    )
    assert a_start.id != b_start.id
    assert a_start.id == "sub_agent:agent_A"
    assert b_start.id == "sub_agent:agent_B"


def test_same_agent_updates_share_one_id():
    s = BlockSerializer("r1")
    start = s.fold(
        SubAgentStart(thread_id="t", agent_id="agent_A", agent_name="A", task_description="x")
    )
    prog = s.fold(
        SubAgentProgress(
            thread_id="t",
            agent_id="agent_A",
            agent_name="A",
            turn=1,
            tool_use_count=1,
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            cost_usd=0.0,
        )
    )
    done = s.fold(SubAgentDone(thread_id="t", agent_id="agent_A", result_summary="ok"))
    assert start.id == prog.id == done.id == "sub_agent:agent_A"
    assert start.payload["phase"] == "start"
    assert prog.payload["phase"] == "progress"
    assert done.payload["phase"] == "done"


def test_backgrounded_a_never_overwrites_progress_b():
    s = BlockSerializer("r1")
    a_bg = s.fold(
        SubAgentBackgrounded(
            thread_id="t", agent_id="agent_A", agent_name="A", background_thread_id="bg1"
        )
    )
    b_prog = s.fold(
        SubAgentProgress(
            thread_id="t",
            agent_id="agent_B",
            agent_name="B",
            turn=1,
            tool_use_count=1,
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            cost_usd=0.0,
        )
    )
    store: dict = {}
    store[a_bg.id] = a_bg
    store[b_prog.id] = b_prog
    assert len(store) == 2
    assert store["sub_agent:agent_A"].payload["phase"] == "backgrounded"
    assert store["sub_agent:agent_B"].payload["phase"] == "progress"
