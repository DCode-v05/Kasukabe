"""Extract file write targets from bash command strings.

Belt-and-suspenders companion to `bash_security.py`. The `check_permission`
pipeline calls `extract_written_paths(command)` from the bypass-immune
step 1.6 to catch bash-level evasions like `echo evil > ~/.bashrc` that would
otherwise skip the platform-tool dangerous-path gate.

Scope:
    - Output redirections: > >> &> 1> 2>
    - tee [-a] [-i] FILE [FILE...]
    - sed -i, sed -i.bak, sed --in-place -> tail non-flag args
    - rm, mv (dst), cp (dst), ln (link name), touch, dd of=FILE

Explicitly NOT in scope (caught or asked about by bash_security.py validators):
    - python -c / bash -c / eval
    - Heredocs
    - Command substitution $(...), backticks
    - Subshells (...)
    - Arbitrary programs that open files (vim, nano, >/proc, etc.)

On shlex parse error the function returns [] — bash_security.analyze_bash_security
is the primary malformed-command gate, and we don't want to double-block on
commands it already handled.
"""

from __future__ import annotations

import shlex
from typing import Iterator


# Shell tokens emitted by shlex that terminate a command's argument list.
SHELL_OPERATOR_TOKENS: frozenset[str] = frozenset(
    {
        "|",
        "||",
        "&",
        "&&",
        ";",
        ">",
        ">>",
        "<",
        "&>",
        "1>",
        "2>",
    }
)

# Subset that starts a fresh pipeline / sequence segment (no '<', no '>'-family).
PIPELINE_SEPARATORS: frozenset[str] = frozenset({"|", "||", "&", "&&", ";"})

# Commands whose entire tail of non-flag arguments are write targets.
WRITE_ALL_TAIL_CMDS: frozenset[str] = frozenset({"rm", "touch"})

# Commands where the final non-flag argument is the write target.
WRITE_LAST_ARG_CMDS: frozenset[str] = frozenset({"mv", "cp", "ln"})

# Wrappers that delegate execution directly to the next word — e.g.
# `sudo rm ~/.bashrc`, `env FOO=bar tee ~/.zshrc`, `nohup nice cp src ~/.bashrc`.
# Without skipping these, the dangerous-target gate would only see "sudo" /
# "env" as the base command and miss the actual write. Limited to wrappers
# whose plain form is `WRAPPER CMD ...`; tools that take their own positional
# arguments (timeout, ionice, chrt, stdbuf) are excluded — handling them would
# need per-tool argument parsing.
PRECOMMAND_PREFIXES: frozenset[str] = frozenset(
    {
        "sudo",
        "doas",
        "command",
        "builtin",
        "exec",
        "noglob",
        "nocorrect",
        "env",
        "nice",
        "nohup",
        "time",
        "strace",
    }
)


def strip_leading_path(cmd: str) -> str:
    """'/usr/bin/sed' -> 'sed'. Absolute paths don't change command identity."""
    return cmd.rsplit("/", 1)[-1]


def is_env_assignment(token: str) -> bool:
    """Detect a `FOO=bar` env-var-assignment token (not a command, not a flag)."""
    if not token or token.startswith("-") or "=" not in token:
        return False
    eq = token.index("=")
    return eq > 0 and token[:eq].replace("_", "").isalnum()


def find_base_command_index(tokens: list[str], start: int) -> int | None:
    """Walk forward from `start`, skipping env-var assignments and precommand
    wrappers (sudo, env, nice, ...), and return the index of the actual base
    command token. Returns None if an operator is hit first or the segment is empty.
    """
    i = start
    while i < len(tokens):
        tok = tokens[i]
        if tok in SHELL_OPERATOR_TOKENS:
            return None
        if is_env_assignment(tok):
            i += 1
            continue
        if strip_leading_path(tok) in PRECOMMAND_PREFIXES:
            i += 1
            continue
        return i
    return None


def iter_non_flag_args(tokens: list[str], start: int) -> Iterator[str]:
    """Yield non-flag, non-operator args from `start+1` until a shell operator.

    Skips `--` and any `-foo` flag (but keeps the literal `-`, which is the
    POSIX stdin/stdout marker, not a flag).
    """
    i = start + 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in SHELL_OPERATOR_TOKENS:
            return
        if tok == "--":
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            i += 1
            continue
        yield tok
        i += 1


def collect_sed_in_place_targets(tokens: list[str], start: int) -> list[str]:
    """sed -i [SCRIPT] FILE... or sed --in-place [SCRIPT] FILE...

    GNU sed treats any `-i`, `-i<ext>`, `--in-place`, or combined short flag
    containing `i` (e.g. `-nEi`) as in-place. Combined-flag handling matters —
    a bug in the previous implementation missed `sed -nEi ... file` entirely
    because `-nEi` does not start with `-i`, which let the dangerous-path gate
    silently skip the write target. Once any form of `-i` is detected we
    return ALL non-flag args after the sed token. This over-reports (the
    script may be counted as a file) but `check_path_safety` on a sed script
    string is harmless — false positives here cost nothing; false negatives
    could miss attacks.
    """
    has_in_place = False
    for t in tokens[start + 1 :]:
        if t == "--in-place":
            has_in_place = True
            break
        if t.startswith("--"):
            # Any other long flag — skip and keep scanning.
            continue
        if t == "-i" or (t.startswith("-i") and "." in t):
            # `-i` or GNU backup form `-i.bak`.
            has_in_place = True
            break
        if t.startswith("-") and len(t) >= 2:
            # Short flag or combined-short-flag token: look for `i` among the
            # individual short-flag characters. Excludes the leading `-`.
            if "i" in t[1:]:
                has_in_place = True
                break
            continue
        # Non-flag token — positional args begin here.
        break

    if not has_in_place:
        return []

    return list(iter_non_flag_args(tokens, start))


