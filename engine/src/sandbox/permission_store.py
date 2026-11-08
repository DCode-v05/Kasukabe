"""Permission rule persistence — parsing, CRUD, and JSON file storage.

Manages permission rules across multiple sources with disk persistence.
Rules use the Tool(content) string format for human-readable config files.

JSON file format (permissions.json):
  {
    "allow": ["file_read", "Bash(npm test)"],
    "deny": ["Bash(npm publish)", "ad:production_*"],
    "ask": ["Bash(git push)"]
  }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.sandbox.shadow_detection import ShadowedRule

from src.sandbox.permissions import HardDeny, PermissionBehavior, PermissionRule

logger = logging.getLogger(__name__)


class PermissionProvenance(Enum):
    """Who granted a permission rule, in the engine's one authority vocabulary.

    The same four words the asset system uses (asset_directory/states.py), so
    "who may override whom" has one answer across the engine rather than a
    separate ladder per subsystem.
    """

    SEPTEMBERAI = "septemberai"
    ENGINE = "engine"
    USER = "user"
    MANAGED = "managed"


PERSISTABLE_PROVENANCE = frozenset({PermissionProvenance.USER})

EDITABLE_PROVENANCE = frozenset({PermissionProvenance.ENGINE, PermissionProvenance.USER})


LEGACY_SOURCE_MAP: dict[str, str] = {
    "hardcoded": HardDeny,
    "system_deny": HardDeny,
    "system_allow": PermissionProvenance.SEPTEMBERAI.value,
    "config": PermissionProvenance.USER.value,
    "user_settings": PermissionProvenance.USER.value,
    "operator_global": PermissionProvenance.USER.value,
    "operator_project": PermissionProvenance.USER.value,
    "project_settings": PermissionProvenance.USER.value,
    "session": PermissionProvenance.ENGINE.value,
    "agent": PermissionProvenance.ENGINE.value,
}


def normalize_source(source_str: str) -> str:
    """Convert a legacy source string to the current vocabulary.

    Returns the input unchanged if it is already a current value.
    """
    if source_str in {p.value for p in PermissionProvenance} or source_str == HardDeny:
        return source_str
    return LEGACY_SOURCE_MAP.get(source_str, source_str)


@dataclass
class PermissionRuleValue:
    tool: str
    content: str | None = None


def escape_rule_content(content: str) -> str:
    """Escape special characters for safe storage in Tool(content) format.

    Order matters: backslashes first, then parens.
    """
    return content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def unescape_rule_content(content: str) -> str:
    """Reverse escaping from escape_rule_content.

    Order matters: parens first, then backslashes.
    """
    return content.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")


def find_first_unescaped(s: str, char: str) -> int:
    """Find index of first unescaped occurrence of char."""
    for i, c in enumerate(s):
        if c == char:
            backslash_count = 0
            j = i - 1
            while j >= 0 and s[j] == "\\":
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                return i
    return -1


def find_last_unescaped(s: str, char: str) -> int:
    """Find index of last unescaped occurrence of char."""
    for i in range(len(s) - 1, -1, -1):
        if s[i] == char:
            backslash_count = 0
            j = i - 1
            while j >= 0 and s[j] == "\\":
                backslash_count += 1
                j -= 1
            if backslash_count % 2 == 0:
                return i
    return -1


def parse_rule_string(rule_string: str) -> PermissionRuleValue:
    """Parse 'Tool' or 'Tool(content)' into PermissionRuleValue.

    Handles escaped parens in content: Bash(python -c "print\\(1\\)")
    """
    open_idx = find_first_unescaped(rule_string, "(")
    if open_idx == -1:
        return PermissionRuleValue(tool=rule_string)

    close_idx = find_last_unescaped(rule_string, ")")
    if close_idx == -1 or close_idx <= open_idx or close_idx != len(rule_string) - 1:
        return PermissionRuleValue(tool=rule_string)

    tool = rule_string[:open_idx]
    if not tool:
        return PermissionRuleValue(tool=rule_string)

    raw_content = rule_string[open_idx + 1 : close_idx]
    if raw_content == "" or raw_content == "*":
        return PermissionRuleValue(tool=tool)

    return PermissionRuleValue(tool=tool, content=unescape_rule_content(raw_content))


def format_rule_string(tool: str, content: str | None = None) -> str:
    """Convert tool + content back to 'Tool(content)' string format."""
    if content is None:
        return tool
    return f"{tool}({escape_rule_content(content)})"


def rule_to_string(rule: PermissionRule) -> str:
    """Convert a PermissionRule to its string representation."""
    if rule.pattern:
        return format_rule_string(rule.tool, rule.pattern)
    return rule.tool


def rule_from_string(
    rule_string: str,
    behavior: PermissionBehavior,
    source: PermissionProvenance = PermissionProvenance.USER,
) -> PermissionRule:
    """Create a PermissionRule from a rule string and behavior."""
    parsed = parse_rule_string(rule_string)
    return PermissionRule(
        tool=parsed.tool,
        pattern=parsed.content or "",
        behavior=behavior,
        source=source.value,
    )


def load_json(path: Path) -> dict:
    """Load JSON file, returning empty dict if missing or invalid."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("Permission file %s is not a JSON object, ignoring", path)
            return {}
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load permission file %s: %s", path, e)
        return {}


