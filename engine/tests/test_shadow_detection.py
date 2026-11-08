"""Tests for shadow detection (src/sandbox/shadow_detection.py)."""

from src.sandbox.permissions import PermissionBehavior, PermissionRule
from src.sandbox.shadow_detection import (
    detect_shadowed_rules,
    is_shared_source,
)


def allow(tool: str, pattern: str = "", source: str = "operator_global") -> PermissionRule:
    return PermissionRule(
        tool=tool, pattern=pattern, behavior=PermissionBehavior.ALLOW, source=source
    )


def deny(tool: str, pattern: str = "", source: str = "operator_global") -> PermissionRule:
    return PermissionRule(
        tool=tool, pattern=pattern, behavior=PermissionBehavior.DENY, source=source
    )


def ask(tool: str, pattern: str = "", source: str = "operator_global") -> PermissionRule:
    return PermissionRule(
        tool=tool, pattern=pattern, behavior=PermissionBehavior.ASK, source=source
    )


class TestDenyShadow:
    def test_tool_wide_deny_shadows_specific_allow(self):
        rules = [deny("Bash"), allow("Bash", "npm install")]
        results = detect_shadowed_rules(rules)
        assert len(results) == 1
        assert results[0].shadow_type == "deny"
        assert results[0].severity == "critical"
        assert results[0].shadowed_rule.pattern == "npm install"
        assert results[0].shadowed_by.tool == "Bash"

    def test_tool_wide_deny_shadows_multiple_allows(self):
        rules = [
            deny("Bash"),
            allow("Bash", "npm install"),
            allow("Bash", "npm test"),
        ]
        results = detect_shadowed_rules(rules)
        assert len(results) == 2
        assert all(r.shadow_type == "deny" for r in results)

    def test_specific_deny_does_not_shadow(self):
        rules = [deny("Bash", "npm publish"), allow("Bash", "npm install")]
        results = detect_shadowed_rules(rules)
        assert len(results) == 0

    def test_deny_different_tool_no_shadow(self):
        rules = [deny("Edit"), allow("Bash", "npm install")]
        results = detect_shadowed_rules(rules)
        assert len(results) == 0


class TestAskShadow:
    def test_tool_wide_ask_shadows_specific_allow(self):
        rules = [ask("Bash"), allow("Bash", "ls")]
        results = detect_shadowed_rules(rules)
        assert len(results) == 1
        assert results[0].shadow_type == "ask"
        assert results[0].severity == "warning"
        assert results[0].shadowed_rule.pattern == "ls"

    def test_specific_ask_does_not_shadow(self):
        rules = [ask("Bash", "git push"), allow("Bash", "ls")]
        results = detect_shadowed_rules(rules)
        assert len(results) == 0

    def test_ask_different_tool_no_shadow(self):
        rules = [ask("Edit"), allow("Bash", "ls")]
        results = detect_shadowed_rules(rules)
        assert len(results) == 0


class TestSeverityOrdering:
    def test_deny_shadow_prevents_duplicate_ask_shadow(self):
        rules = [
            deny("Bash"),
            ask("Bash"),
            allow("Bash", "npm install"),
        ]
        results = detect_shadowed_rules(rules)
        assert len(results) == 1
        assert results[0].shadow_type == "deny"
        assert results[0].severity == "critical"

    def test_ask_shadow_when_no_deny(self):
        rules = [ask("Bash"), allow("Bash", "npm install")]
        results = detect_shadowed_rules(rules)
        assert len(results) == 1
        assert results[0].shadow_type == "ask"


class TestSandboxException:
    def test_ask_shadow_skipped_for_bash_from_operator_global(self):
        rules = [
            ask("Bash", source="operator_global"),
            allow("Bash", "ls"),
        ]
        results = detect_shadowed_rules(rules, sandbox_auto_allow_enabled=True)
        assert len(results) == 0

    def test_ask_shadow_skipped_for_bash_from_legacy_user_settings(self):
        rules = [
            ask("Bash", source="user_settings"),
            allow("Bash", "ls"),
        ]
        results = detect_shadowed_rules(rules, sandbox_auto_allow_enabled=True)
        assert len(results) == 0

    def test_ask_shadow_not_skipped_for_managed(self):
        rules = [
            ask("Bash", source="managed"),
            allow("Bash", "ls"),
        ]
        results = detect_shadowed_rules(rules, sandbox_auto_allow_enabled=True)
        assert len(results) == 1
        assert results[0].shadow_type == "ask"

    def test_ask_shadow_not_skipped_for_non_bash(self):
        rules = [
            ask("Edit", source="operator_global"),
            allow("Edit", "*.py"),
        ]
        results = detect_shadowed_rules(rules, sandbox_auto_allow_enabled=True)
        assert len(results) == 1

    def test_deny_shadow_never_skipped_by_sandbox(self):
        rules = [
            deny("Bash", source="operator_global"),
            allow("Bash", "ls"),
        ]
        results = detect_shadowed_rules(rules, sandbox_auto_allow_enabled=True)
        assert len(results) == 1
        assert results[0].shadow_type == "deny"

    def test_sandbox_disabled_no_exception(self):
        rules = [
            ask("Bash", source="operator_global"),
            allow("Bash", "ls"),
        ]
        results = detect_shadowed_rules(rules, sandbox_auto_allow_enabled=False)
        assert len(results) == 1


