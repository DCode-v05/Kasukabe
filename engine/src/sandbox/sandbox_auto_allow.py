"""Sandbox auto-allow — safe command patterns for sandboxed execution.

When ToolSource.SANDBOXED and FEATURE_SANDBOX_AUTO_ALLOW is "true",
commands matching these patterns are auto-allowed at step 2.5 of the
permission pipeline, after user DENY rules but before user ALLOW rules.

These are SYSTEM_ALLOW rules — defined in code, not operator-configurable.
Operators can still block specific commands with deny rules (step 2), and an
explicit operator ASK rule also outranks this allowlist: a convenience that
exists to reduce prompt fatigue must not silently cancel an instruction the
operator wrote on purpose.
"""

from __future__ import annotations

import os
import re
import logging

logger = logging.getLogger(__name__)

FEATURE_FLAG = "FEATURE_SANDBOX_AUTO_ALLOW"
FEATURE_DEFAULT = "true"


def sandbox_auto_allow_enabled() -> bool:
    """Check whether sandbox auto-allow is active (feature flag)."""
    return os.environ.get(FEATURE_FLAG, FEATURE_DEFAULT).strip().lower() != "false"


COMPOUND_CHARS = frozenset("|;&><`")
SUBSHELL_PATTERN = "$("

SAFE_COMMANDS: frozenset[str] = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "stat",
        "file",
        "pwd",
        "uname",
        "whoami",
        "id",
        "hostname",
        "date",
        "uptime",
        "printenv",
        "grep",
        "rg",
        "ag",
        "ack",
        "fgrep",
        "egrep",
        "sort",
        "uniq",
        "cut",
        "tr",
        "jq",
        "yq",
        "column",
        "fmt",
        "diff",
        "cmp",
        "comm",
        "du",
        "df",
        "basename",
        "dirname",
        "readlink",
        "realpath",
        "which",
        "type",
        "command",
        "ps",
        "pgrep",
        "md5sum",
        "sha256sum",
        "sha1sum",
        "shasum",
        "echo",
        "printf",
        "true",
        "false",
        "test",
        "less",
        "more",
        "tree",
    }
)

SAFE_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset(
        {
            "status",
            "log",
            "diff",
            "branch",
            "tag",
            "remote",
            "rev-parse",
            "show",
            "shortlog",
            "describe",
            "ls-files",
            "ls-tree",
            "cat-file",
            "reflog",
            "blame",
            "whatchanged",
        }
    ),
    "pip": frozenset({"list", "show", "freeze", "check"}),
    "npm": frozenset({"list", "ls", "info", "view", "outdated", "audit"}),
    "docker": frozenset({"ps", "images", "inspect", "logs", "stats", "info", "version"}),
    "cargo": frozenset({"tree", "metadata", "pkgid", "verify-project"}),
}

VERSION_PATTERN = re.compile(r"^\S+\s+(--version|-version|-[vV]|version)\s*$")


def has_compound_operators(command: str) -> bool:
    """Reject commands with shell operators, substitutions, and redirections.

    Catches pipes, chains, redirections, backtick substitution, and $()
    subshells. Compound commands go through the normal permission pipeline
    because each segment may have different safety characteristics.
    """
    in_single_quote = False
    in_double_quote = False
    for i, ch in enumerate(command):
        if ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif not in_single_quote and not in_double_quote:
            if ch in COMPOUND_CHARS:
                return True
            if ch == "$" and i + 1 < len(command) and command[i + 1] == "(":
                return True
    return False


def is_sandbox_auto_allowed(command: str) -> bool:
    """Check if a command is safe to auto-allow in sandbox mode.

    Returns True only for simple, read-only commands with no shell
    operators. Compound commands (pipes, chains, redirections) are
    always rejected and sent through the normal pipeline.
    """
    if not sandbox_auto_allow_enabled():
        return False

    command = command.strip()
    if not command:
        return False

    if has_compound_operators(command):
        return False

    if VERSION_PATTERN.match(command):
        return True

    parts = command.split()
    base = os.path.basename(parts[0])

    if base in SAFE_COMMANDS:
        return True

    if base in SAFE_SUBCOMMANDS and len(parts) >= 2:
        subcmd = parts[1]
        if subcmd in SAFE_SUBCOMMANDS[base]:
            return True

    return False
