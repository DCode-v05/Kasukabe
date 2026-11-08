"""Model catalog, tiers, and cost estimation.

Client-side metadata for the models engine talks to via citadel. The
``provider`` field is informational only — citadel decides which upstream
serves each request based on its own deployment registry. Engine just
passes ``model_id`` through.

The catalog is 100 percent citadel-synced: there is NO static seed. Rows
come exclusively from citadel /v1/models via src/herald/catalog_sync.py
(empty until the first successful sync; an accelerated retry self-heals a
degraded boot). Adding a new model: register it in citadel
(POST /admin/models + /admin/deployments) and the next sync picks it up.
Default model ids live in src/herald/defaults.py (the launch block).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.herald.defaults import MODEL_DEFAULTS


# ── Model definitions ───────────────────────────────────────────────


@dataclass(frozen=True)
class ModelCost:
    input_per_1m: float
    output_per_1m: float
    cache_read_per_1m: float = 0.0
    cache_create_per_1m: float = 0.0


@dataclass(frozen=True)
class ModelDef:
    id: str
    provider: str
    name: str
    context_window: int
    max_output_tokens: int
    supports_tools: bool
    supports_vision: bool
    cost: ModelCost | None
    tier: str = "standard"


DEFAULT_CONTEXT_WINDOW = 200_000


def overlay_lookup(model_id: str) -> ModelDef | None:
    """Citadel-synced catalog lookup (the only source; no static seed).
    Lazy import breaks the cycle (catalog_sync imports catalog)."""
    try:
        from src.herald.catalog_sync import lookup as sync_lookup
    except Exception:
        return None
    return sync_lookup(model_id)


def get_model(model_id: str) -> ModelDef | None:
    return overlay_lookup(model_id)


def get_context_window(model_id: str) -> int:
    """Return the context window for a model, or DEFAULT_CONTEXT_WINDOW if unknown."""
    model = overlay_lookup(model_id)
    if model is not None:
        return model.context_window
    return DEFAULT_CONTEXT_WINDOW


def list_models(provider: str | None = None) -> list[ModelDef]:
    """Every model the synced catalog knows. Empty before the first sync."""
    from src.herald.catalog_sync import get_catalog_overlay

    models = list(get_catalog_overlay().values())
    if provider:
        models = [m for m in models if m.provider == provider]
    return models


# ── Tiers ───────────────────────────────────────────────────────────


class ModelTier(str, Enum):
    HEAVY = "heavy"
    STANDARD = "standard"
    LIGHT = "light"
    REASONING = "reasoning"


# Derived from the herald launch block - edit src/herald/defaults.py, not
# this mapping, when re-modeling an engine version.
TIER_DEFAULTS: dict[ModelTier, str] = {
    ModelTier.HEAVY: MODEL_DEFAULTS.planner,
    ModelTier.STANDARD: MODEL_DEFAULTS.primary,
    ModelTier.LIGHT: MODEL_DEFAULTS.light,
    ModelTier.REASONING: MODEL_DEFAULTS.reasoning,
}

# Maps tier → settings field name that can override the default
TIER_SETTINGS_FIELD: dict[ModelTier, str] = {
    ModelTier.LIGHT: "light_model",
    ModelTier.HEAVY: "planner_model",
}


def cost_sort_key(m: "ModelDef") -> tuple[bool, float]:
    """Sort key: priced models first (ascending input+output per-1M), unpriced last."""
    if m.cost is None:
        return (True, 0.0)
    return (False, m.cost.input_per_1m + m.cost.output_per_1m)


def resolve_model_for_tier(tier: ModelTier, settings: Any = None) -> ModelDef:
    """Resolve a tier to a concrete model.

    Order:
      1. Explicit settings override (e.g. settings.light_model).
      2. Citadel-synced overlay — citadel is the source of truth for which
         models a pool actually has. For non-LIGHT tiers, prefer the overlay
         entry whose id starts with TIER_DEFAULTS[tier] (handles dated
         snapshots like claude-opus-4-7-20260101 → "claude-opus-4-7"). LIGHT
         tier picks the cheapest by design (it is the cheap-on-purpose tier).
         Multiple matches → cheapest by input+output per-1M tokens.
      3. Synthetic fallback carrying the tier's default id when the catalog
         has no row for the tier (citadel unreachable and never synced,
         tier empty in pool, etc.) - metadata is conservative defaults and
         self-heals once the sync lands.
    """
    if settings is not None:
        field = TIER_SETTINGS_FIELD.get(tier)
        if field:
            override = getattr(settings, field, "")
            if override:
                # Overlay-aware: an override that citadel knows (even if it
                # was never in any static data) is honored. Boot-time model
                # validation already vetted it; an id the catalog cannot
                # resolve right now falls through to tier resolution below
                # rather than being silently dropped forever.
                model = overlay_lookup(override)
                if model is not None:
                    return model

    from src.herald.catalog_sync import get_catalog_overlay

    candidates = [m for m in get_catalog_overlay().values() if m.tier == tier.value]
    if candidates:
        if tier != ModelTier.LIGHT:
            preferred = TIER_DEFAULTS.get(tier, "")
            if preferred:
                prefix_matches = [m for m in candidates if m.id.startswith(preferred)]
                if prefix_matches:
                    prefix_matches.sort(key=cost_sort_key)
                    return prefix_matches[0]
        candidates.sort(key=cost_sort_key)
        return candidates[0]

    # Empty catalog (pre-first-sync): still hand back the tier's default id
    # so adapters can be built; citadel is the authority on whether the id
    # actually serves. Conservative metadata, unknown cost.
    model_id = TIER_DEFAULTS[tier]
    return ModelDef(
        id=model_id,
        provider="unknown",
        name=model_id,
        context_window=DEFAULT_CONTEXT_WINDOW,
        max_output_tokens=64000,
        supports_tools=True,
        supports_vision=False,
        cost=None,
        tier=tier.value,
    )


def get_tier_for_model(model_id: str) -> ModelTier | None:
    model = overlay_lookup(model_id)
    if model is None:
        return None
    try:
        return ModelTier(model.tier)
    except ValueError:
        return None


# ── Cost estimation ─────────────────────────────────────────────────


def estimate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
) -> float | None:
    model = overlay_lookup(model_id)
    if model is None or model.cost is None:
        return None
    c = model.cost
    return (
        (input_tokens / 1_000_000) * c.input_per_1m
        + (output_tokens / 1_000_000) * c.output_per_1m
        + (cache_read_tokens / 1_000_000) * c.cache_read_per_1m
        + (cache_create_tokens / 1_000_000) * c.cache_create_per_1m
    )


def format_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}m"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def format_usd(amount: float) -> str:
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"
