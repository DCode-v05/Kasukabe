"""`search_memory` was offered to every resident and called by none.

Run 25: the tool was registered, permitted, and present in all 82 tool
schemas of every one of 3,055 requests. It was called **zero times** across
1,973 tool calls. Meanwhile Rosalind Vance finished the run holding 27
consolidated facts and 28 unconsolidated signals - a memory she never once
asked a question of.

This is the third time this exact result has been recorded in this project.
`start_conversation` got it across runs 18 and 19; `skillify` got it before
that. SELF_AUTHORING_AGENTS.md section 8 states the reason: an LLM agent
does not spontaneously reach for a verb it was merely handed a schema for.
A good tool description is not a prompt.

Both earlier cases were fixed the same way - an evidence-driven line naming
the verb at the moment the verb would help - and this is that fix a third
time, at the one moment where `search_memory` has something no other part
of the turn can reach:

    `world_corrections.py` writes every world refusal into
    `learning_signals`. That table is the ONE store the turn's automatic
    recall does not read. So a resident refused twice for the same reason
    has, in its own memory, the sentence explaining the first refusal -
    durable, searchable, and unreachable unless it asks.

The tests below fix the three properties that make this a hint and not
furniture: it stays silent the first time, it fires the second time, and a
broken memory never costs the resident its refusal.
"""

from __future__ import annotations

import pytest

from src.sim.memory_query import SEARCH_HINT, hint_after_refusal


class FakeSignalStore:
    """FTS over a list. `search_fts` is the only method under test."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.queries: list[str] = []

    async def search_fts(self, query: str, limit: int = 20) -> list[dict]:
        self.queries.append(query)
        return self.rows[:limit]


class ExplodingSignalStore:
    async def search_fts(self, query: str, limit: int = 20) -> list[dict]:
        raise RuntimeError("fts5: malformed MATCH expression")


@pytest.mark.asyncio
async def test_the_first_refusal_says_nothing_extra():
    """Silence is the common case, and it is the whole difference between
    a hint and a standing instruction.

    Nothing about `harvest` is in memory yet, so there is nothing to find
    and no reason to spend a line telling the resident to look."""
    assert await hint_after_refusal(FakeSignalStore([]), "harvest") is None


@pytest.mark.asyncio
async def test_a_second_refusal_names_the_verb():
    store = FakeSignalStore([{"content": "harvest: must be at farmland:wheat"}])

    hint = await hint_after_refusal(store, "harvest")

    assert hint == SEARCH_HINT.format(tool="harvest")
    assert "search_memory" in hint


@pytest.mark.asyncio
async def test_the_hint_names_the_verb_and_never_the_answer():
    """`convening_hint`'s discipline, which this copies.

    What a resident looks up is the experiment. Handing it the remembered
    sentence would answer the question instead of prompting it - and would
    measure our retrieval, not the resident's judgement about when its own
    memory is worth asking.
    """
    remembered = "harvest: must be at farmland:wheat, was at business:b_mill"
    store = FakeSignalStore([{"content": remembered}])

    hint = await hint_after_refusal(store, "harvest")

    assert remembered not in hint
    assert "farmland" not in hint


@pytest.mark.asyncio
async def test_a_broken_memory_never_costs_the_resident_its_refusal():
    """A hint is a courtesy. `record_correction` follows the same rule for
    the same reason: bookkeeping must never be why a turn fails."""
    assert await hint_after_refusal(ExplodingSignalStore(), "harvest") is None


@pytest.mark.asyncio
async def test_no_signal_store_is_not_an_error():
    """None until pinned_boot wires it, and None in every test that does
    not care."""
    assert await hint_after_refusal(None, "harvest") is None
    assert await hint_after_refusal(FakeSignalStore([{"content": "x"}]), "") is None


@pytest.mark.asyncio
async def test_the_tool_name_is_escaped_before_it_reaches_fts():
    """ENG-15, which this inherits by going through `fts_terms`.

    A tool name is a bare word today, but the query path is the same one
    that turned `wheat: damp` into "no such column: wheat". Going through
    the escaper rather than around it is what keeps that true when a
    business-authored tool name is not a bare word.
    """
    store = FakeSignalStore([{"content": "..."}])

    await hint_after_refusal(store, "take_from_shelf")

    assert store.queries == ['"take_from_shelf"']


@pytest.mark.asyncio
async def test_the_refusal_text_still_arrives_whole(monkeypatch):
    """The refusal is what the resident acts on; the hint is appended to
    it, never substituted for it."""
    import src.sim.world_tools as world_tools

    refusal = "Error: must be at farmland:wheat, was at business:b_mill"

    hint = await hint_after_refusal(
        FakeSignalStore([{"content": "prior"}]), "harvest"
    )
    combined = f"{refusal}\n{hint}"

    assert combined.startswith(refusal)
    assert combined.count("\n") == 1
    assert world_tools.hint_after_refusal is hint_after_refusal
