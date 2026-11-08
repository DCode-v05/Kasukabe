"""Integration tests for secret scanning at LC pipeline memory-write sites.

Covers the 'LLM paraphrase introduces a secret' threat: clean signals enter
the LC system, the encoder agents hallucinate a credential in their output,
and the guard blocks the write at every write path (episode, knowledge fact,
knowledge inference, social node, social edge, soul trait).

Uses the shared `db` fixture from conftest.py + real memory stores (no mocks).
The LLM adapter is a small dispatcher that returns canned JSON keyed on the
system prompt — this is the only synthetic dependency and is required to
inject specific secret-bearing strings into LLM output deterministically.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from src.infra.sqlite_pool import SqlitePool
from src.learning_centre.pipeline import LCPipeline
from src.learning_centre.signals import LearningSignal, SignalStore
from src.memory.episodic_memory import EpisodicMemory
from src.memory.knowledge_store import KnowledgeStore
from src.memory.memory_system import MemorySystem
from src.memory.social_graph import SocialGraph
from src.memory.soul import Soul
from tests.test_secret_scanner import CANONICAL_POSITIVES


FAKE_AWS = CANONICAL_POSITIVES["aws-access-token"]
FAKE_GH_PAT = CANONICAL_POSITIVES["github-pat"]


@pytest_asyncio.fixture
async def memory_system(db):
    return MemorySystem(
        episodic_memory=EpisodicMemory(db),
        knowledge_store=KnowledgeStore(db),
        social_graph=SocialGraph(db),
        soul=Soul(db),
    )


@pytest_asyncio.fixture
async def signal_store(db):
    return SignalStore(db)


async def seed_clean_signals(store: SignalStore) -> None:
    """Seed a mix of user-sourced + engine-sourced clean signals for one task."""
    await store.write(
        LearningSignal(
            type="fact",
            source="user",
            subject="team size",
            content="Our team has six engineers on backend.",
            thread_id="task-001",
        )
    )
    await store.write(
        LearningSignal(
            type="preference",
            source="user",
            subject="code review",
            content="Prefers PRs reviewed within 24 hours.",
            thread_id="task-001",
        )
    )
    await store.write(
        LearningSignal(
            type="person",
            source="user",
            subject="alice role",
            content="Alice manages the platform team.",
            thread_id="task-001",
        )
    )


class CannedLLM:
    """Minimal dispatcher that returns pre-set JSON keyed on system prompt.

    Pipeline agent identification:
      - EPISODE_ENCODING_SYSTEM contains "episode creation engine"
      - KNOWLEDGE_ENCODING_SYSTEM contains "knowledge extraction agent"
      - SOCIAL_ENCODING_SYSTEM contains "social graph"
      - CONSOLIDATION_SYSTEM contains "consolidation"
    """

    def __init__(
        self,
        *,
        episode: str = '{"key": "team status update", "description": "Discussed team composition and review policy."}',
        knowledge: str = '{"entries": [{"subject": "team size", "content": "Team has six engineers", "confidence": 0.9, "tags": []}]}',
        social: str = '{"nodes": [], "edges": []}',
        consolidation: str = '{"cross_patterns": [], "soul_updates": [], "contradictions": []}',
    ) -> None:
        self.episode = episode
        self.knowledge = knowledge
        self.social = social
        self.consolidation = consolidation
        self.calls: list[str] = []

    async def generate(self, messages, system=None) -> str:  # type: ignore[no-untyped-def]
        if isinstance(system, list):
            sys_text = " ".join(getattr(b, "text", "") for b in system)
        else:
            sys_text = system or ""
        sys_lower = sys_text.lower()
        if "episode creation engine" in sys_lower:
            self.calls.append("episode")
            return self.episode
        if "knowledge extraction" in sys_lower:
            self.calls.append("knowledge")
            return self.knowledge
        if "social graph" in sys_lower or "social encoding" in sys_lower:
            self.calls.append("social")
            return self.social
        if "consolidation" in sys_lower or "contradiction" in sys_lower:
            self.calls.append("consolidation")
            return self.consolidation
        self.calls.append("unknown")
        return '{"key": null, "description": null}'

    async def stream(self, messages, system=None):  # pragma: no cover
        raise NotImplementedError

    async def generate_with_tools(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def stream_with_tools(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


async def run_pipeline(
    memory_system: MemorySystem,
    signal_store: SignalStore,
    db: SqlitePool,
    llm: CannedLLM,
):
    pipeline = LCPipeline(
        adapter=llm,
        db=db,
        memory_system=memory_system,
        signal_store=signal_store,
    )
    return await pipeline.run_batch()


@pytest.mark.asyncio
async def test_clean_llm_output_writes_normally(
    memory_system,
    signal_store,
    db,
):
    await seed_clean_signals(signal_store)
    llm = CannedLLM()
    result = await run_pipeline(memory_system, signal_store, db, llm)

    assert result.secrets_blocked == 0
    assert result.signals_processed == 3
    assert result.episodes_created == 1
    assert result.knowledge_written >= 1


@pytest.mark.asyncio
async def test_knowledge_write_blocks_on_secret_in_content(
    memory_system,
    signal_store,
    db,
):
    await seed_clean_signals(signal_store)
    dirty_knowledge = json.dumps(
        {
            "entries": [
                {
                    "subject": "api access",
                    "content": f"Team uses AWS with key {FAKE_AWS} for deploys.",
                    "confidence": 0.9,
                    "tags": [],
                }
            ],
        }
    )
    llm = CannedLLM(knowledge=dirty_knowledge)
    result = await run_pipeline(memory_system, signal_store, db, llm)

    assert result.secrets_blocked >= 1
    assert result.knowledge_written == 0
    kcount = await db.fetchval("SELECT count(*) FROM knowledge_store")
    assert int(kcount or 0) == 0


@pytest.mark.asyncio
async def test_knowledge_write_blocks_on_secret_in_subject(
    memory_system,
    signal_store,
    db,
):
    await seed_clean_signals(signal_store)
    dirty_knowledge = json.dumps(
        {
            "entries": [
                {
                    "subject": f"leaked {FAKE_GH_PAT}",
                    "content": "Some description",
                    "confidence": 0.8,
                    "tags": [],
                }
            ],
        }
    )
    llm = CannedLLM(knowledge=dirty_knowledge)
    result = await run_pipeline(memory_system, signal_store, db, llm)

    assert result.secrets_blocked >= 1
    assert result.knowledge_written == 0


@pytest.mark.asyncio
async def test_social_edge_blocks_on_secret_in_relationship(
    memory_system,
    signal_store,
    db,
):
    await seed_clean_signals(signal_store)
    dirty_social = json.dumps(
        {
            "nodes": [
                {
                    "name": "Alice",
                    "attributes": [{"key": "role", "value": "platform lead"}],
                },
                {
                    "name": "Bob",
                    "attributes": [{"key": "role", "value": "engineer"}],
                },
            ],
            "edges": [
                {
                    "from": "Alice",
                    "to": "Bob",
                    "relationship": f"shared aws key {FAKE_AWS} during onboarding",
                }
            ],
        }
    )
    llm = CannedLLM(social=dirty_social)
    result = await run_pipeline(memory_system, signal_store, db, llm)

    assert result.secrets_blocked >= 1
    assert result.social_edges_written == 0
    assert result.social_nodes_enriched >= 1


@pytest.mark.asyncio
async def test_social_node_blocks_on_secret_in_attribute_value(
    memory_system,
    signal_store,
    db,
):
    await seed_clean_signals(signal_store)
    dirty_social = json.dumps(
        {
            "nodes": [
                {
                    "name": "Carol",
                    "attributes": [
                        {"key": "github-token", "value": FAKE_GH_PAT},
                        {"key": "role", "value": "devops"},
                    ],
                }
            ],
            "edges": [],
        }
    )
    llm = CannedLLM(social=dirty_social)
    result = await run_pipeline(memory_system, signal_store, db, llm)

    assert result.secrets_blocked >= 1
    assert result.social_nodes_enriched == 0


@pytest.mark.asyncio
async def test_soul_update_blocks_on_secret_in_value(
    memory_system,
    signal_store,
    db,
):
    await seed_clean_signals(signal_store)
    dirty_consolidation = json.dumps(
        {
            "cross_patterns": [],
            "soul_updates": [
                {
                    "trait": "communication_style",
                    "value": f"prefers concise updates; rotates AKIA-style keys like {FAKE_AWS}",
                    "confidence": 0.6,
                    "evidence_count": 2,
                    "action": "new_signal",
                }
            ],
            "contradictions": [],
        }
    )
    llm = CannedLLM(consolidation=dirty_consolidation)
    result = await run_pipeline(memory_system, signal_store, db, llm)

    assert result.secrets_blocked >= 1
    assert result.soul_traits_updated == 0


@pytest.mark.asyncio
async def test_cross_batch_knowledge_blocks_on_secret(
    memory_system,
    signal_store,
    db,
):
    await seed_clean_signals(signal_store)
    dirty_consolidation = json.dumps(
        {
            "cross_patterns": [
                {
                    "subject": "credentials pattern",
                    "content": f"Team rotates tokens including {FAKE_GH_PAT} every quarter",
                    "confidence": 0.6,
                    "tags": [],
                }
            ],
            "soul_updates": [],
            "contradictions": [],
        }
    )
    llm = CannedLLM(consolidation=dirty_consolidation)
    result = await run_pipeline(memory_system, signal_store, db, llm)

    assert result.secrets_blocked >= 1
    assert result.cross_batch_written == 0


@pytest.mark.asyncio
async def test_multiple_paths_blocked_counter_accumulates(
    memory_system,
    signal_store,
    db,
):
    await seed_clean_signals(signal_store)
    dirty_knowledge = json.dumps(
        {
            "entries": [
                {
                    "subject": "deploy secret",
                    "content": f"uses {FAKE_AWS} for S3",
                    "confidence": 0.7,
                    "tags": [],
                }
            ],
        }
    )
    dirty_social = json.dumps(
        {
            "nodes": [
                {
                    "name": "Dave",
                    "attributes": [
                        {"key": "pat", "value": FAKE_GH_PAT},
                    ],
                }
            ],
            "edges": [],
        }
    )
    llm = CannedLLM(knowledge=dirty_knowledge, social=dirty_social)
    result = await run_pipeline(memory_system, signal_store, db, llm)

    assert result.secrets_blocked >= 2
    kcount = await db.fetchval("SELECT count(*) FROM knowledge_store")
    assert int(kcount or 0) == 0
