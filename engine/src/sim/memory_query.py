"""A resident's second question to its own memory.

THE GAP, from the Memory On Demand spec. Recall runs once per turn,
automatically, against the user's opening words. If it misses there is no
second question - no tool, no fallback. And the retrieval that would
answer one is already written: `Search.unified()` in
`src/engine_core/search.py` is hybrid, cross-store, cached, with entity
fusion, and has **no callers anywhere in the tree**. It was written for a
caller that never arrived.

WHY IT MATTERS MORE HERE THAN IN THE PRODUCT IT WAS WRITTEN FOR.
`unified()` is the only retrieval path that reads `learning_signals` - the
table a correction lands in BEFORE the batch encodes it. Since
`world_corrections.py`, every refusal the world issues writes a
`correction` row there. Without this tool, a resident refused at the mill
this morning has that correction sitting durable, searchable, and
unreachable until the next batch runs; with it, the resident can ask.

WHAT IS DELIBERATELY NOT HERE. It reads. Durable memory is written by the
learning batch, and that asymmetry is load-bearing: it is why a
pipeline-inferred fact is capped below a stated one, and why nothing in a
turn can promote its own guess into permanent memory. A "remember this"
verb is a separate decision with its own confidence policy.

LAW 1. `Search` and `unified()` are frozen engine code. This module
imports and calls them; it does not touch them. The registration lives in
`pinned_boot.py`, the same seam that already registers the memory stream
and prunes the engine's assistant-shaped tools.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from src.sandbox.permissions import PermissionBehavior, PermissionRule, ToolSource
from src.utility_directory.tool_runtime import ToolDef

logger = logging.getLogger("kasukabe.sim.memory_query")

SEARCH_MEMORY_TOOL_NAME = "search_memory"

# ── the rule without which the verb is never callable ────────────────────
#
# `thread.py`'s `check_permission()` denies by default, so a registered
# tool with no rule is silently never callable. Both siblings learned this
# and wrote it down - `session_log.py`'s rule exists because "no
# ToolExecution 'result' phase ever fired; the call was denied before the
# handler ran", and `memory_stream.py` mirrors it. This module registered
# its tool and never got one.
#
# It went unnoticed because it fails in the quietest way the engine has.
# The denial happens before the handler, so no result event fires, so
# `session_log` writes the call with no `status` at all - and a row with no
# outcome does not look like a failure to anything that reads it. The
# turn was billed, the resident was answered nothing, and the trace says
# only that a call was made.
#
# The timing is the sharp part. `search_memory` was called ZERO times in
# run 25, diagnosed above as a reaching problem, and fixed with SEARCH_HINT
# naming the verb at the moment it would help. That worked: the corpus now
# holds 29 calls across civic12 and the two 50-resident runs. Every one of
# them was denied here. The hint earned the reach and this line was where
# the payoff went.
#
# Found by `proof/reach.py`, which declines to score a call whose row
# carries no outcome - "the corpus does not say whether the town answered,
# so neither success nor refusal is evidenced here" - instead of counting
# 29 silent rows as 29 refusals and moving on.
SEARCH_MEMORY_TOOL_ALLOW_RULE = PermissionRule(
    tool=SEARCH_MEMORY_TOOL_NAME,
    pattern="",
    behavior=PermissionBehavior.ALLOW,
    reason="an agent searching its own memory is always safe - read-only, self-scoped",
    source="engine",
)


def install_search_memory_permissions(thread: Any) -> None:
    """Grant the search_memory allow rule on an already-constructed thread -
    same idiom as memory_stream.install_memory_stream_permissions."""
    if SEARCH_MEMORY_TOOL_ALLOW_RULE not in thread.session_rules:
        thread.session_rules.append(SEARCH_MEMORY_TOOL_ALLOW_RULE)

# Recall competes with the turn's real work for context. A tool that can
# return a hundred rows is a tool that will.
def fts_terms(query: str) -> str:
    """A natural-language question, as an FTS5 MATCH expression.

    ENG-15. Each word becomes a double-quoted literal, so nothing in it is
    read as syntax: not the apostrophe in "miller's", not the question
    mark, and above all not the colon, which FTS5 treats as a
    column-name separator.

    Joined with OR rather than the implicit AND. A memory search is a
    RECALL question - "what did the miller say" should return what is
    known about the miller even if no single row holds every word - and
    the ranking below already puts the closest rows first. AND would
    answer a five-word question with silence, which is the behaviour this
    finding is about.

    An internal double quote is doubled, which is how FTS5 escapes one.
    """
    import re

    words = [w for w in re.split(r"[^0-9A-Za-z_]+", query or "") if w]
    # Single characters are dropped as NOISE: "miller's" splits to
    # (miller, s), and a bare "s" ORed in matches half the store.
    #
    # Only when there is something else to search for, though. A query
    # that is nothing BUT a short token is a real query - the resident
    # meant it - and dropping it turns a search into "ask me something
    # with words in it", which is a refusal the resident cannot act on.
    # The first draft of this dropped them unconditionally and broke
    # exactly that case.
    meaty = [w for w in words if len(w) > 1]
    words = meaty or words
    return " OR ".join('"' + w.replace('"', '""') + '"' for w in words)


DEFAULT_LIMIT = 10
MAX_LIMIT = 25
MAX_ROW_CHARS = 300

# `_source` values `unified()` tags its rows with, and the heading each
# gets. "signals" is labelled unconsolidated on purpose: a raw correction
# and an encoded fact must not read alike, because the model is weighing
# one against the other.
SOURCE_HEADINGS = {
    "episodes": "WHAT HAPPENED",
    "knowledge": "WHAT YOU KNOW",
    "social": "PEOPLE",
    "signals": "RECENT, NOT YET CONSOLIDATED",
}


class Retrieval(Protocol):
    async def unified(self, query: str, limit: int = 20) -> list[dict]: ...


def _render(rows: list[dict]) -> str:
    """Grouped by source, with the source named, longest rows elided."""
    if not rows:
        return (
            "Nothing in memory matches that. It may not have happened yet, or "
            "it may have happened to somebody else."
        )
    grouped: dict[str, list[str]] = {}
    for row in rows:
        source = str(row.get("_source") or "episodes")
        text = str(row.get("content") or row.get("value") or row.get("summary") or "").strip()
        if not text:
            continue
        if len(text) > MAX_ROW_CHARS:
            text = text[:MAX_ROW_CHARS].rstrip() + "..."
        grouped.setdefault(source, []).append(text)

    out: list[str] = []
    for source, lines in grouped.items():
        out.append(SOURCE_HEADINGS.get(source, source.upper()))
        out.extend(f"  {line}" for line in lines)
    return "\n".join(out)


def build_search_memory_tool(search: Retrieval | None) -> ToolDef:
    """The tool, or a tool that says so when there is no retrieval behind it."""

    async def handler(tool_input: dict[str, Any]) -> str:
        query = str(tool_input.get("query") or "").strip()
        if not query:
            return "Ask memory something: pass `query` in your own words."
        if search is None:
            return "Memory search is not available in this engine."

        raw_limit = tool_input.get("limit", DEFAULT_LIMIT)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        # ENG-15: hand FTS5 something it can parse.
        #
        # The raw question went straight to `MATCH`, where FTS5 reads it as
        # QUERY SYNTAX rather than as words. Measured on a bare FTS5 table:
        #
        #   "wheat"                    -> 1 row
        #   "miller's wheat"           -> syntax error near "'"
        #   "wheat: damp"              -> no such column: wheat
        #   "what did the miller say?" -> syntax error near "?"
        #
        # A colon is the worst of them: FTS5 takes the token before it as a
        # COLUMN NAME, so an ordinary sentence becomes a schema error. Every
        # one of these was caught by `search.py` and turned into an empty
        # list, which this tool renders as "Nothing in memory matches that."
        # A resident asking its own memory a plain question was told it had
        # no such memory - and there is no way to tell that apart from
        # genuinely having none.
        terms = fts_terms(query)
        if not terms:
            return "Ask memory something with words in it."
        try:
            rows = await search.unified(terms, limit=limit)
        except Exception as exc:  # noqa: BLE001
            # Read as data, not as a crash - the same rule world tool calls
            # follow, so a bad move is information the agent can act on.
            logger.warning("memory search failed for %r: %s", query, exc)
            return f"Memory could not be searched just now ({exc})."
        return _render(list(rows or []))

    return ToolDef(
        name=SEARCH_MEMORY_TOOL_NAME,
        description=(
            "Search your own memory - what you have done, what you have learned, "
            "the people you know, and corrections the world has given you that "
            "are not yet settled. Recall already runs automatically at the start "
            "of a turn against what you were just told; use this when that was "
            "not enough: the subject has moved on, somebody refers to something "
            "you cannot see, or you want to check whether you already know a "
            "price, a place or a person before assuming you do not. "
            "Do not call it to confirm something already in front of you."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to look for, in your own words.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many to return (default {DEFAULT_LIMIT}, max {MAX_LIMIT}).",
                },
            },
            "required": ["query"],
        },
        handler=handler,
        source=ToolSource.INTERNAL,
        is_concurrency_safe=lambda _: True,
        is_read_only=lambda _: True,
    )


# ── the line that names the verb ──────────────────────────────────────
#
# `search_memory` shipped registered, offered in all 82 tool schemas of
# every request in run 25, and was called ZERO times across 1,973 tool
# calls. That is the third time this exact result has been recorded here:
# `start_conversation` got it across runs 18 and 19, `skillify` got it
# before that, and SELF_AUTHORING_AGENTS.md section 8 states the reason —
# an LLM agent does not spontaneously reach for a verb it was merely
# handed a schema for. A good description is not a prompt.
#
# The fix that worked twice is an evidence-driven line naming the verb at
# the moment the verb would help, and this is that fix a third time.
# `world.tools.state.convening_hint` is the model; its discipline is
# copied deliberately:
#
#   NOT a standing instruction. A permanent "you may search your memory"
#   costs every resident a line every turn, carries no reason at any
#   particular moment, and reads as furniture.
#
#   NAMES THE VERB, NEVER THE SUBJECT. What a resident chooses to look up
#   is the experiment. Telling it what it already knows would answer the
#   question instead of prompting it.
#
# WHY A REFUSAL IS THE MOMENT. `world_corrections.py` writes every world
# refusal into `learning_signals`, and `learning_signals` is the ONE store
# the turn's automatic recall does not read — that is the blind window
# both specs are about. So a resident refused for the same reason twice
# has, sitting in its own memory, the sentence explaining the first
# refusal, durable and searchable and unreachable. This fires exactly
# there: not on every refusal, only on one this resident has already been
# given before.

SEARCH_HINT = (
    "You have been told something about `{tool}` before that has not "
    "settled yet. `search_memory` will find it."
)


async def hint_after_refusal(signal_store: Any | None, tool_name: str) -> str | None:
    """One line, or None — which is the common case.

    Asked BEFORE this turn's correction is written, so any hit is prior by
    construction and there is no need to discount the row we are about to
    add. The caller in `world_tools.py` keeps that ordering.

    Never raises. A hint is a courtesy; a resident whose FTS query failed
    should still get its refusal, unchanged. This is the same rule
    `record_correction` follows for the same reason.
    """
    if signal_store is None or not tool_name:
        return None
    terms = fts_terms(tool_name)
    if not terms:
        return None
    try:
        prior = await signal_store.search_fts(terms, limit=1)
    except Exception as exc:  # noqa: BLE001
        logger.debug("no refusal hint for %s: %s", tool_name, exc)
        return None
    if not prior:
        return None
    return SEARCH_HINT.format(tool=tool_name)
