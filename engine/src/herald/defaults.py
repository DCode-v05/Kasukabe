"""Engine model defaults. HERALD OWNS ALL MODEL DEFAULTS.

This is the single code block edited when launching a new engine version:
every default model id the engine uses lives here, and only here. Settings
env vars (LLM_MODEL, PLANNER_MODEL, LIGHT_MODEL, DISPATCH_MODEL,
DISPATCH_MODEL_ALTERNATIVE, FALLBACK_MODEL, LC_MODEL, EMBEDDING_MODEL,
EMBEDDING_DIM) are pure optional overrides; empty/unset means "use the
default below". Overrides and defaults alike are validated against the
citadel-synced catalog at boot (src/herald/model_validation.py).

Whether a model actually exists is citadel's call: these are default id
strings, not catalog entries. The catalog itself is 100 percent synced from
citadel /v1/models (src/herald/catalog_sync.py); there is no static seed.

Intentionally NOT here: src/engine_core/cached_snip.py's model list. That is
a capability allowlist (which models support cache-aware snip), not a
default, and it changes on provider capability rollouts rather than engine
version launches.

This module imports nothing from the rest of the engine so it is cycle-free
and importable from anywhere (config, bootstrap, herald, memory, tests).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ModelRole(str, Enum):
    """Every distinct engine duty that needs a model id."""

    PRIMARY = "primary"
    PLANNER = "planner"  # heavy tier
    LIGHT = "light"
    REASONING = "reasoning"  # tier plumbing only; no settings override
    DISPATCH = "dispatch"
    DISPATCH_ALTERNATIVE = "dispatch_alternative"
    FALLBACK = "fallback"
    LC = "lc"  # Learning Centre batch model
    EMBEDDING = "embedding"


@dataclass(frozen=True)
class EngineModelDefaults:
    """The launch block. Change these values to re-model a new engine version.

    dispatch and dispatch_alternative order is what evals/dispatch_bench.py
    measured, not a preference; re-run the bench before changing them.
    fallback defaults to empty: no mid-turn overload fallback model unless a
    deploy opts in via FALLBACK_MODEL.
    """

    primary: str = "claude-sonnet-4-6"
    planner: str = "claude-opus-4-8"
    light: str = "claude-haiku-4-5"
    reasoning: str = "claude-opus-4-8"
    dispatch: str = "gemini-3.1-flash-lite"
    dispatch_alternative: str = "gpt-5-mini"
    fallback: str = ""
    lc: str = "gpt-5.4-mini"
    embedding: str = "text-embedding-3-small"
    embedding_dim: int = 1536


MODEL_DEFAULTS = EngineModelDefaults()

# Cost-savings baseline used by the router's savings estimator: "what would
# this turn have cost on the flagship". Should track the flagship at launch.
# Price fallbacks for it stay in src/herald/router.py.
BASELINE_MODEL = "claude-opus-4-8"

# Settings field that can override each role (resolve_model reads these).
# REASONING has no settings override by design: it is reachable only through
# tier resolution, which the planner/light overrides already steer.
ROLE_SETTINGS_FIELD: dict[ModelRole, str] = {
    ModelRole.PRIMARY: "llm_model",
    ModelRole.PLANNER: "planner_model",
    ModelRole.LIGHT: "light_model",
    ModelRole.DISPATCH: "dispatch_model",
    ModelRole.DISPATCH_ALTERNATIVE: "dispatch_model_alternative",
    ModelRole.FALLBACK: "fallback_model",
    ModelRole.LC: "lc_model",
    ModelRole.EMBEDDING: "embedding_model",
}

ROLE_DEFAULT: dict[ModelRole, str] = {
    ModelRole.PRIMARY: MODEL_DEFAULTS.primary,
    ModelRole.PLANNER: MODEL_DEFAULTS.planner,
    ModelRole.LIGHT: MODEL_DEFAULTS.light,
    ModelRole.REASONING: MODEL_DEFAULTS.reasoning,
    ModelRole.DISPATCH: MODEL_DEFAULTS.dispatch,
    ModelRole.DISPATCH_ALTERNATIVE: MODEL_DEFAULTS.dispatch_alternative,
    ModelRole.FALLBACK: MODEL_DEFAULTS.fallback,
    ModelRole.LC: MODEL_DEFAULTS.lc,
    ModelRole.EMBEDDING: MODEL_DEFAULTS.embedding,
}


def resolve_model(role: ModelRole, settings: Any = None) -> str:
    """THE one resolution helper: settings override (non-empty) else default.

    Every engine call site that needs a model id for a role goes through
    here; nothing else may hard-code a model id. settings is duck-typed
    (any object carrying the ROLE_SETTINGS_FIELD attribute) and optional so
    library code and tests can resolve defaults without a Settings instance.
    """
    field_name = ROLE_SETTINGS_FIELD.get(role)
    if settings is not None and field_name:
        override = getattr(settings, field_name, "")
        if override:
            return override
    return ROLE_DEFAULT[role]


def resolve_embedding_dim(settings: Any = None) -> int:
    """Embedding dimension: settings.embedding_dim when > 0, else default."""
    if settings is not None:
        dim = getattr(settings, "embedding_dim", 0)
        if dim and dim > 0:
            return int(dim)
    return MODEL_DEFAULTS.embedding_dim


# ── Routing chain defaults ──────────────────────────────────────────
# Consumed by src/herald/config_schema.py default factories. Keys are the
# router's capability bands (SIMPLE/MEDIUM/COMPLEX/REASONING), values are
# [primary, *fallbacks]. The router cost-ranks each chain (cheapest available
# and healthy first); ordering here is the tie-break only. 'premium' stays
# flagship-first (quality over cost) by design.

TIER_CHAIN_DEFAULTS: dict[str, dict[str, list[str]]] = {
    "auto": {
        "SIMPLE": ["gemini-3.1-flash-lite", "gemini-3.5-flash", "claude-haiku-4-5-20251001"],
        "MEDIUM": ["gemini-3.5-flash", "gpt-5.4", "claude-sonnet-4-6"],
        "COMPLEX": ["gpt-5.4", "claude-opus-4-8", "gemini-3.1-pro"],
        "REASONING": ["gemini-3.1-pro", "claude-opus-4-8"],
    },
    "eco": {
        "SIMPLE": ["gemini-3.1-flash-lite", "gemini-3.5-flash", "claude-haiku-4-5-20251001"],
        "MEDIUM": ["gemini-3.5-flash", "gpt-5.4", "claude-sonnet-4-6"],
        "COMPLEX": ["gpt-5.4", "claude-opus-4-8", "gemini-3.1-pro"],
        "REASONING": ["gemini-3.1-pro", "claude-opus-4-8"],
    },
    "premium": {
        "SIMPLE": ["claude-sonnet-4-6", "claude-haiku-4-5"],
        "MEDIUM": ["claude-sonnet-4-6", "claude-opus-4-8"],
        "COMPLEX": ["claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-4-6"],
        "REASONING": ["claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-4-6"],
    },
    "agentic": {
        "SIMPLE": ["gemini-3.1-flash-lite", "gemini-3.5-flash", "claude-haiku-4-5-20251001"],
        "MEDIUM": ["gemini-3.5-flash", "gpt-5.4", "claude-sonnet-4-6"],
        "COMPLEX": ["gpt-5.4", "claude-opus-4-8", "gemini-3.1-pro"],
        "REASONING": ["gemini-3.1-pro", "claude-opus-4-8"],
    },
}

# Task-fit chains (anti-monoculture): genuine quality-peers per task type.
TASK_CHAIN_DEFAULTS: dict[str, list[str]] = {
    "coding": ["gpt-5.4", "claude-sonnet-4-6", "claude-opus-4-8"],
    "reasoning": ["gemini-3.1-pro", "claude-opus-4-8"],
    "agentic": ["claude-sonnet-4-6", "claude-opus-4-8"],
    "writing": ["claude-sonnet-4-6", "claude-opus-4-8"],
    "simple": ["gemini-3.1-flash-lite", "gemini-3.5-flash", "claude-haiku-4-5-20251001"],
    "general": ["gemini-3.5-flash", "claude-sonnet-4-6"],
}
