"""Permission pipeline — rule-based gate before every tool call.

Non-LLM overhead: pure pattern matching on tool source + action.
Auto-allows safe operations; asks user only for write/destructive.

Architecture:
  1. Hardcoded deny rules (catastrophic patterns — never overridden)
  2. Config deny rules (per-deployment customization)
  3. Action classification (tool name + input heuristics)
  4. Permission matrix lookup (source x action -> requires_permission)
  5. User confirmation if required (fail-closed when unavailable)
"""

from __future__ import annotations

import os
import re
import logging
import tempfile
from collections import deque

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from src.sandbox.bash_paths import (
    extract_written_paths,
    find_sed_subcommands,
    tokenize_command,
)
from src.sandbox.command_classifier import classify_command, split_compound_commands
from src.sandbox.dangerous_removal import check_dangerous_removal
from src.sandbox.path_safety import (
    DANGEROUS_DIRS as PATH_SAFETY_DANGEROUS_DIRS,
    DANGEROUS_FILES as PATH_SAFETY_DANGEROUS_FILES,
    PathSafetyResult,
    check_path_safety,
)
from src.sandbox.readonly_allowlist import is_command_read_only
from src.sandbox.sed_edit_parser import SedEditInfo, parse_sed_edit
from src.sandbox.sed_validation import SedBehavior, check_sed_constraints

logger = logging.getLogger(__name__)


class ToolSource(Enum):
    INTERNAL = "internal"
    SANDBOXED = "sandboxed"
    PLATFORM = "platform"
    MCP = "mcp"
    NETWORK = "network"


class ToolAction(Enum):
    SEARCH = "search"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DESTRUCTIVE = "destructive"


class PermissionMode(Enum):
    DEFAULT = "default"
    READ_ONLY = "read_only"
    DONT_ASK = "dont_ask"
    ACCEPT_EDITS = "accept_edits"
    BYPASS = "bypass"


