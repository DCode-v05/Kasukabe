"""Tests for SQLite-backed memory modules: episodic, knowledge, social graph, soul.

Uses in-memory SQLite (`:memory:`) — no files on disk, no cleanup needed.
Tests verify the three-table pattern (regular + vec + fts) and hybrid search.
"""

import pytest
import pytest_asyncio

from src.infra.sqlite_pool import SqlitePool
from src.memory.episodic_memory import EpisodicMemory
from src.memory.knowledge_store import KnowledgeStore
from src.memory.social_graph import SocialGraph
from src.memory.soul import Soul, set_core_soul_cache, clear_core_soul_cache
from src.memory.inmemory_working_memory import InMemoryWorkingMemory
from src.memory.working_memory import EntryType


TEST_USER_ID = "test-user-001"


@pytest_asyncio.fixture
async def db():
    """Create an in-memory SQLite database with all schemas applied."""
    pool = SqlitePool(":memory:")
    await pool.connect()

    from pathlib import Path

    migrations_dir = Path(__file__).parent.parent / "migrations"
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        sql = sql_file.read_text(encoding="utf-8")
        await pool.executescript(sql)
    await pool.commit()

    yield pool
    await pool.close()


class TestEpisodicMemory:
    @pytest_asyncio.fixture
    async def em(self, db):
        return EpisodicMemory(db)

    @pytest.mark.asyncio
    async def test_create_episode(self, em):
        result = await em.create_episode(
            "Budget meeting Q4",
            "Discussed quarterly budget",
            "Full transcript here",
        )
        assert result["id"]
        assert result["key"] == "Budget meeting Q4"
        assert result["tier"] == "episode"

    @pytest.mark.asyncio
    async def test_get_episode_value(self, em):
        ep = await em.create_episode(
            "Test key",
            "Test desc",
            "The full value content",
        )
        value = await em.get_episode_value(ep["id"])
        assert value == "The full value content"

    @pytest.mark.asyncio
    async def test_get_nonexistent_episode(self, em):
        value = await em.get_episode_value("nonexistent-id")
        assert value is None

    @pytest.mark.asyncio
    async def test_search_keyword_fallback(self, em):
        await em.create_episode("Python debugging", "Fixed a bug in parser", "details")
        await em.create_episode("Grocery shopping", "Bought milk and eggs", "receipt")

        results = await em.search_episodes("python bug")
        assert len(results) >= 1
        assert results[0]["key"] == "Python debugging"

    @pytest.mark.asyncio
    async def test_search_no_results(self, em):
        await em.create_episode("Meeting notes", "Team standup", "details")
        results = await em.search_episodes("quantum physics")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_archive_old_episodes(self, em, db):
        ep = await em.create_episode("Old event", "Very old", "Old content")
        await db.execute(
            "UPDATE episodes SET created_at = datetime('now', '-60 days') WHERE id = ?",
            (ep["id"],),
        )
        await db.commit()

        count = await em.archive_old_episodes()
        assert count == 1

        value = await em.get_episode_value(ep["id"])
        assert value == "Old content"


