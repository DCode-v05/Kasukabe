from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SedBehavior(Enum):
    PASSTHROUGH = "passthrough"
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class SedValidationResult:
    behavior: SedBehavior
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SedParseResult:
    flags: tuple[str, ...]
    expressions: tuple[str, ...]
    files: tuple[str, ...]
    has_inplace: bool
    parse_ok: bool
    parse_error: str = ""


SAFE_PRINT_FLAGS: frozenset[str] = frozenset(
    {
        "-n",
        "-E",
        "-r",
        "-z",
        "--quiet",
        "--silent",
        "--regexp-extended",
        "--zero-terminated",
        "--posix",
    }
)

SAFE_SUB_FLAGS: frozenset[str] = frozenset(set("gpimIM") | {str(d) for d in range(1, 10)})

SAFE_PRINT_EXPR_RE = re.compile(r"^(?:\d+|\d+,\d+)?p$")

SAFE_SUB_FLAGS_RE = re.compile(r"^[gpimIM]*[1-9]?[gpimIM]*$")

SAFE_SUB_EXPR_RE = re.compile(r"^s/((?:\\.|[^/\\])*)/((?:\\.|[^/\\])*)/([gpimIM0-9]*)$")

QUIET_FLAG_ALIASES: frozenset[str] = frozenset({"-n", "--quiet", "--silent"})

DANGEROUS_E_NEIGHBORS: frozenset[str] = frozenset({"e", "w", "W"})
DANGEROUS_W_NEIGHBORS: frozenset[str] = frozenset({"e", "E"})


DANGEROUS_SUB_FLAGS_RE = re.compile(
    r"^s([^\\\n]).*?\1.*?\1(.*?)$",
    re.DOTALL,
)

DANGEROUS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[^\x01-\x7F]"), "non-ASCII character in sed expression"),
    (re.compile(r"[{}]"), "curly braces are too complex to analyse safely"),
    (re.compile(r"\n"), "newline in sed expression"),
    (re.compile(r"(?<!s)#"), "comment marker in sed expression"),
    (re.compile(r"^\s*!"), "leading address negation"),
    (re.compile(r"/!"), "address negation after pattern"),
    (re.compile(r"\d~\d"), "tilde step address"),
    (re.compile(r",~\d"), "tilde step address after comma"),
    (re.compile(r"\$~\d"), "tilde step address after $"),
    (re.compile(r"^\s*,"), "bare comma address prefix"),
    (re.compile(r",\s*[+-]\d"), "GNU offset address"),
    (re.compile(r"^s\\"), "backslash delimiter for substitution"),
    (re.compile(r"\\[|#%@]"), "alternate delimiter escape"),
    (re.compile(r"\\/.*[wW]"), "escaped slash followed by w/W"),
    (re.compile(r"^\s*[wW]\s*\S+"), "top-level w/W write command"),
    (re.compile(r"^\s*\d+\s*[wW]\s*\S+"), "numeric-address w/W write command"),
    (re.compile(r"^\s*\$\s*[wW]\s*\S+"), "$-address w/W write command"),
    (re.compile(r"/[^/]*/\s*[wW]\s*\S+"), "pattern-address w/W write command"),
    (re.compile(r"^\s*e(\s|$)"), "top-level e execute command"),
    (re.compile(r"^\s*\d+\s*e(\s|$)"), "numeric-address e execute command"),
    (re.compile(r"^\s*\$\s*e(\s|$)"), "$-address e execute command"),
    (re.compile(r"/[^/]*/\s*e(\s|$)"), "pattern-address e execute command"),
)


