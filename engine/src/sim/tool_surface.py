"""What a resident of Kasukabe is actually handed, and what it is not.

`Thread.get_or_create_thread` registers the engine's own built-in tools
onto the ToolRegistry this package builds and passes in. That is correct
for the product this engine was written for and wrong for a town.
Measured on the last cognition call of data/loop5.db:

    61 tools served: 52 world, 9 engine
    engine-tool schema bytes: 6,887 of 41,569

Two of the nine belong to a resident. `recall_session_log` and
`recall_memories` are a person remembering their own life, and every
report this project has written asks for exactly that. The other seven
describe a world that does not exist here - a user to ask, a mission
brief to report against, an operational scope to expand, a task's content
store to search - and they are handed verbatim to a miner and a baker.

They are not merely useless. Across three runs residents called
`request_replan` four times and `list_content` once: five turns of five
lives spent on machinery that could never have answered, plus 6,887 bytes
of prompt on every call describing it.

WHY HERE. thread.py is frozen under ROADMAP Law 1, and it is right to be:
the registration is correct for its own product. What is wrong is the
surface a RESIDENT sees, and that is this package's business.
`registry.tools` is a plain dict, so the removal is a dict operation
after the thread is built, with no change to frozen code and no fork.

WHAT THIS IS NOT. It is not a permission check. `world_tools.install_
permissions` still governs what may be CALLED; this governs what is
DESCRIBED. A tool nobody can see costs nothing and confuses nobody, which
is the cheaper of the two ways to say no.

AN EIGHTH, LATENT ONE. Seven is the MEASURED count - what loop5.db shows
actually on the wire. `create_skill` was not among them because
`thread.py:1823` returns early on an empty `data_dir`, and this boot
passes none. It is on the list below anyway, because it is one seam
argument away from being served and the review finding that invites that
argument (ENG-06) is open. See its entry for what it would say to a
miner.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kasukabe.sim.tool_surface")

NOT_A_RESIDENTS_TOOL: frozenset[str] = frozenset(
    {
        "ask_user",
        "request_replan",
        "gap_resolution",
        "report_checkpoint",
        "list_content",
        "search_content",
        "tool_search",
        "create_skill",
    }
)


def prune_to_the_towns_tools(thread: Any) -> list[str]:
    """Remove the assistant's tools from a built thread. Returns what went.

    MUST run AFTER `coordinator.get_or_create_thread`, which is the call
    that registers them. Running it before removes nothing and would pass
    every test that did not check the order - which is why
    app/world/tests/test_residents_get_the_towns_tools.py checks it.

    Never raises. A thread shaped some way this function did not expect is
    a reason to leave the surface alone and say so, not a reason to fail a
    boot: an engine that will not start records nothing at all, which is
    strictly worse than one that starts with seven tools too many.
    """
    registry = getattr(thread, "tools", None)
    tools = getattr(registry, "tools", None)
    if not isinstance(tools, dict):
        logger.warning(
            "tool surface left alone: thread.tools is %r, not a registry with "
            "a tools dict - residents keep the assistant's tools this run",
            type(registry).__name__,
        )
        return []

    removed = sorted(name for name in list(tools) if name in NOT_A_RESIDENTS_TOOL)
    for name in removed:
        del tools[name]
    logger.info(
        "tool surface: %d tools served, removed %s",
        len(tools),
        ", ".join(removed) or "nothing",
    )
    return removed
