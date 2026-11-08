"""The same question, asked twice in one turn, does not have a new answer.

`TRADE-04`. In run `deni-a6f9eb4-5h` the town made 1,224 tool calls that
reached the world. **199 of them - one in six - were an exact repeat of a
call the same resident had already made on the same tick**: same verb,
same parameters, same tick. `move` 64 times, `rest` 61, `look_around` 60.

A resident looked around, read the answer, and looked around again. It
walked to the mill, was told it was walking to the mill, and set off for
the mill. Each repeat was billed, each consumed one of a tick's small
call budget, and not one of them could have returned anything new -
because between two calls inside a single turn, the only thing that can
change the world is the resident itself.

WHY IT HAPPENS. A model that cannot see its own effect reaches for
confirmation. Codex's base instructions carry the same rule for the same
reason - *"Do not waste tokens by re-reading files after calling
apply_patch on them. The tool call will fail if it didn't work."* The
town never said the equivalent, so re-reading looked free and prudent.

WHAT THIS DOES. A repeated READ - a tool that only reports - returns the
answer it already gave, with a line saying so, and **is not charged
again**. That is not a refusal and not a rate limit: it is the true
answer plus the fact that it is the same answer, which is the piece the
resident was missing.

WHAT THIS DELIBERATELY DOES NOT DO. It does not touch a repeated ACT.
Harvesting twice gets two harvests; saying the same sentence twice is a
resident repeating itself, which is a real thing residents do and is
worth measuring rather than preventing. Only calls that cannot have a
different answer are folded, and the list is explicit below rather than
inferred, because inferring it would eventually fold something that
does change.
"""

from __future__ import annotations

import json
from typing import Any

# Tools that only report. None of these changes anything, so two
# identical calls on one tick are the same question twice.
#
# Named explicitly rather than derived from "has no events" or "is not
# LABOUR": a tool that quietly acquires a lock, bumps a counter or wakes
# somebody would pass a derived test and must never be folded.
# EVERY NAME HERE IS A TOOL THAT EXISTS. The first draft of this list
# carried seven that do not - `check_wallet`, `check_inventory`,
# `check_prices`, `authoring_context`, and the three memory verbs - and a
# name nothing registers is a rule that silently never applies, which is
# the EVAL-01 failure this project keeps re-learning.
#
# The three memory verbs (`search_memory`, `recall_memories`,
# `recall_session_log`) are real, but they are registered ENGINE-side in
# `pinned_boot` and never pass through this registry, so folding them
# would have to happen there and does not happen here.
#
# `check_wallet` and `check_inventory` do not exist at all: a resident's
# money and holdings are in the perception every turn, not behind a
# verb. `world/tools/registry.py`'s ALWAYS_IN_FULL still named them.
READ_ONLY_TOOLS = frozenset({
    "look_around",
    "describe_place",
    "describe_primitives",
    "describe_person",
    "list_places",
    "find_people",
    "check_debts",
    "quote_action_cost",
    "recall_goals",
    "read_will",
})


def costs_nothing_because_it_does_nothing(
    world: Any, actor_id: str, tool_name: str, params: dict[str, Any]
) -> bool:
    """A call that will run, publish, and change nothing - so it is free.

    TRADE-06, second attempt, and the difference from the first is where
    it is applied. The first tried to FOLD this: short-circuit before the
    handler and hand back a canned result. That dropped `move`'s
    `agent.moved`, which carries the tile coordinates perception and the
    map read, and it left a result claiming "nothing was charged" while
    dispatch had already charged. Two regressions, both caught by tests
    that were right.

    This one only answers a pricing question. The handler still runs, the
    event still publishes, the result is whatever the handler says. All
    that changes is that `dispatch` does not take money for a journey of
    zero tiles - the shape TOOL-06 already uses for an invented tool's
    wrapper.

    `move`, and `rest` in exactly one case. `rest` in general is NOT free:
    it is a unit of work, and `keep_doing` repeats it through this same
    dispatch precisely so each repetition earns proficiency and is
    charged. Folding it silently disabled that feature the first time.

    The one case is a resident's OWN rest while a walk is under way. Run
    28: 226 of 329 rests (69%) were that - time passes only between turns,
    so each returned exactly what the last one did, and each still cost a
    sim on top of the model call. Under a standing commitment it is work
    again and is charged; asked to give the walk up, it changes something
    and is charged.
    """
    if tool_name == "rest":
        if actor_id in world.continuing:
            return False
        if params.get("stop_walking"):
            return False
        movement = world.movement
        try:
            return bool(movement is not None and movement.has_active_journey(actor_id))
        except Exception:
            return False
    if tool_name != "move":
        return False
    from world.tools.place_aliases import satisfies_gate

    wanted = params.get("destination_location_id")
    if not isinstance(wanted, str) or not wanted:
        return False
    try:
        return bool(satisfies_gate(world.location_of(actor_id), wanted))
    except Exception:
        # Cannot establish it, so charge - the existing behaviour, and
        # never the surprising one.
        return False


# THE FIRST ATTEMPT AT TRADE-06 WAS A FOLD, AND IT WAS WRONG.
#
# TRADE-04 folds reads and leaves 136 of run `deni-a6f9eb4-5h`'s 199
# repeats standing - 125 of them `move` (64) and `rest` (61). Both look
# idempotent. Neither is.
#
# `rest` is a unit of WORK. `keep_doing` repeats it through this same
# dispatch precisely so each repetition earns proficiency and is charged
# - that identity is the mechanism behind "a mastered trade is a cheap
# one" (`test_proficiency_rises_under_a_continuing_action`). Folding it
# collapsed three repetitions into one and silently disabled the
# feature.
#
# `move` to where you already stand still has to PUBLISH. Its
# `agent.moved` carries real tile coordinates and `arrived: True`, and
# that event is how perception and the map learn where the resident is
# (`test_move_already_at_destination_is_zero_tick_but_still_real`).
# Short-circuiting it before the handler dropped the event.
#
# So the waste is real and measured, and the fold is not the way to get
# it. Charging nothing for a `move` that goes nowhere is still probably
# right - but it has to happen inside the handler, keeping the event,
# and it needs the charge path to be told rather than bypassed. That is
# a change to how `dispatch` prices a call, not a repeat rule.

def _signature(tool_name: str, params: dict[str, Any]) -> str:
    return tool_name + "|" + json.dumps(params, sort_keys=True, default=str)


def already_answered_this_tick(
    world: Any, actor_id: str, tool_name: str, params: dict[str, Any]
) -> bool:
    """Has this resident already asked exactly this, on this tick?

    Reads `world.tool_invocations`, which `dispatch` already appends to
    for every completed call - so this needs no second bookkeeping and
    cannot drift from what actually happened.
    """
    if tool_name not in READ_ONLY_TOOLS:
        return False
    tick = world.tick()
    wanted = _signature(tool_name, params)
    for record in reversed(world.tool_invocations or ()):
        if record.tick != tick:
            # `tool_invocations` is append-ordered, so the first record
            # from an earlier tick means there are no more from this one.
            break
        if record.actor_id != actor_id:
            continue
        if _signature(record.tool_name, record.params) == wanted:
            return True
    return False


REPEAT_NOTE = (
    "You already asked this on this turn and nothing has changed since - "
    "between two of your own calls, only you can change the town. This one "
    "was not charged."
)
