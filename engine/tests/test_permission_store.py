"""Tests for the permission store (src/sandbox/permission_store.py)."""

import json
import tempfile
from pathlib import Path


from src.sandbox.permissions import PermissionBehavior, PermissionRule
from src.sandbox.permission_store import (
    HardDeny,
    PermissionProvenance,
    PermissionStore,
    PERSISTABLE_PROVENANCE,
    normalize_source,
    escape_rule_content,
    unescape_rule_content,
    parse_rule_string,
    format_rule_string,
    rule_to_string,
    rule_from_string,
)


# ---------------------------------------------------------------------------
# Escape / unescape
# ---------------------------------------------------------------------------


class TestEscapeUnescape:
    def test_escape_parens(self):
        assert escape_rule_content("print(1)") == r"print\(1\)"

    def test_escape_backslash(self):
        assert escape_rule_content(r"test\nvalue") == r"test\\nvalue"

    def test_escape_backslash_then_parens(self):
        result = escape_rule_content(r"echo\(hello)")
        assert result == r"echo\\\(hello\)"

    def test_unescape_parens(self):
        assert unescape_rule_content(r"print\(1\)") == "print(1)"

    def test_unescape_backslash(self):
        assert unescape_rule_content(r"test\\nvalue") == r"test\nvalue"

    def test_round_trip(self):
        original = r'python -c "print(1)"'
        assert unescape_rule_content(escape_rule_content(original)) == original

    def test_round_trip_backslashes_and_parens(self):
        original = r"echo\(hello)"
        assert unescape_rule_content(escape_rule_content(original)) == original

    def test_empty_string(self):
        assert escape_rule_content("") == ""
        assert unescape_rule_content("") == ""


# ---------------------------------------------------------------------------
# Rule string parsing
# ---------------------------------------------------------------------------


class TestParseRuleString:
    def test_tool_only(self):
        result = parse_rule_string("Bash")
        assert result.tool == "Bash"
        assert result.content is None

    def test_tool_with_content(self):
        result = parse_rule_string("Bash(npm test)")
        assert result.tool == "Bash"
        assert result.content == "npm test"

    def test_tool_with_escaped_parens(self):
        result = parse_rule_string(r"Bash(python -c print\(1\))")
        assert result.tool == "Bash"
        assert result.content == "python -c print(1)"

    def test_empty_content_is_tool_level(self):
        result = parse_rule_string("Bash()")
        assert result.tool == "Bash"
        assert result.content is None

    def test_wildcard_content_is_tool_level(self):
        result = parse_rule_string("Bash(*)")
        assert result.tool == "Bash"
        assert result.content is None

    def test_no_closing_paren_is_tool_name(self):
        result = parse_rule_string("Bash(npm test")
        assert result.tool == "Bash(npm test"
        assert result.content is None

    def test_no_tool_name_is_literal(self):
        result = parse_rule_string("(content)")
        assert result.tool == "(content)"
        assert result.content is None

    def test_content_after_closing_paren(self):
        result = parse_rule_string("Bash(npm)extra")
        assert result.tool == "Bash(npm)extra"
        assert result.content is None

    def test_mcp_tool(self):
        result = parse_rule_string("ad:slack__post_message")
        assert result.tool == "ad:slack__post_message"
        assert result.content is None

    def test_prefix_wildcard(self):
        result = parse_rule_string("ad:production_*")
        assert result.tool == "ad:production_*"
        assert result.content is None


# ---------------------------------------------------------------------------
# Rule string formatting
# ---------------------------------------------------------------------------


class TestFormatRuleString:
    def test_tool_only(self):
        assert format_rule_string("Bash") == "Bash"

    def test_tool_with_content(self):
        assert format_rule_string("Bash", "npm test") == "Bash(npm test)"

    def test_escapes_parens_in_content(self):
        assert format_rule_string("Bash", "print(1)") == r"Bash(print\(1\))"

    def test_none_content(self):
        assert format_rule_string("Bash", None) == "Bash"

    def test_round_trip_simple(self):
        s = format_rule_string("Bash", "npm publish")
        parsed = parse_rule_string(s)
        assert parsed.tool == "Bash"
        assert parsed.content == "npm publish"

    def test_round_trip_with_parens(self):
        s = format_rule_string("Bash", 'python -c "print(1)"')
        parsed = parse_rule_string(s)
        assert parsed.tool == "Bash"
        assert parsed.content == 'python -c "print(1)"'


