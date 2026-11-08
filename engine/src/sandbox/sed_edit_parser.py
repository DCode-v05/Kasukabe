from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from src.sandbox.sed_validation import SedParseResult, parse_sed_args


@dataclass(frozen=True, slots=True)
class SedEditInfo:
    file_path: str
    pattern: str  # ERE form (converted from BRE if necessary)
    replacement: str  # literal `&` normalised to `\0` via salt round-trip
    flags: str  # canonical substitution flag string (sorted)
    extended_regex: bool  # True when `-E` / `-r` / `--regexp-extended` was set


# Expression shape: s<delim>pattern<delim>replacement<delim>flags
# We only accept the forward-slash delimiter here — the validator runs first
# and rejects alternate delimiters. Escaped forward slashes inside the pattern
# or replacement are allowed via `\\.`.
SED_SUB_RE = re.compile(r"^s/((?:\\.|[^/\\])*)/((?:\\.|[^/\\])*)/([gpimIM0-9]*)$")

# BRE→ERE conversion needs to swap the escaping of `(`, `)`, `{`, `}`, `+`,
# `?`, `|`. In BRE these are literal unless preceded by `\\`; in ERE they are
# metacharacters unless preceded by `\\`. So for each character `c`:
#   BRE `\c` → ERE `c`
#   BRE  `c` → ERE `\c`
BRE_ERE_SWAP_CHARS = "()+?{}|"

ERE_FLAGS: frozenset[str] = frozenset(
    {
        "-E",
        "-r",
        "--regexp-extended",
    }
)


def salt() -> str:
    """Random placeholder that cannot collide with user-supplied content.

    Uses a fresh random token per parse invocation so multiple parses in the
    same process never share a salt and user content written to the token in
    one call cannot leak into another.
    """
    return f"___SED_EDIT_SALT_{secrets.token_hex(8)}___"


def salt_ampersand(replacement: str, salt: str) -> str:
    """Protect literal-backslash-ampersand and bare-ampersand sequences.

    The sed replacement syntax uses `&` to mean "insert the whole match" and
    `\\&` to mean a literal ampersand. We want to normalise both forms without
    letting user content that already contains the salt leak into the output.
    A two-pass swap through the salt placeholder guarantees no collisions:

      Pass 1: `\\&` → `{salt}LITERAL{salt}`
      Pass 2: `&`   → `\\0`
      Pass 3: `{salt}LITERAL{salt}` → `&`
    """
    lit_marker = f"{salt}LITERAL{salt}"
    amp_marker = f"{salt}AMP{salt}"
    out = replacement.replace("\\&", lit_marker)
    out = out.replace("&", amp_marker)
    out = out.replace(lit_marker, "&")
    out = out.replace(amp_marker, "\\0")
    return out


def bre_to_ere(pattern: str, salt: str) -> str:
    """Convert a BRE pattern to its ERE equivalent using a salt placeholder.

    We walk the pattern character by character so we can distinguish escaped
    from unescaped metacharacters. Every run of `\\c` where `c` is in
    `BRE_ERE_SWAP_CHARS` is replaced with a salted literal that cannot
    collide with user content. Every bare `c` in the swap set is treated as
    literal (the BRE reading) and escaped. Then the salted literals are
    un-escaped back into their original characters — which in ERE means the
    metacharacter form. The two-phase swap prevents ambiguity for patterns
    that contain both forms (e.g. `\\(abc\\)+`).
    """
    placeholders: dict[str, str] = {}
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            nxt = pattern[i + 1]
            if nxt in BRE_ERE_SWAP_CHARS:
                # `\c` in BRE is the ERE `c` (metacharacter form). Stash it in
                # a salted placeholder so the next pass doesn't re-escape it.
                key = f"{salt}SWAP{len(placeholders)}{salt}"
                placeholders[key] = nxt
                out.append(key)
                i += 2
                continue
            # Other escaped characters pass through untouched.
            out.append(ch)
            out.append(nxt)
            i += 2
            continue
        if ch in BRE_ERE_SWAP_CHARS:
            # Bare metacharacter in BRE is a literal — escape it for ERE.
            out.append("\\")
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    result = "".join(out)
    for key, val in placeholders.items():
        result = result.replace(key, val)
    return result


def normalise_flags(flags: str) -> str:
    """Return a canonical form: sort the characters, drop duplicates.

    `gi` and `ig` should produce the same canonical string.
    """
    seen: list[str] = []
    for ch in flags:
        if ch not in seen:
            seen.append(ch)
    return "".join(sorted(seen))


def parse_sed_edit(command_tokens: list[str]) -> SedEditInfo | None:
    """Extract edit info from a tokenised `sed -i 's/pat/rep/flags' file` call.

    Returns `None` on any parse failure so the caller can fall back to a
    generic audit record. Only invoked after `check_sed_constraints` has
    returned PASSTHROUGH — in practice this means the command is guaranteed
    to use the forward-slash delimiter, safe flags, and have at least one
    file argument.
    """
    if not command_tokens:
        return None
    parse: SedParseResult = parse_sed_args(command_tokens[1:])
    if not parse.parse_ok:
        return None
    if not parse.has_inplace:
        return None
    if not parse.expressions:
        return None
    if not parse.files:
        return None
    # Only single-expression, single-file invocations are attributable. Multi
    # -e or multi-file commands fall back to None.
    if len(parse.expressions) != 1:
        return None
    if len(parse.files) != 1:
        return None
    expr = parse.expressions[0]
    m = SED_SUB_RE.match(expr)
    if m is None:
        return None
    pattern = m.group(1)
    replacement = m.group(2)
    flags = m.group(3)

    extended = any(f in ERE_FLAGS for f in parse.flags)
    salt_value = salt()
    pattern_out = pattern if extended else bre_to_ere(pattern, salt_value)
    replacement_out = salt_ampersand(replacement, salt_value)
    return SedEditInfo(
        file_path=parse.files[0],
        pattern=pattern_out,
        replacement=replacement_out,
        flags=normalise_flags(flags),
        extended_regex=extended,
    )
