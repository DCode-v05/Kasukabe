"""Read-only command allowlist for READ_ONLY permission mode.

When PermissionMode.READ_ONLY is active, the classifier may return UNKNOWN/EXECUTE
for commands that are actually safe reads (git rev-parse, python --version, ps aux).
This module provides a rescue path: per-command, per-flag validation to prove a
command is read-only before the denial fires.

Used by check_permission() in permissions.py as a rescue path in the READ_ONLY branch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from src.sandbox.command_classifier import split_compound, strip_safe_redirections


@dataclass(frozen=True)
class CommandConfig:
    safe_flags: dict[str, str] = field(
        default_factory=dict
    )
    regex: re.Pattern[str] | None = None
    dangerous_callback: Callable[[str, list[str]], bool] | None = None
    respects_double_dash: bool = True


def tokenize(cmd: str) -> list[str] | None:
    """Split command into tokens respecting quotes. Returns None on unbalanced quotes."""
    tokens: list[str] = []
    current: list[str] = []
    i = 0
    n = len(cmd)

    while i < n:
        c = cmd[i]
        if c in (" ", "\t"):
            if current:
                tokens.append("".join(current))
                current = []
            i += 1
        elif c == "'":
            i += 1
            while i < n and cmd[i] != "'":
                current.append(cmd[i])
                i += 1
            if i >= n:
                return None
            i += 1
        elif c == '"':
            i += 1
            while i < n and cmd[i] != '"':
                if cmd[i] == "\\" and i + 1 < n:
                    current.append(cmd[i + 1])
                    i += 2
                else:
                    current.append(cmd[i])
                    i += 1
            if i >= n:
                return None
            i += 1
        elif c == "\\" and i + 1 < n:
            current.append(cmd[i + 1])
            i += 2
        else:
            current.append(c)
            i += 1

    if current:
        tokens.append("".join(current))
    return tokens


FLAG_RE = re.compile(r"^-[a-zA-Z0-9]")
BRACE_EXPANSION = re.compile(r"\{[^}]*[,.]")


NUMBER_RE = re.compile(r"-?\d+$")
GIT_NUMERIC_SHORT = re.compile(r"-\d+$")


def validate_flag_arg(value: str, arg_type: str) -> bool:
    if arg_type == "number":
        return bool(NUMBER_RE.match(value))
    if arg_type == "char":
        return len(value) == 1
    return True


def validate_flags(tokens: list[str], start: int, config: CommandConfig) -> bool:
    """Validate all flags in tokens[start:] against config.safe_flags."""
    i = start
    while i < len(tokens):
        tok = tokens[i]

        if "$" in tok:
            return False
        if BRACE_EXPANSION.search(tok):
            return False

        if tok == "--":
            if config.respects_double_dash:
                for j in range(i + 1, len(tokens)):
                    if "$" in tokens[j] or BRACE_EXPANSION.search(tokens[j]):
                        return False
                return True
            i += 1
            continue

        if tok.startswith("-") and len(tok) > 1 and tok != "-":
            flag = tok
            inline_value = None
            has_equals = False
            if "=" in tok and tok.startswith("--"):
                eq_pos = tok.index("=")
                flag = tok[:eq_pos]
                inline_value = tok[eq_pos + 1 :]
                has_equals = True

            if flag in config.safe_flags:
                arg_type = config.safe_flags[flag]
                if arg_type == "none":
                    if has_equals:
                        return False
                    i += 1
                    continue
                if inline_value is not None:
                    if not validate_flag_arg(inline_value, arg_type):
                        return False
                    i += 1
                    continue
                if i + 1 >= len(tokens):
                    return False
                next_tok = tokens[i + 1]
                if "$" in next_tok or BRACE_EXPANSION.search(next_tok):
                    return False
                if not validate_flag_arg(next_tok, arg_type):
                    return False
                i += 2
                continue

            if not tok.startswith("--") and len(tok) > 2:
                all_valid = True
                for ch in tok[1:]:
                    short = f"-{ch}"
                    if short not in config.safe_flags or config.safe_flags[short] != "none":
                        all_valid = False
                        break
                if all_valid:
                    i += 1
                    continue

            if not tok.startswith("--") and GIT_NUMERIC_SHORT.match(tok):
                if "-n" in config.safe_flags:
                    i += 1
                    continue

            return False
        else:
            i += 1

    return True


def reject_positionals_unless_list(raw: str, args: list[str]) -> bool:
    """Reject positional args unless --list/-l present (prevents creation)."""
    has_list = any(a in ("--list", "-l") for a in args)
    if has_list:
        return False
    return any(not a.startswith("-") and a != "--" for a in args)


def reject_any_positional(raw: str, args: list[str]) -> bool:
    """Reject any positional arg."""
    return any(not a.startswith("-") for a in args)


def git_reflog_dangerous(raw: str, args: list[str]) -> bool:
    """Reject expire/delete/exists subcommands."""
    dangerous = {"expire", "delete", "exists"}
    for a in args:
        if a in dangerous:
            return True
    return False


def ps_dangerous(raw: str, args: list[str]) -> bool:
    """Reject BSD-style 'e' flag (shows environment variables)."""
    for a in args:
        if not a.startswith("-") and re.fullmatch(r"[a-zA-Z]*e[a-zA-Z]*", a):
            return True
    return False


def hostname_dangerous(raw: str, args: list[str]) -> bool:
    """Reject positional args (would set hostname)."""
    for a in args:
        if not a.startswith("-"):
            return True
    return False


def date_dangerous(raw: str, args: list[str]) -> bool:
    """Reject positionals not starting with + (format strings are safe, timestamps set time)."""
    for a in args:
        if not a.startswith("-") and not a.startswith("+"):
            return True
    return False


TPUT_DANGEROUS_CAPS = frozenset(
    {
        "init",
        "reset",
        "rs1",
        "rs2",
        "rs3",
        "is1",
        "is2",
        "is3",
        "iprog",
        "if",
        "rf",
        "clear",
        "flash",
        "mc0",
        "mc4",
        "mc5",
        "mc5i",
        "mc5p",
        "pfkey",
        "pfloc",
        "pfx",
        "pfxl",
        "smcup",
        "rmcup",
    }
)


def tput_dangerous(raw: str, args: list[str]) -> bool:
    """Reject dangerous terminal capabilities."""
    for a in args:
        if not a.startswith("-") and a in TPUT_DANGEROUS_CAPS:
            return True
    return False


GIT_LOG_DISPLAY_FLAGS: dict[str, str] = {
    "--oneline": "none",
    "--graph": "none",
    "--decorate": "none",
    "--all": "none",
    "--first-parent": "none",
    "--ancestry-path": "none",
    "-n": "number",
    "--max-count": "number",
    "--since": "string",
    "--until": "string",
    "--after": "string",
    "--before": "string",
    "--author": "string",
    "--committer": "string",
    "--grep": "string",
    "--format": "string",
    "--pretty": "string",
    "--date": "string",
    "--stat": "none",
    "--shortstat": "none",
    "--numstat": "none",
    "--name-only": "none",
    "--name-status": "none",
    "--patch": "none",
    "-p": "none",
    "--color": "string",
    "--no-color": "none",
    "--abbrev-commit": "none",
    "--no-abbrev-commit": "none",
    "--diff-filter": "string",
    "-S": "string",
    "-G": "string",
    "--no-merges": "none",
    "--merges": "none",
    "--reverse": "none",
    "--topo-order": "none",
    "--date-order": "none",
    "--left-right": "none",
    "--cherry": "none",
    "--cherry-pick": "none",
    "--no-walk": "none",
    "--walk-reflogs": "none",
    "--count": "none",
    "-1": "none",
}

GIT_DIFF_FLAGS: dict[str, str] = {
    "--stat": "none",
    "--shortstat": "none",
    "--numstat": "none",
    "--name-only": "none",
    "--name-status": "none",
    "--cached": "none",
    "--staged": "none",
    "--color": "string",
    "--no-color": "none",
    "--word-diff": "string",
    "--word-diff-regex": "string",
    "-U": "number",
    "--unified": "number",
    "--diff-algorithm": "string",
    "--diff-filter": "string",
    "-S": "string",
    "-G": "string",
    "-O": "string",
    "--patch": "none",
    "-p": "none",
    "--check": "none",
    "--summary": "none",
    "--abbrev": "number",
    "--no-ext-diff": "none",
    "--binary": "none",
    "--full-index": "none",
    "--compact-summary": "none",
    "--no-renames": "none",
    "-M": "string",
    "-C": "string",
    "--relative": "string",
    "--src-prefix": "string",
    "--dst-prefix": "string",
    "--no-prefix": "none",
    "-R": "none",
}

CHECKSUM_FLAGS: dict[str, str] = {
    "-b": "none",
    "--binary": "none",
    "-t": "none",
    "--text": "none",
    "-c": "none",
    "--check": "none",
    "--tag": "none",
    "-z": "none",
    "--zero": "none",
    "--strict": "none",
    "--quiet": "none",
    "-w": "none",
    "--warn": "none",
    "--status": "none",
    "--ignore-missing": "none",
}


COMMAND_ALLOWLIST: dict[str, CommandConfig] = {
    "git status": CommandConfig(
        safe_flags={
            "--short": "none",
            "-s": "none",
            "--branch": "none",
            "-b": "none",
            "--porcelain": "none",
            "--long": "none",
            "-v": "none",
            "--verbose": "none",
            "--untracked-files": "string",
            "-u": "string",
            "--ignored": "none",
            "--ignore-submodules": "string",
            "-z": "none",
            "--column": "string",
            "--no-column": "none",
            "--ahead-behind": "none",
            "--no-ahead-behind": "none",
            "--renames": "none",
            "--no-renames": "none",
        }
    ),
    "git log": CommandConfig(safe_flags=GIT_LOG_DISPLAY_FLAGS),
    "git diff": CommandConfig(safe_flags=GIT_DIFF_FLAGS),
    "git show": CommandConfig(
        safe_flags={
            "--stat": "none",
            "--name-only": "none",
            "--name-status": "none",
            "--format": "string",
            "--pretty": "string",
            "--color": "string",
            "--no-color": "none",
            "--patch": "none",
            "-p": "none",
            "--abbrev-commit": "none",
            "--diff-filter": "string",
            "--shortstat": "none",
            "--numstat": "none",
            "-S": "string",
            "-G": "string",
        }
    ),
    "git branch": CommandConfig(
        safe_flags={
            "-a": "none",
            "--all": "none",
            "-r": "none",
            "--remotes": "none",
            "-v": "none",
            "--verbose": "none",
            "--list": "none",
            "-l": "none",
            "--contains": "string",
            "--merged": "string",
            "--no-merged": "string",
            "--sort": "string",
            "--format": "string",
            "--color": "string",
            "--no-color": "none",
            "--abbrev": "number",
            "--no-abbrev": "none",
        },
        dangerous_callback=reject_positionals_unless_list,
    ),
    "git remote": CommandConfig(
        safe_flags={"-v": "none", "--verbose": "none"},
        dangerous_callback=reject_any_positional,
    ),
    "git remote show": CommandConfig(safe_flags={"-n": "none"}),
    "git tag": CommandConfig(
        safe_flags={
            "-l": "none",
            "--list": "none",
            "-n": "number",
            "--contains": "string",
            "--sort": "string",
            "--format": "string",
            "--merged": "string",
            "--no-merged": "string",
            "--color": "string",
            "--no-color": "none",
            "--column": "string",
            "--no-column": "none",
        },
        dangerous_callback=reject_positionals_unless_list,
    ),
    "git describe": CommandConfig(
        safe_flags={
            "--tags": "none",
            "--long": "none",
            "--always": "none",
            "--abbrev": "number",
            "--match": "string",
            "--exclude": "string",
            "--first-parent": "none",
            "--dirty": "none",
            "--broken": "none",
            "--candidates": "number",
            "--all": "none",
        }
    ),
    "git rev-parse": CommandConfig(
        safe_flags={
            "--verify": "none",
            "--short": "none",
            "--abbrev-ref": "none",
            "--show-toplevel": "none",
            "--git-dir": "none",
            "--is-inside-work-tree": "none",
            "--is-bare-repository": "none",
            "--show-cdup": "none",
            "--show-prefix": "none",
            "--symbolic": "none",
            "--symbolic-full-name": "none",
            "--git-common-dir": "none",
            "--show-superproject-working-tree": "none",
            "--absolute-git-dir": "none",
            "--is-inside-git-dir": "none",
            "--sq": "none",
            "--not": "none",
            "--all": "none",
            "--branches": "string",
            "--tags": "string",
            "--remotes": "string",
        }
    ),
    "git blame": CommandConfig(
        safe_flags={
            "-L": "string",
            "--porcelain": "none",
            "-p": "none",
            "--date": "string",
            "-w": "none",
            "--color-lines": "none",
            "--color-by-age": "none",
            "-e": "none",
            "--show-email": "none",
            "--line-porcelain": "none",
            "-s": "none",
            "-n": "none",
            "--show-number": "none",
            "-f": "none",
            "--show-name": "none",
        }
    ),
    "git ls-files": CommandConfig(
        safe_flags={
            "--cached": "none",
            "-c": "none",
            "--deleted": "none",
            "-d": "none",
            "--modified": "none",
            "-m": "none",
            "--others": "none",
            "-o": "none",
            "--ignored": "none",
            "-i": "none",
            "--stage": "none",
            "-s": "none",
            "--exclude": "string",
            "-x": "string",
            "--exclude-from": "string",
            "-X": "string",
            "--exclude-standard": "none",
            "-z": "none",
            "--error-unmatch": "none",
            "--full-name": "none",
            "--abbrev": "number",
        }
    ),
    "git ls-tree": CommandConfig(
        safe_flags={
            "-r": "none",
            "--name-only": "none",
            "--name-status": "none",
            "--long": "none",
            "-l": "none",
            "-d": "none",
            "-t": "none",
            "-z": "none",
            "--full-name": "none",
            "--full-tree": "none",
            "--abbrev": "number",
        }
    ),
    "git shortlog": CommandConfig(
        safe_flags={
            "-s": "none",
            "-n": "none",
            "-e": "none",
            "--group": "string",
            "--format": "string",
            "--all": "none",
            "--no-merges": "none",
        }
    ),
    "git rev-list": CommandConfig(
        safe_flags={
            "--count": "none",
            "--all": "none",
            "--first-parent": "none",
            "--ancestry-path": "none",
            "--since": "string",
            "--until": "string",
            "--after": "string",
            "--before": "string",
            "--author": "string",
            "--grep": "string",
            "--max-count": "number",
            "-n": "number",
            "--format": "string",
            "--no-walk": "none",
            "--no-merges": "none",
            "--merges": "none",
            "--reverse": "none",
            "--topo-order": "none",
            "--date-order": "none",
            "--objects": "none",
        }
    ),
    "git merge-base": CommandConfig(
        safe_flags={
            "--is-ancestor": "none",
            "--fork-point": "none",
            "--octopus": "none",
            "--independent": "none",
            "--all": "none",
        }
    ),
    "git cat-file": CommandConfig(
        safe_flags={
            "-t": "none",
            "-s": "none",
            "-p": "none",
            "-e": "none",
            "--batch-check": "none",
            "--textconv": "none",
        }
    ),
    "git for-each-ref": CommandConfig(
        safe_flags={
            "--format": "string",
            "--sort": "string",
            "--count": "number",
            "--contains": "string",
            "--merged": "string",
            "--no-merged": "string",
            "--points-at": "string",
            "--color": "string",
            "--no-color": "none",
        }
    ),
    "git grep": CommandConfig(
        safe_flags={
            "-n": "none",
            "--line-number": "none",
            "-c": "none",
            "--count": "none",
            "-l": "none",
            "--files-with-matches": "none",
            "--name-only": "none",
            "-L": "none",
            "--files-without-match": "none",
            "-i": "none",
            "--ignore-case": "none",
            "-w": "none",
            "--word-regexp": "none",
            "-e": "string",
            "--and": "none",
            "--or": "none",
            "--not": "none",
            "-E": "none",
            "-P": "none",
            "-F": "none",
            "--all-match": "none",
            "--color": "string",
            "--no-color": "none",
            "-C": "number",
            "-A": "number",
            "-B": "number",
            "--break": "none",
            "--heading": "none",
            "-h": "none",
            "-H": "none",
            "--full-name": "none",
            "--untracked": "none",
            "--no-index": "none",
            "-z": "none",
            "-p": "none",
            "--show-function": "none",
        }
    ),
    "git stash show": CommandConfig(
        safe_flags={
            "--stat": "none",
            "--patch": "none",
            "-p": "none",
            "--color": "string",
            "--no-color": "none",
            "--name-only": "none",
            "--name-status": "none",
            "--diff-filter": "string",
            "-U": "number",
        }
    ),
    "git stash list": CommandConfig(safe_flags=GIT_LOG_DISPLAY_FLAGS),
    "git reflog": CommandConfig(
        safe_flags=GIT_LOG_DISPLAY_FLAGS,
        dangerous_callback=git_reflog_dangerous,
    ),
    "git ls-remote": CommandConfig(
        safe_flags={
            "--branches": "none",
            "--tags": "none",
            "--heads": "none",
            "--refs": "none",
            "--quiet": "none",
            "-q": "none",
            "--sort": "string",
            "--symref": "none",
        }
    ),
    "git config": CommandConfig(
        safe_flags={
            "--get": "none",
            "--get-all": "none",
            "--list": "none",
            "-l": "none",
            "--local": "none",
            "--global": "none",
            "--system": "none",
            "--type": "string",
            "--bool": "none",
            "--int": "none",
            "--bool-or-int": "none",
            "--path": "none",
            "--show-origin": "none",
            "--show-scope": "none",
            "--name-only": "none",
            "-z": "none",
            "--null": "none",
        },
        regex=re.compile(r"(--get|--get-all|--list\b|-l\b)"),
    ),
    "git worktree list": CommandConfig(
        safe_flags={
            "--porcelain": "none",
            "-v": "none",
            "--verbose": "none",
        }
    ),
    "git name-rev": CommandConfig(
        safe_flags={
            "--tags": "none",
            "--refs": "string",
            "--no-undefined": "none",
            "--always": "none",
            "--name-only": "none",
            "--stdin": "none",
        }
    ),
    "grep": CommandConfig(
        safe_flags={
            "-i": "none",
            "--ignore-case": "none",
            "-v": "none",
            "--invert-match": "none",
            "-c": "none",
            "--count": "none",
            "-l": "none",
            "--files-with-matches": "none",
            "-L": "none",
            "--files-without-match": "none",
            "-n": "none",
            "--line-number": "none",
            "-H": "none",
            "--with-filename": "none",
            "-h": "none",
            "--no-filename": "none",
            "-o": "none",
            "--only-matching": "none",
            "-q": "none",
            "--quiet": "none",
            "--silent": "none",
            "-s": "none",
            "--no-messages": "none",
            "-w": "none",
            "--word-regexp": "none",
            "-x": "none",
            "--line-regexp": "none",
            "-E": "none",
            "--extended-regexp": "none",
            "-F": "none",
            "--fixed-strings": "none",
            "-G": "none",
            "--basic-regexp": "none",
            "-P": "none",
            "--perl-regexp": "none",
            "-r": "none",
            "-R": "none",
            "--recursive": "none",
            "-e": "string",
            "--regexp": "string",
            "-f": "string",
            "--file": "string",
            "--include": "string",
            "--exclude": "string",
            "--exclude-dir": "string",
            "--include-dir": "string",
            "-m": "number",
            "--max-count": "number",
            "-A": "number",
            "--after-context": "number",
            "-B": "number",
            "--before-context": "number",
            "-C": "number",
            "--context": "number",
            "--color": "string",
            "--colour": "string",
            "--label": "string",
            "-T": "none",
            "--initial-tab": "none",
            "-Z": "none",
            "--null": "none",
            "-z": "none",
            "--null-data": "none",
            "-a": "none",
            "--text": "none",
            "-b": "none",
            "--byte-offset": "none",
        }
    ),
    "rg": CommandConfig(
        safe_flags={
            "-i": "none",
            "--ignore-case": "none",
            "-S": "none",
            "--smart-case": "none",
            "-s": "none",
            "--case-sensitive": "none",
            "-v": "none",
            "--invert-match": "none",
            "-c": "none",
            "--count": "none",
            "--count-matches": "none",
            "-l": "none",
            "--files-with-matches": "none",
            "--files-without-match": "none",
            "--files": "none",
            "-n": "none",
            "--line-number": "none",
            "-N": "none",
            "--no-line-number": "none",
            "-H": "none",
            "--with-filename": "none",
            "--no-filename": "none",
            "-o": "none",
            "--only-matching": "none",
            "-q": "none",
            "--quiet": "none",
            "-w": "none",
            "--word-regexp": "none",
            "-x": "none",
            "--line-regexp": "none",
            "-F": "none",
            "--fixed-strings": "none",
            "-U": "none",
            "--multiline": "none",
            "--multiline-dotall": "none",
            "-e": "string",
            "--regexp": "string",
            "-f": "string",
            "--file": "string",
            "-g": "string",
            "--glob": "string",
            "--iglob": "string",
            "-t": "string",
            "--type": "string",
            "-T": "string",
            "--type-not": "string",
            "-m": "number",
            "--max-count": "number",
            "--max-depth": "number",
            "--maxdepth": "number",
            "-d": "number",
            "--max-filesize": "string",
            "-A": "number",
            "--after-context": "number",
            "-B": "number",
            "--before-context": "number",
            "-C": "number",
            "--context": "number",
            "--color": "string",
            "--colours": "string",
            "--colors": "string",
            "-p": "none",
            "--pretty": "none",
            "-j": "number",
            "--threads": "number",
            "--heading": "none",
            "--no-heading": "none",
            "-M": "number",
            "--max-columns": "number",
            "--max-columns-preview": "none",
            "-0": "none",
            "--null": "none",
            "--null-data": "none",
            "-a": "none",
            "--text": "none",
            "-b": "none",
            "--byte-offset": "none",
            "--hidden": "none",
            "--no-hidden": "none",
            "--follow": "none",
            "--no-follow": "none",
            "--no-ignore": "none",
            "--no-ignore-dot": "none",
            "--no-ignore-parent": "none",
            "--no-ignore-vcs": "none",
            "--no-ignore-global": "none",
            "--no-ignore-exclude": "none",
            "--no-ignore-files": "none",
            "-z": "none",
            "--search-zip": "none",
            "--sort": "string",
            "--sortr": "string",
            "--path-separator": "string",
            "--trim": "none",
            "--one-file-system": "none",
            "--stats": "none",
            "-E": "string",
            "--encoding": "string",
            "--engine": "string",
            "--binary": "none",
            "--no-binary": "none",
            "--json": "none",
            "--pcre2": "none",
            "--auto-hybrid-regex": "none",
            "--crlf": "none",
            "--no-crlf": "none",
            "--mmap": "none",
            "--no-mmap": "none",
            "--no-config": "none",
            "--no-pcre2-unicode": "none",
            "--pcre2-version": "none",
            "--debug": "none",
            "--trace": "none",
            "--generate": "string",
            "--type-list": "none",
            "--type-add": "string",
            "--type-clear": "string",
            "--no-unicode": "none",
            "--unicode": "none",
            "--passthru": "none",
            "--pre": "string",
            "--pre-glob": "string",
            "--block-buffered": "none",
            "--line-buffered": "none",
            "--field-match-separator": "string",
            "--field-context-separator": "string",
            "--hyperlink-format": "string",
            "--hostname-bin": "string",
            "-L": "none",
        }
    ),
    "find": CommandConfig(
        safe_flags={},
        dangerous_callback=lambda raw, args: bool(
            re.search(r"-(?:delete|exec|execdir|ok|okdir|fprint|fls|fprintf)\b", raw)
        ),
    ),
    "fd": CommandConfig(
        safe_flags={
            "-H": "none",
            "--hidden": "none",
            "-I": "none",
            "--no-ignore": "none",
            "--no-ignore-vcs": "none",
            "--no-ignore-parent": "none",
            "-u": "none",
            "--unrestricted": "none",
            "-s": "none",
            "--case-sensitive": "none",
            "-i": "none",
            "--ignore-case": "none",
            "-g": "none",
            "--glob": "none",
            "--regex": "none",
            "-F": "none",
            "--fixed-strings": "none",
            "-a": "none",
            "--absolute-path": "none",
            "-L": "none",
            "--follow": "none",
            "-p": "none",
            "--full-path": "none",
            "-0": "none",
            "--print0": "none",
            "-1": "none",
            "--one-file-system": "none",
            "--strip-cwd-prefix": "none",
            "--prune": "none",
            "--show-errors": "none",
            "-d": "number",
            "--max-depth": "number",
            "--min-depth": "number",
            "--exact-depth": "number",
            "-t": "string",
            "--type": "string",
            "-e": "string",
            "--extension": "string",
            "-E": "string",
            "--exclude": "string",
            "--ignore-file": "string",
            "-S": "string",
            "--size": "string",
            "--changed-within": "string",
            "--changed-before": "string",
            "--newer": "string",
            "--older": "string",
            "-o": "string",
            "--owner": "string",
            "--color": "string",
            "-c": "string",
            "-j": "number",
            "--threads": "number",
            "--max-results": "number",
            "--base-directory": "string",
            "--path-separator": "string",
            "--search-path": "string",
            "--format": "string",
            "--and": "string",
            "--max-buffer-time": "string",
            "--no-hidden": "none",
            "--no-follow": "none",
        }
    ),
    "ag": CommandConfig(
        safe_flags={
            "-i": "none",
            "--ignore-case": "none",
            "-s": "none",
            "--case-sensitive": "none",
            "-S": "none",
            "--smart-case": "none",
            "-v": "none",
            "--invert-match": "none",
            "-c": "none",
            "--count": "none",
            "-l": "none",
            "--files-with-matches": "none",
            "-L": "none",
            "--files-without-match": "none",
            "-n": "none",
            "--line-number": "none",
            "--no-numbers": "none",
            "-H": "none",
            "--heading": "none",
            "--no-heading": "none",
            "-o": "none",
            "--only-matching": "none",
            "-Q": "none",
            "--literal": "none",
            "-w": "none",
            "--word-regexp": "none",
            "-G": "string",
            "--file-search-regex": "string",
            "-g": "string",
            "--filename-pattern": "string",
            "--ignore": "string",
            "--ignore-dir": "string",
            "-m": "number",
            "--max-count": "number",
            "--depth": "number",
            "-A": "number",
            "--after": "number",
            "-B": "number",
            "--before": "number",
            "-C": "number",
            "--context": "number",
            "--color": "none",
            "--nocolor": "none",
            "-a": "none",
            "--all-text": "none",
            "-u": "none",
            "--unrestricted": "none",
            "-U": "none",
            "--skip-vcs-ignores": "none",
            "-r": "none",
            "--recurse": "none",
            "--hidden": "none",
            "-f": "none",
            "--follow": "none",
            "-z": "none",
            "--search-zip": "none",
            "--stats": "none",
            "--stats-only": "none",
            "-0": "none",
            "--null": "none",
            "--print0": "none",
            "-1": "none",
            "--one-device": "none",
            "-t": "string",
            "--all-types": "none",
            "--list-file-types": "none",
            "-p": "string",
            "--path-to-ignore": "string",
        }
    ),
    "cat": CommandConfig(
        safe_flags={
            "-n": "none",
            "--number": "none",
            "-b": "none",
            "--number-nonblank": "none",
            "-s": "none",
            "--squeeze-blank": "none",
            "-v": "none",
            "--show-nonprinting": "none",
            "-E": "none",
            "--show-ends": "none",
            "-T": "none",
            "--show-tabs": "none",
            "-A": "none",
            "--show-all": "none",
            "-e": "none",
            "-t": "none",
        }
    ),
    "head": CommandConfig(
        safe_flags={
            "-n": "number",
            "--lines": "number",
            "-c": "number",
            "--bytes": "number",
            "-q": "none",
            "--quiet": "none",
            "--silent": "none",
            "-v": "none",
            "--verbose": "none",
            "-z": "none",
            "--zero-terminated": "none",
        }
    ),
    "tail": CommandConfig(
        safe_flags={
            "-n": "number",
            "--lines": "number",
            "-c": "number",
            "--bytes": "number",
            "-f": "none",
            "--follow": "none",
            "-F": "none",
            "-q": "none",
            "--quiet": "none",
            "--silent": "none",
            "-v": "none",
            "--verbose": "none",
            "-z": "none",
            "--zero-terminated": "none",
            "--pid": "number",
            "-s": "number",
            "--sleep-interval": "number",
            "--max-unchanged-stats": "number",
            "--retry": "none",
        }
    ),
    "wc": CommandConfig(
        safe_flags={
            "-l": "none",
            "--lines": "none",
            "-w": "none",
            "--words": "none",
            "-c": "none",
            "--bytes": "none",
            "-m": "none",
            "--chars": "none",
            "-L": "none",
            "--max-line-length": "none",
            "--files0-from": "string",
            "--total": "string",
        }
    ),
    "file": CommandConfig(
        safe_flags={
            "-b": "none",
            "--brief": "none",
            "-i": "none",
            "--mime": "none",
            "--mime-type": "none",
            "--mime-encoding": "none",
            "-L": "none",
            "--dereference": "none",
            "-z": "none",
            "--uncompress": "none",
            "-k": "none",
            "--keep-going": "none",
            "-r": "none",
            "--raw": "none",
            "-N": "none",
            "--no-pad": "none",
            "-p": "none",
            "--preserve-date": "none",
            "-s": "none",
            "--special-files": "none",
            "-0": "none",
            "--print0": "none",
            "-e": "string",
            "--exclude": "string",
            "-F": "string",
            "--separator": "string",
            "-f": "string",
            "--magic-file": "string",
            "-C": "none",
            "--compile": "none",
        }
    ),
    "stat": CommandConfig(
        safe_flags={
            "-L": "none",
            "--dereference": "none",
            "-f": "none",
            "--file-system": "none",
            "-c": "string",
            "--format": "string",
            "--printf": "string",
            "-t": "none",
            "--terse": "none",
        }
    ),
    "du": CommandConfig(
        safe_flags={
            "-h": "none",
            "--human-readable": "none",
            "-s": "none",
            "--summarize": "none",
            "-a": "none",
            "--all": "none",
            "-c": "none",
            "--total": "none",
            "-b": "none",
            "--bytes": "none",
            "-k": "none",
            "-m": "none",
            "-d": "number",
            "--max-depth": "number",
            "--apparent-size": "none",
            "-S": "none",
            "--separate-dirs": "none",
            "-x": "none",
            "--one-file-system": "none",
            "-L": "none",
            "--dereference": "none",
            "-0": "none",
            "--null": "none",
            "--exclude": "string",
            "-X": "string",
            "--exclude-from": "string",
            "--si": "none",
            "--time": "string",
            "--time-style": "string",
            "-t": "string",
            "--threshold": "string",
            "--block-size": "string",
            "--inodes": "none",
        }
    ),
    "df": CommandConfig(
        safe_flags={
            "-h": "none",
            "--human-readable": "none",
            "-H": "none",
            "--si": "none",
            "-T": "none",
            "--print-type": "none",
            "-i": "none",
            "--inodes": "none",
            "-a": "none",
            "--all": "none",
            "-l": "none",
            "--local": "none",
            "-P": "none",
            "--portability": "none",
            "-k": "none",
            "-B": "string",
            "--block-size": "string",
            "-t": "string",
            "--type": "string",
            "-x": "string",
            "--exclude-type": "string",
            "--total": "none",
            "--no-sync": "none",
            "--sync": "none",
            "--output": "string",
        }
    ),
    "ls": CommandConfig(
        safe_flags={
            "-l": "none",
            "-a": "none",
            "-A": "none",
            "--all": "none",
            "--almost-all": "none",
            "-h": "none",
            "--human-readable": "none",
            "--si": "none",
            "-R": "none",
            "--recursive": "none",
            "-r": "none",
            "--reverse": "none",
            "-S": "none",
            "-t": "none",
            "-U": "none",
            "-X": "none",
            "-v": "none",
            "--sort": "string",
            "--time": "string",
            "--time-style": "string",
            "-d": "none",
            "--directory": "none",
            "-i": "none",
            "--inode": "none",
            "-s": "none",
            "--size": "none",
            "-1": "none",
            "-C": "none",
            "-m": "none",
            "-x": "none",
            "-n": "none",
            "--numeric-uid-gid": "none",
            "-g": "none",
            "-o": "none",
            "-p": "none",
            "--indicator-style": "string",
            "-F": "none",
            "--classify": "none",
            "-G": "none",
            "--no-group": "none",
            "--color": "string",
            "--group-directories-first": "none",
            "-B": "none",
            "--ignore-backups": "none",
            "-I": "string",
            "--ignore": "string",
            "-w": "number",
            "--width": "number",
            "-T": "number",
            "--tabsize": "number",
            "--hide": "string",
            "--show-control-chars": "none",
            "-q": "none",
            "--hide-control-chars": "none",
            "-Q": "none",
            "--quote-name": "none",
            "--quoting-style": "string",
            "-N": "none",
            "--literal": "none",
            "-Z": "none",
            "--context": "none",
            "-k": "none",
            "--kibibytes": "none",
            "--full-time": "none",
            "--author": "none",
            "-L": "none",
            "--dereference": "none",
            "-H": "none",
            "--block-size": "string",
            "-f": "none",
        }
    ),
    "ps": CommandConfig(
        safe_flags={
            "-e": "none",
            "-A": "none",
            "-a": "none",
            "-d": "none",
            "-f": "none",
            "-l": "none",
            "-j": "none",
            "-u": "string",
            "-U": "string",
            "-p": "string",
            "-g": "string",
            "-G": "string",
            "-s": "string",
            "-t": "string",
            "-o": "string",
            "-O": "string",
            "--format": "string",
            "--sort": "string",
            "-m": "none",
            "-H": "none",
            "--forest": "none",
            "-w": "none",
            "--width": "number",
            "--no-headers": "none",
            "--headers": "none",
            "-N": "none",
            "--deselect": "none",
            "-c": "none",
            "-L": "none",
            "-T": "none",
            "-M": "none",
            "-Z": "none",
            "--context": "none",
            "--cumulative": "none",
            "--ppid": "string",
            "-C": "string",
            "-q": "string",
            "--quick-pid": "string",
        },
        dangerous_callback=ps_dangerous,
    ),
    "hostname": CommandConfig(
        safe_flags={
            "-f": "none",
            "--fqdn": "none",
            "--long": "none",
            "-s": "none",
            "--short": "none",
            "-i": "none",
            "--ip-address": "none",
            "-I": "none",
            "--all-ip-addresses": "none",
            "-a": "none",
            "--alias": "none",
            "-d": "none",
            "--domain": "none",
            "-A": "none",
            "--all-fqdns": "none",
            "-y": "none",
            "--yp": "none",
            "--nis": "none",
        },
        dangerous_callback=hostname_dangerous,
    ),
    "date": CommandConfig(
        safe_flags={
            "-d": "string",
            "--date": "string",
            "-r": "string",
            "--reference": "string",
            "-u": "none",
            "--utc": "none",
            "--universal": "none",
            "-I": "string",
            "--iso-8601": "string",
            "-R": "none",
            "--rfc-email": "none",
            "--rfc-3339": "string",
            "--debug": "none",
        },
        dangerous_callback=date_dangerous,
    ),
    "which": CommandConfig(safe_flags={"-a": "none", "--all": "none"}),
    "type": CommandConfig(safe_flags={"-a": "none", "-t": "none", "-p": "none", "-f": "none"}),
    "echo": CommandConfig(safe_flags={"-n": "none", "-e": "none", "-E": "none"}),
    "printf": CommandConfig(safe_flags={}),
    "python --version": CommandConfig(safe_flags={}, regex=re.compile(r"^python\s+--version$")),
    "python3 --version": CommandConfig(safe_flags={}, regex=re.compile(r"^python3\s+--version$")),
    "node --version": CommandConfig(safe_flags={}, regex=re.compile(r"^node\s+--version$")),
    "node -v": CommandConfig(safe_flags={}, regex=re.compile(r"^node\s+-v$")),
    "npm --version": CommandConfig(safe_flags={}, regex=re.compile(r"^npm\s+--version$")),
    "sha256sum": CommandConfig(safe_flags=CHECKSUM_FLAGS),
    "sha1sum": CommandConfig(safe_flags=CHECKSUM_FLAGS),
    "md5sum": CommandConfig(safe_flags=CHECKSUM_FLAGS),
    "sort": CommandConfig(
        safe_flags={
            "-b": "none",
            "--ignore-leading-blanks": "none",
            "-d": "none",
            "--dictionary-order": "none",
            "-f": "none",
            "--ignore-case": "none",
            "-g": "none",
            "--general-numeric-sort": "none",
            "-i": "none",
            "--ignore-nonprinting": "none",
            "-M": "none",
            "--month-sort": "none",
            "-h": "none",
            "--human-numeric-sort": "none",
            "-n": "none",
            "--numeric-sort": "none",
            "-R": "none",
            "--random-sort": "none",
            "-V": "none",
            "--version-sort": "none",
            "-r": "none",
            "--reverse": "none",
            "-s": "none",
            "--stable": "none",
            "-u": "none",
            "--unique": "none",
            "-c": "none",
            "-C": "none",
            "--check": "none",
            "-m": "none",
            "--merge": "none",
            "-k": "string",
            "--key": "string",
            "-t": "char",
            "--field-separator": "char",
            "-z": "none",
            "--zero-terminated": "none",
            "-S": "string",
            "--buffer-size": "string",
            "-T": "string",
            "--temporary-directory": "string",
            "--parallel": "number",
            "--random-source": "string",
            "--batch-size": "number",
            "--compress-program": "string",
            "--files0-from": "string",
        }
    ),
    "base64": CommandConfig(
        safe_flags={
            "-d": "none",
            "--decode": "none",
            "-i": "none",
            "--ignore-garbage": "none",
            "-w": "number",
            "--wrap": "number",
        },
        respects_double_dash=False,
    ),
    "tree": CommandConfig(
        safe_flags={
            "-a": "none",
            "--all": "none",
            "-d": "none",
            "-f": "none",
            "-i": "none",
            "-l": "none",
            "-x": "none",
            "-L": "number",
            "--level": "number",
            "-P": "string",
            "--pattern": "string",
            "-I": "string",
            "--prune": "none",
            "--noreport": "none",
            "-p": "none",
            "-u": "none",
            "-g": "none",
            "-s": "none",
            "-h": "none",
            "--si": "none",
            "-D": "none",
            "--timefmt": "string",
            "-t": "none",
            "-r": "none",
            "-v": "none",
            "-U": "none",
            "-S": "none",
            "-n": "none",
            "-C": "none",
            "--charset": "string",
            "-q": "none",
            "-N": "none",
            "-J": "none",
            "-X": "none",
            "-H": "string",
            "--dirsfirst": "none",
            "--sort": "string",
            "--du": "none",
            "--filelimit": "number",
            "--matchdirs": "none",
            "--metafirst": "none",
            "--gitignore": "none",
            "--gitfile": "string",
            "-F": "none",
            "--inodes": "none",
            "--device": "none",
        }
    ),
    "man": CommandConfig(
        safe_flags={
            "-k": "none",
            "--apropos": "none",
            "-f": "none",
            "--whatis": "none",
            "-a": "none",
            "--all": "none",
            "-w": "none",
            "--where": "none",
            "--path": "none",
            "--location": "none",
            "-l": "none",
            "--local-file": "none",
            "-S": "string",
            "-s": "string",
            "--sections": "string",
            "-M": "string",
            "--manpath": "string",
            "-L": "string",
            "--locale": "string",
            "-m": "string",
            "--systems": "string",
            "-e": "string",
            "--extension": "string",
            "-E": "string",
            "--encoding": "string",
            "--nh": "none",
            "--nj": "none",
            "-7": "none",
            "--ascii": "none",
            "-H": "string",
            "--html": "string",
            "-i": "none",
            "--ignore-case": "none",
            "-I": "none",
            "--match-case": "none",
            "--regex": "none",
            "--wildcard": "none",
            "--names-only": "none",
            "-K": "none",
            "--global-apropos": "none",
            "-d": "none",
            "--debug": "none",
        }
    ),
    "tput": CommandConfig(
        safe_flags={"-T": "string", "-x": "none"},
        dangerous_callback=tput_dangerous,
    ),
}

COMMAND_ALLOWLIST["fdfind"] = COMMAND_ALLOWLIST["fd"]

SORTED_ENTRIES: list[tuple[tuple[str, ...], CommandConfig]] = sorted(
    ((tuple(k.split()), v) for k, v in COMMAND_ALLOWLIST.items()),
    key=lambda e: -len(e[0]),
)


def is_command_read_only(command: str) -> bool:
    """Check if a shell command is provably read-only via the allowlist.

    Returns True only if ALL subcommands in a compound command pass validation.
    """
    if not command or not command.strip():
        return False

    if "`" in command:
        return False

    segments = split_compound(command)
    if not segments:
        return False

    for seg in segments:
        if not is_subcommand_read_only(seg):
            return False
    return True


def is_subcommand_read_only(cmd: str) -> bool:
    """Check a single (non-compound) subcommand against the allowlist."""
    cleaned = strip_safe_redirections(cmd.strip())

    tokens = tokenize(cleaned)
    if not tokens:
        return False

    matched_config: CommandConfig | None = None
    prefix_len = 0

    for key_parts, config in SORTED_ENTRIES:
        n = len(key_parts)
        if n <= len(tokens) and tuple(tokens[:n]) == key_parts:
            matched_config = config
            prefix_len = n
            break

    if matched_config is None:
        return False

    remaining_tokens = tokens[prefix_len:]

    if matched_config.regex is not None:
        if not matched_config.regex.search(cleaned):
            return False

    if matched_config.safe_flags and not validate_flags(remaining_tokens, 0, matched_config):
        return False

    if matched_config.dangerous_callback is not None:
        if matched_config.dangerous_callback(cleaned, remaining_tokens):
            return False

    return True