class TestKnowledgeStore:
    @pytest_asyncio.fixture
    async def ks(self, db):
        return KnowledgeStore(db)

    @pytest.mark.asyncio
    async def test_write_fact(self, ks):
        result = await ks.write_knowledge_fact(
            "Budget",
            "Our Q4 budget is $50k",
            tags=["finance"],
        )
        assert result["subject"] == "Budget"
        assert result["confidence"] == 1.0
        assert result["source"] == "user_stated"

    @pytest.mark.asyncio
    async def test_write_inference(self, ks):
        result = await ks.write_knowledge_inference(
            "User role",
            "User works in marketing",
            confidence=0.6,
        )
        assert result["source"] == "system_inferred"
        assert result["confidence"] == 0.6

    @pytest.mark.asyncio
    async def test_search_keyword_fallback(self, ks):
        await ks.write_knowledge_fact("Team size", "Team has 5 engineers")
        await ks.write_knowledge_fact("Office location", "Office is in Austin")

        results = await ks.search_knowledge("engineers team")
        assert len(results) >= 1
        assert results[0]["subject"] == "Team size"

    @pytest.mark.asyncio
    async def test_update_confidence(self, ks):
        entry = await ks.write_knowledge_inference(
            "Hobby",
            "User likes hiking",
            confidence=0.5,
        )
        updated = await ks.update_confidence(entry["id"], 0.8)
        assert updated["confidence"] == 0.8

    @pytest.mark.asyncio
    async def test_supersede_entry(self, ks):
        entry = await ks.write_knowledge_fact("Budget", "Budget is $50k")
        superseded = await ks.supersede_entry(entry["id"])
        assert superseded["superseded_at"] is not None

        results = await ks.search_knowledge("budget")
        assert all(r["id"] != entry["id"] for r in results)

    @pytest.mark.asyncio
    async def test_confidence_decay(self, ks, db):
        entry = await ks.write_knowledge_inference(
            "Preference",
            "User prefers dark mode",
            confidence=0.5,
        )
        await db.execute(
            "UPDATE knowledge_store SET last_reinforced = datetime('now', '-60 days') WHERE id = ?",
            (entry["id"],),
        )
        await db.commit()

        affected = await ks.run_confidence_decay()
        assert affected >= 1

    @pytest.mark.asyncio
    async def test_staleness_tier(self, ks, db):
        entry = await ks.write_knowledge_fact("Fresh", "Just learned")
        tier = await ks.get_fact_staleness_tier(entry["id"])
        assert tier == "fresh"

    @pytest.mark.asyncio
    async def test_increment_usage(self, ks):
        entry = await ks.write_knowledge_fact("Test", "Content")
        await ks.increment_usage(entry["id"])
        row = await ks.db.fetchrow(
            "SELECT usage_count FROM knowledge_store WHERE id = ?", (entry["id"],)
        )
        assert row["usage_count"] == 1


class TestSocialGraph:
    @pytest_asyncio.fixture
    async def sg(self, db):
        return SocialGraph(db)

    @pytest.mark.asyncio
    async def test_create_person(self, sg):
        person = await sg.create_person("Alice Smith")
        assert person["name"] == "Alice Smith"
        assert person["is_primary"] is False
        assert person["attributes"] == {}

    @pytest.mark.asyncio
    async def test_create_primary_person(self, sg):
        person = await sg.create_person("Me", is_primary=True)
        assert person["is_primary"] is True

        primary = await sg.get_primary_person()
        assert primary is not None
        assert primary["name"] == "Me"

    @pytest.mark.asyncio
    async def test_no_primary_person(self, sg):
        result = await sg.get_primary_person()
        assert result is None

    @pytest.mark.asyncio
    async def test_enrich_node(self, sg):
        person = await sg.create_person("Bob")
        enriched = await sg.enrich_social_graph_node(
            person["id"],
            {"role": "Engineer", "company": "Acme"},
        )
        assert enriched["attributes"]["role"] == "Engineer"
        assert enriched["attributes"]["company"] == "Acme"

    @pytest.mark.asyncio
    async def test_enrich_edge(self, sg):
        alice = await sg.create_person("Alice")
        bob = await sg.create_person("Bob")
        edge = await sg.enrich_social_graph_edge(
            alice["id"],
            bob["id"],
            {"user_perspective": "colleagues", "contexts": ["work"]},
        )
        assert edge["user_perspective"] == "colleagues"
        assert "work" in edge["contexts"]

    @pytest.mark.asyncio
    async def test_edge_upsert_merges_contexts(self, sg):
        alice = await sg.create_person("Alice")
        bob = await sg.create_person("Bob")
        await sg.enrich_social_graph_edge(
            alice["id"],
            bob["id"],
            {"user_perspective": "friends", "contexts": ["school"]},
        )
        updated = await sg.enrich_social_graph_edge(
            alice["id"],
            bob["id"],
            {"engine_inference": "close relationship", "contexts": ["work"]},
        )
        assert "school" in updated["contexts"]
        assert "work" in updated["contexts"]
        assert updated["engine_inference"] == "close relationship"
        assert updated["user_perspective"] == "friends"

    @pytest.mark.asyncio
    async def test_search_nodes(self, sg):
        await sg.create_person("Alice Johnson")
        await sg.create_person("Bob Smith")

        results = await sg.search_social_graph("Alice")
        assert len(results) >= 1
        assert results[0]["name"] == "Alice Johnson"

    @pytest.mark.asyncio
    async def test_update_primary_person(self, sg):
        await sg.create_person("Original Name", is_primary=True)
        updated = await sg.update_primary_person({"name": "New Name", "title": "CEO"})
        assert updated["name"] == "New Name" or updated["attributes"].get("title") == "CEO"

    @pytest.mark.asyncio
    async def test_find_or_create_dedup_exact_name(self, sg):
        """Calling find_or_create_person twice with the same name returns the same person."""
        first = await sg.find_or_create_person("Sarah")
        second = await sg.find_or_create_person("Sarah")
        assert first["id"] == second["id"]

    @pytest.mark.asyncio
    async def test_find_or_create_dedup_case_insensitive(self, sg):
        """Case-insensitive dedup: 'sarah' matches 'Sarah'."""
        first = await sg.find_or_create_person("Sarah")
        second = await sg.find_or_create_person("sarah")
        assert first["id"] == second["id"]

    @pytest.mark.asyncio
    async def test_find_or_create_different_names_create_separate(self, sg):
        """Different names create different people."""
        alice = await sg.find_or_create_person("Alice")
        bob = await sg.find_or_create_person("Bob")
        assert alice["id"] != bob["id"]

    @pytest.mark.asyncio
    async def test_find_or_create_dedup_with_few_nodes(self, sg):
        """Dedup works even with fewer than 5 nodes (exact match path)."""
        first = await sg.find_or_create_person("Diana")
        second = await sg.find_or_create_person("Diana")
        assert first["id"] == second["id"]


