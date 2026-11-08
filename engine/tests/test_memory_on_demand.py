"""A resident can ask its own memory a question, and learning runs by day.

Two specs, one commit, because they are the two halves of the same
complaint: what a resident learns does not reach the next turn.

**Memory On Demand.** Recall runs once, automatically, on the opening
words of a turn. If it misses there is no second question - no tool, no
fallback. The retrieval that would answer one is written and dead:
`Search.unified()` has no callers anywhere in the tree, and it is the only
path that reads `learning_signals`, the table a correction lands in before
the batch encodes it. So a correction the world issued this morning is
durable, searchable, and invisible.

**The cadence.** `SimLearningGate` runs a batch at the wake-to-asleep
edge and never otherwise. Every run this project has made starts at dawn
and dies before dusk, so `learning_ran` is zero in every session log ever
checked - not because the gate is broken, but because the trigger it
waits for has never happened. Twenty runs, no learning, and it was read
three times as "the residents did not learn".

Law 1 holds throughout: `src/engine_core/search.py` and
`src/learning_centre/` are frozen and are read, called and imported here,
never edited. Both changes live in `src/sim/`, the seam this package owns.
"""

from __future__ import annotations

import asyncio


from src.sim.learning_gate import SimLearningGate


class _Pipeline:
    def __init__(self) -> None:
        self.runs = 0

    async def run_batch(self):  # noqa: ANN201
        self.runs += 1
        return {"batch": self.runs}


# ── the cadence: learning that does not wait for a night ──────────────


def test_the_sleep_edge_still_runs_a_batch() -> None:
    """The behaviour that already existed must not regress: falling asleep
    is still the moment a day is consolidated."""
    gate = SimLearningGate(pipeline=_Pipeline())
    asyncio.run(gate.on_tick(False))
    asyncio.run(gate.on_tick(True))
    assert gate.pipeline.runs == 1


def test_a_long_sleep_still_runs_exactly_one_batch() -> None:
    gate = SimLearningGate(pipeline=_Pipeline())
    asyncio.run(gate.on_tick(True))
    for _ in range(5):
        asyncio.run(gate.on_tick(True))
    assert gate.pipeline.runs == 1


def test_learning_runs_during_a_day_that_never_reaches_night() -> None:
    """The fix. Twenty runs have started at dawn and stopped before dusk,
    so the sleep edge never arrived and no batch ever ran. A waking
    interval means a run that lives only one morning still consolidates
    that morning."""
    gate = SimLearningGate(pipeline=_Pipeline(), awake_interval_ticks=10)
    for _ in range(25):
        asyncio.run(gate.on_tick(False))
    assert gate.pipeline.runs == 2, (
        "a 25-tick morning at a 10-tick interval should consolidate twice"
    )


def test_the_waking_interval_can_be_switched_off() -> None:
    """Zero means the old behaviour exactly - sleep edge only - so a run
    that wants the original discipline can have it."""
    gate = SimLearningGate(pipeline=_Pipeline(), awake_interval_ticks=0)
    for _ in range(50):
        asyncio.run(gate.on_tick(False))
    assert gate.pipeline.runs == 0


def test_a_waking_batch_does_not_consume_the_sleep_batch() -> None:
    """Falling asleep after a daytime batch must still consolidate the
    rest of the day; otherwise the interval would silently cancel the
    night."""
    gate = SimLearningGate(pipeline=_Pipeline(), awake_interval_ticks=5)
    for _ in range(6):
        asyncio.run(gate.on_tick(False))
    assert gate.pipeline.runs == 1
    asyncio.run(gate.on_tick(True))
    assert gate.pipeline.runs == 2


def test_a_batch_that_raises_does_not_end_the_turn() -> None:
    """Consolidation is bookkeeping about turns that already happened. A
    resident whose tick dies because the learning batch failed has been
    punished for the pipeline's problem."""
    class _Broken:
        async def run_batch(self):  # noqa: ANN201
            raise RuntimeError("model unavailable")

    gate = SimLearningGate(pipeline=_Broken(), awake_interval_ticks=2)
    for _ in range(3):
        assert asyncio.run(gate.on_tick(False)) is None


# ── memory on demand: the second question ─────────────────────────────


def test_the_tool_exists_and_is_read_only() -> None:
    from src.sim.memory_query import build_search_memory_tool

    spec = build_search_memory_tool(search=None)
    assert spec.name == "search_memory"
    # Both are PREDICATES in this engine - `Callable[[dict], bool]` - not
    # flags, so that a tool can be read-only for one input and not another.
    # `search_memory` is read-only for every input there is.
    assert spec.is_read_only({}) is True
    assert spec.is_concurrency_safe({}) is True, (
        "a read-only recall that serialises a parallel batch is a bottleneck "
        "with no reason to exist"
    )