class PermissionBehavior(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


DONT_ASK_ENABLED: bool = os.environ.get("IE_FEATURE_DONT_ASK") == "1"
DONT_ASK_WARNED: bool = False


def sed_validation_enabled() -> bool:
    """Return True unless FEATURE_SED_VALIDATION is explicitly disabled.

    The sed validator is on by default. Operators can roll back to the pre-
    validator behaviour (no step 4.5, hardcoded `sed -i` deny at the executor
    layer is the only gate) by setting `FEATURE_SED_VALIDATION=0` in the
    environment. Any other value, or no value at all, means the validator is
    active. Evaluated per call — test fixtures can monkeypatch the env var.
    """
    return os.environ.get("FEATURE_SED_VALIDATION", "1") != "0"


def dangerous_removal_enabled() -> bool:
    """Return True unless FEATURE_DANGEROUS_REMOVAL is explicitly disabled.

    The path-based dangerous removal gate at step 1.7 is on by default.
    Operators can roll back to the pre-gate behaviour (only the flag-based
    `DESTRUCTIVE_PATTERNS` in `command_classifier.py` and the static
    `HARDCODED_DENIALS` regex for `rm -rf /`) by setting
    `FEATURE_DANGEROUS_REMOVAL=false` in the environment. Only the literal
    case-insensitive string ``false`` disables the gate; any other value,
    or no value at all, leaves it active. Evaluated per call — test
    fixtures can monkeypatch the env var.
    """
    return os.environ.get("FEATURE_DANGEROUS_REMOVAL", "true").strip().lower() != "false"


def resolve_permission_mode(requested: PermissionMode) -> PermissionMode:
    """Resolve DONT_ASK to DEFAULT when feature flag is off."""
    if requested == PermissionMode.DONT_ASK and not DONT_ASK_ENABLED:
        global DONT_ASK_WARNED
        if not DONT_ASK_WARNED:
            logger.warning(
                "DONT_ASK mode requested but IE_FEATURE_DONT_ASK not set — falling back to DEFAULT"
            )
            DONT_ASK_WARNED = True
        return PermissionMode.DEFAULT
    return requested


@dataclass
class PermissionRule:
    tool: str
    pattern: str = ""
    behavior: PermissionBehavior = PermissionBehavior.DENY
    reason: str = ""
    source: str = "user"


DenyRule = PermissionRule


HardDeny = "hard_deny"

USER_PROVENANCE = "user"


HARDCODED_DENIALS: list[PermissionRule] = [
    DenyRule(tool="*", pattern=r"rm\s+-rf\s+[/~]", reason="catastrophic deletion", source=HardDeny),
    DenyRule(
        tool="*",
        pattern=r"curl.*\|\s*(sh|bash)",
        reason="piped remote code execution",
        source=HardDeny,
    ),
    DenyRule(
        tool="*",
        pattern=r"wget.*\|\s*(sh|bash)",
        reason="piped remote code execution",
        source=HardDeny,
    ),
    DenyRule(
        tool="*",
        pattern=r"(\.ssh/authorized_keys|\.gnupg)",
        reason="sensitive credential path",
        source=HardDeny,
    ),
    DenyRule(
        tool="*",
        pattern=r"git\s+push\s+--force\s+(main|master)",
        reason="force push to protected branch",
        source=HardDeny,
    ),
    DenyRule(
        tool="*",
        pattern=r"DROP\s+TABLE|DELETE\s+FROM\s+\w+\s*$",
        reason="destructive SQL without WHERE",
        source=HardDeny,
    ),
]

COMPILED_PATTERNS: dict[str, re.Pattern[str]] = {}


def get_compiled_pattern(pattern: str) -> re.Pattern[str] | None:
    if not pattern:
        return None
    cached = COMPILED_PATTERNS.get(pattern)
    if cached is not None:
        return cached
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
        COMPILED_PATTERNS[pattern] = compiled
        return compiled
    except re.error:
        return None


SENSITIVE_PATHS: frozenset[str] = frozenset(
    {
        "~/.ssh",
        "~/.gnupg",
        "~/.aws/credentials",
        "~/.config/gcloud",
        "/etc/shadow",
        "/etc/passwd",
        "/etc/sudoers",
        "/usr/bin",
        "/usr/sbin",
        "/bin",
        "/sbin",
        "/etc/cron.d",
        "/etc/crontab",
    }
)

BLOCKED_PATHS: frozenset[str] = frozenset(
    {
        "~/.ssh/authorized_keys",
        "~/.ssh/id_rsa",
        "~/.ssh/id_ed25519",
        "~/.gnupg/private-keys-v1.d",
    }
)

DANGEROUS_FILES: frozenset[str] = PATH_SAFETY_DANGEROUS_FILES
DANGEROUS_DIRECTORIES: frozenset[str] = PATH_SAFETY_DANGEROUS_DIRS

BLOCKED_RESOLVED: dict[str, str] = {
    p: os.path.realpath(os.path.expanduser(p)) for p in BLOCKED_PATHS
}
SENSITIVE_RESOLVED: dict[str, str] = {
    p: os.path.realpath(os.path.expanduser(p)) for p in SENSITIVE_PATHS
}
DANGEROUS_FILES_LOWER: frozenset[str] = frozenset(f.lower() for f in DANGEROUS_FILES)
DANGEROUS_DIRS_LOWER: frozenset[str] = frozenset(d.lower() for d in DANGEROUS_DIRECTORIES)
TEMP_DIR: str = os.path.realpath(tempfile.gettempdir())


@dataclass
class PathValidationResult:
    allowed: bool
    resolved_path: str = ""
    reason: str = ""


def path_match(candidate: str, reference: str) -> bool:
    """Case-insensitive check: candidate equals or is inside reference directory."""
    c = candidate.lower()
    r = reference.lower()
    return c == r or c.startswith(r + os.sep)


def check_dangerous_filename(path: str, operation: str) -> PathValidationResult | None:
    """Block writes to shell config / IDE config files (code execution risk)."""
    if operation not in ("write", "create"):
        return None
    basename = os.path.basename(path).lower()
    if basename in DANGEROUS_FILES_LOWER:
        return PathValidationResult(
            allowed=False,
            resolved_path=path,
            reason=f"Write to dangerous file: {basename}",
        )
    return None


def check_dangerous_directory(path: str, operation: str) -> PathValidationResult | None:
    """Block writes into directories that contain sensitive config or hooks."""
    if operation not in ("write", "create"):
        return None
    for part in path.split(os.sep):
        if part.lower() in DANGEROUS_DIRS_LOWER:
            return PathValidationResult(
                allowed=False,
                resolved_path=path,
                reason=f"Write into dangerous directory: {part}",
            )
    return None


def validate_path(
    path: str,
    operation: str,
    allowed_roots: list[str] | None = None,
) -> PathValidationResult:
    """Validate a file path for safety before tool execution.

    Steps: reject empty, reject shell expansion (including Zsh =), reject UNC,
    reject unsafe tilde, reject glob in writes, expand/resolve/canonicalize,
    check blocked/sensitive/dangerous on both original and resolved paths,
    enforce allowed_roots.
    """
    if not path or not path.strip():
        return PathValidationResult(allowed=False, reason="Empty path")

    clean = path.strip().strip("\"'")

    if "$" in clean or "`" in clean or "%" in clean:
        return PathValidationResult(
            allowed=False,
            resolved_path=clean,
            reason="Shell expansion syntax in path",
        )
    if clean.startswith("="):
        return PathValidationResult(
            allowed=False,
            resolved_path=clean,
            reason="Zsh equals expansion syntax in path",
        )

    if clean.startswith("\\\\") or clean.startswith("//"):
        return PathValidationResult(
            allowed=False,
            resolved_path=clean,
            reason="UNC paths are blocked",
        )

    if clean.startswith("~") and len(clean) > 1 and clean[1] != "/":
        return PathValidationResult(
            allowed=False,
            resolved_path=clean,
            reason="Unsafe tilde expansion variant",
        )

    if operation in ("write", "create") and any(c in clean for c in "*?["):
        return PathValidationResult(
            allowed=False,
            resolved_path=clean,
            reason="Glob patterns not allowed in write operations",
        )

    expanded = os.path.expanduser(clean)

    absolute = os.path.abspath(expanded)

    try:
        canonical = os.path.realpath(absolute)
    except (OSError, ValueError) as e:
        logger.debug("Could not resolve symlinks for %s: %s", absolute, e)
        canonical = absolute

    paths_to_check = [canonical]
    if absolute != canonical:
        paths_to_check.append(absolute)

    for blocked, blocked_resolved in BLOCKED_RESOLVED.items():
        for p in paths_to_check:
            if path_match(p, blocked_resolved):
                return PathValidationResult(
                    allowed=False,
                    resolved_path=canonical,
                    reason=f"Blocked path: {blocked}",
                )

    for sensitive, sensitive_resolved in SENSITIVE_RESOLVED.items():
        for p in paths_to_check:
            if path_match(p, sensitive_resolved):
                if operation in ("write", "create"):
                    return PathValidationResult(
                        allowed=False,
                        resolved_path=canonical,
                        reason=f"Write to sensitive path: {sensitive}",
                    )
                return PathValidationResult(
                    allowed=True,
                    resolved_path=canonical,
                    reason=f"Sensitive path read: {sensitive}",
                )

    for p in paths_to_check:
        result = check_dangerous_filename(p, operation)
        if result is not None:
            return result
        result = check_dangerous_directory(p, operation)
        if result is not None:
            return result

    if allowed_roots:
        in_allowed = False
        for root in allowed_roots:
            root_canonical = os.path.realpath(os.path.expanduser(root))
            if path_match(canonical, root_canonical):
                in_allowed = True
                break
        if not in_allowed:
            if not (canonical.startswith(TEMP_DIR + os.sep) or canonical == TEMP_DIR):
                return PathValidationResult(
                    allowed=False,
                    resolved_path=canonical,
                    reason=f"Path outside allowed directories: {canonical}",
                )

    return PathValidationResult(allowed=True, resolved_path=canonical)


PERMISSION_MATRIX: dict[tuple[ToolSource, ToolAction], bool] = {
    (ToolSource.INTERNAL, ToolAction.SEARCH): False,
    (ToolSource.INTERNAL, ToolAction.READ): False,
    (ToolSource.INTERNAL, ToolAction.WRITE): False,
    (ToolSource.INTERNAL, ToolAction.EXECUTE): False,
    (ToolSource.INTERNAL, ToolAction.DESTRUCTIVE): False,
    (ToolSource.SANDBOXED, ToolAction.SEARCH): False,
    (ToolSource.SANDBOXED, ToolAction.READ): False,
    (ToolSource.SANDBOXED, ToolAction.WRITE): True,
    (ToolSource.SANDBOXED, ToolAction.EXECUTE): False,
    (ToolSource.SANDBOXED, ToolAction.DESTRUCTIVE): True,
    (ToolSource.PLATFORM, ToolAction.SEARCH): False,
    (ToolSource.PLATFORM, ToolAction.READ): False,
    (ToolSource.PLATFORM, ToolAction.WRITE): True,
    (ToolSource.PLATFORM, ToolAction.EXECUTE): True,
    (ToolSource.PLATFORM, ToolAction.DESTRUCTIVE): True,
    (ToolSource.MCP, ToolAction.SEARCH): False,
    (ToolSource.MCP, ToolAction.READ): False,
    (ToolSource.MCP, ToolAction.WRITE): True,
    (ToolSource.MCP, ToolAction.EXECUTE): True,
    (ToolSource.MCP, ToolAction.DESTRUCTIVE): True,
    (ToolSource.NETWORK, ToolAction.SEARCH): False,
    (ToolSource.NETWORK, ToolAction.READ): False,
}


@dataclass
class PermissionResult:
    allowed: bool
    reason: str = ""
    source: ToolSource = ToolSource.INTERNAL
    action: ToolAction = ToolAction.SEARCH
    matched_rule: PermissionRule | None = None
    needs_user_confirmation: bool = False
    new_rule: PermissionRule | None = None
    persist: bool = False
    suggested_rule: str = ""
    feedback: str = ""
    content_blocks: list[dict] = field(default_factory=list)
    sed_edit_info: SedEditInfo | None = None


class DenialLimitExceeded(Exception):
    """Raised in headless mode when total denials exceed MAX_TOTAL."""


@dataclass
class DenialResult:
    """Result from DenialTracker.record_denial(). Presence signals fallback to prompting."""

    injection: str
    hit_total_limit: bool
    total_count: int = 0
    recent_tools: str = ""


@dataclass
class DenialTracker:
    """Tracks consecutive and total denials to enforce corrective behavior.

    When thresholds are hit, returns a DenialResult signalling the caller
    to fall back to prompting the user (mechanical enforcement) rather than
    relying on the LLM to obey an injected message.
    """

    consecutive: int = 0
    total: int = 0
    denied_tools: deque[str] = field(default_factory=lambda: deque(maxlen=10))

    MAX_CONSECUTIVE: int = 3
    MAX_TOTAL: int = 20

    def record_denial(self, tool_name: str) -> DenialResult | None:
        """Record a denial. Returns a DenialResult if threshold hit, else None."""
        self.consecutive += 1
        self.total += 1
        self.denied_tools.append(tool_name)

        if self.total >= self.MAX_TOTAL:
            recent = ", ".join(set(list(self.denied_tools)[-5:]))
            return DenialResult(
                injection=(
                    f"[System: {self.total} tool calls have been denied this "
                    f"session (recent: {recent}). Please review before continuing.]"
                ),
                hit_total_limit=True,
                total_count=self.total,
                recent_tools=recent,
            )

        if self.consecutive >= self.MAX_CONSECUTIVE:
            recent = ", ".join(set(list(self.denied_tools)[-3:]))
            return DenialResult(
                injection=(
                    f"[System: {self.consecutive} consecutive permission denials "
                    f"(tools: {recent}). Stop retrying denied tools. "
                    "Use a different approach or ask the user for guidance.]"
                ),
                hit_total_limit=False,
                total_count=self.total,
                recent_tools=recent,
            )

        return None

    def record_success(self):
        """Record a successful tool execution. Resets consecutive counter."""
        if self.consecutive > 0:
            self.consecutive = 0

    def reset(self):
        """Reset for new execute() call (new user message)."""
        self.consecutive = 0

    def reset_total(self):
        """Reset total after user reviews at the total threshold."""
        self.total = 0
        self.consecutive = 0


INTERNAL_SEARCH_TOOLS = frozenset(
    {
        "ask_user",
        "report_checkpoint",
        "search_content",
        "list_content",
        "tool_search",
        "request_plan",
        "request_replan",
        "gap_resolution",
    }
)

INTERNAL_WRITE_TOOLS = frozenset({"create_skill"})
INTERNAL_EXECUTE_TOOLS = frozenset({"spawn_sub_agent"})

INTERNAL_TOOL_NAMES = INTERNAL_SEARCH_TOOLS | INTERNAL_WRITE_TOOLS | INTERNAL_EXECUTE_TOOLS

NETWORK_TOOL_NAMES = frozenset({"web_fetch", "web_search"})

MCP_READ_PREFIXES = ("get_", "list_", "search_", "read_", "fetch_", "find_", "query_")
MCP_WRITE_PREFIXES = (
    "create_",
    "update_",
    "delete_",
    "post_",
    "send_",
    "write_",
    "set_",
    "put_",
    "add_",
    "edit_",
)
MCP_DESTRUCTIVE_PREFIXES = ("drop_", "destroy_", "purge_", "remove_")

CLS_TO_ACTION = {
    "DESTRUCTIVE": ToolAction.DESTRUCTIVE,
    "WRITE": ToolAction.WRITE,
    "READ": ToolAction.READ,
    "SEARCH": ToolAction.SEARCH,
}

PLATFORM_READ_TOOLS = frozenset({"file_read", "glob", "grep"})
PLATFORM_WRITE_TOOLS = frozenset({"file_write", "file_edit"})


def classify_action(tool_name: str, tool_input: dict[str, Any], source: ToolSource) -> ToolAction:
    """Classify the action type of a tool call from its name, input, and source."""

    if source == ToolSource.INTERNAL:
        if tool_name in INTERNAL_SEARCH_TOOLS:
            return ToolAction.SEARCH
        if tool_name in INTERNAL_WRITE_TOOLS:
            return ToolAction.WRITE
        if tool_name in INTERNAL_EXECUTE_TOOLS:
            return ToolAction.EXECUTE
        return ToolAction.SEARCH

    if source == ToolSource.NETWORK:
        return ToolAction.SEARCH

    if source == ToolSource.MCP:
        bare_name = tool_name.split(":", 1)[-1] if ":" in tool_name else tool_name
        action_segment = bare_name.rsplit(".", 1)[-1] if "." in bare_name else bare_name
        verb = action_segment.lower()
        if verb.startswith(MCP_DESTRUCTIVE_PREFIXES):
            return ToolAction.DESTRUCTIVE
        if verb.startswith(MCP_WRITE_PREFIXES):
            return ToolAction.WRITE
        if verb.startswith(MCP_READ_PREFIXES):
            return ToolAction.READ
        return ToolAction.EXECUTE

    if source == ToolSource.PLATFORM:
        if tool_name in PLATFORM_READ_TOOLS:
            return ToolAction.READ
        if tool_name in PLATFORM_WRITE_TOOLS:
            return ToolAction.WRITE
        return ToolAction.EXECUTE

    if source == ToolSource.SANDBOXED:
        command = tool_input.get("command", "") or tool_input.get("code", "")
        if isinstance(command, str) and command.strip():
            result = classify_command(command)
            return CLS_TO_ACTION.get(result.classification, ToolAction.EXECUTE)
        return ToolAction.EXECUTE

    return ToolAction.EXECUTE


def match_rule(rule: PermissionRule, tool_name: str, serialized_input: str) -> bool:
    """Check if a permission rule matches the given tool call."""
    if rule.tool != "*":
        if rule.tool.endswith("*"):
            prefix = rule.tool[:-1]
            if not tool_name.startswith(prefix):
                return False
        elif rule.tool != tool_name:
            return False

    if rule.pattern:
        compiled = get_compiled_pattern(rule.pattern)
        if compiled is None:
            logger.warning("Invalid regex in deny rule: %s", rule.pattern)
            return False
        if not compiled.search(serialized_input):
            return False

    return True


match_deny_rule = match_rule


def build_suggested_rule(
    tool_name: str, tool_input: dict[str, Any], behavior: PermissionBehavior
) -> str:
    """Human-readable description of what an 'Always' choice would create.

    Uses the Tool(content) format from permission_store for consistency.
    """
    from src.sandbox.permission_store import format_rule_string

    verb = behavior.value
    serialized = str(tool_input)
    if len(serialized) <= 80:
        return f"{verb} {format_rule_string(tool_name, serialized)}"
    return f"{verb} {tool_name}"


RULE_SOURCE_TIER: dict[str, str] = {
    "hard_deny": "system",
    "septemberai": "system",
    "engine": "session",
    "user": "persistent",
    "managed": "persistent",
}


def allow_feedback(reason: str, detail: str = "") -> str:
    return f"allowed by {reason}: {detail}" if detail else f"allowed by {reason}"


def build_rule_feedback(rule: "PermissionRule") -> str:
    from src.sandbox.permission_store import normalize_source, rule_to_string

    tier = RULE_SOURCE_TIER.get(normalize_source(rule.source), rule.source)
    return allow_feedback(
        "rule",
        f"{rule_to_string(rule)} [source={rule.source}, tier={tier}]",
    )


def truncate_command(command: str, limit: int = 80) -> str:
    return command if len(command) <= limit else command[: limit - 3] + "..."


MATRIX_FEEDBACK: dict[tuple[ToolSource, ToolAction], str] = {
    (s, a): f"allowed by permission matrix: {s.value}.{a.value} auto-approved"
    for (s, a) in PERMISSION_MATRIX
}


AskUserParams = str | dict
AskUserReply = str | dict


def parse_ask_request(params: AskUserParams) -> tuple[str, list[str], str]:
    """Extract (question, choices, suggested_rule) from ask-user params.

    Legacy callers pass a plain string (question only); permission
    prompts pass a dict with the full shape. Shared by ``default_ask_user``
    and ``await_block_input`` so both normalize identically.
    """
    if isinstance(params, dict):
        question = str(params.get("question", ""))
        choices = list(params.get("choices") or [])
        suggested = str(params.get("suggested_rule", ""))
        return question, choices, suggested
    return str(params), [], ""


def flatten_ask_reply(reply: AskUserReply) -> str:
    """Reduce a dict reply to the bare response string for legacy callers."""
    if isinstance(reply, dict):
        return str(reply.get("response", reply.get("answer", "")))
    return str(reply)


def parse_ask_response(raw: Any) -> tuple[str, str, list[dict]]:
    """Parse the return value of ask_user_fn into (decision, feedback, content_blocks).

    ask_user_fn may return either:
    - A plain string (backward-compat): the entire string is the decision.
      feedback and content_blocks are empty.
    - A dict with keys:
        "response"       (str)  — the decision word (allow / deny / allow_always / deny_always / …)
        "feedback"       (str, optional) — text the user typed when approving; maps to
                         PermissionAllowDecision.acceptFeedback in the reference implementation.
        "content_blocks" (list, optional) — provider-neutral content-block dicts (images, text)
                         the user attached while interacting with the permission prompt; maps to
                         PermissionAllowDecision.contentBlocks / PermissionAskDecision.contentBlocks.

    Both "feedback" and "content_blocks" are ignored when the user denies — content_blocks is
    still propagated on deny so the post-tool pipeline can attach them to the error message.
    """
    if isinstance(raw, dict):
        decision = str(raw.get("response", raw.get("decision", ""))).strip().lower()
        feedback = str(raw.get("feedback", "")).strip()
        blocks = raw.get("content_blocks") or []
        if not isinstance(blocks, list):
            blocks = []
        return decision, feedback, blocks
    return str(raw).strip().lower(), "", []


async def check_permission(
    tool_name: str,
    tool_input: dict[str, Any],
    source: ToolSource,
    rules: list[PermissionRule] | None = None,
    ask_user_fn: Callable[[dict[str, Any]], Awaitable[str | dict[str, Any]]] | None = None,
    allowed_roots: list[str] | None = None,
    permission_mode: PermissionMode = PermissionMode.DEFAULT,
    *,
    deny_rules: list[PermissionRule] | None = None,
    permission_request_fn: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None,
) -> PermissionResult:
    """Check whether a tool call is permitted.

    Pipeline:
      1.   Hardcoded denials (catastrophic patterns) — always deny, bypass-immune
      1.5  AST-based bash security analysis (SANDBOXED) — bypass-immune
      1.55 Classify action (hoisted pure call, so step 1.6 can read the action)
      1.6  Bypass-immune dangerous-target gate — blocks writes to DANGEROUS_FILES,
           DANGEROUS_DIRS, and Windows-evasion patterns BEFORE user rules are
           evaluated. Cannot be overridden by allow rules, permission modes, or
           hooks. Covers PLATFORM file_path inputs AND SANDBOXED bash commands
           (via extract_written_paths).
      1.7  Bypass-immune dangerous-removal gate — SANDBOXED bash only. Walks
           every compound-command segment, extracts paths from rm/rmdir/unlink
           argv (handling POSIX `--`), and hard-denies any catastrophic target
           (root, home, /etc, /usr, wildcards, Windows drive roots). Runs both
           a raw and a realpath-resolved check to defeat symlink attacks and
           macOS /tmp → /private/tmp canonicalisation. Gated on
           FEATURE_DANGEROUS_REMOVAL (default: on). Cannot be overridden by
           any rule, mode, hook, or user approval.
      2.   DENY rules — deny immediately if matched
      2.5  Sandbox auto-allow (SYSTEM_ALLOW) — SANDBOXED only. Gated on
           FEATURE_SANDBOX_AUTO_ALLOW env var (default: "true"). Safe
           read-only commands (ls, cat, grep, git status, etc.) are
           auto-allowed without prompting. Only fires when no DENY rule
           matched at step 2. Bypasses subsequent ALLOW and ASK rules
           for matched commands.
      3.   ALLOW rules — allow immediately if matched (skip prompt)
      4.   ASK rules — force prompt if matched (even if matrix says auto-allow).
           Note: sandbox auto-allow at step 2.5 exits before ASK rules
           for safe commands in SANDBOXED mode.
      4.5  Sed validator (SANDBOXED sed commands only) — runs only when the
           FEATURE_SED_VALIDATION env var is not "0" (default: on). Mirrors
           the src bashPermissions.ts position: after user ALLOW rules have
           had their chance to short-circuit, before mode enforcement. Safe
           allowlist match → passthrough. Denylist hit or unknown pattern →
           escalate to ask. `-i` gating is driven by PermissionMode.ACCEPT_EDITS.
      5.5  validate_path() — defense-in-depth (blocked/sensitive paths, roots)
      5.9  Permission mode enforcement
      6.   Permission matrix lookup
      7.   User prompt with 4 choices (allow / allow_always / deny / deny_always)
    """
    all_rules = rules if rules is not None else (deny_rules or [])

    serialized = str(tool_input)

    for rule in HARDCODED_DENIALS:
        if match_rule(rule, tool_name, serialized):
            logger.warning(
                "Permission DENIED (hardcoded): tool=%s reason=%s",
                tool_name,
                rule.reason,
            )
            return PermissionResult(
                allowed=False,
                reason=f"Blocked by safety rule: {rule.reason}",
                source=source,
                matched_rule=rule,
            )

    if source == ToolSource.SANDBOXED:
        command = tool_input.get("command", "") or tool_input.get("code", "")
        if isinstance(command, str) and command.strip():
            from src.sandbox.bash_security import analyze_bash_security

            sec_result = analyze_bash_security(command)
            if sec_result.blocked:
                logger.warning(
                    "Permission DENIED (bash security check #%d): tool=%s reason=%s",
                    sec_result.check_id,
                    tool_name,
                    sec_result.message,
                )
                return PermissionResult(
                    allowed=False,
                    reason=f"Blocked by security check #{sec_result.check_id}: {sec_result.message}",
                    source=source,
                )

    action = classify_action(tool_name, tool_input, source)

    hit_path: str | None = None
    hit: PathSafetyResult | None = None

    if source == ToolSource.PLATFORM and action in (ToolAction.WRITE, ToolAction.DESTRUCTIVE):
        file_path = tool_input.get("file_path") or tool_input.get("path") or ""
        if isinstance(file_path, str) and file_path:
            safety = check_path_safety(file_path)
            if safety is not None and safety.is_dangerous:
                hit_path, hit = file_path, safety

    elif source == ToolSource.SANDBOXED:
        command = tool_input.get("command", "") or tool_input.get("code", "")
        if isinstance(command, str) and command.strip():
            for candidate in extract_written_paths(command):
                safety = check_path_safety(candidate)
                if safety is not None and safety.is_dangerous:
                    hit_path, hit = candidate, safety
                    break

    if hit is not None:
        logger.warning(
            "Permission DENIED (bypass-immune path safety): tool=%s path=%s reason=%s classifier_approvable=%s",
            tool_name,
            hit_path,
            hit.reason,
            hit.classifier_approvable,
        )
        return PermissionResult(
            allowed=False,
            reason=hit.reason,
            source=source,
            action=action,
        )

    if source == ToolSource.SANDBOXED and dangerous_removal_enabled():
        command = tool_input.get("command", "") or tool_input.get("code", "")
        if (
            isinstance(command, str)
            and command.strip()
            and ("rm" in command or "unlink" in command)
        ):
            cwd = tool_input.get("cwd") or os.getcwd()
            for segment in split_compound_commands(command):
                tokens = tokenize_command(segment)
                if not tokens:
                    continue
                seg_cmd = os.path.basename(tokens[0])
                seg_args = tokens[1:]
                reason = check_dangerous_removal(seg_cmd, seg_args, cwd)
                if reason:
                    logger.warning(
                        "Permission HARD-DENIED (dangerous removal): tool=%s segment=%s reason=%s",
                        tool_name,
                        segment,
                        reason,
                    )
                    return PermissionResult(
                        allowed=False,
                        reason=(
                            f"Dangerous removal blocked: {reason}. "
                            "This command would remove a critical system "
                            "path and cannot be auto-allowed by permission "
                            "rules."
                        ),
                        source=source,
                        action=action,
                    )

    matched_deny: PermissionRule | None = None
    matched_allow: PermissionRule | None = None
    matched_ask_rule: PermissionRule | None = None
    for rule in all_rules:
        if not match_rule(rule, tool_name, serialized):
            continue
        if rule.behavior == PermissionBehavior.DENY and matched_deny is None:
            matched_deny = rule
        elif rule.behavior == PermissionBehavior.ALLOW and matched_allow is None:
            matched_allow = rule
        elif rule.behavior == PermissionBehavior.ASK and matched_ask_rule is None:
            matched_ask_rule = rule

    ask_forced = matched_ask_rule is not None

    if matched_deny is not None:
        logger.info(
            "Permission DENIED (rule): tool=%s reason=%s source=%s",
            tool_name,
            matched_deny.reason,
            matched_deny.source,
        )
        return PermissionResult(
            allowed=False,
            reason=f"Blocked by {matched_deny.source} rule: {matched_deny.reason}",
            source=source,
            action=action,
            matched_rule=matched_deny,
        )

    if permission_mode == PermissionMode.BYPASS and not ask_forced:
        logger.info("Permission ALLOWED (bypass mode): tool=%s action=%s", tool_name, action)
        return PermissionResult(
            allowed=True,
            source=source,
            action=action,
            feedback=allow_feedback("bypass permission mode", tool_name),
        )

    if source == ToolSource.SANDBOXED and not ask_forced:
        command = tool_input.get("command", "") or tool_input.get("code", "")
        if isinstance(command, str) and command.strip():
            from src.sandbox.sandbox_auto_allow import is_sandbox_auto_allowed

            if is_sandbox_auto_allowed(command):
                logger.debug(
                    "Permission ALLOWED (sandbox auto-allow): tool=%s cmd=%.80s",
                    tool_name,
                    command.strip(),
                )
                return PermissionResult(
                    allowed=True,
                    source=source,
                    action=action,
                    feedback=allow_feedback(
                        "sandbox safe-command allowlist",
                        truncate_command(command.strip()),
                    ),
                )

    if matched_allow is not None and not ask_forced:
        logger.info(
            "Permission ALLOWED (rule): tool=%s reason=%s source=%s",
            tool_name,
            matched_allow.reason,
            matched_allow.source,
        )
        return PermissionResult(
            allowed=True,
            source=source,
            action=action,
            matched_rule=matched_allow,
            feedback=build_rule_feedback(matched_allow),
        )
    sed_ask_reason: str | None = None

    sed_edit_info: SedEditInfo | None = None
    if source == ToolSource.SANDBOXED and sed_validation_enabled():
        command = tool_input.get("command", "") or tool_input.get("code", "")
        if isinstance(command, str) and "sed" in command:
            sub_strings = split_compound_commands(command)
            sed_subcommands = find_sed_subcommands(sub_strings)
            if sed_subcommands:
                allow_file_writes = permission_mode == PermissionMode.ACCEPT_EDITS
                for sub_tokens in sed_subcommands:
                    sed_result = check_sed_constraints(
                        sub_tokens,
                        allow_file_writes=allow_file_writes,
                    )
                    if sed_result.behavior == SedBehavior.ASK:
                        ask_forced = True
                        if sed_ask_reason is None:
                            sed_ask_reason = sed_result.reason
                        continue
                    if sed_edit_info is None:
                        sed_edit_info = parse_sed_edit(sub_tokens)
                if sed_ask_reason is None and not ask_forced and len(sub_strings) == 1:
                    logger.debug(
                        "Permission ALLOWED (sed validator passthrough): command=%.80s",
                        command,
                    )
                    return PermissionResult(
                        allowed=True,
                        source=source,
                        action=action,
                        sed_edit_info=sed_edit_info,
                        feedback=allow_feedback("sed safe-pattern validator"),
                    )

    if source == ToolSource.PLATFORM:
        file_path = tool_input.get("file_path") or tool_input.get("path") or ""
        if file_path:
            op = "write" if action in (ToolAction.WRITE, ToolAction.DESTRUCTIVE) else "read"
            path_result = validate_path(file_path, op, allowed_roots=allowed_roots)
            if not path_result.allowed:
                logger.warning(
                    "Permission DENIED (path validation): tool=%s path=%s reason=%s",
                    tool_name,
                    file_path,
                    path_result.reason,
                )
                return PermissionResult(
                    allowed=False,
                    reason=f"Path validation failed: {path_result.reason}",
                    source=source,
                    action=action,
                )

    mode = (
        permission_mode
        if permission_mode == PermissionMode.DEFAULT
        else resolve_permission_mode(permission_mode)
    )

    if mode == PermissionMode.READ_ONLY:
        if action not in (ToolAction.SEARCH, ToolAction.READ):
            if source == ToolSource.SANDBOXED:
                command = tool_input.get("command", "") or tool_input.get("code", "")
                if isinstance(command, str) and command.strip() and is_command_read_only(command):
                    logger.debug(
                        "Permission ALLOWED (READ_ONLY allowlist rescue): tool=%s command=%.80s",
                        tool_name,
                        command,
                    )
                    return PermissionResult(
                        allowed=True,
                        source=source,
                        action=action,
                        feedback=allow_feedback("read-only allowlist rescue"),
                    )
            logger.info(
                "Permission DENIED (READ_ONLY mode): tool=%s action=%s",
                tool_name,
                action.value,
            )
            return PermissionResult(
                allowed=False,
                reason=f"Read-only mode: {action.value} operations blocked",
                source=source,
                action=action,
            )
        if not ask_forced:
            return PermissionResult(
                allowed=True,
                source=source,
                action=action,
                feedback=allow_feedback("READ_ONLY mode", f"{action.value} is read-safe"),
            )

    requires_permission = PERMISSION_MATRIX.get((source, action))
    if requires_permission is None:
        requires_permission = True

    if not requires_permission and not ask_forced:
        return PermissionResult(
            allowed=True,
            source=source,
            action=action,
            feedback=MATRIX_FEEDBACK[(source, action)],
        )

    suggested = build_suggested_rule(tool_name, tool_input, PermissionBehavior.ALLOW)

    if ask_user_fn is None:
        if permission_request_fn is not None:
            try:
                hook_decision = await permission_request_fn(
                    {
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                        "source": source.value,
                        "action": action.value,
                    }
                )
            except Exception as hook_exc:  # noqa: BLE001
                logger.debug(
                    "PermissionRequest hook failed for tool=%s: %s",
                    tool_name,
                    hook_exc,
                )
                hook_decision = None

            if hook_decision is not None:
                decision_lower = str(hook_decision).strip().lower()
                if decision_lower == "allow":
                    return PermissionResult(allowed=True, source=source, action=action)
                elif decision_lower == "deny":
                    return PermissionResult(
                        allowed=False,
                        reason=f"Permission denied by PermissionRequest hook for {tool_name}",
                        source=source,
                        action=action,
                    )

        logger.info(
            "Permission DENIED (no ask_user, fail-closed): tool=%s source=%s action=%s",
            tool_name,
            source.value,
            action.value,
        )
        fail_reason = f"Permission required for {action.value} on {source.value} tool, but no user approval channel available"
        if sed_ask_reason:
            fail_reason = f"{fail_reason} ({sed_ask_reason})"
        return PermissionResult(
            allowed=False,
            reason=fail_reason,
            source=source,
            action=action,
            needs_user_confirmation=True,
            suggested_rule=suggested,
        )

    if mode == PermissionMode.DONT_ASK:
        logger.info(
            "Permission DENIED (DONT_ASK mode): tool=%s source=%s action=%s",
            tool_name,
            source.value,
            action.value,
        )
        return PermissionResult(
            allowed=False,
            reason="Permission required but DONT_ASK mode active — denied",
            source=source,
            action=action,
        )

    if matched_ask_rule and matched_ask_rule.reason:
        ask_reason = f" (rule: {matched_ask_rule.reason})"
    elif sed_ask_reason:
        ask_reason = f" ({sed_ask_reason})"
    else:
        ask_reason = ""
    try:
        raw_response = await ask_user_fn(
            {
                "question": (
                    f"Tool '{tool_name}' wants to perform a {action.value} operation{ask_reason}. "
                    f"Allow? (allow / allow_always / deny / deny_always)"
                ),
                "choices": ["allow", "allow_always", "deny", "deny_always"],
                "suggested_rule": suggested,
            }
        )
        response_lower, feedback, content_blocks = parse_ask_response(raw_response)

        if response_lower in ("allow_always",):
            new_rule = PermissionRule(
                tool=tool_name,
                behavior=PermissionBehavior.ALLOW,
                reason=f"User chose allow_always for {tool_name}",
                source=USER_PROVENANCE,
            )
            return PermissionResult(
                allowed=True,
                source=source,
                action=action,
                new_rule=new_rule,
                persist=True,
                suggested_rule=suggested,
                feedback=feedback,
                content_blocks=content_blocks,
            )

        if response_lower in ("deny_always",):
            new_rule = PermissionRule(
                tool=tool_name,
                behavior=PermissionBehavior.DENY,
                reason=f"User chose deny_always for {tool_name}",
                source=USER_PROVENANCE,
            )
            return PermissionResult(
                allowed=False,
                source=source,
                action=action,
                reason=f"User denied (always) {action.value} on tool '{tool_name}'",
                new_rule=new_rule,
                persist=True,
                suggested_rule=suggested,
                content_blocks=content_blocks,
            )

        approved = response_lower in ("yes", "y", "approve", "allow", "ok", "sure")
        if approved:
            return PermissionResult(
                allowed=True,
                source=source,
                action=action,
                suggested_rule=suggested,
                feedback=feedback,
                content_blocks=content_blocks,
            )
        else:
            return PermissionResult(
                allowed=False,
                reason=f"User denied {action.value} on tool '{tool_name}'",
                source=source,
                action=action,
                suggested_rule=suggested,
                content_blocks=content_blocks,
            )
    except Exception as e:
        logger.warning("ask_user failed during permission check: %s", e)
        return PermissionResult(
            allowed=False,
            reason=f"Permission check failed: {e}",
            source=source,
            action=action,
        )