class TestSoul:
    @pytest_asyncio.fixture
    async def soul(self, db):
        clear_core_soul_cache()
        return Soul(db)

    @pytest.mark.asyncio
    async def test_core_soul_from_cache(self, soul):
        set_core_soul_cache({"version": "1.0.0", "preamble": "Test soul"})
        result = await soul.get_core_soul()
        assert result["version"] == "1.0.0"
        clear_core_soul_cache()

    @pytest.mark.asyncio
    async def test_core_soul_none_without_cache(self, soul):
        result = await soul.get_core_soul()
        assert result is None

    @pytest.mark.asyncio
    async def test_evolved_soul_signal(self, soul):
        result = await soul.update_evolved_soul(
            "communication_style",
            "Prefers concise responses",
            confidence=0.5,
            evidence_count=2,
        )
        assert result["status"] == "signal"
        assert result["confidence"] == 0.5

    @pytest.mark.asyncio
    async def test_evolved_soul_expressed(self, soul):
        result = await soul.update_evolved_soul(
            "communication_style",
            "Prefers concise responses",
            confidence=0.8,
            evidence_count=5,
        )
        assert result["status"] == "expressed"

    @pytest.mark.asyncio
    async def test_get_expressed_only(self, soul):
        await soul.update_evolved_soul("trait_a", "value_a", 0.5, 1)
        await soul.update_evolved_soul("trait_b", "value_b", 0.8, 5)

        expressed = await soul.get_evolved_soul()
        assert len(expressed) == 1
        assert expressed[0]["trait"] == "trait_b"

    @pytest.mark.asyncio
    async def test_get_full_traits(self, soul):
        await soul.update_evolved_soul("trait_a", "value_a", 0.5, 1)
        await soul.update_evolved_soul("trait_b", "value_b", 0.8, 5)

        full = await soul.get_evolved_soul_full()
        assert len(full) == 2

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, soul):
        await soul.update_evolved_soul("style", "formal", 0.5, 2)
        updated = await soul.update_evolved_soul("style", "casual", 0.9, 10)
        assert updated["value"] == "casual"
        assert updated["status"] == "expressed"

        full = await soul.get_evolved_soul_full()
        style_traits = [t for t in full if t["trait"] == "style"]
        assert len(style_traits) == 1


