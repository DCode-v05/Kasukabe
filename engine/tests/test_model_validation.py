"""Post-sync model validation: fatal when synced, warn when degraded.

validate_resolved_models checks every RESOLVED model id (env override else
herald default) against the citadel-synced catalog. These tests drive the
overlay directly (no network): a canned synced catalog, a degraded empty
one, tier mismatches, and the zero-embedding-rows downgrade.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.herald import catalog_sync
from src.herald.catalog import ModelDef
from src.herald.defaults import MODEL_DEFAULTS
from src.herald.model_validation import validate_resolved_models


def modeldef(model_id: str, tier: str = "standard") -> ModelDef:
    return ModelDef(
        id=model_id,
        provider="anthropic",
        name=model_id,
        context_window=200_000,
        max_output_tokens=8192,
        supports_tools=True,
        supports_vision=False,
        cost=None,
        tier=tier,
    )


FULL_CATALOG_IDS = [
    (MODEL_DEFAULTS.primary, "standard"),
    (MODEL_DEFAULTS.planner, "heavy"),
    (MODEL_DEFAULTS.light, "light"),
    (MODEL_DEFAULTS.reasoning, "heavy"),
    (MODEL_DEFAULTS.dispatch, "light"),
    (MODEL_DEFAULTS.dispatch_alternative, "light"),
    (MODEL_DEFAULTS.lc, "light"),
    (MODEL_DEFAULTS.embedding, "embedding"),
]


@pytest.fixture
def overlay(monkeypatch):
    """Install a canned synced overlay; restore afterwards."""

    def install(entries: list[tuple[str, str]]):
        canned = {mid: modeldef(mid, tier) for mid, tier in entries}
        monkeypatch.setattr(catalog_sync, "runtime_overlay", canned)
        return canned

    return install


class TestSynced:
    def test_all_defaults_pass_against_full_catalog(self, overlay):
        overlay(FULL_CATALOG_IDS)
        fatal, warnings = validate_resolved_models(Settings(), synced=True)
        assert fatal == []
        assert warnings == []

    def test_unknown_override_is_fatal(self, overlay):
        overlay(FULL_CATALOG_IDS)
        s = Settings(llm_model="model-that-does-not-exist")
        fatal, warnings = validate_resolved_models(s, synced=True)
        assert len(fatal) == 1
        assert "model-that-does-not-exist" in fatal[0]
        assert "env override LLM_MODEL" in fatal[0]

    def test_unknown_default_names_the_defaults_file(self, overlay):
        entries = [e for e in FULL_CATALOG_IDS if e[0] != MODEL_DEFAULTS.lc]
        overlay(entries)
        fatal, warnings = validate_resolved_models(Settings(), synced=True)
        assert len(fatal) == 1
        assert MODEL_DEFAULTS.lc in fatal[0]
        assert "herald default" in fatal[0]

    def test_chat_role_with_embedding_tier_is_fatal(self, overlay):
        entries = [e for e in FULL_CATALOG_IDS if e[0] != MODEL_DEFAULTS.lc]
        entries.append((MODEL_DEFAULTS.lc, "embedding"))
        overlay(entries)
        fatal, warnings = validate_resolved_models(Settings(), synced=True)
        assert any("non-chat tier" in f for f in fatal)

    def test_embedding_wrong_tier_is_fatal(self, overlay):
        entries = [e for e in FULL_CATALOG_IDS if e[0] != MODEL_DEFAULTS.embedding]
        entries.append((MODEL_DEFAULTS.embedding, "standard"))
        overlay(entries)
        fatal, warnings = validate_resolved_models(Settings(), synced=True)
        assert any("expected 'embedding'" in f for f in fatal)

    def test_zero_embedding_rows_downgrades_to_warning(self, overlay):
        entries = [e for e in FULL_CATALOG_IDS if e[1] != "embedding"]
        overlay(entries)
        fatal, warnings = validate_resolved_models(Settings(), synced=True)
        assert fatal == []
        assert any("no embedding-tier rows" in w for w in warnings)

    def test_fallback_checked_only_when_set(self, overlay):
        overlay(FULL_CATALOG_IDS)
        fatal, _ = validate_resolved_models(Settings(), synced=True)
        assert fatal == []
        s = Settings(fallback_model="ghost-model")
        fatal, _ = validate_resolved_models(s, synced=True)
        assert any("ghost-model" in f for f in fatal)

    def test_overlay_only_model_accepted(self, overlay):
        entries = FULL_CATALOG_IDS + [("brand-new-overlay-model", "standard")]
        overlay(entries)
        s = Settings(llm_model="brand-new-overlay-model")
        fatal, warnings = validate_resolved_models(s, synced=True)
        assert fatal == []


class TestDegraded:
    def test_empty_catalog_single_consolidated_warning(self, overlay):
        overlay([])
        fatal, warnings = validate_resolved_models(Settings(), synced=False)
        assert fatal == []
        assert len(warnings) == 1
        assert "accelerated retry" in warnings[0]

    def test_degraded_findings_never_fatal(self, overlay):
        overlay([(MODEL_DEFAULTS.primary, "standard")])
        s = Settings(llm_model="missing-model")
        fatal, warnings = validate_resolved_models(s, synced=False)
        assert fatal == []
        assert any("missing-model" in w for w in warnings)
