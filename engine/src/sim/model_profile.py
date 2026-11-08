"""The one-model allowlist: moonshotai.kimi-k2.5, flex pricing, nothing else.

DESIGN.md section 4 (final decision 2026-07-30) fixes the sim's ENTIRE model
roster - every herald consumer, not just the agent's own cognition - at
exactly one id, served flex-tier off Bedrock ap-south-1 behind the local
broker:

    moonshotai.kimi-k2.5   $0.36 in / $1.80 out per 1M

"Kimi K2.5 is the ONLY model for EVERY herald consumer - main thread,
learning centre, summarizer, importance scoring, any subsystem that asks
herald for a model. There is no second model and no model-choice mechanic."
That sentence is why this module exposes exactly one id constant (``MODEL_ID``)
rather than a role -> id mapping: a mapping, even a trivial one where every
role maps to the same value, is itself a model-choice mechanic waiting to
drift the day someone edits one row and not the others. There is nothing to
choose between, so nothing here is shaped like a choice.

This id must match app/broker/config.py's MODEL_ALLOWLIST entry EXACTLY
(broker/allowlist.py's check_model_allowed does a plain string equality, no
prefix or version-suffix tolerance) - "moonshotai.kimi-k2.5", not the bare
"kimi-k2.5" a model's own display name might suggest. Getting this string
wrong is not a cosmetic bug: every request would 403 at the broker.

Two independent guards keep a second model from ever reaching the broker:

1. ``assert_allowed`` / ``guarded_create_llm`` - checked at adapter
   construction, in-process, before any HTTP leaves the engine.
2. The fake-broker test (tests/test_sim_fake_broker.py) - checked at the
   wire, after the fact, by recording every model id the broker transport
   actually saw and asserting the recorded set is exactly ``{MODEL_ID}``.

Neither guard is the catalog's real job in production (that is
src/herald/catalog_sync.py's citadel /v1/models poll); this module exists
because the sim boot (pinned_boot.py) does not run that poll or
src/herald/model_validation.py at all - see pinned_boot.py's module
docstring for why. ``register_into_catalog_overlay`` below seeds the same
runtime_overlay catalog_sync.py owns, so herald's cost-estimation and
tier-resolution helpers (get_model, estimate_cost, resolve_model_for_tier)
see a correct, single-row catalog without a live sync ever running.
"""

from __future__ import annotations

import os

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.herald.catalog import ModelCost, ModelDef

if TYPE_CHECKING:
    from src.config import Settings
    from src.herald.base import LLM


class SimModelNotAllowed(ValueError):
    """Raised when sim code would resolve or request a model other than
    MODEL_ID. Always a bug in the sim profile, never a broker error - the
    broker is never reached when this fires."""


MODEL_ID = "moonshotai.kimi-k2.5"

ALLOWED_MODEL_IDS: frozenset[str] = frozenset({MODEL_ID})


@dataclass(frozen=True)
class FlexPricing:
    """Bedrock flex-tier pricing metadata (DESIGN.md section 11).

    Flex pricing is a Bedrock inference-profile pricing CLASS, not a herald
    ModelTier (STANDARD/LIGHT/...; see src/herald/catalog.py's ModelTier -
    that enum classifies chat-vs-embedding capability, and is unrelated).
    This dataclass carries the numbers the sim needs and herald's ModelDef
    doesn't have a field for: which Bedrock region serves the model and the
    RPM bucket every herald consumer shares by using the one model (DESIGN.md
    section 9: "100 RPM per model ... is the binding constraint").
    """

    input_per_1m: float
    output_per_1m: float
    bedrock_region: str = "ap-south-1"
    rpm_limit: int = 100
    pricing_tier: str = "flex"


@dataclass(frozen=True)
class SimModelSpec:
    """Everything the sim profile knows about the one allowlisted model."""

    model_id: str
    provider: str
    context_window: int
    max_output_tokens: int
    pricing: FlexPricing


SIM_CONTEXT_WINDOW = int(os.environ.get("KASUKABE_CONTEXT_WINDOW", "131072"))

