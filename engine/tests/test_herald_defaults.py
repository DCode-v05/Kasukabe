"""Herald model defaults: the single launch-time model block.

Covers resolve_model precedence (settings override beats default, empty
falls through), resolve_embedding_dim, immutability of MODEL_DEFAULTS, and
the meta-invariant that every ROLE_SETTINGS_FIELD points at a real Settings
field so an override can actually be set from the environment.
"""

from __future__ import annotations

import dataclasses

import pytest

from src.config import Settings
from src.herald.defaults import (
    MODEL_DEFAULTS,
    ROLE_DEFAULT,
    ROLE_SETTINGS_FIELD,
    ModelRole,
    resolve_embedding_dim,
    resolve_model,
)


class TestResolveModel:
    def test_default_without_settings(self):
        for role in ModelRole:
            assert resolve_model(role) == ROLE_DEFAULT[role]

    def test_default_with_none_settings(self):
        assert resolve_model(ModelRole.PRIMARY, None) == MODEL_DEFAULTS.primary

    def test_override_beats_default_per_role(self):
        for role, field_name in ROLE_SETTINGS_FIELD.items():
            s = Settings(**{field_name: "custom-model-x"})
            assert resolve_model(role, s) == "custom-model-x", role

    def test_empty_override_falls_through(self):
        for role, field_name in ROLE_SETTINGS_FIELD.items():
            s = Settings(**{field_name: ""})
            assert resolve_model(role, s) == ROLE_DEFAULT[role], role

    def test_reasoning_has_no_settings_override(self):
        assert ModelRole.REASONING not in ROLE_SETTINGS_FIELD
        s = Settings(llm_model="something-else")
        assert resolve_model(ModelRole.REASONING, s) == MODEL_DEFAULTS.reasoning

    def test_fallback_default_is_empty(self):
        # No mid-turn overload fallback unless a deploy opts in.
        assert resolve_model(ModelRole.FALLBACK) == ""


class TestResolveEmbeddingDim:
    def test_default(self):
        assert resolve_embedding_dim() == MODEL_DEFAULTS.embedding_dim

    def test_zero_falls_through(self):
        s = Settings(embedding_dim=0)
        assert resolve_embedding_dim(s) == MODEL_DEFAULTS.embedding_dim

    def test_positive_override_wins(self):
        s = Settings(embedding_dim=768)
        assert resolve_embedding_dim(s) == 768


class TestDefaultsBlock:
    def test_model_defaults_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            MODEL_DEFAULTS.primary = "mutated"

    def test_every_role_has_a_default_entry(self):
        for role in ModelRole:
            assert role in ROLE_DEFAULT

    def test_role_settings_fields_are_real_settings_fields(self):
        settings_fields = {f.name for f in dataclasses.fields(Settings)}
        for role, field_name in ROLE_SETTINGS_FIELD.items():
            assert field_name in settings_fields, (
                f"{role}: '{field_name}' is not a Settings field"
            )

    def test_chain_defaults_shape(self):
        from src.herald.defaults import TASK_CHAIN_DEFAULTS, TIER_CHAIN_DEFAULTS

        assert set(TIER_CHAIN_DEFAULTS) == {"auto", "eco", "premium", "agentic"}
        for profile, bands in TIER_CHAIN_DEFAULTS.items():
            assert set(bands) == {"SIMPLE", "MEDIUM", "COMPLEX", "REASONING"}, profile
            for band, chain in bands.items():
                assert chain and all(isinstance(m, str) and m for m in chain), (profile, band)
        for task, chain in TASK_CHAIN_DEFAULTS.items():
            assert chain and all(isinstance(m, str) and m for m in chain), task
