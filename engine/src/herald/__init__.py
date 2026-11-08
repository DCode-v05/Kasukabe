"""LLM layer — citadel gateway client.

The LLM is a commodity. The engine is the product. The citadel gateway
holds every provider key and decides which upstream serves each request.
Engine speaks one wire shape (Anthropic Messages) over one URL
(settings.cdt_url) with one bearer token (settings.cdt_key).

Usage:
    from src.herald import create_llm, LLM, Message, ToolDef, LLMResponse

    llm = create_llm(settings)
    response = await llm.generate([Message(role="user", content="Hello")])

How to add a new model:
    1. Register the model + a deployment in citadel
       (POST /admin/models, POST /admin/deployments). The engine's catalog
       is 100 percent synced from citadel /v1/models; there is no static
       seed to edit.
    2. Optional: set LLM_MODEL=<id> in .env to override the launch default.
       Defaults live in src/herald/defaults.py (the launch block).

Architecture:
    types.py            → Provider-neutral dataclasses
    base.py             → LLM Protocol (interface engine programs against)
    defaults.py         → Launch-time model defaults (the one block to edit)
    catalog.py          → Client-side model metadata (tiers, cost estimates)
    catalog_sync.py     → Citadel-synced runtime catalog (the only source)
    transport.py        → CitadelClient (raw httpx + SSE decode, no vendor SDK)
    provider.py         → Single Provider; speaks the Anthropic-Messages wire
                          to citadel over CitadelClient
    __init__.py         → Factory (create_llm) + re-exports
"""

from src.herald.base import LLM
from src.herald.catalog import (
    ModelCost,
    ModelDef,
    ModelTier,
    TIER_DEFAULTS,
    estimate_cost,
    format_tokens,
    format_usd,
    get_model,
    get_tier_for_model,
    list_models,
    resolve_model_for_tier,
)
from src.herald.defaults import MODEL_DEFAULTS, ModelRole, resolve_model
from src.herald.types import (
    ContentBlock,
    ImageBlock,
    LLMResponse,
    Message,
    PersistedBlock,
    StopReason,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolCall,
    ToolDef,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)
from src.config import Settings


def create_llm(settings: Settings, model_id: str | None = None) -> LLM:
    """Factory: build a Provider that routes through the citadel gateway.

    All inference flows over settings.cdt_url with settings.cdt_key. Citadel
    fans out to Anthropic, OpenAI, or Gemini upstream based on the
    deployment registered for ``model_id`` — engine never picks the
    upstream provider directly.

    ``model_id`` overrides the resolved primary model (LLM_MODEL env else
    the herald default) when present. Both must match a model registered in
    citadel's deployments table.

    """
    from src.herald.provider import Provider

    return Provider(settings, model_override=model_id)


__all__ = [
    # Protocol
    "LLM",
    "create_llm",
    # Types
    "Message",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ThinkingBlock",
    "ImageBlock",
    "PersistedBlock",
    "ContentBlock",
    "ToolDef",
    "ToolCall",
    "LLMResponse",
    "StreamEvent",
    "StopReason",
    "Usage",
    # Catalog + defaults
    "ModelCost",
    "ModelDef",
    "MODEL_DEFAULTS",
    "ModelRole",
    "ModelTier",
    "TIER_DEFAULTS",
    "resolve_model",
    "get_model",
    "list_models",
    "resolve_model_for_tier",
    "get_tier_for_model",
    "estimate_cost",
    "format_tokens",
    "format_usd",
    # v2.0.0: src.herald.router — multi-dimensional scoring, session pinning, fallback chains
]