# ---------------------------------------------------------------------------
# rule_to_string / rule_from_string
# ---------------------------------------------------------------------------


class TestRuleConversions:
    def test_rule_to_string_tool_only(self):
        rule = PermissionRule(tool="Bash", behavior=PermissionBehavior.ALLOW)
        assert rule_to_string(rule) == "Bash"

    def test_rule_to_string_with_pattern(self):
        rule = PermissionRule(tool="Bash", pattern="npm publish", behavior=PermissionBehavior.DENY)
        assert rule_to_string(rule) == "Bash(npm publish)"

    def test_rule_from_string_tool_only(self):
        rule = rule_from_string("file_read", PermissionBehavior.ALLOW)
        assert rule.tool == "file_read"
        assert rule.pattern == ""
        assert rule.behavior == PermissionBehavior.ALLOW
        assert rule.source == "user"

    def test_rule_from_string_with_content(self):
        rule = rule_from_string(
            "Bash(npm test)", PermissionBehavior.ALLOW, PermissionProvenance.USER
        )
        assert rule.tool == "Bash"
        assert rule.pattern == "npm test"
        assert rule.behavior == PermissionBehavior.ALLOW
        assert rule.source == "user"

    def test_round_trip(self):
        original = PermissionRule(
            tool="Bash",
            pattern="npm publish",
            behavior=PermissionBehavior.DENY,
            source="user",
        )
        s = rule_to_string(original)
        restored = rule_from_string(s, original.behavior, PermissionProvenance.USER)
        assert restored.tool == original.tool
        assert restored.pattern == original.pattern
        assert restored.behavior == original.behavior
        assert restored.source == original.source


# ---------------------------------------------------------------------------
# PermissionProvenance
# ---------------------------------------------------------------------------


class TestPermissionProvenance:
    def test_persistable_sources(self):
        """Only the user's own tier is written to disk."""
        assert PermissionProvenance.USER in PERSISTABLE_PROVENANCE
        assert PermissionProvenance.ENGINE not in PERSISTABLE_PROVENANCE
        assert HardDeny not in PERSISTABLE_PROVENANCE
        assert PermissionProvenance.SEPTEMBERAI not in PERSISTABLE_PROVENANCE
        assert PermissionProvenance.MANAGED not in PERSISTABLE_PROVENANCE

    def test_normalize_legacy_source_hardcoded(self):
        assert normalize_source("hardcoded") == HardDeny

    def test_normalize_legacy_source_config(self):
        assert normalize_source("config") == "user"

    def test_normalize_legacy_source_user_settings(self):
        assert normalize_source("user_settings") == "user"

    def test_normalize_legacy_source_project_settings(self):
        """The project tier is gone; its rules fold into the user tier."""
        assert normalize_source("project_settings") == "user"
        assert normalize_source("operator_project") == "user"

    def test_normalize_legacy_source_session(self):
        assert normalize_source("session") == "engine"

    def test_normalize_new_source_passthrough(self):
        assert normalize_source(HardDeny) == HardDeny
        assert normalize_source("user") == "user"
        assert normalize_source("engine") == "engine"
        assert normalize_source("managed") == "managed"
        assert normalize_source("septemberai") == "septemberai"


# ---------------------------------------------------------------------------
# PermissionStore — load / save / CRUD
# ---------------------------------------------------------------------------


