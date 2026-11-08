"""Permission provenance — one authority vocabulary across the engine.

Permission rules now name their origin with the same four words the asset
system already uses (``src/asset_directory/states.py``): septemberai, engine,
user, managed. Two vocabularies for one idea was the thing worth removing.

``HARD_DENY`` is deliberately NOT one of them. Catastrophic denials are an
invariant that outranks every tier, not a tier that happens to sort first —
folding it into a ranked ladder at position zero would let a user rule outrank
``rm -rf /``, and would read as a clean rename in review.

Editability is a separate axis from authority, following Claude Code's
``EDITABLE_SOURCES`` (utils/permissions/permissionsLoader.ts): the tiers a
runtime may write are not the same set as the tiers that carry weight.
"""

from __future__ import annotations

import json

import pytest

from src.sandbox.permission_store import (
    EDITABLE_PROVENANCE,
    PERSISTABLE_PROVENANCE,
    HardDeny,
    PermissionProvenance,
    PermissionStore,
    normalize_source,
)
from src.sandbox.permissions import PermissionBehavior, PermissionRule


class TestVocabulary:
    def test_the_four_tiers_are_exactly_these(self):
        assert {p.value for p in PermissionProvenance} == {
            "septemberai",
            "engine",
            "user",
            "managed",
        }

    def test_hard_deny_is_not_a_provenance(self):
        """It is an invariant above the ladder, not a rung on it."""
        assert "hard_deny" not in {p.value for p in PermissionProvenance}
        assert not isinstance(HardDeny, PermissionProvenance)

    def test_operator_project_tier_is_gone(self):
        """cwd-relative authority was removed; it must not come back."""
        values = {p.value for p in PermissionProvenance}
        assert "operator_project" not in values
        assert "project_settings" not in values


class TestPersistenceAndEditability:
    def test_only_the_user_tier_is_written_to_disk(self):
        assert PERSISTABLE_PROVENANCE == frozenset({PermissionProvenance.USER})

    def test_managed_is_not_persistable(self):
        """Managed rules arrive from the control plane, never from a local file."""
        assert PermissionProvenance.MANAGED not in PERSISTABLE_PROVENANCE

    def test_editable_tiers_are_engine_and_user(self):
        assert EDITABLE_PROVENANCE == frozenset(
            {PermissionProvenance.ENGINE, PermissionProvenance.USER}
        )

    def test_managed_and_septemberai_are_not_editable(self):
        """A runtime must not be able to rewrite authority it does not own."""
        assert PermissionProvenance.MANAGED not in EDITABLE_PROVENANCE
        assert PermissionProvenance.SEPTEMBERAI not in EDITABLE_PROVENANCE


class TestLegacyStrings:
    @pytest.mark.parametrize(
        "old,new",
        [
            ("operator_global", "user"),
            ("user_settings", "user"),
            ("config", "user"),
            ("session", "engine"),
            ("agent", "engine"),
            ("system_allow", "septemberai"),
            ("operator_project", "user"),
            ("project_settings", "user"),
        ],
    )
    def test_old_source_strings_map_forward(self, old, new):
        """A permissions.json written by an older engine must still load."""
        assert normalize_source(old) == new

    def test_hardcoded_maps_to_hard_deny(self):
        assert normalize_source("hardcoded") == HardDeny

    def test_current_values_pass_through_unchanged(self):
        for p in PermissionProvenance:
            assert normalize_source(p.value) == p.value


class TestStore:
    def test_loads_the_user_file_as_the_user_tier(self, tmp_path):
        f = tmp_path / "permissions.json"
        f.write_text(json.dumps({"allow": ["Bash(ls)"]}), encoding="utf-8")

        store = PermissionStore(user_path=f)
        store.load()
        assert [r.source for r in store.get_rules()] == [PermissionProvenance.USER.value]

    def test_saves_the_user_tier_back_to_its_file(self, tmp_path):
        f = tmp_path / "permissions.json"
        store = PermissionStore(user_path=f)
        store.add_rule(
            PermissionRule(
                tool="Bash",
                pattern="ls",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionProvenance.USER.value,
            )
        )
        store.save(PermissionProvenance.USER)
        assert "Bash(ls)" in f.read_text(encoding="utf-8")

    def test_saving_a_non_persistable_tier_writes_nothing(self, tmp_path):
        """A managed rule must never end up in the user's own file."""
        f = tmp_path / "permissions.json"
        store = PermissionStore(user_path=f)
        store.add_rule(
            PermissionRule(
                tool="Bash",
                pattern="rm",
                behavior=PermissionBehavior.DENY,
                source=PermissionProvenance.MANAGED.value,
            )
        )
        store.save(PermissionProvenance.MANAGED)
        assert not f.exists()

    def test_legacy_file_loads_and_is_retagged(self, tmp_path):
        """An old file tagged operator_global becomes a user rule."""
        f = tmp_path / "permissions.json"
        f.write_text(json.dumps({"deny": ["Bash(curl)"]}), encoding="utf-8")
        store = PermissionStore(user_path=f)
        store.load()
        assert store.get_rules()[0].source == PermissionProvenance.USER.value

    def test_store_takes_no_project_path(self, tmp_path):
        """The constructor must not accept a tier that no longer exists."""
        with pytest.raises(TypeError):
            PermissionStore(user_path=tmp_path / "p.json", project_path=tmp_path / "q.json")


class TestShadowDetectionVocabulary:
    def test_display_map_covers_every_provenance(self):
        from src.sandbox.shadow_detection import SOURCE_DISPLAY

        for p in PermissionProvenance:
            assert p.value in SOURCE_DISPLAY, f"{p.value} has no display entry"

    def test_managed_is_the_shared_tier(self):
        """Shared means visible to people other than this user."""
        from src.sandbox.shadow_detection import is_shared_source

        assert is_shared_source(PermissionProvenance.MANAGED.value)
        assert not is_shared_source(PermissionProvenance.USER.value)
        assert not is_shared_source(PermissionProvenance.ENGINE.value)
