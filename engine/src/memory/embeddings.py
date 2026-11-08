"""Embedding utilities — EmbeddingService and similarity scoring.

EmbeddingService: async, process-level singleton using OpenAI text-embedding-3-small
(1536 dims) or a zero-vector fallback ("none" provider). Never returns [].
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
from typing import Any
from datetime import datetime, timezone

from src.herald.defaults import (
    MODEL_DEFAULTS,
    ModelRole,
    resolve_embedding_dim,
    resolve_model,
)

log = logging.getLogger(__name__)


STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "it",
        "as",
        "be",
        "was",
        "were",
        "been",
        "are",
        "am",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "he",
        "she",
        "they",
        "them",
        "his",
        "her",
        "its",
        "not",
        "no",
        "so",
        "if",
        "can",
        "will",
        "just",
        "about",
        "up",
    }
)


def stem(word: str) -> str:
    """Lightweight suffix-stripping stemmer. No external dependencies.

    Handles common English suffixes. Not as thorough as Porter but covers
    the cases that matter for memory search (plurals, verb forms, -tion/-ment).
    """
    if len(word) <= 3:
        return word

    # -ies → -y (companies → company)
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    # -ves → -f (knives → knif — close enough for matching)
    if word.endswith("ves") and len(word) > 4:
        return word[:-3] + "f"
    # -ing → strip (running → run, processing → process)
    if word.endswith("ing") and len(word) > 5:
        base = word[:-3]
        if len(base) >= 2 and base[-1] == base[-2] and base[-1] in "bdglmnprt":
            return base[:-1]
        return base
    # -tion, -sion → strip
    if word.endswith("tion") and len(word) > 5:
        return word[:-4]
    if word.endswith("sion") and len(word) > 5:
        return word[:-4]
    # -ment → strip
    if word.endswith("ment") and len(word) > 5:
        return word[:-4]
    # -ness → strip
    if word.endswith("ness") and len(word) > 5:
        return word[:-4]
    # -able, -ible → strip
    if word.endswith("able") and len(word) > 5:
        return word[:-4]
    if word.endswith("ible") and len(word) > 5:
        return word[:-4]
    # -ly → strip
    if word.endswith("ly") and len(word) > 4:
        return word[:-2]
    # -ed → strip (processed → process, planned → plan)
    if word.endswith("ed") and len(word) > 4:
        base = word[:-2]
        if len(base) >= 2 and base[-1] == base[-2] and base[-1] in "bdglmnprt":
            return base[:-1]
        return base
    # -er → strip (runner → run, bigger → big)
    if word.endswith("er") and len(word) > 4:
        base = word[:-2]
        if len(base) >= 2 and base[-1] == base[-2] and base[-1] in "bdglmnprt":
            return base[:-1]
        return base
    # -es → strip (processes → process)
    if word.endswith("es") and len(word) > 4:
        return word[:-2]
    # -s → strip (projects → project), but not words ending in "ss" (process, access)
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]

    return word


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, filter stop words, stem."""
    raw = re.findall(r"[a-z0-9]+", text.lower())
    return [stem(w) for w in raw if w not in STOP_WORDS]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0.0 if either is zero."""
    if len(a) != len(b) or not a:
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))

    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0

    return dot / (mag_a * mag_b)


def keyword_score(query: str, text: str) -> float:
    """Word-overlap ratio between query and text.

    Tokens are stemmed and stop-words filtered before comparison.
    Returns the fraction of query stems found in the text (0.0 to 1.0).
    """
    query_words = set(tokenize(query))
    if not query_words:
        return 0.0

    text_words = set(tokenize(text))
    overlap = query_words & text_words
    return len(overlap) / len(query_words)


def recency_boost(created_at: str, half_life_days: float = 30.0) -> float:
    """Exponential decay multiplier for time-sensitive search results.

    Returns 1.0 for now, 0.5 after half_life_days, 0.25 after 2x, etc.
    Clamped to [0.5, 1.0] so old results are deprioritized but never buried.
    """
    try:
        ts = datetime.fromisoformat(created_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        raw = math.pow(0.5, age_days / half_life_days)
        return max(0.5, min(1.0, raw))
    except Exception:
        return 0.75


def hybrid_score(
    query: str,
    text: str,
    embedding_a: list[float] | None = None,
    embedding_b: list[float] | None = None,
    keyword_weight: float = 0.3,
    semantic_weight: float = 0.7,
    precomputed_semantic: float | None = None,
) -> float:
    """Combined score: keyword overlap + semantic similarity.

    Default weights: 30% keyword, 70% semantic (per architecture spec).
    Falls back to keyword-only if no semantic signal available.

    precomputed_semantic: when the caller already has a similarity score
    (e.g. from sqlite-vec), pass it directly to skip cosine_similarity().
    """
    kw = keyword_score(query, text)

    if precomputed_semantic is not None:
        sem = precomputed_semantic
    elif embedding_a and embedding_b:
        sem = cosine_similarity(embedding_a, embedding_b)
    else:
        return kw

    return keyword_weight * kw + semantic_weight * sem


class EmbeddingService:
    """Async embedding service — process-level singleton.

    Routes through citadel's ``/v1/embeddings`` (OpenAI-compatible
    shape). Engine never holds the upstream provider key directly — the
    same ``CDT_KEY`` that authenticates inference also authenticates
    embeddings.

    embed() is async, thread-safe via asyncio.Lock, and NEVER returns [].
    On any failure (citadel unreachable, model not in pool, etc.) it
    returns a zero vector of the configured dimension. Callers can
    detect degraded mode via ``.is_real``.
    """

    def __init__(
        self,
        *,
        cdt_url: str,
        cdt_key: str,
        model: str = MODEL_DEFAULTS.embedding,
        dim: int = MODEL_DEFAULTS.embedding_dim,
    ) -> None:
        self.cdt_url = cdt_url.rstrip("/")
        self.cdt_key = cdt_key
        self.model = model
        self.dim = dim
        self.lock = asyncio.Lock()
        self.client: Any | None = None  # AsyncSeptember (untyped SDK), lazy

    def zero_vector(self) -> list[float]:
        return [0.0] * self.dim

    @property
    def is_real(self) -> bool:
        """True iff citadel URL + key are configured. False → zero vectors."""
        return bool(self.cdt_url and self.cdt_key)

    async def ensure_client(self) -> None:
        """Lazy-init the citadel SDK client. One per service instance.

        The SDK applies bearer auth, default retries with backoff, and a
        typed error hierarchy; the engine no longer hand-rolls the httpx
        client or its auth headers for embeddings.
        """
        if self.client is not None or not self.is_real:
            return
        try:
            from september import AsyncSeptember

            self.client = AsyncSeptember(
                citadel={"api_key": self.cdt_key, "base_url": self.cdt_url},
                timeout=30.0,
            )
        except Exception:
            log.warning(
                "Failed to init citadel embedding client — zero vectors",
                exc_info=True,
            )

    async def embed(self, text: str) -> list[float]:
        """Embed text → vector. Never returns []."""
        if not self.is_real:
            return self.zero_vector()

        async with self.lock:
            await self.ensure_client()
        if self.client is None:
            return self.zero_vector()

        try:
            body = await self.client.citadel.embeddings.create(
                {"model": self.model, "input": text},
            )
            usage = body.get("usage") or {}
            total_tokens = int(usage.get("total_tokens", 0) or 0)
            if total_tokens:
                from src.herald.usage_tracker import record_embedding_usage

                record_embedding_usage(total_tokens)
            vec = body.get("data", [{}])[0].get("embedding") or []
            if len(vec) == self.dim:
                return vec
            return list(vec[: self.dim]) + [0.0] * max(0, self.dim - len(vec))
        except Exception:
            log.warning("Embedding API call failed — zero vector", exc_info=True)
            return self.zero_vector()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts. One vector per input, same order. Cap 100/batch."""
        if not texts:
            return []
        if not self.is_real:
            return [self.zero_vector() for _ in texts]

        async with self.lock:
            await self.ensure_client()
        if self.client is None:
            return [self.zero_vector() for _ in texts]

        results: list[list[float]] = []
        for start in range(0, len(texts), 100):
            chunk = texts[start : start + 100]
            try:
                body = await self.client.citadel.embeddings.create(
                    {"model": self.model, "input": chunk},
                )
                usage = body.get("usage") or {}
                total_tokens = int(usage.get("total_tokens", 0) or 0)
                if total_tokens:
                    from src.herald.usage_tracker import record_embedding_usage

                    record_embedding_usage(total_tokens)
                data = sorted(body.get("data", []), key=lambda x: x.get("index", 0))
                for item in data:
                    vec = item.get("embedding") or []
                    if len(vec) == self.dim:
                        results.append(vec)
                    else:
                        results.append(list(vec[: self.dim]) + [0.0] * max(0, self.dim - len(vec)))
            except Exception:
                log.warning("Batch embedding failed — zero vectors", exc_info=True)
                results.extend(self.zero_vector() for _ in chunk)

        return results

    async def aclose(self) -> None:
        """Close the SDK client. Called on shutdown."""
        if self.client is not None:
            try:
                await self.client.aclose()
            except Exception:
                pass


singleton: EmbeddingService | None = None
singleton_key: tuple[str, str, str, int] | None = None
singleton_lock = threading.Lock()


def get_embedding_service(settings: object) -> EmbeddingService:
    """Return (or create) the process-level EmbeddingService singleton.

    Reads ``cdt_url``, ``cdt_key``, ``embedding_model``, ``embedding_dim``
    from the Settings object. Singleton is keyed on those four — when
    settings change (rotation, etc.) a fresh client is built.
    """
    global singleton, singleton_key
    key = (
        getattr(settings, "cdt_url", ""),
        getattr(settings, "cdt_key", ""),
        resolve_model(ModelRole.EMBEDDING, settings),
        resolve_embedding_dim(settings),
    )
    if singleton is not None and singleton_key == key:
        return singleton
    with singleton_lock:
        if singleton is None or singleton_key != key:
            singleton = EmbeddingService(
                cdt_url=key[0],
                cdt_key=key[1],
                model=key[2],
                dim=key[3],
            )
            singleton_key = key
    return singleton
