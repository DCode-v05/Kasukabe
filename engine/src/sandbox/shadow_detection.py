"""Shadow detection — find permission rules that are unreachable.

Detects when one permission rule makes another rule unreachable:
- Deny shadow (CRITICAL): tool-wide deny blocks a specific allow entirely
- Ask shadow (WARNING): tool-wide ask bypasses a specific allow (always prompts)

Severity ordering: deny-shadows checked first. If a rule is already
deny-shadowed, skip the ask-shadow check to avoid duplicate warnings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.sandbox.permissions import PermissionBehavior, PermissionRule

logger = logging.getLogger(__name__)

# Keyed on the CURRENT vocabulary only. Legacy strings reach it through
# normalize_source in format_source, so the list of old names lives in exactly
# one place (permission_store.LEGACY_SOURCE_MAP) rather than being mirrored here
# where the two could drift apart.
SOURCE_DISPLAY: dict[str, tuple[str, str]] = {
    "hard_deny": ("System safety rule", "(code-defined, never overridable)"),
    "septemberai": ("Baked auto-allow", "(ships with the build)"),
    "engine": ("Engine runtime rule", "(session-only)"),
    "user": ("User config", "($SEPTEMBER_HOME/config/permissions.json)"),
    "managed": ("Managed policy", "(control plane)"),
}

# Shared = authored by someone other than this user, so a shadow warning here
# is not something they can simply fix themselves. Only the managed tier
# qualifies now that the cwd-relative project tier is gone.
SHARED_SOURCES: frozenset[str] = frozenset({"managed"})


def is_shared_source(source: str) -> bool:
    """Check if a permission rule source is shared (visible to other users).

    Shared sources are committed to git or managed by admins — other
    users/agents may not have the same sandbox config. Personal sources
    (operator_global, agent) are per-deployment and the user's own
    sandbox handles them.
    """
    return source in SHARED_SOURCES


def format_source(source: str) -> str:
    """Format a rule source for display in warning messages.

    Legacy source strings are normalized first, so a rule loaded from a
    permissions.json written by an older engine still names its origin instead
    of degrading to a bare "settings".
    """
    from src.sandbox.permission_store import normalize_source

    abstract, file_ref = SOURCE_DISPLAY.get(normalize_source(source), ("settings", ""))
    return f"{abstract} {file_ref}".strip()


@dataclass
class ShadowedRule:
    shadowed_rule: PermissionRule
    shadowed_by: PermissionRule
    shadow_type: str  # "deny" or "ask"
    severity: str  # "critical" or "warning"
    reason: str  # descriptive explanation
    fix_suggestion: str


def build_shadow_message(
    shadowing_rule: PermissionRule,
    shadowed_rule: PermissionRule,
    shadow_type: str,
) -> tuple[str, str]:
    """Build reason and fix suggestion for a shadowed rule."""
    src_display = format_source(shadowing_rule.source)
    tgt_display = format_source(shadowed_rule.source)
    tool = shadowing_rule.tool
    verb = "Blocked" if shadow_type == "deny" else "Shadowed"
    reason = f'{verb} by "{tool}" {shadow_type} rule (from {src_display})'
    fix = (
        f'Remove the "{tool}" {shadow_type} rule from {src_display}, '
        f"or remove the specific allow rule from {tgt_display}"
    )
    return reason, fix


def detect_shadowed_rules(
    rules: list[PermissionRule],
    sandbox_auto_allow_enabled: bool = False,
) -> list[ShadowedRule]:
    """Scan all rules and return unreachable (shadowed) rules.

    A tool-wide rule (empty pattern) shadows any specific rule (has a
    pattern) for the same tool when the tool-wide rule's behavior would
    prevent the specific rule from ever firing.

    Args:
        rules: All permission rules from all sources.
        sandbox_auto_allow_enabled: When True, suppress ask-shadow
            warnings for Bash from operator_global (system auto-allow
            handles those commands).
    """
    results: list[ShadowedRule] = []
    deny_shadowed_keys: set[tuple[str, str]] = set()

    deny_rules = [r for r in rules if r.behavior == PermissionBehavior.DENY]
    allow_rules = [r for r in rules if r.behavior == PermissionBehavior.ALLOW]
    ask_rules = [r for r in rules if r.behavior == PermissionBehavior.ASK]

    # Phase 1: deny shadows (highest severity)
    tool_wide_denies = [r for r in deny_rules if not r.pattern]
    for deny_rule in tool_wide_denies:
        for allow_rule in allow_rules:
            if allow_rule.tool == deny_rule.tool and allow_rule.pattern:
                reason, fix = build_shadow_message(deny_rule, allow_rule, "deny")
                results.append(
                    ShadowedRule(
                        shadowed_rule=allow_rule,
                        shadowed_by=deny_rule,
                        shadow_type="deny",
                        severity="critical",
                        reason=reason,
                        fix_suggestion=fix,
                    )
                )
                deny_shadowed_keys.add((allow_rule.tool, allow_rule.pattern))

    # Phase 2: ask shadows (lower severity, skip if already deny-shadowed)
    tool_wide_asks = [r for r in ask_rules if not r.pattern]
    for ask_rule in tool_wide_asks:
        for allow_rule in allow_rules:
            if allow_rule.tool == ask_rule.tool and allow_rule.pattern:
                key = (allow_rule.tool, allow_rule.pattern)
                if key in deny_shadowed_keys:
                    continue
                # Sandbox exception: personal ask rules for Bash don't
                # shadow when sandbox auto-allow is active — the user's
                # own sandbox handles those. Shared sources (project
                # config) always warn because other agents may not have
                # sandbox enabled.
                if (
                    sandbox_auto_allow_enabled
                    and allow_rule.tool == "Bash"
                    and not is_shared_source(ask_rule.source)
                ):
                    continue
                reason, fix = build_shadow_message(ask_rule, allow_rule, "ask")
                results.append(
                    ShadowedRule(
                        shadowed_rule=allow_rule,
                        shadowed_by=ask_rule,
                        shadow_type="ask",
                        severity="warning",
                        reason=reason,
                        fix_suggestion=fix,
                    )
                )

    return results
