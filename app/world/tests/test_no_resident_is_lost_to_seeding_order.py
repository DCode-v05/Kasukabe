"""No resident may be dropped from the record for being seeded early.

Run 16 lost one resident entirely. `agents.parent_agent_id` is a
self-referencing foreign key, Ainsley Holloway's row named her mother
Hester, and Ainsley is the second agent in the cast while Hester is the
twentieth. SQLite rejected the row for pointing at someone who did not
exist yet, and after that every write naming Ainsley failed the same
foreign key: 66 wallet credits, 45 memories, 13 utterances and one skill,
125 records, each one a WARNING line in a run that carried on.

The report then read the hole as silence. Ainsley speaks seven times in
the run and has no rows at all, so the two conversations she was the
centre of - selling Bram's copper, the errand that took her across the
whole town - exist only in a log file.

Two independent things have to hold, and this file asserts both:

  * a cast seeds completely whatever order its kin appear in, and
  * a write that IS rejected is counted, not just logged.

The second matters even with the first fixed: foreign keys are not the
only way a write can fail, and "logged and carried on" is how 125 records
went missing without anything going red.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supabase.config import SupabaseConfig
from supabase.sqlite_client import SqliteClient
from world.life.models import (
    AgentLifeRecord,
    DormancyState,
    GenesisAgent,
    HealthState,
)
from world.wiring import TraceStore

GENESIS_20 = Path(__file__).resolve().parent.parent / "genesis_20.json"


def _life(agent: dict) -> AgentLifeRecord:
    return AgentLifeRecord(
        agent_id=agent["agent_id"],
        age_years=agent["age_years"],
        sex=agent["sex"],
        household_id=agent["household_id"],
        spouse_id=agent["spouse_id"],
        marital_status=agent["marital_status"],
        parent_ids=list(agent["parent_ids"]),
        children_ids=list(agent["children_ids"]),
        sponsor_parent_id=agent["sponsor_parent_id"],
        health=HealthState(),
        dormancy=DormancyState(),
        alive=True,
    )


@pytest.fixture
def store(tmp_path: Path) -> TraceStore:
    client = SqliteClient(SupabaseConfig(sqlite_path=str(tmp_path / "trace.db")))
    return TraceStore(client)


@pytest.mark.asyncio
async def test_the_whole_cast_seeds_in_genesis_order(store: TraceStore) -> None:
    """The exact incident: genesis_20 as written, seeded in file order,
    with a child appearing eighteen places before her mother."""
    agents = json.loads(GENESIS_20.read_text(encoding="utf-8"))["agents"]
    for agent in agents:
        await store.seed_agent(_life(agent), GenesisAgent.model_validate(agent), tick=0)

    seeded = {
        row[0] for row in store.client.conn.execute("select id from agents")
    }
    missing = sorted({a["agent_id"] for a in agents} - seeded)
    assert not missing, f"residents rejected by the store: {missing}"


@pytest.mark.asyncio
async def test_a_child_seeded_before_its_parent_still_lands(store: TraceStore) -> None:
    """The general case, stated without reference to any one cast: order
    must not decide who exists."""
    agents = json.loads(GENESIS_20.read_text(encoding="utf-8"))["agents"]
    by_id = {a["agent_id"]: a for a in agents}
    child, parent = by_id["n02"], by_id["n20"]
    assert parent["agent_id"] in child["parent_ids"], "fixture assumes this kin link"

    await store.seed_agent(_life(child), GenesisAgent.model_validate(child), tick=0)
    await store.seed_agent(_life(parent), GenesisAgent.model_validate(parent), tick=0)

    rows = {row[0] for row in store.client.conn.execute("select id from agents")}
    assert {"n02", "n20"} <= rows


@pytest.mark.asyncio
async def test_the_kin_link_survives_and_is_not_quietly_dropped(store: TraceStore) -> None:
    """Seeding must not buy its success by throwing the parent away -
    that would trade a loud failure for a silent one, which is the trade
    this whole suite exists to refuse."""
    agents = json.loads(GENESIS_20.read_text(encoding="utf-8"))["agents"]
    for agent in agents:
        await store.seed_agent(_life(agent), GenesisAgent.model_validate(agent), tick=0)

    parent = store.client.conn.execute(
        "select parent_agent_id from agents where id = 'n02'"
    ).fetchone()[0]
    assert parent == "n20", (
        f"Ainsley's mother is recorded as {parent!r}; the row landed but the "
        "kin link it exists to carry did not"
    )


@pytest.mark.asyncio
async def test_every_resident_can_be_spoken_about_once_seeded(store: TraceStore) -> None:
    """The downstream half. A seeded cast has to accept an utterance
    naming any two of its members - that is the foreign key that actually
    broke."""
    agents = json.loads(GENESIS_20.read_text(encoding="utf-8"))["agents"]
    for agent in agents:
        await store.seed_agent(_life(agent), GenesisAgent.model_validate(agent), tick=0)

    ids = [a["agent_id"] for a in agents]
    for speaker, listener in zip(ids, ids[1:] + ids[:1]):
        await store.client.insert(
            "utterances",
            [{
                "speaker_agent_id": speaker,
                "listener_agent_id": listener,
                "conversation_id": f"c-{speaker}",
                "location": "market_square",
                "content": "the price is the price",
                "is_public": True,
                "tick": 1,
            }],
        )

    count = store.client.conn.execute("select count(*) from utterances").fetchone()[0]
    assert count == len(ids), f"{len(ids) - count} utterance(s) refused by the store"


@pytest.mark.asyncio
async def test_a_rejected_write_is_counted_not_merely_logged(store: TraceStore) -> None:
    """An utterance naming somebody who was never seeded is exactly what
    happened 13 times. It must leave a number behind."""
    before = store.stats()["rows_dropped"]

    await store.record_write_failure(
        "agent.utterance.spoken", RuntimeError("FOREIGN KEY constraint failed")
    )

    stats = store.stats()
    assert stats["rows_dropped"] == before + 1
    assert stats["writes_failed"] >= 1
    assert "FOREIGN KEY" in str(stats["last_failure_message"])
    assert stats["last_failure_type"] == "agent.utterance.spoken"


@pytest.mark.asyncio
async def test_the_recorder_halts_once_failures_are_persistent(store: TraceStore) -> None:
    """Three in a row, matching the broker's writers - one blip must not
    stop a run, a broken recorder must."""
    assert not store.is_halted

    for _ in range(2):
        await store.record_write_failure("agent.utterance.spoken", RuntimeError("nope"))
    assert not store.is_halted, "two failures is a blip, not a broken store"

    await store.record_write_failure("agent.utterance.spoken", RuntimeError("nope"))
    assert store.is_halted, "three consecutive rejections must stop being survivable"


@pytest.mark.asyncio
async def test_a_successful_write_clears_the_streak(store: TraceStore) -> None:
    """A recorder that recovers must not stay halted, or a single bad
    tick ends every later run."""
    agents = json.loads(GENESIS_20.read_text(encoding="utf-8"))["agents"]
    for _ in range(2):
        await store.record_write_failure("agent.memory.recorded", RuntimeError("nope"))

    first = agents[0]
    await store.seed_agent(_life(first), GenesisAgent.model_validate(first), tick=0)

    assert store.stats()["consecutive_failures"] == 0
    assert not store.is_halted


@pytest.mark.asyncio
async def test_speech_from_an_unseeded_resident_counts_as_a_loss(store: TraceStore) -> None:
    """The quietest version of the bug, and the one that produced the
    "(no reply)" wall: the recorder knows it cannot place this speaker,
    returns early, and the run reads the gap as silence. Skipping is
    allowed. Skipping without counting is not."""
    from world.core.events import WorldEvent

    before = store.stats()["rows_dropped"]
    await store.record_utterance_if_new(
        WorldEvent(
            type="agent.utterance.spoken",
            tick=37,
            payload={
                "speaker_agent_id": "n02",
                "listener_agent_id": None,
                "conversation_id": "c1",
                "location_id": "market_square",
                "content": "The copper - give it to me, I'll sell it.",
                "is_public": True,
            },
        )
    )
    assert store.stats()["rows_dropped"] == before + 1


@pytest.mark.asyncio
async def test_a_remark_with_no_thread_id_is_still_recorded(store: TraceStore) -> None:
    from world.core.events import WorldEvent

    agents = json.loads(GENESIS_20.read_text())["agents"]
    first = agents[0]
    await store.seed_agent(_life(first), GenesisAgent.model_validate(first), tick=0)

    await store.record_utterance_if_new(
        WorldEvent(
            type="agent.utterance.spoken",
            tick=41,
            payload={
                "speaker_agent_id": first["agent_id"],
                "listener_agent_id": None,
                "location_id": "carpentry",
                "content": "Thirty-six sim for all three.",
                "is_public": True,
            },
        )
    )

    rows = store.client.conn.execute(
        "select conversation_id, content from utterances"
    ).fetchall()
    assert len(rows) == 1, "the remark was dropped for want of a thread id"
    assert rows[0][0], "it was filed with an empty thread id, which the schema forbids"
    assert rows[0][1] == "Thirty-six sim for all three."


@pytest.mark.asyncio
async def test_the_same_unthreaded_remark_cannot_land_twice(store: TraceStore) -> None:
    """A minted id has to be deterministic, or a retry files the same
    sentence under two conversations and the transcript grows people who
    said everything twice."""
    from world.core.events import WorldEvent

    agents = json.loads(GENESIS_20.read_text())["agents"]
    first = agents[0]
    await store.seed_agent(_life(first), GenesisAgent.model_validate(first), tick=0)

    event = WorldEvent(
        type="agent.utterance.spoken",
        tick=41,
        payload={
            "speaker_agent_id": first["agent_id"],
            "listener_agent_id": None,
            "location_id": "carpentry",
            "content": "Thirty-six sim for all three.",
            "is_public": True,
        },
    )
    await store.record_utterance_if_new(event)
    await store.record_utterance_if_new(event)

    ids = [r[0] for r in store.client.conn.execute("select conversation_id from utterances")]
    assert len(set(ids)) == 1, f"one remark was filed under {len(set(ids))} conversations"


@pytest.mark.asyncio
async def test_minting_a_thread_id_is_counted(store: TraceStore) -> None:
    """A fallback nobody can see is how a defect becomes the normal case."""
    from world.core.events import WorldEvent

    agents = json.loads(GENESIS_20.read_text())["agents"]
    first = agents[0]
    await store.seed_agent(_life(first), GenesisAgent.model_validate(first), tick=0)

    before = store.stats().get("unthreaded_utterances", 0)
    await store.record_utterance_if_new(
        WorldEvent(
            type="agent.utterance.spoken", tick=41,
            payload={
                "speaker_agent_id": first["agent_id"], "listener_agent_id": None,
                "location_id": "carpentry", "content": "aye", "is_public": True,
            },
        )
    )
    assert store.stats()["unthreaded_utterances"] == before + 1


@pytest.mark.asyncio
async def test_the_trace_records_everyone_a_remark_addressed(store: TraceStore) -> None:
    """Baseline B2 - "a resident named in a remark is told they were
    addressed" - is checked against the trace store, and until now the
    store had no column to check.

    `listener_agent_id` holds exactly one id. That is Goffman's dyadic
    model, which he wrote a paper to call inadequate, applied to a town
    whose rooms hold five people. The world has carried the full list
    since `social.everyone_addressed`; this is the half that reaches disk.
    """
    from world.core.events import WorldEvent

    agents = json.loads(GENESIS_20.read_text())["agents"]
    for agent in agents[:3]:
        await store.seed_agent(_life(agent), GenesisAgent.model_validate(agent), tick=0)
    speaker, first, second = (a["agent_id"] for a in agents[:3])

    await store.record_utterance_if_new(
        WorldEvent(
            type="agent.utterance.spoken",
            tick=40,
            payload={
                "speaker_agent_id": speaker,
                "listener_agent_id": first,
                "addressed_agent_ids": [first, second],
                "conversation_id": "c-1",
                "location_id": "home:h_forge",
                "content": "Anselm, walk to temple. Tova, two sim is the flat of it.",
                "is_public": True,
            },
        )
    )

    stored = store.client.conn.execute(
        "select listener_agent_id, addressed_agent_ids from utterances"
    ).fetchone()
    assert stored[0] == first
    assert stored[1] and second in stored[1], (
        "the second person the remark spoke to is still invisible to the trace"
    )


@pytest.mark.asyncio
async def test_an_addressee_the_store_never_met_is_not_written(store: TraceStore) -> None:
    """Same foreign-key discipline as `listener_agent_id`: a name the
    store has no row for would fail the constraint and take the whole
    remark down with it, which is how run 16 lost 125 records."""
    from world.core.events import WorldEvent

    agents = json.loads(GENESIS_20.read_text())["agents"]
    first = agents[0]
    await store.seed_agent(_life(first), GenesisAgent.model_validate(first), tick=0)

    await store.record_utterance_if_new(
        WorldEvent(
            type="agent.utterance.spoken", tick=41,
            payload={
                "speaker_agent_id": first["agent_id"],
                "listener_agent_id": None,
                "addressed_agent_ids": [first["agent_id"], "nobody-at-all"],
                "conversation_id": "c-2",
                "location_id": "mill",
                "content": "anyone here?",
                "is_public": True,
            },
        )
    )
    stored = store.client.conn.execute(
        "select addressed_agent_ids from utterances"
    ).fetchone()[0]
    assert "nobody-at-all" not in (stored or "")
    assert first["agent_id"] in (stored or "")
