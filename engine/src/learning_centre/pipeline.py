"""Learning Centre Pipeline v2 — Signal-based grouped encoding.

Reads learning signals (captured by post-turn hook + WM flush),
groups them, and encodes into episodes, knowledge, and social graph using a
cheap model (GPT-4o-mini). Replaces per-trajectory LLM extraction.

Batch phases:
  Phase 1: Episode creation — group signals by thread_id, one LLM call per task
  Phase 2: Knowledge encoding — cluster signals by topic, one LLM call per cluster
  Phase 3: Consolidation — one LLM call for cross-patterns, soul, contradictions
  Phase 4: Deterministic: confidence decay, skill evolution
  Phase 5: Cleanup — delete processed signals, invalidate consciousness cache
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.herald import LLM, Message
from src.herald.types import SystemPromptBlock
from src.security.write_guard import check_content_for_secrets

logger = logging.getLogger(__name__)

MD_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def strip_markdown_json(raw: str) -> str:
    m = MD_JSON_RE.search(raw)
    return m.group(1) if m else raw.strip()


def repair_json(text: str) -> str:
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass
    candidate = text.rstrip()
    for _ in range(3):
        if candidate.endswith("}}"):
            candidate = candidate[:-1]
        elif candidate.endswith("]]"):
            candidate = candidate[:-1]
        else:
            break
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    candidate = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        pass
    return text


EPISODE_ENCODING_SYSTEM = (
    "You are an episode creation engine for a personal AI assistant's memory.\n\n"
    "Given a set of learning signals from a conversation (both user-side and "
    "engine-side observations), create an episode record.\n\n"
    "The episode captures WHAT HAPPENED — a narrative record of the user's "
    "digital life, not a data extraction.\n\n"
    "Produce:\n"
    "1. key: One sentence (max 20 words) summarizing the event. "
    "MUST start with a date if temporal signals are present. Examples:\n"
    '   - "2026-03-15 | Debugged payment service outage — race condition in order queue"\n'
    '   - "2026-03-20 | Reviewed caching architecture with self-hosted Redis on EC2"\n'
    '   - "Discussed Python 3.12 deployment patterns for AWS ECS"\n\n'
    "2. description: 2-5 sentence narrative capturing:\n"
    "   - What the user wanted to accomplish\n"
    "   - What approach the engine took (tools used, recommendations made)\n"
    "   - What happened (outcome, corrections, decisions)\n"
    "   - Who was involved (people mentioned)\n"
    "   - What was learned (corrections, new knowledge)\n\n"
    'Return ONLY valid JSON: {"key": "...", "description": "..."}\n'
    'If the signals are too trivial for an episode: {"key": null, "description": null}'
)

KNOWLEDGE_ENCODING_SYSTEM = (
    "You are a knowledge extraction agent for a personal AI assistant.\n\n"
    "Given learning signals from conversations, extract entries to store.\n"
    "Two types:\n"
    '- "knowledge": facts about the user, their team, tools, environment, preferences\n'
    '- "process": learned workflows, procedures, recurring patterns\n'
    "  e.g. 'deploys on Tuesdays', 'code review needs 2 approvals',\n"
    "  'checks Grafana first when debugging production'\n\n"
    "For each entry:\n"
    '{"subject": "short topic", "content": "the information",\n'
    ' "type": "knowledge" or "process", "confidence": 0.4-1.0,\n'
    ' "tags": ["relevant", "tags"]}\n\n'
    "Confidence rules:\n"
    "- User-stated facts: 1.0\n"
    "- Inferred knowledge: 0.4-0.8\n"
    "- Corrections: 0.7-0.8\n"
    "- Processes observed once: 0.4-0.6\n"
    "- Processes observed repeatedly: 0.7-0.8\n\n"
    'Return ONLY valid JSON: {"entries": [...]}\n'
    'If nothing: {"entries": []}'
)

SOCIAL_ENCODING_SYSTEM = (
    "You are a social graph extraction agent for a personal AI assistant.\n\n"
    "Given learning signals from conversations, extract people and relationships.\n\n"
    "For each person:\n"
    '{"name": "full name", "attributes": [{"key": "role", "value": "CTO"}, ...]}\n\n'
    "For each relationship:\n"
    '{"from": "person A", "to": "person B", "relationship": "manages"}\n\n'
    'Return ONLY valid JSON: {"nodes": [...], "edges": [...]}\n'
    'If no people mentioned: {"nodes": [], "edges": []}'
)

CONSOLIDATION_SYSTEM = (
    "You are a consolidation engine for a personal AI assistant's memory.\n\n"
    "Given:\n"
    "- New knowledge entries written this batch\n"
    "- New episodes created this batch\n"
    "- Current soul traits (personality/preferences)\n"
    "- Entries flagged as potential contradictions during dedup\n\n"
    "Produce:\n"
    "1. cross_patterns: Insights that span multiple topics or conversations.\n"
    '   Each: {"subject": "...", "content": "...", "confidence": 0.4-0.8, '
    '"tags": ["cross-batch"]}\n\n'
    "2. soul_updates: Personality/preference trait changes.\n"
    '   Each: {"action": "new_signal" or "reinforce", "trait": "...", '
    '"value": "...", "confidence": 0.1-0.9, "evidence_count": N}\n'
    "   - New signals: low confidence (0.3-0.5), evidence_count=1\n"
    "   - Reinforcements: increment evidence_count, never lower confidence\n\n"
    "3. contradictions: Resolution of conflicting entries.\n"
    '   Each: {"action": "supersede_existing" or "discard_new" or "keep_both",\n'
    '          "existing_entry_id": "...", "new_entry_id": "...", "reason": "..."}\n\n'
    "Return ONLY valid JSON:\n"
    '{"cross_patterns": [...], "soul_updates": [...], "contradictions": [...]}\n'
    'If nothing: {"cross_patterns": [], "soul_updates": [], "contradictions": []}'
)


@dataclass
class LCPipelineResult:
    episodes_created: int = 0
    knowledge_written: int = 0
    knowledge_skipped_dedup: int = 0
    social_nodes_created: int = 0
    social_nodes_enriched: int = 0
    social_edges_written: int = 0
    errors: list[str] = field(default_factory=list)
    cross_batch_written: int = 0
    cross_batch_skipped: int = 0
    soul_traits_updated: int = 0
    entries_decayed: int = 0
    contradictions_resolved: int = 0
    contradictions_blocked: int = 0
    skill_gotchas_added: int = 0
    skill_forks_created: int = 0
    signals_processed: int = 0
    secrets_blocked: int = 0


EPISODE_CONTEXT_MAX_SIGNALS = 120

DEDUP_SKIP_THRESHOLD = 0.8
DEDUP_UPDATE_THRESHOLD = 0.5
MAX_LC_CONFIDENCE = 0.8

MEANINGFUL_USER_TYPES = {"fact", "preference", "person", "correction", "event", "relationship"}


class LCPipeline:
    """Learning Centre pipeline v2 — signal-based grouped encoding.

    Phase 1: Episode creation from signals grouped by thread_id
    Phase 2: Knowledge encoding from signals grouped by topic
    Phase 3: Consolidation (cross-patterns, soul, contradictions)
    Phase 4: Deterministic (confidence decay, skill evolution)
    Phase 5: Cleanup (delete processed signals, invalidate cache)
    """

    def __init__(
        self,
        adapter: LLM,
        db: Any,
        memory_system: Any,
        ud_write: Any | None = None,
        settings: Any | None = None,
        asset_directory: Any | None = None,
        on_data_changed: Any | None = None,
        signal_store: Any | None = None,
        embedding_service: Any | None = None,
    ) -> None:
        self.adapter = adapter
        self.db = db
        self.memory = memory_system
        self.ud_write = ud_write
        self.settings = settings
        self.asset_directory = asset_directory
        self.on_data_changed = on_data_changed
        if signal_store is not None:
            self.signal_store = signal_store
        elif db is not None:
            from src.learning_centre.signals import SignalStore

            self.signal_store = SignalStore(db)
        else:
            self.signal_store = None
        self.embedding_service = embedding_service

    async def run_batch(self) -> LCPipelineResult:
        result = LCPipelineResult()

        if self.signal_store is None:
            result.errors.append("No signal store available")
            await self.run_deterministic_steps(result)
            return result

        signals = await self.signal_store.pull_unprocessed()
        if not signals:
            await self.run_deterministic_steps(result)
            return result

        batch_id = uuid.uuid4().hex[:12]
        result.signals_processed = len(signals)
        logger.info("LC batch %s: %d signals to process", batch_id, len(signals))

        by_task = self.group_by_task(signals)
        all_signal_context = json.dumps(
            [
                {
                    "type": s["type"],
                    "source": s["source"],
                    "subject": s["subject"],
                    "content": s["content"][:300],
                }
                for s in signals
                if s["type"] not in ("system_event", "checkpoint")
            ],
            default=str,
        )


        episode_descriptions: list[str] = []
        for thread_id, task_signals in by_task.items():
            if self.is_trivial_task(task_signals):
                continue
            recent_signals = [
                s
                for s in task_signals
                if s["type"] not in ("system_event", "checkpoint")
            ][-EPISODE_CONTEXT_MAX_SIGNALS:]
            task_context = json.dumps(
                [
                    {
                        "type": s["type"],
                        "source": s["source"],
                        "subject": s["subject"],
                        "content": s["content"][:300],
                        "temporal_ref": s.get("temporal_ref"),
                    }
                    for s in recent_signals
                ],
                default=str,
            )
            try:
                raw = await self.adapter.generate(
                    [Message(role="user", content=task_context)],
                    system=[SystemPromptBlock(text=EPISODE_ENCODING_SYSTEM)],
                )
                parsed = json.loads(repair_json(strip_markdown_json(raw)))
                if parsed.get("key") and parsed.get("description"):
                    value = self.build_episode_value(task_signals)
                    if self.has_secrets(
                        parsed["key"] + "\n" + parsed["description"] + "\n" + value,
                        "episode-create",
                        result,
                    ):
                        continue
                    await self.memory.create_episode(
                        parsed["key"],
                        parsed["description"],
                        value,
                    )
                    result.episodes_created += 1
                    episode_descriptions.append(parsed["description"])
            except Exception as exc:
                result.errors.append(f"Episode agent (task {thread_id}): {exc}")


        batch_entry_ids: list[tuple[str, str]] = []
        groups = await self.cluster_signals(signals)
        for group in groups:
            group_context = json.dumps(
                [
                    {
                        "type": s["type"],
                        "source": s["source"],
                        "subject": s["subject"],
                        "content": s["content"][:300],
                    }
                    for s in group
                ],
                default=str,
            )
            try:
                raw = await self.adapter.generate(
                    [Message(role="user", content=group_context)],
                    system=[SystemPromptBlock(text=KNOWLEDGE_ENCODING_SYSTEM)],
                )
                parsed = json.loads(repair_json(strip_markdown_json(raw)))
                for entry in parsed.get("entries", []):
                    written_entry = await self.write_knowledge_entry(entry, result)
                    if written_entry:
                        batch_entry_ids.append(written_entry)
            except Exception as exc:
                result.errors.append(f"Knowledge agent: {exc}")


        try:
            raw = await self.adapter.generate(
                [Message(role="user", content=all_signal_context)],
                system=[SystemPromptBlock(text=SOCIAL_ENCODING_SYSTEM)],
            )
            parsed = json.loads(repair_json(strip_markdown_json(raw)))
            await self.write_social_updates(parsed, result)
        except Exception as exc:
            result.errors.append(f"Social agent: {exc}")

        signal_ids = [s["id"] for s in signals]
        await self.signal_store.mark_processed(signal_ids, batch_id)


        try:
            await self.consolidate(
                episode_descriptions,
                batch_entry_ids,
                result,
            )
        except Exception as exc:
            result.errors.append(f"Consolidation: {exc}")


        await self.run_deterministic_steps(result)


        wrote_something = (
            result.episodes_created > 0
            or result.knowledge_written > 0
            or result.social_nodes_enriched > 0
            or result.soul_traits_updated > 0
        )
        if wrote_something:
            try:
                deleted = await self.signal_store.delete_batch(batch_id)
                logger.info("LC batch %s: deleted %d of its own signals", batch_id, deleted)
            except Exception as exc:
                result.errors.append(f"Signal cleanup: {exc}")
        else:
            await self.signal_store.unmark_processed(batch_id)
            logger.warning(
                "LC batch %s: encoding produced 0 results from %d signals — keeping for retry",
                batch_id,
                result.signals_processed,
            )

        if self.on_data_changed is not None:
            self.on_data_changed()

        logger.info(
            "LC batch %s complete: %d signals, %d episodes, %d knowledge, %d soul",
            batch_id,
            result.signals_processed,
            result.episodes_created,
            result.knowledge_written,
            result.soul_traits_updated,
        )

        return result


    def group_by_task(self, signals: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for s in signals:
            tid = s.get("thread_id", "")
            if tid:
                groups.setdefault(tid, []).append(s)
        return groups

    def is_trivial_task(self, signals: list[dict]) -> bool:
        return not any(
            s["type"] in MEANINGFUL_USER_TYPES and s["source"] == "user" for s in signals
        )

    def build_episode_value(self, signals: list[dict]) -> str:
        return json.dumps(
            {
                "signals": [
                    {
                        "type": s["type"],
                        "source": s["source"],
                        "content": s["content"][:200],
                        "temporal_ref": s.get("temporal_ref"),
                    }
                    for s in signals
                    if s["type"] not in ("system_event",)
                ],
                "thread_id": signals[0]["thread_id"] if signals else "",
                "signal_count": len(signals),
            },
            default=str,
        )

    async def cluster_signals(self, signals: list[dict]) -> list[list[dict]]:
        if len(signals) <= 5:
            return [signals] if signals else []

        if self.embedding_service is None or not getattr(self.embedding_service, "is_real", False):
            groups: dict[str, list[dict]] = {}
            for s in signals:
                key = (s.get("subject") or "general")[:30]
                groups.setdefault(key, []).append(s)
            return list(groups.values())

        texts = [f"{s.get('subject', '')}: {s['content'][:200]}" for s in signals]
        try:
            embeddings = await self.embedding_service.embed_batch(texts)
        except Exception:
            return [signals]

        result_groups: list[list[dict]] = []
        assigned: set[int] = set()
        for i in range(len(signals)):
            if i in assigned:
                continue
            group = [signals[i]]
            assigned.add(i)
            for j in range(i + 1, len(signals)):
                if j in assigned:
                    continue
                sim = self.cosine_similarity(embeddings[i], embeddings[j])
                if sim > 0.7:
                    group.append(signals[j])
                    assigned.add(j)
            result_groups.append(group)
        return result_groups

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def has_secrets(
        self,
        content: str,
        site: str,
        result: LCPipelineResult,
    ) -> bool:
        """Scan content at an LC memory-write boundary.

        Returns True if a secret is detected — caller should skip the write.
        Logs the block with rule labels only (never the matched text, never
        LLM-derived surrounding strings), increments `result.secrets_blocked`.
        """
        labels = check_content_for_secrets(content)
        if not labels:
            return False
        logger.warning("LC: secret blocked at %s: %s", site, labels)
        result.secrets_blocked += 1
        return True

    async def write_knowledge_entry(
        self,
        entry: dict,
        result: LCPipelineResult,
    ) -> tuple[str, str] | None:
        subject = entry.get("subject", "")
        content = entry.get("content", "")
        if not subject or not content:
            return None

        confidence = float(entry.get("confidence", 0.6))
        tags = entry.get("tags", [])
        is_user_stated = confidence >= 1.0
        if not is_user_stated:
            confidence = min(confidence, MAX_LC_CONFIDENCE)

        if self.has_secrets(
            subject + "\n" + content,
            "knowledge-write",
            result,
        ):
            return None

        action = await self.dedup_knowledge(subject, content, confidence)
        if action == "skip":
            result.knowledge_skipped_dedup += 1
            return None

        try:
            if is_user_stated:
                written = await self.memory.write_knowledge_fact(
                    subject,
                    content,
                    tags=tags,
                )
            else:
                written = await self.memory.write_knowledge_inference(
                    subject,
                    content,
                    confidence=confidence,
                    tags=tags,
                )
            result.knowledge_written += 1
            entry_id = written.get("id", "") if isinstance(written, dict) else ""
            return (entry_id, subject) if entry_id else None
        except Exception:
            logger.warning("Failed to write knowledge %r", subject, exc_info=True)
            return None

    async def write_social_updates(
        self,
        social: dict,
        result: LCPipelineResult,
    ) -> None:
        for node in social.get("nodes", []):
            name = node.get("name", "")
            if not name:
                continue
            try:
                attrs = {
                    a["key"]: a["value"]
                    for a in node.get("attributes", [])
                    if a.get("key") and a.get("value")
                }
                if attrs and self.has_secrets(
                    "\n".join(
                        [str(k) for k in attrs] + [str(v) for v in attrs.values()],
                    ),
                    "social-node-enrich",
                    result,
                ):
                    continue
                person = await self.memory.find_or_create_person(name)
                if attrs:
                    await self.memory.enrich_social_graph_node(person["id"], attrs)
                    result.social_nodes_enriched += 1
                else:
                    result.social_nodes_created += 1
            except Exception:
                logger.warning("Failed to write social node %r", name, exc_info=True)

        for edge in social.get("edges", []):
            try:
                from_name = edge.get("from", "")
                to_name = edge.get("to", "")
                rel = edge.get("relationship", "")
                if from_name and to_name and rel:
                    if self.has_secrets(rel, "social-edge", result):
                        continue
                    node_a = await self.memory.find_or_create_person(from_name)
                    node_b = await self.memory.find_or_create_person(to_name)
                    await self.memory.enrich_social_graph_edge(
                        node_a["id"],
                        node_b["id"],
                        {"engine_inference": rel},
                    )
                    result.social_edges_written += 1
            except Exception:
                logger.warning("Failed to write social edge", exc_info=True)

    async def dedup_knowledge(
        self,
        subject: str,
        content: str,
        confidence: float,
    ) -> str:
        try:
            results = await self.memory.search_knowledge(subject, limit=3)
        except Exception:
            return "write"

        if not results:
            return "write"

        top = results[0]
        top_score = top.get("score", 0.0)
        top_id = top.get("id", "")

        if top_score >= DEDUP_SKIP_THRESHOLD:
            if top_id:
                existing_conf = top.get("confidence", 0.5)
                boosted = min(existing_conf + 0.05, 1.0)
                try:
                    await self.memory.update_confidence(top_id, boosted)
                    await self.memory.increment_usage(top_id)
                except Exception:
                    pass
            return "skip"

        if top_score >= DEDUP_UPDATE_THRESHOLD:
            if top_id:
                try:
                    await self.memory.supersede_entry(top_id)
                except Exception:
                    logger.warning("Failed to supersede entry %s", top_id)
            return "updated"

        return "write"

    async def consolidate(
        self,
        episode_descriptions: list[str],
        batch_entry_ids: list[tuple[str, str]],
        result: LCPipelineResult,
    ) -> None:
        if not episode_descriptions and not batch_entry_ids:
            return

        current_traits = []
        try:
            current_traits = await self.memory.get_evolved_soul_full()
        except Exception:
            pass

        context = json.dumps(
            {
                "new_episodes": episode_descriptions[:20],
                "new_knowledge_count": result.knowledge_written,
                "current_traits": current_traits,
                "flagged_entries": [
                    {"id": eid, "subject": subj} for eid, subj in batch_entry_ids[:30]
                ],
            },
            default=str,
        )

        raw = await self.adapter.generate(
            [Message(role="user", content=context)],
            system=[SystemPromptBlock(text=CONSOLIDATION_SYSTEM)],
        )
        parsed = json.loads(repair_json(strip_markdown_json(raw)))

        for entry in parsed.get("cross_patterns", []):
            subject = entry.get("subject", "")
            content = entry.get("content", "")
            if not subject or not content:
                continue
            confidence = min(float(entry.get("confidence", 0.6)), MAX_LC_CONFIDENCE)
            if self.has_secrets(
                subject + "\n" + content,
                "cross-batch-knowledge",
                result,
            ):
                continue
            action = await self.dedup_knowledge(subject, content, confidence)
            if action == "skip":
                result.cross_batch_skipped += 1
                continue
            try:
                await self.memory.write_knowledge_inference(
                    subject,
                    content,
                    confidence=confidence,
                    tags=entry.get("tags", []),
                )
                result.cross_batch_written += 1
            except Exception:
                logger.warning("Failed to write cross-batch %r", subject, exc_info=True)

        existing_traits = {t["trait"]: t for t in current_traits}
        for update in parsed.get("soul_updates", []):
            trait = update.get("trait", "")
            value = update.get("value", "")
            if not trait or not value:
                continue
            if update.get("action") not in ("new_signal", "reinforce"):
                continue

            confidence = float(update.get("confidence", 0.4))
            evidence = int(update.get("evidence_count", 1))
            if trait in existing_traits:
                ex = existing_traits[trait]
                confidence = max(confidence, float(ex.get("confidence", 0)))
                evidence = max(evidence, int(ex.get("evidence_count", 0)))

            if self.has_secrets(value, "soul-update", result):
                continue

            try:
                await self.memory.update_evolved_soul(
                    trait,
                    value,
                    confidence,
                    evidence,
                )
                result.soul_traits_updated += 1
            except Exception:
                logger.warning("Failed to update soul trait %r", trait, exc_info=True)

        for c in parsed.get("contradictions", []):
            action = c.get("action", "")
            try:
                if action == "supersede_existing" and c.get("existing_entry_id"):
                    await self.memory.supersede_entry(c["existing_entry_id"])
                    result.contradictions_resolved += 1
                elif action == "discard_new" and c.get("new_entry_id"):
                    await self.memory.supersede_entry(c["new_entry_id"])
                    result.contradictions_blocked += 1
            except Exception:
                logger.warning("Contradiction resolution failed", exc_info=True)


    async def run_deterministic_steps(self, result: LCPipelineResult) -> None:
        try:
            result.entries_decayed = await self.step8_confidence_decay()
        except Exception as exc:
            result.errors.append(f"Step 8 error: {exc}")

        try:
            gotchas, forks = await self.step11_skill_evolution()
            result.skill_gotchas_added = gotchas
            result.skill_forks_created = forks
        except Exception as exc:
            result.errors.append(f"Step 11 error: {exc}")

    SOUL_DEMOTE_THRESHOLD = 0.5
    SOUL_REMOVE_THRESHOLD = 0.2
    SOUL_DECAY_FACTOR = 0.9

    async def step8_confidence_decay(self) -> int:
        total = 0
        try:
            ks_affected = await self.memory.run_confidence_decay()
            total += ks_affected
        except Exception:
            logger.warning("LC Step 8: KS decay failed", exc_info=True)

        try:
            traits = await self.memory.get_evolved_soul_full()
        except Exception:
            return total

        for trait in traits:
            conf = float(trait.get("confidence", 0))
            decayed = conf * self.SOUL_DECAY_FACTOR

            if trait.get("status") == "signal" and decayed < self.SOUL_REMOVE_THRESHOLD:
                try:
                    await self.memory.update_evolved_soul(
                        trait["trait"],
                        trait["value"],
                        0.0,
                        int(trait.get("evidence_count", 0)),
                    )
                    total += 1
                except Exception:
                    logger.warning(
                        "LC Step 8: failed to remove trait %r", trait["trait"], exc_info=True
                    )
            elif trait.get("status") == "expressed" and decayed < self.SOUL_DEMOTE_THRESHOLD:
                try:
                    await self.memory.update_evolved_soul(
                        trait["trait"],
                        trait["value"],
                        decayed,
                        int(trait.get("evidence_count", 0)),
                    )
                    total += 1
                except Exception:
                    logger.warning(
                        "LC Step 8: failed to demote trait %r", trait["trait"], exc_info=True
                    )

        return total

    async def step11_skill_evolution(self) -> tuple[int, int]:
        """Read correction signals from learning_signals to drive skill evolution."""
        if self.signal_store is None:
            return (0, 0)

        gotchas_added = 0
        forks_created = 0

        try:
            corrections = await self.db.fetch(
                "SELECT content, thread_id FROM learning_signals "
                "WHERE type = 'correction' AND source = 'user' "
                "ORDER BY created_at DESC LIMIT 50",
            )
        except Exception:
            return (0, 0)

        try:
            tool_calls = await self.db.fetch(
                "SELECT subject, thread_id FROM learning_signals "
                "WHERE type = 'tool_call' AND subject LIKE 'skill:%' "
                "ORDER BY created_at DESC LIMIT 100",
            )
        except Exception:
            return (0, 0)

        task_skills: dict[str, set[str]] = {}
        for tc in tool_calls:
            tid = tc["thread_id"]
            skill = tc["subject"].replace("skill:", "")
            task_skills.setdefault(tid, set()).add(skill)

        for corr in corrections:
            tid = corr["thread_id"]
            skills = task_skills.get(tid, set())
            if not skills:
                continue

            for skill_name in skills:
                try:
                    await self.db.execute(
                        """INSERT INTO user_skills (id, name, source, occurrence_count)
                           VALUES (?, ?, 'platform', 1)
                           ON CONFLICT (name) DO UPDATE SET
                             correction_count = correction_count + 1,
                             last_used = datetime('now')""",
                        (uuid.uuid4().hex, skill_name),
                    )
                    await self.db.commit()
                    gotchas_added += 1
                except Exception:
                    pass

        return gotchas_added, forks_created
