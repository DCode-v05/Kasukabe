"""Integration tests for secret scanning in portability.import_memory().

Covers the gap identified in the security audit: import_memory() is an
external-input boundary (user-uploaded bundle) that bypasses SignalStore and
LC pipeline guards entirely. Each subsection of the bundle is guarded; these
tests verify that a secret in any free-text field raises SecretDetectedError
before any write lands in the database.

Uses the shared `db` fixture from conftest.py (real in-memory SqlitePool +
migrations, sqlite-vec loaded as in production). No mocks.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.memory.episodic_memory import EpisodicMemory
from src.memory.knowledge_store import KnowledgeStore
from src.memory.portability import import_memory
from src.memory.social_graph import SocialGraph
from src.memory.soul import Soul
from src.security.secret_scanner import SecretDetectedError
from tests.test_secret_scanner import CANONICAL_POSITIVES


FAKE_AWS = CANONICAL_POSITIVES["aws-access-token"]
FAKE_GH_PAT = CANONICAL_POSITIVES["github-pat"]


@pytest_asyncio.fixture
async def stores(db):
    return {
        "episodic_memory": EpisodicMemory(db),
        "knowledge_store": KnowledgeStore(db),
        "social_graph": SocialGraph(db),
        "soul": Soul(db),
        "_db": db,
    }


def make_node(ref_id: str, name: str, attributes: dict | None = None) -> dict:
    """Build a social_graph node record with all portability fields set."""
    return {
        "ref_id": ref_id,
        "name": name,
        "is_primary": False,
        "attributes": attributes or {},
        "created_at": None,
        "updated_at": None,
    }


def make_knowledge(subject: str, content: str) -> dict:
    """Build a knowledge_store record with all portability fields set."""
    return {
        "subject": subject,
        "content": content,
        "type": "knowledge",
        "tags": [],
        "confidence": 0.7,
        "source": "engine_inferred",
        "usage_count": 0,
        "created_at": None,
        "last_reinforced": None,
        "valid_from": None,
        "valid_until": None,
        "recorded_at": None,
        "superseded_at": None,
    }


def make_edge(a_ref: str, b_ref: str, **fields) -> dict:
    """Build a social_graph edge record; free-text fields default to empty."""
    return {
        "person_a_ref": a_ref,
        "person_b_ref": b_ref,
        "user_perspective": fields.get("user_perspective", ""),
        "engine_inference": fields.get("engine_inference", ""),
        "observed_dynamics": fields.get("observed_dynamics", ""),
        "contexts": fields.get("contexts", []),
        "created_at": None,
        "updated_at": None,
    }


def clean_bundle() -> dict:
    return {
        "version": "1.0.0",
        "exported_at": "2026-01-01T00:00:00+00:00",
        "evolved_soul": [
            {
                "trait": "communication_style",
                "value": "concise and direct",
                "status": "active",
                "confidence": 0.8,
                "evidence_count": 3,
                "last_reinforced": None,
                "created_at": None,
            },
        ],
        "episodes": [
            {
                "key": "project kickoff",
                "description": "Discussed project goals and timeline.",
                "value": "Team agreed on Q2 deadline.",
                "tier": "active",
                "created_at": None,
            },
        ],
        "knowledge": [
            {
                **make_knowledge("team size", "Team has six engineers on backend."),
                "confidence": 0.9,
            },
        ],
        "social_graph": {
            "nodes": [make_node("node-1", "Alice", {"role": "platform lead"})],
            "edges": [],
        },
    }


async def run(bundle: dict, stores: dict) -> dict:
    return await import_memory(
        bundle=bundle,
        episodic_memory=stores["episodic_memory"],
        knowledge_store=stores["knowledge_store"],
        social_graph=stores["social_graph"],
        soul=stores["soul"],
    )


@pytest.mark.asyncio
async def test_clean_bundle_imports_successfully(stores):
    db = stores["_db"]
    result = await run(clean_bundle(), stores)
    assert result["evolved_traits"] == 1
    assert result["episodes"] == 1
    assert result["knowledge"] == 1
    assert result["social_nodes"] == 1

    ep_count = await db.fetchval("SELECT count(*) FROM episodes")
    ks_count = await db.fetchval("SELECT count(*) FROM knowledge_store")
    assert int(ep_count or 0) == 1
    assert int(ks_count or 0) == 1


@pytest.mark.asyncio
async def test_secret_in_soul_trait_value_raises(stores):
    db = stores["_db"]
    bundle = clean_bundle()
    bundle["evolved_soul"][0]["value"] = f"uses AWS key {FAKE_AWS} for deploys"

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "AWS Access Token" in str(exc_info.value)
    assert FAKE_AWS not in str(exc_info.value)
    assert exc_info.value.where == "portability-import-soul"

    trait_count = await db.fetchval("SELECT count(*) FROM evolved_soul")
    assert int(trait_count or 0) == 0


@pytest.mark.asyncio
async def test_secret_in_soul_trait_name_raises(stores):
    bundle = clean_bundle()
    bundle["evolved_soul"][0]["trait"] = f"leaked-{FAKE_GH_PAT}"

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "GitHub PAT" in str(exc_info.value)
    assert exc_info.value.where == "portability-import-soul"


@pytest.mark.asyncio
async def test_secret_in_episode_value_raises(stores):
    db = stores["_db"]
    bundle = clean_bundle()
    bundle["episodes"][0]["value"] = f"Key in use: {FAKE_AWS}"

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "AWS Access Token" in str(exc_info.value)
    assert exc_info.value.where == "portability-import-episode"

    ep_count = await db.fetchval("SELECT count(*) FROM episodes")
    assert int(ep_count or 0) == 0


@pytest.mark.asyncio
async def test_secret_in_episode_description_raises(stores):
    bundle = clean_bundle()
    bundle["episodes"][0]["description"] = f"Discussed rotating {FAKE_GH_PAT}"

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "GitHub PAT" in str(exc_info.value)
    assert exc_info.value.where == "portability-import-episode"


@pytest.mark.asyncio
async def test_secret_in_knowledge_content_raises(stores):
    db = stores["_db"]
    bundle = clean_bundle()
    bundle["knowledge"][0]["content"] = f"Team uses {FAKE_AWS} for S3 access."

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "AWS Access Token" in str(exc_info.value)
    assert exc_info.value.where == "portability-import-knowledge"

    ks_count = await db.fetchval("SELECT count(*) FROM knowledge_store")
    assert int(ks_count or 0) == 0


@pytest.mark.asyncio
async def test_secret_in_knowledge_subject_raises(stores):
    bundle = clean_bundle()
    bundle["knowledge"][0]["subject"] = f"leaked-{FAKE_GH_PAT}"

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "GitHub PAT" in str(exc_info.value)
    assert exc_info.value.where == "portability-import-knowledge"


@pytest.mark.asyncio
async def test_secret_in_node_attribute_value_raises(stores):
    db = stores["_db"]
    bundle = clean_bundle()
    bundle["social_graph"]["nodes"][0]["attributes"] = {
        "github-token": FAKE_GH_PAT,
        "role": "devops",
    }

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "GitHub PAT" in str(exc_info.value)
    assert exc_info.value.where == "portability-import-social-node"

    node_count = await db.fetchval("SELECT count(*) FROM social_graph_nodes")
    assert int(node_count or 0) == 0


@pytest.mark.asyncio
async def test_secret_in_node_attribute_key_raises(stores):
    """Attribute keys are persisted alongside values and embedded as
    `key + " " + value`. A secret used as the key (clean value) must still
    be caught — otherwise the secret lands in the DB and the embedding API."""
    db = stores["_db"]
    bundle = clean_bundle()
    bundle["social_graph"]["nodes"][0]["attributes"] = {
        FAKE_GH_PAT: "devops-user",
        "role": "engineer",
    }

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "GitHub PAT" in str(exc_info.value)
    assert FAKE_GH_PAT not in str(exc_info.value)
    assert exc_info.value.where == "portability-import-social-node"

    node_count = await db.fetchval("SELECT count(*) FROM social_graph_nodes")
    assert int(node_count or 0) == 0


@pytest.mark.asyncio
async def test_secret_in_node_name_raises(stores):
    bundle = clean_bundle()
    bundle["social_graph"]["nodes"][0]["name"] = f"Alice ({FAKE_AWS})"

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "AWS Access Token" in str(exc_info.value)
    assert exc_info.value.where == "portability-import-social-node"


@pytest.mark.asyncio
async def test_secret_in_edge_user_perspective_raises(stores):
    """Edge guard fires even when node ref exists; no edge is written."""
    db = stores["_db"]
    bundle = clean_bundle()
    bundle["social_graph"]["nodes"].append(make_node("node-2", "Bob"))
    bundle["social_graph"]["edges"].append(
        make_edge(
            "node-1",
            "node-2",
            user_perspective=f"shared aws key {FAKE_AWS} during onboarding",
        )
    )

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "AWS Access Token" in str(exc_info.value)
    assert exc_info.value.where == "portability-import-social-edge"

    edge_count = await db.fetchval("SELECT count(*) FROM social_graph_edges")
    assert int(edge_count or 0) == 0


@pytest.mark.asyncio
async def test_secret_in_edge_contexts_raises(stores):
    bundle = clean_bundle()
    bundle["social_graph"]["nodes"].append(make_node("node-2", "Bob"))
    bundle["social_graph"]["edges"].append(
        make_edge(
            "node-1",
            "node-2",
            contexts=[f"context with {FAKE_GH_PAT} embedded"],
        )
    )

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert "GitHub PAT" in str(exc_info.value)
    assert exc_info.value.where == "portability-import-social-edge"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "section,field,value,expected_where",
    [
        ("soul_trait_value", None, None, "portability-import-soul"),
        ("episode_value", None, None, "portability-import-episode"),
        ("knowledge_content", None, None, "portability-import-knowledge"),
        ("node_attr", None, None, "portability-import-social-node"),
    ],
)
async def test_where_label_identifies_correct_section(
    section,
    field,
    value,
    expected_where,
    stores,
):
    """Verify that `where` in SecretDetectedError names the correct section."""
    bundle = clean_bundle()

    if section == "soul_trait_value":
        bundle["evolved_soul"][0]["value"] = f"cred: {FAKE_AWS}"
        bundle["episodes"] = []
        bundle["knowledge"] = []
        bundle["social_graph"] = {"nodes": [], "edges": []}
    elif section == "episode_value":
        bundle["evolved_soul"] = []
        bundle["episodes"][0]["value"] = f"cred: {FAKE_AWS}"
        bundle["knowledge"] = []
        bundle["social_graph"] = {"nodes": [], "edges": []}
    elif section == "knowledge_content":
        bundle["evolved_soul"] = []
        bundle["episodes"] = []
        bundle["knowledge"][0]["content"] = f"cred: {FAKE_AWS}"
        bundle["social_graph"] = {"nodes": [], "edges": []}
    elif section == "node_attr":
        bundle["evolved_soul"] = []
        bundle["episodes"] = []
        bundle["knowledge"] = []
        bundle["social_graph"]["nodes"][0]["attributes"] = {"tok": FAKE_GH_PAT}
        bundle["social_graph"]["edges"] = []

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)

    assert exc_info.value.where == expected_where


@pytest.mark.asyncio
async def test_secret_in_last_record_writes_nothing(stores):
    """A secret in the final record of a multi-record bundle must roll back
    every prior section — no episodes, no knowledge, no traits, no nodes."""
    db = stores["_db"]
    bundle = clean_bundle()
    bundle["episodes"].append(
        {
            "key": "second-clean",
            "description": "no secret here",
            "value": "fully clean text",
            "tier": "active",
            "created_at": None,
        }
    )
    bundle["knowledge"].append(
        make_knowledge(
            "rotation policy",
            f"keys rotate every 90 days; example bad key {FAKE_GH_PAT}",
        )
    )

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)
    assert exc_info.value.where == "portability-import-knowledge"

    assert int(await db.fetchval("SELECT count(*) FROM evolved_soul") or 0) == 0
    assert int(await db.fetchval("SELECT count(*) FROM episodes") or 0) == 0
    assert int(await db.fetchval("SELECT count(*) FROM knowledge_store") or 0) == 0
    assert int(await db.fetchval("SELECT count(*) FROM social_graph_nodes") or 0) == 0


@pytest.mark.asyncio
async def test_secret_in_edge_rolls_back_all_nodes(stores):
    """Edges import after nodes. A secret in the last edge must still leave
    zero nodes behind — pre-scan runs before the first node write."""
    db = stores["_db"]
    bundle = clean_bundle()
    bundle["social_graph"]["nodes"].append(make_node("node-2", "Bob", {"role": "ic"}))
    bundle["social_graph"]["edges"].append(
        make_edge(
            "node-1",
            "node-2",
            user_perspective=f"shared key during pairing: {FAKE_AWS}",
        )
    )

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)
    assert exc_info.value.where == "portability-import-social-edge"

    assert int(await db.fetchval("SELECT count(*) FROM social_graph_nodes") or 0) == 0
    assert int(await db.fetchval("SELECT count(*) FROM social_graph_edges") or 0) == 0
    assert int(await db.fetchval("SELECT count(*) FROM episodes") or 0) == 0
    assert int(await db.fetchval("SELECT count(*) FROM knowledge_store") or 0) == 0


@pytest.mark.asyncio
async def test_first_matching_section_wins_where_label(stores):
    """When multiple sections carry secrets, the pre-scan reports the first
    section in import order (soul → episodes → knowledge → nodes → edges)."""
    bundle = clean_bundle()
    bundle["episodes"][0]["value"] = f"episode secret {FAKE_AWS}"
    bundle["knowledge"][0]["content"] = f"knowledge secret {FAKE_GH_PAT}"

    with pytest.raises(SecretDetectedError) as exc_info:
        await run(bundle, stores)
    assert exc_info.value.where == "portability-import-episode"