class TestEdgeCases:
    def test_empty_rules(self):
        assert detect_shadowed_rules([]) == []

    def test_only_tool_wide_rules_no_shadow(self):
        rules = [deny("Bash"), allow("Edit"), ask("Grep")]
        results = detect_shadowed_rules(rules)
        assert len(results) == 0

    def test_only_specific_rules_no_shadow(self):
        rules = [
            deny("Bash", "npm publish"),
            allow("Bash", "npm install"),
        ]
        results = detect_shadowed_rules(rules)
        assert len(results) == 0

    def test_same_source_shadow(self):
        rules = [
            deny("Bash", source="operator_global"),
            allow("Bash", "npm install", source="operator_global"),
        ]
        results = detect_shadowed_rules(rules)
        assert len(results) == 1

    def test_cross_source_shadow(self):
        rules = [
            deny("Bash", source="operator_project"),
            allow("Bash", "npm install", source="operator_global"),
        ]
        results = detect_shadowed_rules(rules)
        assert len(results) == 1
        assert results[0].shadowed_by.source == "operator_project"
        assert results[0].shadowed_rule.source == "operator_global"


class TestReasonField:
    def test_deny_reason_describes_blocking(self):
        rules = [
            deny("Bash", source="operator_global"),
            allow("Bash", "npm install"),
        ]
        results = detect_shadowed_rules(rules)
        assert 'Blocked by "Bash" deny rule' in results[0].reason
        assert "User config" in results[0].reason

    def test_ask_reason_describes_shadowing(self):
        rules = [
            ask("Bash", source="managed"),
            allow("Bash", "ls"),
        ]
        results = detect_shadowed_rules(rules)
        assert 'Shadowed by "Bash" ask rule' in results[0].reason
        assert "Managed policy" in results[0].reason


class TestFixSuggestions:
    def test_deny_fix_references_both_sources(self):
        rules = [
            deny("Bash", source="managed"),
            allow("Bash", "npm install", source="user"),
        ]
        results = detect_shadowed_rules(rules)
        fix = results[0].fix_suggestion
        assert "Managed policy" in fix
        assert "User config" in fix

    def test_ask_fix_references_both_sources(self):
        rules = [
            ask("Bash", source="managed"),
            allow("Bash", "ls", source="engine"),
        ]
        results = detect_shadowed_rules(rules)
        fix = results[0].fix_suggestion
        assert "Managed policy" in fix
        assert "Engine runtime rule" in fix

    def test_engine_source_fix(self):
        rules = [
            deny("Bash", source="engine"),
            allow("Bash", "ls"),
        ]
        results = detect_shadowed_rules(rules)
        assert "Engine runtime rule" in results[0].fix_suggestion

    def test_legacy_source_fix(self):
        rules = [
            deny("Bash", source="hardcoded"),
            allow("Bash", "ls"),
        ]
        results = detect_shadowed_rules(rules)
        assert "System safety rule" in results[0].fix_suggestion


class TestIsSharedSource:
    def test_managed_is_shared(self):
        """Managed policy is authored elsewhere, so the user cannot just edit it."""
        assert is_shared_source("managed") is True

    def test_folded_project_tiers_are_no_longer_shared(self):
        """They now resolve to the user's own tier, which is personal."""
        assert is_shared_source("operator_project") is False
        assert is_shared_source("project_settings") is False

    def test_operator_global_is_personal(self):
        assert is_shared_source("operator_global") is False

    def test_agent_is_personal(self):
        assert is_shared_source("agent") is False

    def test_system_deny_is_personal(self):
        assert is_shared_source("system_deny") is False

    def test_user_settings_legacy_is_personal(self):
        assert is_shared_source("user_settings") is False