def has_dangerous_pattern(expressions: tuple[str, ...]) -> tuple[bool, str]:
    """Run the denylist against every expression.

    The denylist runs BEFORE the allowlist (defense in depth) — any expression
    that hits a dangerous pattern is rejected even if it also happens to match
    the safe-substitution allowlist.
    """
    for expr in expressions:
        if not expr:
            continue
        for pattern, reason in DANGEROUS_PATTERNS:
            if pattern.search(expr):
                return True, reason
        m = DANGEROUS_SUB_FLAGS_RE.match(expr)
        if m is not None:
            trailing = m.group(2) or ""
            if any(ch in trailing for ch in "wWeE"):
                return True, "dangerous substitution flag (w/W/e/E)"
        if re.search(r"y[^\\\n]", expr) and re.search(r"[wWeE]", expr):
            return True, "y transliterate combined with w/W/e/E"
    return False, ""


def split_combined_short_flag(token: str) -> list[str]:
    """`-nEi` -> ['-n', '-E', '-i']. Long flags (`--foo`) are returned as-is.

    A single short flag (`-n`) is returned as-is. Empty or non-flag tokens
    return the input unchanged inside a one-element list.
    """
    if not token or not token.startswith("-"):
        return [token]
    if token.startswith("--"):
        return [token]
    if len(token) <= 2:
        return [token]
    if token.startswith("-i") and "." in token:
        return [token]
    return [f"-{ch}" for ch in token[1:]]


def parse_sed_args(args: list[str]) -> SedParseResult:
    """Parse a list of sed arguments into flags, expressions, and files.

    Handles `-e expr`, `--expression=expr`, `-e=expr`, positional expression
    (when no `-e` flag is present), and combined short flags (`-nEi`). Rejects
    dangerous flag combinations (`-ew`, `-eW`, `-ee`, `-we`, `-wE`) before
    proceeding. Sets `has_inplace` if any form of `-i` / `--in-place` /
    `-i<ext>` is seen.
    """
    flags: list[str] = []
    expressions: list[str] = []
    files: list[str] = []
    has_inplace = False
    has_dash_e = False

    def fail(msg: str) -> SedParseResult:
        return SedParseResult(
            flags=tuple(flags),
            expressions=tuple(expressions),
            files=tuple(files),
            has_inplace=has_inplace,
            parse_ok=False,
            parse_error=msg,
        )

    i = 0
    n = len(args)
    while i < n:
        tok = args[i]

        if tok.startswith("--expression="):
            expressions.append(tok[len("--expression=") :])
            has_dash_e = True
            i += 1
            continue

        if tok.startswith("-e="):
            expressions.append(tok[len("-e=") :])
            has_dash_e = True
            i += 1
            continue

        if tok == "-e" or tok == "--expression":
            if i + 1 >= n:
                return fail("dangling -e with no expression argument")
            expressions.append(args[i + 1])
            has_dash_e = True
            i += 2
            continue

        if tok.startswith("--"):
            flags.append(tok)
            if tok == "--in-place":
                has_inplace = True
            i += 1
            continue

        if tok.startswith("-") and tok != "-" and len(tok) >= 2:
            letters = tok[1:]
            letters_for_check = letters.split(".", 1)[0] if letters.startswith("i") else letters
            if "e" in letters_for_check:
                if any(
                    (ch != "e" and ch in letters_for_check)
                    or (ch == "e" and letters_for_check.count("e") > 1)
                    for ch in DANGEROUS_E_NEIGHBORS
                ):
                    return fail(f"dangerous flag combination: {tok}")
            if "w" in letters_for_check and any(
                ch in letters_for_check for ch in DANGEROUS_W_NEIGHBORS
            ):
                return fail(f"dangerous flag combination: {tok}")
            split = split_combined_short_flag(tok)
            produced_e = False
            for sub in split:
                if sub == "-e":
                    produced_e = True
                    has_dash_e = True
                    continue
                flags.append(sub)
                if sub == "-i" or (sub.startswith("-i") and len(sub) > 2):
                    has_inplace = True
            if produced_e:
                if i + 1 >= n:
                    return fail("dangling -e with no expression argument")
                expressions.append(args[i + 1])
                i += 2
                continue
            i += 1
            continue

        if not has_dash_e and not expressions:
            expressions.append(tok)
            has_dash_e = True
            i += 1
            continue

        files.append(tok)
        i += 1

    return SedParseResult(
        flags=tuple(flags),
        expressions=tuple(expressions),
        files=tuple(files),
        has_inplace=has_inplace,
        parse_ok=True,
    )


