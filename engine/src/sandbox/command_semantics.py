"""Exit code semantics for known commands.

Maps commands like grep, diff, test to their non-error exit codes
so the sandbox doesn't treat e.g. grep exit 1 (no matches) as failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.sandbox.command_classifier import split_compound

# Each lambda: (exit_code, stdout, stderr) -> (is_error, semantic_message | None)
# is_error=False means this exit code is expected/non-error.
# semantic_message provides context for the model when output is empty.
CommandSemantic = Callable[[int, str, str], tuple[bool, str | None]]


def exit1_means(msg: str) -> CommandSemantic:
    """Factory for commands where exit 1 is informational, exit >=2 is error."""
    return lambda code, out, err: (
        (False, None) if code == 0 else (False, msg) if code == 1 else (True, None)
    )


COMMAND_SEMANTICS: dict[str, CommandSemantic] = {
    "grep": exit1_means("No matches found"),
    "rg": exit1_means("No matches found"),
    "find": exit1_means("Some directories were inaccessible"),
    "diff": exit1_means("Files differ"),
    "test": exit1_means("Condition is false"),
    "[": exit1_means("Condition is false"),
}


def default_semantic(code: int, out: str, err: str) -> tuple[bool, str | None]:
    if code == 0:
        return (False, None)
    # Only provide a message when stderr doesn't already explain the failure
    msg = f"Command failed with exit code {code}" if not err else None
    return (True, msg)


# Prefixes stripped when extracting the base command name
STRIP_PREFIXES = {"sudo", "command", "env", "nice", "nohup", "time", "strace"}


def extract_base_command(command: str) -> str:
    """Extract the command that determines the exit code (last in pipeline).

    Heuristic — quoted pipes won't be split correctly for deeply nested
    cases, but handles the common patterns. Not used for security decisions.
    """
    segments = split_compound(command)
    if not segments:
        return ""
    last = segments[-1].strip()

    # Strip env var assignments (FOO=bar grep ...)
    words = last.split()
    while words and "=" in words[0] and not words[0].startswith("-"):
        words.pop(0)

    # Strip common prefixes like sudo, env, command
    while words and words[0] in STRIP_PREFIXES:
        words.pop(0)

    if not words:
        return ""

    # Extract basename (handle /usr/bin/grep -> grep)
    return Path(words[0]).name


def interpret_exit_code(
    command: str,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
) -> tuple[bool, str | None]:
    """Interpret exit code based on command semantics.

    Returns (is_error, semantic_message).
    is_error=False means the exit code is expected for this command.
    semantic_message provides human-readable context (e.g. "No matches found").
    """
    base = extract_base_command(command)
    semantic = COMMAND_SEMANTICS.get(base, default_semantic)
    return semantic(exit_code, stdout, stderr)
