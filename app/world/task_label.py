"""What a turn was FOR, decided from the trace and nothing else.

Evaluation spec section 4. Its opening sentence is the whole reason this
file exists: *"C1 cannot be measured without one, and no turn carries one
today."*

C1 is the roadmap's first compounding quantity - median tokens per turn,
per task, per resident, day five against day one. Without a label there is
nothing to group by, so all of section 2.4 is unmeasurable rather than
merely unmeasured, and "the residents got more efficient" stays an
impression.

DETERMINISTIC, NEVER A MODEL. The spec is explicit and the reason is not
cost: a label a judge assigned would make C1 a measurement of the judge.
Every rule below reads the tools a turn actually called.
"""

from __future__ import annotations

from world.ledger.action_costs import DEFAULT_SCHEDULE, ActionCostCategory

# Rules 4 and 5. Speech and observation are the two ways a turn can be
# real work to a person and no work at all to the ledger.
SPEECH_TOOLS = frozenset({"say", "start_conversation", "convene"})
OBSERVATION_TOOLS = frozenset({
    "look_around", "list_places", "describe_place", "describe_person",
    "describe_primitives", "quote_action_cost", "find_people", "read_will",
    "recall_memories", "recall_session_log", "search_memory", "check_debts",
    "move", "rest",
})
# Rule 3's categories, from the cost schedule rather than a second list
# that could drift from it.
WORK_CATEGORIES = frozenset({ActionCostCategory.LABOUR, ActionCostCategory.TRADE})


def is_work(tool: str) -> bool:
    rule = DEFAULT_SCHEDULE.rule_for(tool)
    return rule is not None and rule.category in WORK_CATEGORIES


def label_for_turn(
    *,
    tools_called: list[str],
    continuing_tool: str | None = None,
    intention_tool: str | None = None,
) -> str:
    """The spec's six rules, in the spec's order.

    The order is an order and not a set: what a resident IS doing beats
    what it said it would do, and both beat what it happened to call.
    """
    if continuing_tool:
        return continuing_tool
    if intention_tool:
        return intention_tool

    # FIRST work tool, not last. A turn that harvests and then sells is a
    # harvesting turn that ended in a sale; grouping it under `sell` would
    # hide the work C1 is trying to see get cheaper.
    for tool in tools_called:
        if is_work(tool):
            return tool

    if not tools_called:
        return "idle"
    if all(tool in SPEECH_TOOLS for tool in tools_called):
        return "talk"
    # EVAL-04: observation ONLY, per the spec's rule 5 - "`look` if it
    # called only observation tools". This used to admit SPEECH_TOOLS as
    # well, which made a turn of `["look_around", "say"]` a `look` here and
    # an `idle` in stakes_report's copy. Deduplicating the two forced the
    # question of which was right, and the spec answers it: a turn that
    # both looked and spoke is neither rule 4 nor rule 5, so it falls
    # through to rule 6.
    if all(tool in OBSERVATION_TOOLS for tool in tools_called):
        return "look"
    return "idle"
