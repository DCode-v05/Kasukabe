"""A resident who wrote a whole scene and touched nothing is never told.

MEASURED, run short1 (docs/runs/series/short1): 26 of 71 agent turns
produced a tool call. Thirty-seven per cent. It is not spread evenly and
that is the finding - n01, n07 and n08 acted on 4 turns of 7, while n06
acted on 1 of 8 and n03 on 1 of 7.

What n06 wrote instead, on three consecutive turns, with `actions: []`:

    *Wren stands at the edge of his field, the morning mist still clinging
    to the wheat.* ... "The thing is," he says, "it's going to be a dry
    one." *He finds Marta where he knew she would be.*

    *Wren takes his time answering. He looks at each of them in turn -
    Marta, Idris, Corwin, Liora.* "I haven't eaten either. And the field's
    waiting." ... "What do you need from me before I walk to the strip?"

Marta was standing right there. She was asked a direct question. She
never heard it, because none of it happened.

WHY THE TWO EXISTING RULES DID NOT CATCH IT. Both are already in the
prompt and both work for most residents: law rule 6 in the soul seed
("words you think, or narrate, or describe yourself saying reach nobody")
and the turn-close line at the very end of every perception ("nothing you
have thought or written above has happened yet"). n06 read both, three
turns running, and narrated anyway.

The thing neither of them is is EVIDENCE. They are rules stated in
advance, and a model that believes it is complying does not re-examine a
rule it thinks it followed. What was missing is the one fact that would
tell it otherwise: that the last turn reached nobody. The world knows it -
`tool_invocations` records every call with its actor and tick - and never
said so.

WHAT THIS IS NOT. It is not an instruction and it names no action, no
subject and no addressee. It reports what happened, in the past tense,
the way `open_questions` reports that somebody asked you something. A line
reading "you must call a tool" would produce a town that calls tools when
told to, which measures our prompt and not the residents.

It also does not fire on a turn that was DELIBERATELY quiet. Standing and
thinking is a legitimate thing for a person to do, and a resident nagged
for it would learn that the report is noise.
"""

from __future__ import annotations


from world.tools.state import SILENT_TURN_REPORT_MIN, WorldState


class Invocation:
    def __init__(self, actor_id: str, tick: int, tool_name: str = "say") -> None:
        self.actor_id = actor_id
        self.tick = tick
        self.tool_name = tool_name


def world_with(*invocations: Invocation) -> WorldState:
    world = WorldState.__new__(WorldState)
    world.tool_invocations = list(invocations)
    world.turn_ticks = {}
    return world


def test_a_resident_who_acted_last_turn_is_told_nothing() -> None:
    world = world_with(Invocation("n06", 10))
    WorldState.note_turn(world, "n06", 10)
    WorldState.note_turn(world, "n06", 13)
    assert WorldState.silent_turns_report(world, "n06") is None


def test_one_silent_turn_is_not_worth_mentioning() -> None:
    """Standing and thinking is a thing a person does. A report that fired
    on the first quiet turn would fire on nearly everyone, and a resident
    would rightly learn to skip it."""
    world = world_with()
    WorldState.note_turn(world, "n06", 10)
    WorldState.note_turn(world, "n06", 13)
    assert WorldState.silent_turns_report(world, "n06") is None


def test_a_run_of_silent_turns_is_reported() -> None:
    """n06 wrote three consecutive scenes with actions [], and Marta was
    asked a direct question in one of them and never heard it."""
    world = world_with()
    for tick in range(10, 10 + 3 * (SILENT_TURN_REPORT_MIN + 1), 3):
        WorldState.note_turn(world, "n06", tick)
    report = WorldState.silent_turns_report(world, "n06")
    assert report is not None
    assert str(SILENT_TURN_REPORT_MIN) in report or "turns" in report


def test_the_report_states_a_fact_and_gives_no_order() -> None:
    """The whole design. A line reading "you must call a tool" produces a
    town that calls tools when told to, which measures the prompt."""
    world = world_with()
    for tick in range(10, 10 + 3 * (SILENT_TURN_REPORT_MIN + 1), 3):
        WorldState.note_turn(world, "n06", tick)
    report = WorldState.silent_turns_report(world, "n06")
    lowered = report.lower()
    for order in ("you must", "you should", "make sure", "remember to", "call a tool"):
        assert order not in lowered, f"the report gives an order: {order!r}"
    assert "reached nobody" in lowered or "reached no one" in lowered


def test_acting_again_clears_it() -> None:
    """It reports a run that is still going, not a permanent record. A
    resident carrying every quiet turn of the whole run would be reading a
    grievance list."""
    world = world_with()
    for tick in range(10, 10 + 3 * (SILENT_TURN_REPORT_MIN + 1), 3):
        WorldState.note_turn(world, "n06", tick)
    assert WorldState.silent_turns_report(world, "n06") is not None

    acted_at = max(world.turn_ticks["n06"]) + 3
    world.tool_invocations.append(Invocation("n06", acted_at))
    WorldState.note_turn(world, "n06", acted_at)
    WorldState.note_turn(world, "n06", acted_at + 3)
    assert WorldState.silent_turns_report(world, "n06") is None


def test_another_residents_actions_do_not_count_as_yours() -> None:
    world = world_with()
    for tick in range(10, 10 + 3 * (SILENT_TURN_REPORT_MIN + 1), 3):
        WorldState.note_turn(world, "n06", tick)
        world.tool_invocations.append(Invocation("n01", tick))
    assert WorldState.silent_turns_report(world, "n06") is not None


def test_a_resident_on_its_first_turn_is_told_nothing() -> None:
    """There is no previous turn to have been silent."""
    world = world_with()
    WorldState.note_turn(world, "n06", 1)
    assert WorldState.silent_turns_report(world, "n06") is None


def test_an_unknown_resident_does_not_break_the_query() -> None:
    assert WorldState.silent_turns_report(world_with(), "nobody") is None


def test_malformed_invocation_rows_do_not_break_it() -> None:
    """This runs on every agent's every turn. A bad row must not take a
    resident's turn down with it."""
    world = world_with()
    world.tool_invocations = [object(), Invocation("n06", 1)]
    for tick in range(10, 10 + 3 * (SILENT_TURN_REPORT_MIN + 1), 3):
        WorldState.note_turn(world, "n06", tick)
    assert WorldState.silent_turns_report(world, "n06") is not None


def test_it_reaches_the_perception() -> None:
    from world.retrieval_depth import render_perception_text
    import inspect

    assert "silent_turns" in inspect.signature(render_perception_text).parameters, (
        "a report nothing renders is a report nobody reads"
    )