def test_the_description_names_the_trigger_not_the_capability() -> None:
    """A tool the model does not know when to reach for is one it either
    ignores or calls before every action. The description has to say WHEN."""
    from src.sim.memory_query import build_search_memory_tool

    text = build_search_memory_tool(search=None).description.lower()
    assert "already" in text or "automatically" in text, (
        "nothing tells the model recall already ran at the top of the turn"
    )
    assert "do not" in text or "not to" in text, (
        "nothing tells the model when NOT to call it, which is the failure "
        "mode of a memory tool"
    )


def test_it_returns_what_the_frozen_retrieval_returns() -> None:
    from src.sim.memory_query import build_search_memory_tool

    class _Search:
        def __init__(self) -> None:
            self.asked: list[str] = []

        async def unified(self, query: str, limit: int = 20):  # noqa: ANN201
            self.asked.append(query)
            return [
                {"_source": "signals", "content": "the world refused craft at the mill"},
                {"_source": "episodes", "content": "sold three planks to Nell"},
            ]

    search = _Search()
    spec = build_search_memory_tool(search=search)
    out = asyncio.run(spec.handler({"query": "who buys planks"}))
    # ENG-15: the raw question no longer reaches FTS5. It was read there
    # as query SYNTAX, not words - an apostrophe or a question mark was a
    # syntax error, and a colon made the token before it a column name.
    # Every one of those was swallowed into "Nothing in memory matches
    # that", so a resident asking its own memory a plain question was told
    # it had no such memory. This test asserted the raw string went
    # through, which is exactly the behaviour the finding is about.
    assert search.asked == ['"who" OR "buys" OR "planks"']
    assert "planks" in out


def test_pending_signals_are_labelled_as_not_yet_consolidated() -> None:
    """A raw correction and an encoded fact must not read alike: the model
    is weighing one against the other and needs to know which is which."""
    from src.sim.memory_query import build_search_memory_tool

    class _Search:
        async def unified(self, query: str, limit: int = 20):  # noqa: ANN201
            return [{"_source": "signals", "content": "craft needs a kitchen"}]

    out = asyncio.run(build_search_memory_tool(search=_Search()).handler({"query": "craft"}))
    assert "not yet consolidated" in out.lower() or "unconsolidated" in out.lower()


def test_the_limit_is_bounded_however_the_model_asks() -> None:
    """Recall competes with the turn's real work for context. A tool that
    can return a hundred rows is a tool that will."""
    from src.sim.memory_query import MAX_LIMIT, build_search_memory_tool

    seen: dict = {}

    class _Search:
        async def unified(self, query: str, limit: int = 20):  # noqa: ANN201
            seen["limit"] = limit
            return []

    spec = build_search_memory_tool(search=_Search())
    asyncio.run(spec.handler({"query": "x", "limit": 500}))
    assert seen["limit"] <= MAX_LIMIT


def test_no_memory_at_all_is_said_plainly() -> None:
    from src.sim.memory_query import build_search_memory_tool

    class _Empty:
        async def unified(self, query: str, limit: int = 20):  # noqa: ANN201
            return []

    out = asyncio.run(build_search_memory_tool(search=_Empty()).handler({"query": "x"}))
    assert "nothing" in out.lower() or "no " in out.lower()


def test_a_broken_store_answers_rather_than_raising() -> None:
    """A recall failure must read as data, exactly like a refused world
    tool - the agent loop treats a bad move as information, not a crash."""
    from src.sim.memory_query import build_search_memory_tool

    class _Broken:
        async def unified(self, query: str, limit: int = 20):  # noqa: ANN201
            raise RuntimeError("db locked")

    out = asyncio.run(build_search_memory_tool(search=_Broken()).handler({"query": "x"}))
    assert isinstance(out, str) and out


def test_the_boot_path_registers_it_with_a_real_search() -> None:
    """A tool nobody registers, reading a Search nobody built, is the
    exact shape of the dead `unified()` this spec exists to revive."""
    from pathlib import Path

    boot = Path(__file__).resolve().parent.parent / "src" / "sim" / "pinned_boot.py"
    text = boot.read_text(encoding="utf-8")
    assert "build_search_memory_tool" in text
    assert "Search(" in text, "no Search instance is ever constructed"


def test_the_pruner_does_not_strip_it_from_a_resident() -> None:
    """`prune_to_the_towns_tools` removes the engine's seven
    assistant-shaped built-ins. It works from a remove-list, so a newly
    registered tool survives - but "survives because of how the list
    happens to be written" is worth an assertion, not an assumption."""
    from src.sim.memory_query import SEARCH_MEMORY_TOOL_NAME
    from src.sim.tool_surface import NOT_A_RESIDENTS_TOOL

    assert SEARCH_MEMORY_TOOL_NAME not in NOT_A_RESIDENTS_TOOL