def save_json(path: Path, data: dict) -> None:
    """Write JSON to file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class PermissionStore:
    """Manages permission rules across multiple sources with disk persistence.

    One persistent tier: the user's own file under the engine root. There is
    deliberately no cwd-relative tier — a file the sandboxed agent could create
    must never be an authority source.
    """

    def __init__(self, user_path: Path | None = None) -> None:
        self.user_path = user_path
        self.rules: list[PermissionRule] = []

    def load(self) -> None:
        """Load rules from the persistent tier and run shadow detection."""
        self.rules = []
        if self.user_path:
            self.load_from_file(self.user_path, PermissionProvenance.USER)
        self.log_shadow_warnings()

    def log_shadow_warnings(self) -> None:
        """Run shadow detection and log warnings for unreachable rules."""
        for s in self.detect_shadows():
            logger.warning(
                "%s: %s. Fix: %s",
                s.severity.upper(),
                s.reason,
                s.fix_suggestion,
            )

    def detect_shadows(self) -> list[ShadowedRule]:
        """Run shadow detection on current rules."""
        from src.sandbox.shadow_detection import detect_shadowed_rules
        from src.sandbox.sandbox_auto_allow import sandbox_auto_allow_enabled

        return detect_shadowed_rules(self.rules, sandbox_auto_allow_enabled())

    def load_from_file(self, path: Path, source: PermissionProvenance) -> None:
        """Load rules from a single JSON file."""
        data = load_json(path)
        for behavior in PermissionBehavior:
            for rule_string in data.get(behavior.value, []):
                if isinstance(rule_string, str):
                    self.rules.append(rule_from_string(rule_string, behavior, source))

    def save(self, source: PermissionProvenance) -> None:
        """Write rules for a given provenance back to its JSON file.

        A non-persistable tier is a no-op rather than an error: callers sweep
        every tier on shutdown, and only the user's own belongs on disk.
        """
        if source not in PERSISTABLE_PROVENANCE:
            return
        path = self.path_for_source(source)
        if path is None:
            return
        data: dict[str, list[str]] = {b.value: [] for b in PermissionBehavior}
        for rule in self.rules:
            if rule.source == source.value:
                data[rule.behavior.value].append(rule_to_string(rule))
        data = {k: v for k, v in data.items() if v}
        save_json(path, data)
        logger.info("Saved %d permission rules to %s", sum(len(v) for v in data.values()), path)

    def path_for_source(self, source: PermissionProvenance) -> Path | None:
        if source == PermissionProvenance.USER:
            return self.user_path
        return None


    def add_rule(self, rule: PermissionRule) -> None:
        """Add a rule. Deduplicates against existing rules with same tool+pattern+behavior+source."""
        for existing in self.rules:
            if (
                existing.tool == rule.tool
                and existing.pattern == rule.pattern
                and existing.behavior == rule.behavior
                and existing.source == rule.source
            ):
                return
        self.rules.append(rule)

    def remove_rule(
        self,
        tool: str,
        behavior: PermissionBehavior,
        pattern: str = "",
        source: PermissionProvenance | None = None,
    ) -> int:
        """Remove matching rules. Returns count of removed rules."""
        before = len(self.rules)
        self.rules = [
            r
            for r in self.rules
            if not (
                r.tool == tool
                and r.behavior == behavior
                and r.pattern == pattern
                and (source is None or r.source == source.value)
            )
        ]
        return before - len(self.rules)

    def replace_rules(
        self,
        rules: list[PermissionRule],
        behavior: PermissionBehavior,
        source: PermissionProvenance,
    ) -> None:
        """Replace all rules with given behavior+source with new set."""
        self.rules = [
            r for r in self.rules if not (r.behavior == behavior and r.source == source.value)
        ]
        self.rules.extend(rules)


    def get_rules(self) -> list[PermissionRule]:
        """Return all rules (all sources). Returns internal list — do not mutate."""
        return self.rules

    def get_rules_by_source(self, source: PermissionProvenance) -> list[PermissionRule]:
        return [r for r in self.rules if r.source == source.value]

    def get_rules_by_behavior(self, behavior: PermissionBehavior) -> list[PermissionRule]:
        return [r for r in self.rules if r.behavior == behavior]
