"""A resident can ask its own memory a question in words.

`ENG-15`. `search_memory` passed the resident's raw question straight to
FTS5 `MATCH`, where it is read as QUERY SYNTAX rather than as words.
Measured against a bare FTS5 table:

    "wheat"                    -> 1 row
    "miller's wheat"           -> syntax error near "'"
    "wheat: damp"              -> no such column: wheat
    "what did the miller say?" -> syntax error near "?"

The colon is the worst of them: FTS5 takes the token before it as a
COLUMN NAME, so an ordinary sentence becomes a schema error.

Every one of those was caught by `search.py` and turned into an empty
list, which the tool renders as "Nothing in memory matches that." A
resident asking its own memory a plain question was told it had no such
memory - and nothing distinguishes that from genuinely having none. The
one instrument a resident has for recalling its own life answered
silence to three questions in four.

OR, not AND. A memory search is a recall question: "what did the miller
say" should surface what is known about the miller even when no single
row holds every word. The implicit AND would answer a five-word question
with silence, which is the behaviour this finding is about.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.sim.memory_query import fts_terms


@pytest.fixture
def fts():
    """A real FTS5 table. The whole finding is about what FTS5 accepts,
    so a fake matcher would prove nothing."""
    conn = sqlite3.connect(":memory:")
    conn.execute("create virtual table t using fts5(body)")
    conn.execute("insert into t values ('the miller said the wheat is damp')")
    conn.execute("insert into t values ('the baker wants flour')")
    return conn


def _match(conn, query: str) -> int:
    return len(
        conn.execute("select * from t where t match ?", (fts_terms(query),)).fetchall()
    )


@pytest.mark.parametrize(
    "question",
    [
        "wheat",
        "miller's wheat",
        "wheat: damp",
        "what did the miller say?",
        'he said "damp" twice',
        "flour, wheat; damp",
    ],
)
def test_a_natural_question_does_not_raise(fts, question) -> None:
    """The finding. Three of these four were syntax errors before."""
    try:
        _match(fts, question)
    except sqlite3.OperationalError as exc:
        pytest.fail(
            f"ENG-15: {question!r} is still a MATCH syntax error ({exc}), so "
            "the resident is told its memory holds nothing"
        )


def test_a_question_with_an_apostrophe_finds_the_row(fts) -> None:
    assert _match(fts, "miller's wheat") == 1


def test_a_question_with_a_colon_finds_the_row(fts) -> None:
    """The colon case: FTS5 read `wheat` as a column name."""
    assert _match(fts, "wheat: damp") == 1


def test_a_five_word_question_is_not_answered_with_silence(fts) -> None:
    """OR, not AND. No single row holds every word of an ordinary
    question, and AND would return nothing for all of them."""
    assert _match(fts, "what did the miller say?") >= 1


def test_the_terms_are_quoted_literals() -> None:
    """Each word is a quoted literal, so nothing inside it is syntax."""
    assert fts_terms("wheat damp") == '"wheat" OR "damp"'


def test_an_internal_quote_is_escaped() -> None:
    """FTS5 escapes a double quote by doubling it. A term that closes its
    own quote early is a syntax error again."""
    assert '""' in fts_terms('say "damp"') or fts_terms('say "damp"') == (
        '"say" OR "damp"'
    )


def test_noise_only_input_yields_nothing_to_search() -> None:
    """An empty MATCH expression is itself a syntax error, so the caller
    has to be told rather than handed one."""
    assert fts_terms("!!!") == ""
    assert fts_terms("   ") == ""


def test_a_stray_letter_is_not_searched_for(fts) -> None:
    """"miller's" splits to (miller, s); a bare "s" ORed in matches
    half the store and buries the row that mattered."""
    assert '"s"' not in fts_terms("miller's wheat")


def test_the_tool_says_so_rather_than_searching_for_nothing() -> None:
    """With no searchable words, the handler must not call `unified` with
    an empty expression - that is the syntax error this fix removes,
    reintroduced at the other end."""
    import inspect

    from src.sim import memory_query

    source = inspect.getsource(memory_query)
    assert "if not terms:" in source, (
        "ENG-15: an all-punctuation query still reaches FTS5 as an empty "
        "MATCH, which is a syntax error"
    )


# ── the wiring, not the helper ──────────────────────────────────────────

def test_the_tool_sends_the_sanitised_query_to_search() -> None:
    """`fts_terms` having no caller would be the whole finding again.

    Every test above calls the helper directly, and all of them stayed
    green when the handler was reverted to pass the raw question through -
    a sanitiser with no caller, which is the defect this review keeps
    turning up. This one drives the real handler and looks at what
    `Retrieval.unified` was actually handed.
    """
    import asyncio

    from src.sim.memory_query import build_search_memory_tool

    seen: list[str] = []

    class _Search:
        async def unified(self, query: str, limit: int = 20) -> list[dict]:
            seen.append(query)
            return []

    tool = build_search_memory_tool(_Search())
    asyncio.run(tool.handler({"query": "wheat: damp"}))

    assert seen == ['"wheat" OR "damp"'], (
        f"ENG-15: the handler passed {seen!r} to FTS5. The raw question is "
        "a MATCH syntax error, swallowed into 'Nothing in memory matches "
        "that' - fts_terms is not on the path"
    )


def test_a_query_with_no_words_never_reaches_fts5() -> None:
    """An empty MATCH expression is itself a syntax error, so the caller
    is told rather than handed one."""
    import asyncio

    from src.sim.memory_query import build_search_memory_tool

    seen: list[str] = []

    class _Search:
        async def unified(self, query: str, limit: int = 20) -> list[dict]:
            seen.append(query)
            return []

    tool = build_search_memory_tool(_Search())
    answer = asyncio.run(tool.handler({"query": "!!!"}))

    assert seen == [], "an empty MATCH expression was sent to FTS5"
    assert "words" in answer