def tokenize_command(command: str) -> list[str]:
    """POSIX-safe tokenisation of a shell command string.

    Wraps `shlex.split(command, posix=True)`. Returns `[]` on empty / non-str
    input, and `[]` on any shlex parse error (unclosed quotes, etc.) — matching
    the belt-and-suspenders philosophy of `extract_written_paths` so callers
    don't have to special-case malformed commands. The primary gate for
    malformed bash is `src/sandbox/bash_security.py`; this helper exists only
    to give other sandbox modules a single, consistent tokeniser entry point.
    """
    if not isinstance(command, str) or not command.strip():
        return []
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return []


def find_sed_subcommands(sub_commands: list[str]) -> list[list[str]]:
    """Given already-split sub-command strings, return the token lists of
    every sub-command whose base is `sed`.

    Split separately from the iterator below so `permissions.py` step 4.5
    can call `split_compound_commands` once and reuse the sub-string list
    both for this scan and for the standalone-vs-compound decision.
    """
    out: list[list[str]] = []
    for sub in sub_commands:
        sub_tokens = tokenize_command(sub)
        if not sub_tokens:
            continue
        base_idx = find_base_command_index(sub_tokens, 0)
        if base_idx is None:
            continue
        if strip_leading_path(sub_tokens[base_idx]) == "sed":
            out.append(sub_tokens[base_idx:])
    return out


def iter_sed_subcommand_tokens(command: str) -> list[list[str]]:
    """Convenience wrapper: split the command and return sed sub-commands.

    Handles compound commands — pipelines (`|`), sequences (`;`),
    conjunctions (`&&`), disjunctions (`||`) — so callers can validate every
    sed invocation inside a larger command line. Returns an empty list on
    empty / non-string input or when the command contains no sed.
    """
    from src.sandbox.command_classifier import split_compound_commands

    if not isinstance(command, str) or not command.strip():
        return []
    return find_sed_subcommands(split_compound_commands(command))


def collect_dd_of(tokens: list[str], start: int) -> list[str]:
    """dd if=... of=FILE bs=... — find any of=FILE operand."""
    targets: list[str] = []
    i = start + 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in SHELL_OPERATOR_TOKENS:
            break
        if tok.startswith("of="):
            targets.append(tok[3:])
        i += 1
    return targets


def extract_written_paths(command: str) -> list[str]:
    """Return file paths the given bash command would write to, via shlex tokenization.

    Returns an empty list on shlex parse error. This is intentional — the
    bash_security AST validators are the primary defense for malformed or
    hostile commands, and duplicating their block signal here would produce
    spurious errors.
    """
    if not isinstance(command, str) or not command.strip():
        return []

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return []

    if not tokens:
        return []

    targets: list[str] = []

    # shlex.split() does NOT split ">" from adjacent tokens when they were
    # already separated by whitespace. "echo hi >foo" tokenizes to
    # ['echo', 'hi', '>foo'] — we have to handle both forms.
    i = 0
    command_start = 0  # index where the current pipeline segment's command begins
    while i < len(tokens):
        tok = tokens[i]

        if tok in (">", ">>", "&>", "1>", "2>"):
            if i + 1 < len(tokens):
                targets.append(tokens[i + 1])
            i += 2
            continue

        for prefix in (">>", "&>", "1>", "2>", ">"):
            if tok.startswith(prefix) and len(tok) > len(prefix):
                targets.append(tok[len(prefix) :])
                break

        if tok in PIPELINE_SEPARATORS:
            i += 1
            command_start = i
            continue

        if i == command_start:
            # Skip past env-var assignments and wrappers (sudo, env, nice, ...)
            # so `sudo rm ~/.bashrc` and `env FOO=bar tee ~/.zshrc` are
            # recognized as writes to the actual target.
            base_idx = find_base_command_index(tokens, i)
            if base_idx is not None:
                base = strip_leading_path(tokens[base_idx])

                if base == "tee":
                    targets.extend(iter_non_flag_args(tokens, base_idx))
                elif base == "sed":
                    targets.extend(collect_sed_in_place_targets(tokens, base_idx))
                elif base in WRITE_ALL_TAIL_CMDS:
                    targets.extend(iter_non_flag_args(tokens, base_idx))
                elif base in WRITE_LAST_ARG_CMDS:
                    last = None
                    for arg in iter_non_flag_args(tokens, base_idx):
                        last = arg
                    if last is not None:
                        targets.append(last)
                elif base == "dd":
                    targets.extend(collect_dd_of(tokens, base_idx))

        i += 1

    return list(dict.fromkeys(t for t in targets if t))
