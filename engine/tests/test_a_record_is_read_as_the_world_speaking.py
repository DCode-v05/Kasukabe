"""A resident copied its turn records instead of acting.

Run 28b's trace (`docs/runs/run-28-trace-analysis.md`, and the artifact built
from it). A resident's stored history is perception / record / perception /
record, and every record sits in the ASSISTANT role - ENG-09 stores a turn as
what it did, in the resident's own slot. So the model met three or more past
"selves" that had written

    RECORD OF THE TURN THAT JUST ENDED (written by the world, not by you):
    - world:say(listener_agent_id="n20", content="...") -> {...}

and continued the genre. Of run 28b's 235 first calls of a turn that followed
a record, 101 (43%) wrote record-shaped text and 111 (47%) called no tool.
The 45 first calls with no record before them: 2 and 5.

The same 235 calls also carried the frozen engine's complexity nudge
(`engine_core/thread.py:2921`): "Consider calling request_plan to create a
structured approach before proceeding." `request_plan` was served as a tool in
0 of run 28b's 885 resident calls. It points at a tool the resident does not
have, in the words of an assistant working for a user.

Both are fixed on the wire, in `resident_voice.py`, where the adapter the
thread calls is already wrapped. What is STORED does not change: ENG-09's
records, the session log and the perception rows stay exactly as written.
What the model is HANDED changes: a record is folded into the world's next
message, so the only words in the resident's slot are ones it produced
this turn.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.herald.types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from src.sim.bounded_monologue import end_the_turn_on_a_cut_monologue
from src.sim.resident_voice import speak_as_resident
from src.sim.session_log import SILENT_TURN_RECORD, render_action_record

NUDGE = (
    "<system-reminder>This task appears complex. Consider calling request_plan "
    "to create a structured approach before proceeding.</system-reminder>"
)
RECORD = render_action_record([
    {"tool": "world:say", "params": {"listener_agent_id": "n20", "content": "Morning."},
     "result": '{"heard_by": ["Hester Vance"]}', "status": "success"},
])


EVENT = SimpleNamespace(type="text", text="I'll walk to the mill.")


class _Adapter:
    """Records what it was handed, and streams one event back."""

    def __init__(self) -> None:
        self.seen: list[list[Message]] = []
        self.model_id = "test-model"

    async def stream_with_tools(self, messages, **kwargs):
        self.seen.append(list(messages))
        yield EVENT

    async def stream(self, messages, **kwargs):
        self.seen.append(list(messages))
        yield EVENT


def _call(wrapped, messages, method="stream_with_tools"):
    async def drain():
        return [event async for event in getattr(wrapped, method)(messages, system="s", tools=[])]
    return asyncio.run(drain())


def _text(message: Message) -> str:
    return message.text()


def test_the_plan_nudge_for_a_tool_residents_do_not_have_is_removed() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter, agent_name="Hester Vance")

    _call(wrapped, [Message("user", [TextBlock(NUDGE), TextBlock("It is game-day 1, 06:00.")])])

    sent = adapter.seen[0][-1]
    assert "request_plan" not in _text(sent)
    assert "It is game-day 1, 06:00." in _text(sent)
    assert wrapped.plan_nudges_removed == 1


def test_the_nudge_is_removed_from_plain_string_content_too() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, [Message("user", NUDGE + "\n\nIt is game-day 1, 06:00.")])

    assert _text(adapter.seen[0][-1]).strip() == "It is game-day 1, 06:00."


def test_every_other_system_reminder_stays() -> None:
    """Recall and matched skills are the engine's memory reaching the
    resident. Only the nudge towards a tool it lacks goes."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)
    recall = "<system-reminder>You remember: Marta owes you 40 sim.</system-reminder>"

    _call(wrapped, [Message("user", [TextBlock(recall), TextBlock(NUDGE), TextBlock("Now.")])])

    assert recall in _text(adapter.seen[0][-1])


def test_a_record_never_reaches_the_model_as_its_own_words() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, [
        Message("user", "It is game-day 0, 21:50. You are at street."),
        Message("assistant", RECORD),
        Message("user", "It is game-day 1, 06:00. You are at the market."),
        Message("assistant", SILENT_TURN_RECORD),
        Message("user", [TextBlock(NUDGE), TextBlock("It is game-day 1, 06:10.")]),
    ])

    sent = adapter.seen[0]
    assert all("RECORD OF THE TURN" not in _text(m) for m in sent if m.role == "assistant")
    joined = "\n".join(_text(m) for m in sent)
    # Nothing is lost, and it stays in the order it happened.
    order = [joined.index(s) for s in (
        "21:50", "world:say", "06:00", "no tool was called", "06:10",
    )]
    assert order == sorted(order)
    assert wrapped.records_moved == 2