class TestPermissionStore:
    def tmp_dir(self):
        return tempfile.mkdtemp()

    def test_load_empty(self):
        store = PermissionStore(user_path=Path("/nonexistent/permissions.json"))
        store.load()
        assert store.get_rules() == []

    def test_load_from_json(self):
        d = self.tmp_dir()
        p = Path(d) / "permissions.json"
        p.write_text(
            json.dumps(
                {
                    "allow": ["file_read", "Bash(npm test)"],
                    "deny": ["ad:production_*"],
                    "ask": ["Bash(git push)"],
                }
            )
        )

        store = PermissionStore(user_path=p)
        store.load()
        rules = store.get_rules()
        assert len(rules) == 4

        allow_rules = store.get_rules_by_behavior(PermissionBehavior.ALLOW)
        assert len(allow_rules) == 2
        assert allow_rules[0].tool == "file_read"
        assert allow_rules[0].pattern == ""
        assert allow_rules[1].tool == "Bash"
        assert allow_rules[1].pattern == "npm test"

        deny_rules = store.get_rules_by_behavior(PermissionBehavior.DENY)
        assert len(deny_rules) == 1
        assert deny_rules[0].tool == "ad:production_*"

        ask_rules = store.get_rules_by_behavior(PermissionBehavior.ASK)
        assert len(ask_rules) == 1
        assert ask_rules[0].tool == "Bash"
        assert ask_rules[0].pattern == "git push"

    def test_load_tags_every_rule_with_the_user_tier(self):
        d = self.tmp_dir()
        user_p = Path(d) / "user" / "permissions.json"
        user_p.parent.mkdir(parents=True)
        user_p.write_text(json.dumps({"allow": ["file_read"], "deny": ["Bash(npm publish)"]}))

        store = PermissionStore(user_path=user_p)
        store.load()
        rules = store.get_rules()
        assert len(rules) == 2
        assert {r.source for r in rules} == {PermissionProvenance.USER.value}

        by_tool = {r.tool: r for r in rules}
        assert by_tool["file_read"].behavior == PermissionBehavior.ALLOW
        assert by_tool["Bash"].pattern == "npm publish"

    def test_save_round_trip(self):
        d = self.tmp_dir()
        p = Path(d) / "permissions.json"

        store = PermissionStore(user_path=p)
        store.add_rule(
            PermissionRule(
                tool="file_read",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionProvenance.USER.value,
            )
        )
        store.add_rule(
            PermissionRule(
                tool="Bash",
                pattern="npm publish",
                behavior=PermissionBehavior.DENY,
                source=PermissionProvenance.USER.value,
            )
        )
        store.save(PermissionProvenance.USER)

        # Load into fresh store
        store2 = PermissionStore(user_path=p)
        store2.load()
        rules = store2.get_rules()
        assert len(rules) == 2
        assert rules[0].tool == "file_read"
        assert rules[0].behavior == PermissionBehavior.ALLOW
        assert rules[1].tool == "Bash"
        assert rules[1].pattern == "npm publish"
        assert rules[1].behavior == PermissionBehavior.DENY

    def test_save_creates_parent_dirs(self):
        d = self.tmp_dir()
        p = Path(d) / "deep" / "nested" / "permissions.json"

        store = PermissionStore(user_path=p)
        store.add_rule(
            PermissionRule(
                tool="file_read",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionProvenance.USER.value,
            )
        )
        store.save(PermissionProvenance.USER)
        assert p.exists()

    def test_save_session_rules_is_noop(self):
        d = self.tmp_dir()
        p = Path(d) / "permissions.json"

        store = PermissionStore(user_path=p)
        store.add_rule(
            PermissionRule(
                tool="Bash",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionProvenance.ENGINE.value,
            )
        )
        store.save(PermissionProvenance.ENGINE)
        assert not p.exists()

    def test_add_rule_deduplicates(self):
        store = PermissionStore()
        rule = PermissionRule(
            tool="file_read",
            behavior=PermissionBehavior.ALLOW,
            source=PermissionProvenance.USER.value,
        )
        store.add_rule(rule)
        store.add_rule(rule)
        assert len(store.get_rules()) == 1

    def test_remove_rule(self):
        store = PermissionStore()
        store.add_rule(
            PermissionRule(
                tool="Bash",
                pattern="npm publish",
                behavior=PermissionBehavior.DENY,
                source=PermissionProvenance.USER.value,
            )
        )
        store.add_rule(
            PermissionRule(
                tool="file_read",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionProvenance.USER.value,
            )
        )

        removed = store.remove_rule("Bash", PermissionBehavior.DENY, pattern="npm publish")
        assert removed == 1
        assert len(store.get_rules()) == 1
        assert store.get_rules()[0].tool == "file_read"

    def test_remove_rule_by_source(self):
        store = PermissionStore()
        store.add_rule(
            PermissionRule(
                tool="Bash",
                behavior=PermissionBehavior.DENY,
                source=PermissionProvenance.USER.value,
            )
        )
        store.add_rule(
            PermissionRule(
                tool="Bash",
                behavior=PermissionBehavior.DENY,
                source=PermissionProvenance.ENGINE.value,
            )
        )

        removed = store.remove_rule(
            "Bash",
            PermissionBehavior.DENY,
            source=PermissionProvenance.USER,
        )
        assert removed == 1
        remaining = store.get_rules()
        assert len(remaining) == 1
        assert remaining[0].source == "engine"

    def test_remove_nonexistent_returns_zero(self):
        store = PermissionStore()
        assert store.remove_rule("nonexistent", PermissionBehavior.DENY) == 0

    def test_replace_rules(self):
        store = PermissionStore()
        store.add_rule(
            PermissionRule(
                tool="old_allow",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionProvenance.USER.value,
            )
        )
        store.add_rule(
            PermissionRule(
                tool="keep_deny",
                behavior=PermissionBehavior.DENY,
                source=PermissionProvenance.USER.value,
            )
        )

        new_rules = [
            PermissionRule(
                tool="new_allow",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionProvenance.USER.value,
            )
        ]
        store.replace_rules(
            new_rules, PermissionBehavior.ALLOW, PermissionProvenance.USER
        )

        allow_rules = store.get_rules_by_behavior(PermissionBehavior.ALLOW)
        assert len(allow_rules) == 1
        assert allow_rules[0].tool == "new_allow"

        # DENY rule untouched
        deny_rules = store.get_rules_by_behavior(PermissionBehavior.DENY)
        assert len(deny_rules) == 1
        assert deny_rules[0].tool == "keep_deny"

    def test_get_rules_by_source(self):
        store = PermissionStore()
        store.add_rule(
            PermissionRule(
                tool="a",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionProvenance.USER.value,
            )
        )
        store.add_rule(
            PermissionRule(
                tool="b",
                behavior=PermissionBehavior.DENY,
                source=PermissionProvenance.MANAGED.value,
            )
        )
        store.add_rule(
            PermissionRule(
                tool="c",
                behavior=PermissionBehavior.ASK,
                source=PermissionProvenance.ENGINE.value,
            )
        )

        assert len(store.get_rules_by_source(PermissionProvenance.USER)) == 1
        assert len(store.get_rules_by_source(PermissionProvenance.MANAGED)) == 1
        assert len(store.get_rules_by_source(PermissionProvenance.ENGINE)) == 1
        assert len(store.get_rules_by_source(PermissionProvenance.SEPTEMBERAI)) == 0

    def test_load_invalid_json_returns_empty(self):
        d = self.tmp_dir()
        p = Path(d) / "permissions.json"
        p.write_text("not valid json {{{")

        store = PermissionStore(user_path=p)
        store.load()
        assert store.get_rules() == []

    def test_load_non_dict_returns_empty(self):
        d = self.tmp_dir()
        p = Path(d) / "permissions.json"
        p.write_text('["array", "not", "dict"]')

        store = PermissionStore(user_path=p)
        store.load()
        assert store.get_rules() == []

    def test_load_skips_non_string_entries(self):
        d = self.tmp_dir()
        p = Path(d) / "permissions.json"
        p.write_text(json.dumps({"allow": ["Bash", 42, None, "file_read"]}))

        store = PermissionStore(user_path=p)
        store.load()
        rules = store.get_rules()
        assert len(rules) == 2

    def test_save_omits_empty_keys(self):
        d = self.tmp_dir()
        p = Path(d) / "permissions.json"

        store = PermissionStore(user_path=p)
        store.add_rule(
            PermissionRule(
                tool="Bash",
                behavior=PermissionBehavior.ALLOW,
                source=PermissionProvenance.USER.value,
            )
        )
        store.save(PermissionProvenance.USER)

        data = json.loads(p.read_text())
        assert "allow" in data
        assert "deny" not in data
        assert "ask" not in data
