"""ENG-01: the learning centre must be able to write a row.

`pinned_boot` built `MemorySystem(working_memory=working_memory)` and
nothing else, and `ConsciousnessManager()` with no arguments. Every other
component was None, and `MemorySystem` raises RuntimeError on a write to a
None component - thirteen raise sites.

The learning pipeline catches those into `result.errors` and carries on, so
a batch reported cleanly while writing nothing. Run 23 shows
`auto:learning_centre.*` calls at 55-108K input tokens returning 6-14 output
tokens, over and over, for nothing; run 22 left 7,138 signals unconsolidated.

`consolidate()` returns early when no episode and no knowledge was written,
so soul updates never ran either. Every compounding quantity this project
measures was zero by construction rather than by run length.
"""
from __future__ import annotations

import inspect

import pytest


# ── the wiring ──────────────────────────────────────────────────────────

def test_boot_constructs_all_four_stores():
    """All four take `embedding_service=None` and skip the vector insert, so
    there is no cost argument for leaving them out."""
    from src.sim import pinned_boot

    src = inspect.getsource(pinned_boot)
    for store in ("EpisodicMemory(db", "KnowledgeStore(db", "SocialGraph(db", "Soul(db)"):
        assert store in src, (
            f"{store}...) is never constructed, so every write through it "
            "raises RuntimeError into result.errors and the batch reports "
            "success having written nothing (ENG-01)"
        )


def test_the_memory_system_is_given_them():
    """The stores existing is not the fix; MemorySystem holding them is."""
    from src.sim import pinned_boot

    src = inspect.getsource(pinned_boot)
    i = src.index("memory_system = MemorySystem(")
    block = src[i : src.index(")", src.index("soul=", i))]
    for field in ("episodic_memory=", "knowledge_store=", "social_graph=", "soul="):
        assert field in block, f"MemorySystem is built without {field}"


def test_the_consciousness_manager_is_given_them_too():
    """Otherwise the DYNAMIC layer renders nothing and a resident's prompt
    never carries what it learned."""
    from src.sim import pinned_boot

    src = inspect.getsource(pinned_boot)
    i = src.index("consciousness_mgr = ConsciousnessManager(")
    block = src[i : src.index(")", src.index("knowledge_store=", i))]
    for field in ("soul=", "social_graph=", "episodic_memory=", "knowledge_store="):
        assert field in block


def test_a_batch_that_writes_something_invalidates_the_prompt():
    from src.sim import pinned_boot

    src = inspect.getsource(pinned_boot)
    i = src.index("lc_pipeline = LCPipeline(")
    assert "on_data_changed=" in src[i : i + 600], (
        "the cached prompt prefix is never invalidated, so a resident that "
        "learns something keeps being shown a prompt that predates it"
    )


# ── the guard ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "error",
    [
        "EpisodicMemory not configured",
        "knowledge_store is None",
        "MemorySystem.sg not configured",
    ],
)
def test_an_unconfigured_error_is_recognised(error):
    from src.sim.agent_adapter import _is_unconfigured

    assert _is_unconfigured(error) is True


@pytest.mark.parametrize(
    "error",
    ["broker refused the call", "json decode failed", "timed out after 120s"],
)
def test_a_real_failure_is_not_mistaken_for_a_wiring_fault(error):
    from src.sim.agent_adapter import _is_unconfigured

    assert _is_unconfigured(error) is False, (
        "a genuine pipeline failure must not be reported as a wiring fault - "
        "that would send the next reader to the wrong place"
    )


# ── behaviour: the stores actually write, with no embedder ──────────────

@pytest.mark.asyncio
async def test_an_episode_reaches_the_database_with_no_embedder(db):
    """The real risk, tested rather than assumed.

    The stores are built with `embedding_service=None` so the town pays for
    no embedding calls. That is only safe if the write path skips the vector
    insert instead of failing on it - so this writes a real episode through
    a real SqlitePool and reads the row back.
    """
    from src.memory.episodic_memory import EpisodicMemory

    episodic = EpisodicMemory(db, None)
    await episodic.create_episode(
        key="baked-bread-day-0",
        description="I baked bread before dawn",
        value="Flour from the mill, sold four loaves to Wren.",
    )

    rows = await db.fetch("SELECT key FROM episodes")
    assert len(rows) == 1, (
        f"{len(rows)} episodes after writing one - the write path needs an "
        "embedder, so building the store without one buys nothing (ENG-01)"
    )
    assert rows[0]["key"] == "baked-bread-day-0"


@pytest.mark.asyncio
async def test_memory_system_no_longer_raises_on_a_write(db):
    """The thirteen raise sites, closed.

    `MemorySystem` raises RuntimeError on a write to a None component. With
    the stores passed it must not - and this is the exact call the learning
    pipeline makes and swallows into `result.errors`.
    """
    from src.memory.episodic_memory import EpisodicMemory
    from src.memory.knowledge_store import KnowledgeStore
    from src.memory.memory_system import MemorySystem
    from src.memory.social_graph import SocialGraph
    from src.memory.soul import Soul

    system = MemorySystem(
        working_memory=None,
        episodic_memory=EpisodicMemory(db, None),
        knowledge_store=KnowledgeStore(db, None),
        social_graph=SocialGraph(db, None),
        soul=Soul(db),
    )
    await system.create_episode(key="k", description="d", value="v")
    assert len(await db.fetch("SELECT key FROM episodes")) == 1


@pytest.mark.asyncio
async def test_a_bare_memory_system_still_raises(db):
    """The failure the fix removes, pinned so the test means something."""
    from src.memory.memory_system import MemorySystem

    with pytest.raises(RuntimeError):
        await MemorySystem(working_memory=None).create_episode(
            key="k", description="d", value="v"
        )


def test_the_route_shouts_when_nothing_is_configured(caplog):
    """The log line that would have surfaced this in 23 runs.

    A batch that wrote nothing BECAUSE nothing was configured is a different
    failure from a batch that had nothing to say, and from here the two
    looked identical.
    """
    import logging

    from src.sim import agent_adapter

    src = inspect.getsource(agent_adapter)
    assert "_is_unconfigured" in src
    i = src.index("if failures and all(_is_unconfigured")
    block = src[i : i + 900]
    assert "logger.error" in block, (
        "a wiring fault is logged at a level nobody reads, which is how "
        "ENG-01 survived 23 runs"
    )
    assert "wiring fault" in block