def test_the_roles_still_alternate_and_open_with_the_world() -> None:
    """Converse refuses two turns in a row from the same role, and a
    conversation that opens on the assistant. Bounded history can open its
    window on a record (run 28b's bodies do)."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, [
        Message("assistant", RECORD),
        Message("user", "It is game-day 1, 06:00."),
        Message("assistant", SILENT_TURN_RECORD),
        Message("user", "It is game-day 1, 06:10."),
    ])

    roles = [m.role for m in adapter.seen[0]]
    assert roles[0] == "user"
    assert all(a != b for a, b in zip(roles, roles[1:])), roles


def test_this_turns_own_calls_stay_in_the_residents_voice() -> None:
    """A tool call made THIS turn is the model's own act, and the
    result that answers it must follow it unchanged."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)
    use = Message("assistant", [ToolUseBlock(id="t1", name="world:look_around", input={})])
    result = Message("user", [ToolResultBlock(tool_use_id="t1", content="The market.")])

    _call(wrapped, [
        Message("user", "It is game-day 1, 06:00."),
        Message("assistant", RECORD),
        Message("user", "It is game-day 1, 06:10."),
        use,
        result,
    ])

    assert adapter.seen[0][-2:] == [use, result]


def test_a_reply_that_is_not_a_record_stays_the_residents() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, [
        Message("user", "It is game-day 1, 06:00."),
        Message("assistant", "I'll take the planks to the mill."),
        Message("user", "It is game-day 1, 06:10."),
    ])

    assert [m.role for m in adapter.seen[0]] == ["user", "assistant", "user"]
    assert wrapped.records_moved == 0


def test_the_engines_own_history_is_not_edited() -> None:
    """The thread keeps these objects. The wire gets new ones."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)
    history = [
        Message("user", "It is game-day 1, 06:00."),
        Message("assistant", RECORD),
        Message("user", [TextBlock(NUDGE), TextBlock("It is game-day 1, 06:10.")]),
    ]
    before = list(history)

    _call(wrapped, history)

    assert history == before
    assert "request_plan" in history[2].text()


def test_the_events_come_back_unchanged() -> None:
    wrapped = speak_as_resident(_Adapter())
    assert _call(wrapped, [Message("user", "Now.")]) == [EVENT]


def test_the_no_tools_path_is_cleaned_too() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, [
        Message("user", "It is game-day 1, 06:00."),
        Message("assistant", RECORD),
        Message("user", NUDGE + "\n\nNow."),
    ], method="stream")

    assert [m.role for m in adapter.seen[0]] == ["user"]
    assert "request_plan" not in _text(adapter.seen[0][0])


def test_the_world_voice_can_be_turned_off(monkeypatch) -> None:
    """One knob, so a run can put records back in the resident's slot
    and measure the difference. The nudge goes either way."""
    import src.sim.resident_voice as rv

    monkeypatch.setattr(rv, "RECORDS_IN_WORLD_VOICE", False)
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, [
        Message("user", "It is game-day 1, 06:00."),
        Message("assistant", RECORD),
        Message("user", [TextBlock(NUDGE), TextBlock("Now.")]),
    ])

    assert [m.role for m in adapter.seen[0]] == ["user", "assistant", "user"]
    assert "request_plan" not in _text(adapter.seen[0][-1])


def test_the_chain_boot_builds_reaches_the_rewrite() -> None:
    """`pinned_boot` wraps the voice adapter in the monologue guard. A
    rewrite that only works when called directly is not running."""
    adapter = _Adapter()
    chain = end_the_turn_on_a_cut_monologue(speak_as_resident(adapter))

    _call(chain, [
        Message("user", "It is game-day 1, 06:00."),
        Message("assistant", RECORD),
        Message("user", [TextBlock(NUDGE), TextBlock("Now.")]),
    ])

    sent = adapter.seen[0]
    assert all(m.role == "user" for m in sent)
    assert "request_plan" not in "".join(_text(m) for m in sent)