def is_safe_print(parse: SedParseResult) -> bool:
    """Safe-print allowlist: `-n` silent mode, expressions like `p`, `1,5p`.

    File arguments are allowed — print is read-only.
    """
    if not any(f in QUIET_FLAG_ALIASES for f in parse.flags):
        return False
    for f in parse.flags:
        if f not in SAFE_PRINT_FLAGS:
            return False
    if parse.has_inplace:
        return False
    if not parse.expressions:
        return False
    for expr in parse.expressions:
        parts = expr.split(";")
        if not parts:
            return False
        for part in parts:
            if not SAFE_PRINT_EXPR_RE.match(part.strip()):
                return False
    return True


def is_safe_substitution(parse: SedParseResult, allow_file_writes: bool) -> bool:
    """Safe-substitution allowlist: `s/pattern/replacement/flags` with slash
    delimiter only. Safe flag set. Files and `-i` gated on allow_file_writes.

    Matches src `sedValidation.ts` strictness:
      - Exactly one expression (no multi `-e`).
      - No semicolons anywhere in the expression — even literal semicolons in
        the replacement are rejected because src treats `;` as a command
        separator and we refuse to reason about `s/foo/bar/g;<something>`.
    """
    if len(parse.expressions) != 1:
        return False
    if not allow_file_writes:
        if parse.has_inplace:
            return False
        if parse.files:
            return False
    expr = parse.expressions[0]
    if ";" in expr:
        return False
    m = SAFE_SUB_EXPR_RE.match(expr)
    if m is None:
        return False
    flags_part = m.group(3)
    if flags_part and not SAFE_SUB_FLAGS_RE.match(flags_part):
        return False
    for ch in flags_part:
        if ch not in SAFE_SUB_FLAGS:
            return False
    return True


def check_sed_constraints(
    command_tokens: list[str],
    *,
    allow_file_writes: bool,
) -> SedValidationResult:
    """Two-level validator for a tokenised sed command.

    Caller must verify `command_tokens[0]` is sed; this function hands
    `command_tokens[1:]` to `parse_sed_args` and runs:
      1. Parse — on failure → `sed-parse: <error>` ASK.
      2. Denylist — defense in depth, runs before allowlist → `sed-denylist: <reason>` ASK.
      3. Safe-print allowlist → PASSTHROUGH.
      4. Safe-substitution allowlist (gated on `allow_file_writes`) → PASSTHROUGH.
      5. Fall through → `sed-unknown: not in safe allowlist` ASK.

    Reason prefixes are stable and parseable by downstream consumers, mirroring
    the `PathSafetyResult.reason` prefix contract in architecture/sections/50_sandbox.md.
    """
    if not command_tokens:
        return SedValidationResult(
            behavior=SedBehavior.ASK,
            reason="sed-parse: empty sed command",
        )
    parse = parse_sed_args(command_tokens[1:])
    if not parse.parse_ok:
        return SedValidationResult(
            behavior=SedBehavior.ASK,
            reason=f"sed-parse: {parse.parse_error}",
        )
    dangerous, reason = has_dangerous_pattern(parse.expressions)
    if dangerous:
        return SedValidationResult(
            behavior=SedBehavior.ASK,
            reason=f"sed-denylist: {reason}",
        )
    if is_safe_print(parse):
        return SedValidationResult(behavior=SedBehavior.PASSTHROUGH)
    if is_safe_substitution(parse, allow_file_writes=allow_file_writes):
        return SedValidationResult(behavior=SedBehavior.PASSTHROUGH)
    return SedValidationResult(
        behavior=SedBehavior.ASK,
        reason="sed-unknown: not in safe allowlist",
    )