class TestKeywordScoring:
    def test_exact_match(self):
        from src.memory.embeddings import keyword_score

        assert keyword_score("project budget", "the project budget review") == 1.0

    def test_stemming_plural(self):
        from src.memory.embeddings import keyword_score

        assert keyword_score("project", "multiple projects were started") > 0

    def test_stemming_verb_forms(self):
        from src.memory.embeddings import keyword_score

        assert keyword_score("running", "user went for a run") > 0

    def test_stemming_ed_suffix(self):
        from src.memory.embeddings import keyword_score

        assert keyword_score("processed", "we need to process the data") > 0

    def test_stop_words_only_returns_zero(self):
        from src.memory.embeddings import keyword_score

        assert keyword_score("the", "the quick brown fox") == 0.0

    def test_stop_words_filtered_from_query(self):
        from src.memory.embeddings import keyword_score

        score = keyword_score("the budget", "budget allocation for Q4")
        assert score == 1.0

    def test_empty_after_stop_removal(self):
        from src.memory.embeddings import keyword_score

        assert keyword_score("the a an", "some text here") == 0.0

    def test_no_match(self):
        from src.memory.embeddings import keyword_score

        assert keyword_score("quantum physics", "budget meeting notes") == 0.0

    def test_partial_match(self):
        from src.memory.embeddings import keyword_score

        score = keyword_score("project budget timeline", "the project budget review")
        assert 0.0 < score < 1.0

    def test_stem_function_directly(self):
        from src.memory.embeddings import stem

        assert stem("running") == "run"
        assert stem("projects") == "project"
        assert stem("companies") == "company"
        assert stem("processed") == "process"
        assert stem("management") == "manage"
        assert stem("run") == "run"
        assert stem("ai") == "ai"


class TestRecencyBoost:
    def test_recent_is_near_one(self):
        from src.memory.embeddings import recency_boost
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        assert recency_boost(now) >= 0.99

    def test_30_days_is_half(self):
        from src.memory.embeddings import recency_boost
        from datetime import datetime, timezone, timedelta

        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        boost = recency_boost(old)
        assert 0.48 <= boost <= 0.52

    def test_old_clamped_at_half(self):
        from src.memory.embeddings import recency_boost
        from datetime import datetime, timezone, timedelta

        ancient = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        assert recency_boost(ancient) == 0.5

    def test_unparseable_returns_fallback(self):
        from src.memory.embeddings import recency_boost

        assert recency_boost("not-a-date") == 0.75

    def test_empty_string_returns_fallback(self):
        from src.memory.embeddings import recency_boost

        assert recency_boost("") == 0.75

    @pytest.mark.asyncio
    async def test_episodic_search_favors_recent(self, db):
        """Recent episode ranks higher than old episode with identical content."""
        from datetime import datetime, timezone, timedelta

        em = EpisodicMemory(db)

        old_time = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        await db.execute(
            "INSERT INTO episodes (id, key, description, value, created_at) VALUES (?, ?, ?, ?, ?)",
            ("old-ep", "budget review", "quarterly budget analysis", "details", old_time),
        )
        await db.execute(
            "INSERT INTO episodes_fts (id, key, description) VALUES (?, ?, ?)",
            ("old-ep", "budget review", "quarterly budget analysis"),
        )

        now_time = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO episodes (id, key, description, value, created_at) VALUES (?, ?, ?, ?, ?)",
            ("new-ep", "budget review", "quarterly budget analysis", "details", now_time),
        )
        await db.execute(
            "INSERT INTO episodes_fts (id, key, description) VALUES (?, ?, ?)",
            ("new-ep", "budget review", "quarterly budget analysis"),
        )
        await db.commit()

        results = await em.search_episodes("budget review")
        assert len(results) >= 2
        assert results[0]["id"] == "new-ep"
        assert results[0]["score"] > results[1]["score"]


