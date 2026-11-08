"""Comprehensive shell command classifier for the permission pipeline.

Classifies bash/zsh commands as SEARCH, READ, WRITE, DESTRUCTIVE, or UNKNOWN
by handling compound commands, Zsh-specific threats, safety flag awareness,
quote-aware analysis, and safe redirection stripping.

Used by classify_action() in permissions.py for SANDBOXED tool source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SubcommandResult:
    raw: str
    classification: str
    destructive_warning: str | None = None


@dataclass(frozen=True, slots=True)
class CommandClassification:
    classification: str
    destructive_warning: str | None
    subcommands: tuple[SubcommandResult, ...]


PRIORITY = {"UNKNOWN": 0, "SEARCH": 1, "READ": 2, "WRITE": 3, "DESTRUCTIVE": 4}


SAFE_REDIRECTIONS = re.compile(
    r"[012]?>\s*/dev/null(?=\s|$)"
    r"|2>&1(?=\s|$)"
    r"|<\s*/dev/null(?=\s|$)"
)


def strip_safe_redirections(cmd: str) -> str:
    """Remove safe redirections (>/dev/null, 2>&1, </dev/null) before analysis.

    Trailing boundary (?=\\s|$) prevents prefix attacks like /dev/nullo.
    """
    return SAFE_REDIRECTIONS.sub("", cmd)


def strip_quotes(cmd: str) -> str:
    """Remove content inside single/double quotes, preserving structural tokens.

    Walks character-by-character tracking quote state and backslash escapes.
    Returns the 'skeleton' string for pattern matching.
    """
    result: list[str] = []
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == "'":
            i += 1
            while i < n and cmd[i] != "'":
                i += 1
            i += 1
        elif c == '"':
            i += 1
            while i < n and cmd[i] != '"':
                if cmd[i] == "\\" and i + 1 < n:
                    i += 2
                else:
                    i += 1
            i += 1
        elif c == "\\" and i + 1 < n:
            result.append(cmd[i : i + 2])
            i += 2
        else:
            result.append(c)
            i += 1
    return "".join(result)


def split_compound_commands(cmd: str) -> list[str]:
    """Public wrapper around `split_compound` for cross-module use.

    `src/sandbox/bash_paths.py` and step 4.5 in `permissions.py` need the
    same shell-operator-aware splitter to find sub-commands inside pipelines
    and sequences. Rather than duplicate the character-level scanner, this
    thin alias exposes it publicly while keeping the internal name stable.
    """
    return split_compound(cmd)


def split_compound(cmd: str) -> list[str]:
    """Split on shell operators (&&, ||, ;, |) while respecting quotes.

    Single-pass character scanner. Returns list of individual command strings.
    """
    segments: list[str] = []
    current: list[str] = []
    i = 0
    n = len(cmd)

    while i < n:
        c = cmd[i]

        if c == "'":
            current.append(c)
            i += 1
            while i < n and cmd[i] != "'":
                current.append(cmd[i])
                i += 1
            if i < n:
                current.append(cmd[i])
                i += 1
        elif c == '"':
            current.append(c)
            i += 1
            while i < n and cmd[i] != '"':
                if cmd[i] == "\\" and i + 1 < n:
                    current.append(cmd[i])
                    current.append(cmd[i + 1])
                    i += 2
                else:
                    current.append(cmd[i])
                    i += 1
            if i < n:
                current.append(cmd[i])
                i += 1
        elif c == "&" and i + 1 < n and cmd[i + 1] == "&":
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
            i += 2
        elif c == "|" and i + 1 < n and cmd[i + 1] == "|":
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
            i += 2
        elif c == "|":
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
            i += 1
        elif c == ";":
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
            i += 1
        else:
            current.append(c)
            i += 1

    seg = "".join(current).strip()
    if seg:
        segments.append(seg)

    return segments


DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.I), "git reset --hard discards uncommitted work"),
    (
        re.compile(r"\bgit\s+push\b[^;&|\n]*(--force-with-lease)\b", re.I),
        "git push --force-with-lease rewrites remote history",
    ),
    (
        re.compile(r"\bgit\s+push\b[^;&|\n]*(--force\b|-f\b)", re.I),
        "git push --force rewrites remote history",
    ),
    (
        re.compile(r"\bgit\s+clean\s+(?!.*(-n\b|--dry-run\b)).*-[a-zA-Z]*f", re.I),
        "git clean -f deletes untracked files",
    ),
    (
        re.compile(r"\bgit\s+checkout\s+(--\s+)?\.(?=\s|$)", re.I),
        "git checkout . discards all changes",
    ),
    (
        re.compile(r"\bgit\s+restore\s+(--\s+)?\.(?=\s|$)", re.I),
        "git restore . discards all changes",
    ),
    (
        re.compile(r"\bgit\s+stash\s+(drop|clear)\b", re.I),
        "git stash drop/clear loses stashed work",
    ),
    (
        re.compile(r"\bgit\s+branch\s+(-D\b|--delete\s+--force\b|--force\s+--delete\b)", re.I),
        "git branch force-delete removes branch",
    ),
    (
        re.compile(r"\bgit\s+(commit|push|merge)\s+.*--no-verify\b", re.I),
        "--no-verify bypasses safety hooks",
    ),
    (re.compile(r"\bgit\s+commit\s+.*--amend\b", re.I), "git commit --amend rewrites last commit"),
    (
        re.compile(r"(?:^|[;&|\n]\s*)rm\s+.*-[a-zA-Z]*r[a-zA-Z]*f", re.I),
        "rm -rf recursively force-deletes",
    ),
    (
        re.compile(r"(?:^|[;&|\n]\s*)rm\s+.*-[a-zA-Z]*f[a-zA-Z]*r", re.I),
        "rm -fr recursively force-deletes",
    ),
    (
        re.compile(r"(?:^|[;&|\n]\s*)rm\s+(?!.*-[a-zA-Z]*r).*-[a-zA-Z]*f", re.I),
        "rm -f force-deletes without confirmation",
    ),
    (
        re.compile(r"(?:^|[;&|\n]\s*)rm\s+(?!.*-[a-zA-Z]*f).*-[a-zA-Z]*r", re.I),
        "rm -r recursively deletes",
    ),
    (
        re.compile(r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b", re.I),
        "DROP/TRUNCATE destroys database objects",
    ),
    (
        re.compile(r"\bDELETE\s+FROM\b(?!.*\bWHERE\b)", re.I),
        "DELETE FROM without WHERE deletes all rows",
    ),
    (re.compile(r"\bkubectl\s+delete\b", re.I), "kubectl delete removes cluster resources"),
    (re.compile(r"\bterraform\s+destroy\b", re.I), "terraform destroy tears down infrastructure"),
]

PIPE_TO_SHELL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b(curl|wget)\b.*\|\s*(sh|bash|zsh)\b", re.I),
        "piping download to shell executes arbitrary code",
    ),
]

ZSH_DANGEROUS_CMDS = (
    "zmodload",
    "emulate",
    "sysopen",
    "sysread",
    "syswrite",
    "sysseek",
    "zpty",
    "ztcp",
    "zsocket",
    "mapfile",
    "zf_rm",
    "zf_mv",
    "zf_ln",
    "zf_chmod",
    "zf_chown",
    "zf_mkdir",
    "zf_rmdir",
    "zf_chgrp",
)
ZSH_CMDS_PATTERN = re.compile(r"\b(" + "|".join(re.escape(c) for c in ZSH_DANGEROUS_CMDS) + r")\b")

ZSH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<![=\w])=[a-zA-Z_]\w*"), "Zsh =cmd expansion can execute arbitrary commands"),
    (ZSH_CMDS_PATTERN, "Zsh builtin bypasses sandbox"),
    (re.compile(r"<\(|>\(|=\("), "process substitution can execute hidden commands"),
    (re.compile(r"\(e:|(\(\+)"), "Zsh glob qualifier can execute code"),
    (re.compile(r"\}\s*always\s*\{"), "Zsh always block alters control flow"),
    (re.compile(r"\bfc\s+-[a-zA-Z]*e"), "fc -e can execute arbitrary commands via editor"),
]

JQ_SYSTEM = re.compile(r"\bjq\b.*\bsystem\s*\(", re.I)


WRITE_PATTERNS = re.compile(
    r"("
    r"\becho\s+.*>"
    r"|\btee\s"
    r"|\bcp\s"
    r"|\bmv\s"
    r"|\bmkdir\s"
    r"|\btouch\s"
    r"|\bchmod\s"
    r"|\bchown\s"
    r"|\bgit\s+(commit|add|stash|merge|rebase|cherry-pick)\b"
    r"|\bpatch\s"
    r"|\b(npm|pip|cargo|go)\s+install\b"
    r")",
    re.I,
)


READ_PATTERNS = re.compile(
    r"^\s*git\s+(status|log|diff|show|branch|remote|tag|describe|rev-parse|shortlog)\b",
    re.I,
)


SEARCH_PATTERNS = re.compile(
    r"^\s*(grep|find|ls|cat|head|tail|wc|rg|ag|fd|which|type|echo|printf|file|stat|du|df)\b",
    re.I,
)


CD_CMD = re.compile(r"^\s*cd\s", re.I)
GIT_CMD = re.compile(r"\bgit\b", re.I)
WRITE_TO_GIT_INTERNALS = re.compile(
    r"(>\s*\.git/|cp\s+.*\.git/|mv\s+.*\.git/|tee\s+.*\.git/)", re.I
)


def classify_single(cmd: str) -> SubcommandResult:
    """Classify a single (non-compound) command."""
    raw = cmd
    cleaned = strip_safe_redirections(cmd)
    pre_quote = cleaned
    stripped = strip_quotes(cleaned)

    for pattern, warning in DESTRUCTIVE_PATTERNS:
        if pattern.search(stripped):
            return SubcommandResult(
                raw=raw, classification="DESTRUCTIVE", destructive_warning=warning
            )

    for pattern, warning in ZSH_PATTERNS:
        if pattern.search(stripped):
            return SubcommandResult(
                raw=raw, classification="DESTRUCTIVE", destructive_warning=warning
            )

    if JQ_SYSTEM.search(pre_quote):
        return SubcommandResult(
            raw=raw,
            classification="DESTRUCTIVE",
            destructive_warning="jq system() executes shell commands",
        )

    if WRITE_PATTERNS.search(stripped):
        return SubcommandResult(raw=raw, classification="WRITE")

    if READ_PATTERNS.match(stripped):
        return SubcommandResult(raw=raw, classification="READ")

    if SEARCH_PATTERNS.match(stripped):
        return SubcommandResult(raw=raw, classification="SEARCH")

    return SubcommandResult(raw=raw, classification="UNKNOWN")


def classify_command(command: str) -> CommandClassification:
    """Classify a shell command, handling compound commands.

    Splits on shell operators (&&, ||, ;, |), classifies each subcommand,
    applies cross-subcommand rules, returns the most dangerous classification.
    """
    if not command or not command.strip():
        return CommandClassification(
            classification="UNKNOWN",
            destructive_warning=None,
            subcommands=(),
        )

    full_stripped = strip_quotes(strip_safe_redirections(command))
    for pattern, warning in PIPE_TO_SHELL_PATTERNS:
        if pattern.search(full_stripped):
            return CommandClassification(
                classification="DESTRUCTIVE",
                destructive_warning=warning,
                subcommands=(
                    SubcommandResult(
                        raw=command, classification="DESTRUCTIVE", destructive_warning=warning
                    ),
                ),
            )

    segments = split_compound(command)
    results = [classify_single(seg) for seg in segments]

    has_cd = any(CD_CMD.match(r.raw) for r in results)
    has_git = any(GIT_CMD.search(r.raw) for r in results)
    has_write_to_git = any(WRITE_TO_GIT_INTERNALS.search(r.raw) for r in results)

    compound_warning: str | None = None
    if has_cd and has_git:
        compound_warning = "cd + git compound may escape sandbox via hooks"
    elif has_write_to_git and has_git:
        compound_warning = "writing to .git internals alongside git execution"

    if compound_warning:
        return CommandClassification(
            classification="DESTRUCTIVE",
            destructive_warning=compound_warning,
            subcommands=tuple(results),
        )

    best = results[0]
    for r in results[1:]:
        if PRIORITY.get(r.classification, 0) > PRIORITY.get(best.classification, 0):
            best = r

    return CommandClassification(
        classification=best.classification,
        destructive_warning=best.destructive_warning,
        subcommands=tuple(results),
    )
