"""A resident re-read six superseded pictures of the world on every call.

Run 29, measured from the trace: the median resident call grew from 14,110
input tokens in the first game-hour to 23,759 by the last - 1.68x, against
N10's bar of 1.25x - and it grew because of what history carries. Each
stored perception is a full picture of the world at that moment: the time,
the place, who is here, what is priced, the town's civic notice, the
backlog of things said. The current perception supersedes every one of
them, and yet late in the run 61% of the world's message (a median 20,747
characters, ~5,200 tokens) was OLDER perceptions, re-sent on every call of
every turn, with no prompt cache to make re-reading them cheap.

The turn records are the resident's memory of what it did and did their
job (G6 51% -> 94% in run 29), and they stay whole. What a record needs
from the perception it followed is when and where; that is the first line,
and the first line is what an older perception is reduced to. The current
perception is untouched, and so is everything this turn has done.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.herald.types import Message, TextBlock, ToolResultBlock, ToolUseBlock
from src.sim.bounded_monologue import end_the_turn_on_a_cut_monologue
from src.sim.resident_voice import speak_as_resident
from src.sim.session_log import render_action_record

EVENT = SimpleNamespace(type="text", text="ok")


def _perception(clock: str, place: str = "street") -> str:
    return (
        f"It is game-day 0, {clock} (day). You are at {place}.\n"
        "You work for your living.\n"
        "Said to you, and not yet answered:\n"
        '  - Marta Thorne (n07), 3 tick(s) ago: "I need bread. Three loaves."\n'
        "You hold: bread 2.\n"
        "You have 3730 sim in hand.\n"
        "The town: an election is open for candidates.\n"
        "With you, and nobody else: Bram Holloway (n01), Aldous Vance (n14).\n"
        "Nothing you have thought or written above has happened yet."
    )


RECORD = render_action_record([
    {"tool": "world:say", "params": {"content": "Morning."}, "result": "{}", "status": "success"},
])


class _Adapter:
    def __init__(self) -> None:
        self.seen: list[list[Message]] = []
        self.model_id = "test-model"

    async def stream_with_tools(self, messages, **kwargs):
        self.seen.append(list(messages))
        yield EVENT

    async def stream(self, messages, **kwargs):
        self.seen.append(list(messages))
        yield EVENT


def _call(wrapped, messages):
    async def drain():
        return [e async for e in wrapped.stream_with_tools(messages, system="s", tools=[])]
    return asyncio.run(drain())


def _wire(adapter) -> str:
    return "\n".join(m.text() for m in adapter.seen[0])


def _history() -> list[Message]:
    return [
        Message("user", _perception("07:00")),
        Message("assistant", RECORD),
        Message("user", _perception("08:00", "Kasukabe Farmland")),
        Message("assistant", RECORD),
        Message("user", _perception("09:00")),
    ]


def test_an_older_perception_is_reduced_to_when_and_where() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, _history())

    wire = _wire(adapter)
    assert "It is game-day 0, 07:00 (day). You are at street." in wire
    assert "It is game-day 0, 08:00 (day). You are at Kasukabe Farmland." in wire
    assert wire.count("You have 3730 sim in hand.") == 1
    assert wire.count("Said to you, and not yet answered:") == 1
    assert wrapped.perceptions_compacted == 2


def test_the_current_perception_is_whole() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, _history())

    assert _perception("09:00") in _wire(adapter)


def test_the_records_are_whole() -> None:
    """The memory of what was done is the part that moved G6. It stays."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, _history())

    assert _wire(adapter).count(RECORD) == 2


def test_a_perception_carrying_the_engines_reminders_is_still_found() -> None:
    """The current turn's perception arrives as blocks, after the recall
    reminders the engine prepends. It is still the current one."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)
    history = _history()
    history[-1] = Message("user", [
        TextBlock("<system-reminder>You remember: Marta owes you 40 sim.</system-reminder>"),
        TextBlock(_perception("09:00")),
    ])

    _call(wrapped, history)

    wire = _wire(adapter)
    assert _perception("09:00") in wire
    assert "Marta owes you 40 sim" in wire


def test_this_turns_own_calls_and_results_are_untouched() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)
    use = Message("assistant", [ToolUseBlock(id="t1", name="world:look_around", input={})])
    result = Message("user", [ToolResultBlock(tool_use_id="t1", content=_perception("09:10"))])

    _call(wrapped, _history() + [use, result])

    assert adapter.seen[0][-2:] == [use, result]


def test_the_roles_still_alternate() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, [Message("assistant", RECORD)] + _history())

    roles = [m.role for m in adapter.seen[0]]
    assert roles[0] == "user"
    assert all(a != b for a, b in zip(roles, roles[1:])), roles


def test_the_engines_history_is_not_edited() -> None:
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)
    history = _history()
    before = [m.text() for m in history]

    _call(wrapped, history)

    assert [m.text() for m in history] == before


def test_it_can_be_turned_off(monkeypatch) -> None:
    """One knob, so a run can put the full pictures back and measure the
    difference."""
    import src.sim.resident_voice as rv

    monkeypatch.setattr(rv, "OLD_PERCEPTIONS_IN_FULL", True)
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)

    _call(wrapped, _history())

    assert _wire(adapter).count("You have 3730 sim in hand.") == 3
    assert wrapped.perceptions_compacted == 0


def test_the_chain_boot_builds_reaches_it() -> None:
    adapter = _Adapter()
    chain = end_the_turn_on_a_cut_monologue(speak_as_resident(adapter))

    _call(chain, _history())

    assert _wire(adapter).count("You have 3730 sim in hand.") == 1


def test_it_is_what_makes_the_prompt_stop_growing() -> None:
    """The measure N10 cares about: with six perceptions in history the
    world's message is not six times one perception."""
    adapter = _Adapter()
    wrapped = speak_as_resident(adapter)
    history: list[Message] = []
    for hour in range(6, 12):
        history += [Message("user", _perception(f"{hour:02d}:00")), Message("assistant", RECORD)]
    history.append(Message("user", _perception("12:00")))

    _call(wrapped, history)

    one = len(_perception("12:00"))
    assert len(_wire(adapter)) < one + 6 * (len(RECORD) + 120), len(_wire(adapter))