class TestUnifiedSearch:
    @pytest.mark.asyncio
    async def test_unified_merges_sources(self, db):
        """Unified search returns results tagged with their source."""
        from src.engine_core.search import Search

        em = EpisodicMemory(db)
        ks = KnowledgeStore(db)
        sg = SocialGraph(db)

        await em.create_episode("budget meeting", "discussed Q4 budget", "details")
        await ks.write_knowledge_fact("budget", "Q4 budget is 50k")
        await sg.create_person("Sarah")

        search = Search(db, episodic_memory=em, knowledge_store=ks, social_graph=sg)
        results = await search.unified("budget")

        sources = {r.get("_source") for r in results}
        assert "episode" in sources
        assert "knowledge" in sources

    @pytest.mark.asyncio
    async def test_unified_boosts_cross_reference(self, db):
        """Cross-reference boost increases score of episode mentioning a social graph person."""
        from src.engine_core.search import Search

        em = EpisodicMemory(db)
        ks = KnowledgeStore(db)
        sg = SocialGraph(db)

        await sg.create_person("Sarah")
        await em.create_episode("meeting with Sarah", "Sarah discussed project plans", "details")

        search_no_sg = Search(db, episodic_memory=em, knowledge_store=ks)
        results_no_sg = await search_no_sg.unified("Sarah meeting")
        ep_no_sg = next((r for r in results_no_sg if r.get("_source") == "episode"), None)
        assert ep_no_sg is not None
        base_score = ep_no_sg["score"]

        search_with_sg = Search(db, episodic_memory=em, knowledge_store=ks, social_graph=sg)
        results_with_sg = await search_with_sg.unified("Sarah meeting")
        ep_with_sg = next((r for r in results_with_sg if r.get("_source") == "episode"), None)
        assert ep_with_sg is not None
        boosted_score = ep_with_sg["score"]

        assert boosted_score > base_score

    @pytest.mark.asyncio
    async def test_unified_no_social_no_boost(self, db):
        """Without social graph results, no boost is applied."""
        from src.engine_core.search import Search

        em = EpisodicMemory(db)
        ks = KnowledgeStore(db)

        await em.create_episode("budget meeting", "discussed Q4", "details")

        search = Search(db, episodic_memory=em, knowledge_store=ks)
        results = await search.unified("budget")
        assert len(results) >= 1
        assert results[0].get("_source") == "episode"

    @pytest.mark.asyncio
    async def test_unified_empty_query(self, db):
        """Empty query returns empty results."""
        from src.engine_core.search import Search

        search = Search(db)
        results = await search.unified("")
        assert results == []


class TestConsciousnessDepth:
    @pytest.mark.asyncio
    async def test_render_includes_current_context(self, db):
        """Consciousness render() includes recent episodes and active knowledge."""
        from src.memory.consciousness import ConsciousnessContext

        ctx = ConsciousnessContext(
            core_soul={"preamble": "You are September."},
            evolved_traits=[],
            user_persona=None,
            recent_episodes=[
                {"key": "budget review with Sarah", "description": "quarterly analysis"},
                {"key": "code refactor for auth module", "description": "cleaned up auth"},
            ],
            active_knowledge=[
                {"subject": "Q4 budget", "content": "Budget is 50k for Q4"},
                {"subject": "auth deadline", "content": "Auth module ships by March 30"},
            ],
        )
        rendered = ctx.render()
        assert "Current Context" in rendered
        assert "budget review with Sarah" in rendered
        assert "code refactor for auth module" in rendered
        assert "Q4 budget" in rendered
        assert "auth deadline" in rendered

    @pytest.mark.asyncio
    async def test_render_empty_context_omits_section(self, db):
        """No episodes or knowledge → no Current Context section."""
        from src.memory.consciousness import ConsciousnessContext

        ctx = ConsciousnessContext(
            core_soul={"preamble": "You are September."},
            evolved_traits=[],
            user_persona=None,
            recent_episodes=[],
            active_knowledge=[],
        )
        rendered = ctx.render()
        assert "Current Context" not in rendered

    @pytest.mark.asyncio
    async def test_render_evolved_traits_as_behavioral_adaptations(self, db):
        """Evolved traits render under 'Behavioral Adaptations' heading."""
        from src.memory.consciousness import ConsciousnessContext

        ctx = ConsciousnessContext(
            core_soul={"preamble": "You are September."},
            evolved_traits=[{"trait": "concise_responses", "value": "User prefers short answers"}],
            user_persona=None,
        )
        rendered = ctx.render()
        assert "Behavioral Adaptations" in rendered
        assert "concise_responses" in rendered

    @pytest.mark.asyncio
    async def test_manager_builds_with_episodes_and_knowledge(self, db):
        """ConsciousnessManager fetches recent episodes and active knowledge."""
        from src.memory.consciousness import ConsciousnessManager, CORE_SOUL

        if not CORE_SOUL:
            import src.memory.consciousness as cm

            cm.CORE_SOUL = {"preamble": "Test soul"}

        em = EpisodicMemory(db)
        ks = KnowledgeStore(db)
        sg = SocialGraph(db)
        soul_mod = Soul(db)

        await em.create_episode("recent task", "did something important", "details")
        await ks.write_knowledge_fact("key fact", "this matters a lot")

        mgr = ConsciousnessManager(
            soul=soul_mod,
            social_graph=sg,
            episodic_memory=em,
            knowledge_store=ks,
        )
        ctx = await mgr.get()
        assert ctx is not None
        rendered = ctx.render()
        assert "recent task" in rendered
        assert "key fact" in rendered