MODEL_SPEC = SimModelSpec(
    model_id=MODEL_ID,
    provider="moonshotai",
    context_window=SIM_CONTEXT_WINDOW,
    max_output_tokens=32_000,
    pricing=FlexPricing(input_per_1m=0.36, output_per_1m=1.80),
)

SIM_MODEL_SPECS: dict[str, SimModelSpec] = {MODEL_ID: MODEL_SPEC}


def assert_allowed(model_id: str) -> None:
    """Raise SimModelNotAllowed unless ``model_id`` is exactly MODEL_ID.

    Call this at every point sim code is about to turn a model id into an
    adapter or a catalog entry. Cheap and synchronous by design so nothing
    ever has an excuse to skip it on a hot path.
    """
    if model_id not in ALLOWED_MODEL_IDS:
        raise SimModelNotAllowed(
            f"model {model_id!r} is not the Kasukabe sim model ({MODEL_ID!r}). "
            "DESIGN.md section 4 fixes the sim roster at exactly this one id for "
            "every herald consumer - main thread, learning centre, summarizer, "
            "importance scoring, any subsystem that asks herald for a model. "
            "If a second model is genuinely needed, that is a DESIGN.md change, "
            "not a config override."
        )


def spec_to_model_def(spec: SimModelSpec) -> ModelDef:
    """Convert a SimModelSpec to herald's client-side ModelDef shape.

    tier="standard": the allowlisted model is an ordinary chat model (no
    embedding, no reasoning-tier routing chain reaches it - see
    pinned_boot.py's docstring on why REASONING is unreachable in this
    topology).
    """
    return ModelDef(
        id=spec.model_id,
        provider=spec.provider,
        name=spec.model_id,
        context_window=spec.context_window,
        max_output_tokens=spec.max_output_tokens,
        supports_tools=True,
        supports_vision=False,
        cost=ModelCost(
            input_per_1m=spec.pricing.input_per_1m,
            output_per_1m=spec.pricing.output_per_1m,
        ),
        tier="standard",
    )


def register_into_catalog_overlay() -> dict[str, ModelDef]:
    """Seed herald's runtime catalog with exactly the one allowlisted model.

    Mutates src.herald.catalog_sync.runtime_overlay in place (clear + set,
    not a fresh dict assignment) so any reference callers already imported
    the module-level object stays valid - the same reason catalog_sync's own
    full_sync() mutates in place rather than rebinding the name.

    Registering here (rather than running the real citadel /v1/models poll
    against the broker) is deliberate: the sim boot never calls
    catalog_sync.initialize() or model_validation.validate_resolved_models()
    at all (see pinned_boot.py) precisely because those check EVERY chat
    role, including REASONING, which has no settings override and would
    require a model outside this sim's one-row catalog. This function gives
    herald's cost-estimation and tier-resolution helpers a correct, populated
    catalog anyway, deterministically, with no network round-trip.

    Returns the overlay dict for callers that want to assert on it directly.
    """
    from src.herald import catalog_sync

    catalog_sync.runtime_overlay.clear()
    catalog_sync.runtime_overlay[MODEL_ID] = spec_to_model_def(MODEL_SPEC)
    catalog_sync.ever_synced = True
    return catalog_sync.runtime_overlay


def guarded_create_llm(settings: "Settings", model_id: str = MODEL_ID) -> "LLM":
    """The only path sim code may use to build an LLM adapter.

    Thin wrapper over herald.create_llm that asserts the allowlist first.
    ``model_id`` defaults to MODEL_ID rather than requiring every caller to
    spell it out - every herald consumer in this sim uses the same model, so
    a caller that omits the argument gets the only correct answer by
    construction. A caller that DOES pass something else still goes through
    the guard, so a future edit that resolves a model id from settings/
    defaults instead of the literal can never silently widen the roster.
    Every sim adapter (primary thread, learning centre) is built through
    this function, never through herald.create_llm directly.
    """
    assert_allowed(model_id)
    from src.herald import create_llm

    return create_llm(settings, model_id=model_id)
