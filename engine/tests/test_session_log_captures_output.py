"""The session log must record what the model SAID, not only what it thought.

Reasoning is optional - a model may answer with none at all - but text output
happens on every turn that is not a bare tool call. The writer subscribed to
ThinkingDelta and ToolExecution and never to TextDelta, so every session in
every run recorded `"reasoning": ""` and no speech at all. A log that keeps
the thinking and drops the words cannot answer "what did this agent say",
which is the whole question a reader of a conversation is asking.
"""

from __future__ import annotations

import json

import pytest

from src.engine_core.events import ExecutionSummary, TextDelta, ThinkingDelta
from src.sim.session_log import SessionLogWriter


def summary() -> ExecutionSummary:
    return ExecutionSummary(
        thread_id="t", status="completed", exit_reason="completed",
        tool_calls_count=0, num_turns=1, model_id="moonshotai.kimi-k2.5",
    )


@pytest.mark.asyncio
async def test_model_text_output_is_recorded(tmp_path):
    log = tmp_path / "session_log.jsonl"
    writer = SessionLogWriter(agent_id="a0001", log_path=log)

    writer.start_turn("It is game-day 0, 08:00.")
    await writer._on_event(TextDelta(thread_id="t", text="Morning, Halcyon. "))
    await writer._on_event(TextDelta(thread_id="t", text="Any work going today?"))
    await writer._on_event(summary())

    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["output"] == "Morning, Halcyon. Any work going today?"


@pytest.mark.asyncio
async def test_reasoning_and_output_are_kept_apart(tmp_path):
    """They are different things and a reader needs to tell them apart:
    one is what the agent thought, the other is what the town heard."""
    log = tmp_path / "session_log.jsonl"
    writer = SessionLogWriter(agent_id="a0001", log_path=log)

    writer.start_turn("input")
    await writer._on_event(ThinkingDelta(thread_id="t", text="I should ask about work."))
    await writer._on_event(TextDelta(thread_id="t", text="Any work going?"))
    await writer._on_event(summary())

    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["reasoning"] == "I should ask about work."
    assert entry["output"] == "Any work going?"


@pytest.mark.asyncio
async def test_a_turn_with_no_reasoning_still_records_what_was_said(tmp_path):
    """The common case: models frequently answer with no thinking block at
    all, and every one of those turns used to record nothing."""
    log = tmp_path / "session_log.jsonl"
    writer = SessionLogWriter(agent_id="a0001", log_path=log)

    writer.start_turn("input")
    await writer._on_event(TextDelta(thread_id="t", text="Walking to the field."))
    await writer._on_event(summary())

    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["reasoning"] == ""
    assert entry["output"] == "Walking to the field."


@pytest.mark.asyncio
async def test_a_silent_tool_only_turn_records_an_empty_string(tmp_path):
    """Not a missing key: a reader distinguishing "said nothing" from "we
    did not record it" needs the field to be present either way."""
    log = tmp_path / "session_log.jsonl"
    writer = SessionLogWriter(agent_id="a0001", log_path=log)

    writer.start_turn("input")
    await writer._on_event(summary())

    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert entry["output"] == ""
    assert "output" in entry


@pytest.mark.asyncio
async def test_the_recovery_context_reads_back_what_was_said(tmp_path):
    """An agent that has lost its context gets its own words back, not just
    its own thoughts."""
    from src.sim.session_log import render_recovery_context

    log = tmp_path / "session_log.jsonl"
    writer = SessionLogWriter(agent_id="a0001", log_path=log)
    writer.start_turn("input")
    await writer._on_event(TextDelta(thread_id="t", text="I offered Bram my wheat."))
    await writer._on_event(summary())

    assert "You said: I offered Bram my wheat." in render_recovery_context(log)