class TestEmbeddingBatch:
    @pytest.mark.asyncio
    async def test_batch_none_provider(self):
        """'none' provider returns zero vectors for all inputs."""
        from src.memory.embeddings import EmbeddingService

        svc = EmbeddingService(cdt_url="", cdt_key="", dim=4)
        results = await svc.embed_batch(["hello", "world", "test"])
        assert len(results) == 3
        assert all(len(v) == 4 for v in results)
        assert all(all(x == 0.0 for x in v) for v in results)

    @pytest.mark.asyncio
    async def test_batch_empty_list(self):
        """Empty input returns empty output."""
        from src.memory.embeddings import EmbeddingService

        svc = EmbeddingService(cdt_url="", cdt_key="", dim=4)
        results = await svc.embed_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_batch_single_item(self):
        """Single-item batch works like regular embed."""
        from src.memory.embeddings import EmbeddingService

        svc = EmbeddingService(cdt_url="", cdt_key="", dim=4)
        results = await svc.embed_batch(["hello"])
        assert len(results) == 1
        assert len(results[0]) == 4

    @pytest.mark.asyncio
    async def test_single_embed_unchanged(self):
        """Regular embed() still works after adding embed_batch()."""
        from src.memory.embeddings import EmbeddingService

        svc = EmbeddingService(cdt_url="", cdt_key="", dim=8)
        vec = await svc.embed("test")
        assert len(vec) == 8
        assert all(x == 0.0 for x in vec)


class TestInMemoryWorkingMemory:
    @pytest.mark.asyncio
    async def test_write_and_read(self):
        wm = InMemoryWorkingMemory("test-user")
        await wm.write("task-1", EntryType.MESSAGE_USER, "Hello world")
        entries = await wm.read("task-1")
        assert len(entries) == 1
        assert entries[0].content == "Hello world"

    @pytest.mark.asyncio
    async def test_read_by_type(self):
        wm = InMemoryWorkingMemory("test-user")
        await wm.write("task-1", EntryType.MESSAGE_USER, "User msg")
        await wm.write("task-1", EntryType.MESSAGE_ASSISTANT, "Assistant msg")
        await wm.write("task-1", EntryType.TOOL_CALL, "tool call")

        messages = await wm.read_messages("task-1")
        assert len(messages) == 2

    @pytest.mark.asyncio
    async def test_read_all_sorted(self):
        wm = InMemoryWorkingMemory("test-user")
        await wm.write("task-1", EntryType.MESSAGE_USER, "First")
        await wm.write("task-2", EntryType.MESSAGE_USER, "Second")
        all_items = await wm.read_all()
        assert len(all_items) == 2
        assert all_items[0].timestamp <= all_items[1].timestamp

    @pytest.mark.asyncio
    async def test_clear_task(self):
        wm = InMemoryWorkingMemory("test-user")
        await wm.write("task-1", EntryType.MESSAGE_USER, "Content")
        await wm.clear_task("task-1")
        entries = await wm.read("task-1")
        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_context_summary(self):
        wm = InMemoryWorkingMemory("test-user")
        await wm.write("task-1", EntryType.MESSAGE_USER, "Planning quarterly review")
        summary = await wm.get_context_summary()
        assert "quarterly review" in summary

    @pytest.mark.asyncio
    async def test_session_id(self):
        wm = InMemoryWorkingMemory("user-xyz")
        assert wm.session_id == "user-xyz"
