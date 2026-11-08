"""AST-based bash security analysis — 26 validators for the permission pipeline.

Runs bypass-immune before permission decisions. Catches sophisticated attacks
that regex classification cannot detect: command substitution hiding, heredoc
abuse, Unicode homoglyphs, control character injection, parser differentials, etc.

Each validator returns SecurityResult | None. None means "no opinion, next."
First block or allow wins. The runner uses misparsing-priority deferral:
misparsing blocks take precedence over non-misparsing asks.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from src.sandbox.command_classifier import (
    split_compound,
    strip_safe_redirections,
    JQ_SYSTEM,
)
from src.sandbox.command_classifier import ZSH_DANGEROUS_CMDS as ZSH_DANGEROUS_CMDS_TUPLE


@dataclass(frozen=True, slots=True)
class SecurityResult:
    blocked: bool
    check_id: int
    behavior: str
    message: str
    is_misparsing: bool = False


@dataclass(frozen=True, slots=True)
class ValidationContext:
    original_command: str
    base_command: str
    fully_unquoted: str
    fully_unquoted_pre_strip: str
    unquoted_keep_quotes: str


PASSTHROUGH = SecurityResult(blocked=False, check_id=0, behavior="passthrough", message="")


def extract_quoted_content(cmd: str) -> tuple[str, str, str]:
    """Extract quote-aware variants of a command string.

    Returns:
        (with_double_quotes, fully_unquoted, unquoted_keep_quotes)

        with_double_quotes: single-quote content removed, double-quote content kept
        fully_unquoted: all quoted content removed
        unquoted_keep_quotes: quote chars kept but inner content removed
    """
    with_double: list[str] = []
    fully_unq: list[str] = []
    keep_quotes: list[str] = []
    i = 0
    n = len(cmd)

    while i < n:
        c = cmd[i]
        if c == "'":
            keep_quotes.append("'")
            i += 1
            while i < n and cmd[i] != "'":
                with_double.append(cmd[i])
                i += 1
            if i < n:
                keep_quotes.append("'")
                i += 1
        elif c == '"':
            keep_quotes.append('"')
            i += 1
            while i < n and cmd[i] != '"':
                if cmd[i] == "\\" and i + 1 < n:
                    with_double.append(cmd[i : i + 2])
                    i += 2
                else:
                    with_double.append(cmd[i])
                    i += 1
            if i < n:
                keep_quotes.append('"')
                i += 1
        elif c == "\\" and i + 1 < n:
            with_double.append(cmd[i : i + 2])
            fully_unq.append(cmd[i : i + 2])
            keep_quotes.append(cmd[i : i + 2])
            i += 2
        else:
            with_double.append(c)
            fully_unq.append(c)
            keep_quotes.append(c)
            i += 1

    return "".join(with_double), "".join(fully_unq), "".join(keep_quotes)


def has_unescaped_char(content: str, char: str) -> bool:
    """Check if content contains char not preceded by backslash."""
    i = 0
    n = len(content)
    while i < n:
        if content[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if content[i] == char:
            return True
        i += 1
    return False


def is_escaped_at_position(content: str, pos: int) -> bool:
    """Check if character at pos is preceded by an odd number of backslashes."""
    count = 0
    p = pos - 1
    while p >= 0 and content[p] == "\\":
        count += 1
        p -= 1
    return count % 2 == 1


PRECOMMAND_MODIFIERS = frozenset({"command", "builtin", "noglob", "nocorrect", "exec"})


def get_base_command(cmd: str) -> str:
    """Extract the first word (base command), skipping env assignments and precommand modifiers."""
    stripped = cmd.strip()
    parts = stripped.split()
    for part in parts:
        if "=" in part and not part.startswith("-"):
            eq_pos = part.index("=")
            if eq_pos > 0 and part[:eq_pos].replace("_", "").isalnum():
                continue
        if part in PRECOMMAND_MODIFIERS:
            continue
        return part
    return stripped


SHELL_OPERATORS = frozenset(";|&<>")


def has_backslash_escaped_operator(cmd: str) -> tuple[bool, str]:
    """Quote-aware scan for backslash-escaped shell operators.

    Returns (found, operator_char). Handles backslash BEFORE quote toggles,
    correctly processes \\" inside double quotes.
    """
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)

    while i < n:
        c = cmd[i]

        if c == "\\" and i + 1 < n:
            if in_single:
                i += 1
                continue
            next_c = cmd[i + 1]
            if in_double:
                if next_c in ('"', "\\", "$", "`"):
                    i += 2
                    continue
                i += 1
                continue
            if next_c in SHELL_OPERATORS:
                return True, next_c
            i += 2
            continue

        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double

        i += 1

    return False, ""


HEREDOC_OP = re.compile(
    r"<<-?\s*(?:"
    r"'([A-Za-z_]\w*)'"
    r'|"([A-Za-z_]\w*)"'
    r"|\\([A-Za-z_]\w*)"
    r")"
)


def extract_heredoc_bodies(cmd: str) -> str:
    """Extract and remove quoted heredoc bodies from command.

    Simplified heredoc extraction: finds <<'DELIM' or <<"DELIM" or <<\\DELIM
    patterns, locates the body, and replaces with placeholder. This prevents
    heredoc body content from triggering validators.
    Only handles quoted/escaped delimiters (body is literal text, no expansion).
    """
    if "<<" not in cmd:
        return cmd

    first_heredoc = cmd.index("<<")
    prefix = cmd[:first_heredoc]
    if "$'" in prefix or '$"' in prefix:
        return cmd

    result = cmd

    for m in HEREDOC_OP.finditer(cmd):
        delimiter = m.group(1) or m.group(2) or m.group(3)
        if not delimiter:
            continue

        is_strip = "-" in cmd[m.start() : m.start() + 4] and cmd[m.start() + 2] == "-"

        body_start = cmd.find("\n", m.end())
        if body_start == -1:
            continue
        body_start += 1

        body_end = None
        pos = body_start
        while pos < len(cmd):
            line_end = cmd.find("\n", pos)
            if line_end == -1:
                line = cmd[pos:]
                line_end = len(cmd)
            else:
                line = cmd[pos:line_end]

            check_line = line.lstrip("\t") if is_strip else line
            if check_line.strip() == delimiter:
                body_end = pos
                break
            pos = line_end + 1

        if body_end is not None:
            body = cmd[body_start:body_end]
            result = result.replace(body, " __HEREDOC_BODY__ ", 1)

    return result


def build_context(cmd: str) -> ValidationContext:
    """Build a ValidationContext from a raw command."""
    _, fully_unquoted_pre_strip, _ = extract_quoted_content(cmd)

    processed = extract_heredoc_bodies(cmd)
    cleaned = strip_safe_redirections(processed)
    _, fully_unquoted, keep_quotes = extract_quoted_content(cleaned)
    return ValidationContext(
        original_command=cmd,
        base_command=get_base_command(cmd),
        fully_unquoted=fully_unquoted,
        fully_unquoted_pre_strip=fully_unquoted_pre_strip,
        unquoted_keep_quotes=keep_quotes,
    )


def validate_empty(ctx: ValidationContext) -> SecurityResult | None:
    """Empty command is safe — short-circuit with allow."""
    if not ctx.original_command or not ctx.original_command.strip():
        return SecurityResult(blocked=False, check_id=1, behavior="allow", message="empty command")
    return None


INCOMPLETE_START = re.compile(r"^\s*([;&|]|&&|\|\||\t|-\s)")


def validate_incomplete_commands(ctx: ValidationContext) -> SecurityResult | None:
    """Commands starting with operators, tabs, or bare dashes are suspicious."""
    if INCOMPLETE_START.match(ctx.original_command):
        return SecurityResult(
            blocked=True,
            check_id=2,
            behavior="ask",
            message="command starts with operator, tab, or dash — possible injection fragment",
        )
    return None


SAFE_HEREDOC_PATTERN = re.compile(
    r"\$\(cat\s+<<(-?)\s*(?:"
    r"'([A-Za-z_][A-Za-z0-9_]*)'"
    r'|"([A-Za-z_][A-Za-z0-9_]*)"'
    r"|\\([A-Za-z_][A-Za-z0-9_]*)"
    r")\s*\n"
)
SAFE_REMAINDER_CHARS = re.compile(r'^[a-zA-Z0-9 \t"\'.\-/_@=,:+~]*$')
NESTED_HEREDOC = re.compile(r"<<\s*['\"]?[A-Za-z_]")
GIT_COMMIT_REMAINDER_METACHARS = re.compile(r"[;&|`$\n\r()]")
GIT_COMMIT_C_SUBSTITUTION = re.compile(r"\bgit\s+commit\s+.*-[Cc]\s+\$")
FC_E_PATTERN = re.compile(r"\bfc\s+-[a-zA-Z]*e")


def validate_safe_heredoc(ctx: ValidationContext) -> SecurityResult | None:
    r"""$(cat <<'DELIM'...) or $(cat <<"DELIM"...) or $(cat <<\DELIM...) is safe.

    Must run before validate_dangerous_patterns which would block on $().
    Line-based delimiter matching. Rejects nested heredocs and $() in
    command-name position. Validates remaining text after heredoc.
    Handles <<- (tab-stripping) and PST_EOFTOKEN (DELIM) inline).
    """
    cmd = ctx.original_command.strip()

    dollar_paren_pos = cmd.find("$(")
    if dollar_paren_pos == -1:
        return None
    if dollar_paren_pos > 0:
        prefix = cmd[:dollar_paren_pos].strip()
        if not prefix:
            return None

    m = SAFE_HEREDOC_PATTERN.search(cmd)
    if not m:
        return None

    is_strip = m.group(1) == "-"
    delimiter = m.group(2) or m.group(3) or m.group(4)
    if not delimiter:
        return None

    body_start = m.end()

    body_end = None
    closing_end = None
    pos = body_start
    while pos < len(cmd):
        line_end = cmd.find("\n", pos)
        if line_end == -1:
            line = cmd[pos:]
            line_end = len(cmd)
        else:
            line = cmd[pos:line_end]

        check_line = line.lstrip("\t") if is_strip else line

        if check_line.strip() == delimiter:
            body_end = pos
            closing_end = line_end + 1 if line_end < len(cmd) else line_end
            break

        if check_line.strip() == delimiter + ")":
            body_end = pos
            closing_end = line_end + 1 if line_end < len(cmd) else line_end
            break

        pos = line_end + 1 if line_end < len(cmd) else line_end + 1

    if body_end is None:
        return None

    body = cmd[body_start:body_end]

    if NESTED_HEREDOC.search(body):
        return SecurityResult(
            blocked=True,
            check_id=3,
            behavior="ask",
            message="nested heredoc inside safe heredoc pattern",
        )

    if "$(" in body or "`" in body:
        return SecurityResult(
            blocked=True,
            check_id=3,
            behavior="ask",
            message="command substitution inside heredoc body",
        )

    remainder = cmd[closing_end:].strip().rstrip(")")
    if remainder and not SAFE_REMAINDER_CHARS.match(remainder):
        return SecurityResult(
            blocked=True,
            check_id=3,
            behavior="ask",
            message="unsafe characters in remainder after heredoc",
        )

    return SecurityResult(
        blocked=False,
        check_id=3,
        behavior="allow",
        message="safe heredoc with quoted delimiter",
    )


GIT_COMMIT_PATTERN = re.compile(
    r"""^\s*git\s+commit\s+"""
    r"""[^;&|`$<>()\n\r]*?"""
    r"""-m\s+"""
    r"""(['"])([\s\S]*?)\1"""
    r"""(.*)$""",
    re.I,
)


def validate_git_commit(ctx: ValidationContext) -> SecurityResult | None:
    """git commit -m with simple quoted message is safe.

    Blocks command substitution in double-quoted messages ($(), backticks, ${}).
    Catches redirect attacks and messages starting with dash (obfuscation).
    """
    cmd = ctx.original_command.strip()
    m = GIT_COMMIT_PATTERN.match(cmd)
    if not m:
        return None

    quote_char = m.group(1)
    message = m.group(2)
    remainder = m.group(3).strip()

    if message.startswith("-"):
        return SecurityResult(
            blocked=True,
            check_id=4,
            behavior="ask",
            message="git commit message starts with dash — possible flag obfuscation",
        )

    if quote_char == '"':
        if "$(" in message or "`" in message or "${" in message:
            return SecurityResult(
                blocked=True,
                check_id=4,
                behavior="ask",
                message="command substitution in git commit message",
            )

    if "\\" in message:
        return SecurityResult(
            blocked=True,
            check_id=4,
            behavior="ask",
            message="backslash in git commit message — possible quote boundary attack",
        )

    if remainder:
        if GIT_COMMIT_REMAINDER_METACHARS.search(remainder):
            return SecurityResult(
                blocked=True,
                check_id=4,
                behavior="ask",
                message="shell metacharacter in remainder after git commit message",
            )
        remainder_unquoted = extract_quoted_content(remainder)[1]
        if "<" in remainder_unquoted or ">" in remainder_unquoted:
            return SecurityResult(
                blocked=True,
                check_id=4,
                behavior="ask",
                message="redirect after git commit -m — possible redirect attack",
            )

    return SecurityResult(
        blocked=False,
        check_id=4,
        behavior="allow",
        message="safe git commit with simple message",
    )


JQ_FILE_FLAGS = re.compile(
    r"\bjq\b.*\s(-f|--from-file|--rawfile|--slurpfile|-L|--library-path)\b",
    re.I,
)


def validate_jq_command(ctx: ValidationContext) -> SecurityResult | None:
    """jq with system() executes shell commands. File flags can read arbitrary files."""
    cmd = ctx.original_command
    if "jq" not in cmd.lower():
        return None

    if JQ_SYSTEM.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=5,
            behavior="ask",
            message="jq system() executes shell commands",
        )

    if JQ_FILE_FLAGS.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=5,
            behavior="ask",
            message="jq file flag can read arbitrary files",
        )

    return None


ANSI_C_QUOTE = re.compile(r"\$'[^']*'")
LOCALE_QUOTE = re.compile(r'\$"[^"]*"')
EMPTY_SPECIAL_DASH = re.compile(r"""\$['"]["']+-""")
EMPTY_PAIR_DASH = re.compile(r'(?:^|[\s])(?:\'\'|"")-')
TRIPLE_QUOTES = re.compile(r"(?:^|[\s])['\x22]{3,}")
QUOTED_DASH_FLAG = re.compile(r"(?:^|[\s])['\x22]-[a-zA-Z]")
FLAG_CONTINUATION = re.compile(r"""-['"]+[a-zA-Z]|['"]-['"]*[a-zA-Z]""")
ADJACENT_QUOTE_CHAIN = re.compile(r"""['"]{2,}-|['"]+-['"]+""")
HOMO_EMPTY_QUOTED_DASH = re.compile(r"(?:'{3,}|\x22{3,})['\x22]?-[a-zA-Z]")
OBFUSCATED_FLAG = re.compile(r"""-[a-zA-Z]*['"][a-zA-Z]""")
DASH_IN_QUOTES = re.compile(r"""(['"])-[a-zA-Z]""")
CUT_D_PATTERN = re.compile(r"\bcut\b.*\s-d\s*['\x22.]")


def validate_obfuscated_flags(ctx: ValidationContext) -> SecurityResult | None:
    """Comprehensive obfuscation detection for flags using quoting tricks."""
    cmd = ctx.original_command

    if CUT_D_PATTERN.search(cmd):
        return None

    if ANSI_C_QUOTE.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message="ANSI-C quoting ($'...') can encode arbitrary characters including flags",
        )

    if LOCALE_QUOTE.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message='locale quoting ($"...") can alter string interpretation',
        )

    if EMPTY_SPECIAL_DASH.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message="empty special quote before dash — flag obfuscation",
        )

    if EMPTY_PAIR_DASH.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message="empty quote pair before dash — flag obfuscation",
        )

    if HOMO_EMPTY_QUOTED_DASH.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message="consecutive empty quotes adjacent to flag — obfuscation",
        )

    if TRIPLE_QUOTES.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message="3+ consecutive quotes — possible obfuscation",
        )

    if FLAG_CONTINUATION.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message="quote-interrupted flag — possible obfuscation",
        )

    if ADJACENT_QUOTE_CHAIN.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message="adjacent quote chain near dash — flag obfuscation",
        )

    if OBFUSCATED_FLAG.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message="obfuscated flag — quoted characters inside flag",
        )

    if QUOTED_DASH_FLAG.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=6,
            behavior="ask",
            message="quoted string starting with dash — possible hidden flag",
        )

    if DASH_IN_QUOTES.search(cmd):
        for m in DASH_IN_QUOTES.finditer(cmd):
            pos = m.start()
            if pos > 0 and cmd[pos - 1] in (" ", "\t"):
                return SecurityResult(
                    blocked=True,
                    check_id=6,
                    behavior="ask",
                    message="leading dash inside quoted string — possible hidden flag",
                )

    return None


META_IN_FLAG_VALUE = re.compile(
    r"""(-(?:name|path|iname|regex|exec)\s+)['"]([^'"]*[;&|][^'"]*)['"]""",
    re.I,
)


def validate_shell_metacharacters(ctx: ValidationContext) -> SecurityResult | None:
    """Shell metacharacters (;|&) inside quoted flag values like -name or -path."""
    cmd = ctx.original_command
    if META_IN_FLAG_VALUE.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=7,
            behavior="ask",
            message="shell metacharacter inside flag value — possible injection",
        )
    return None


VAR_NEAR_REDIRECT = re.compile(r"[<>|]\s*\$[A-Za-z_]|\$[A-Za-z_]\w*\s*[|<>]")


def validate_dangerous_variables(ctx: ValidationContext) -> SecurityResult | None:
    """$VAR near redirections or pipes — variable content could be attacker-controlled."""
    if VAR_NEAR_REDIRECT.search(ctx.fully_unquoted):
        return SecurityResult(
            blocked=True,
            check_id=8,
            behavior="ask",
            message="variable expansion near redirection or pipe",
        )
    return None


DANGEROUS_NEWLINE = re.compile(r"(?<![\s]\\)[\n\r]\s*\S")


def validate_newlines(ctx: ValidationContext) -> SecurityResult | None:
    """Embedded newlines can hide commands. Allows backslash-newline continuations."""
    cmd = ctx.original_command
    if "<<" in cmd:
        return None
    content = ctx.fully_unquoted_pre_strip
    if DANGEROUS_NEWLINE.search(content):
        return SecurityResult(
            blocked=True,
            check_id=9,
            behavior="ask",
            message="embedded newline — possible hidden command",
        )
    return None


UNESCAPED_BACKTICK = re.compile(r"(?<!\\)`")
CMD_SUBSTITUTION = re.compile(
    r"\$\("
    r"|\$\{"
    r"|\$\["
    r"|<\("
    r"|>\("
    r"|=\("
    r"|~\["
    r"|\(e:"
    r"|\(\+"
    r"|<#"
)
ZSH_EQUALS_EXPANSION = re.compile(r"(?<![=\w])=[a-zA-Z_]\w*")
ZSH_ALWAYS_BLOCK = re.compile(r"\}\s*always\s*\{")


def validate_dangerous_patterns(ctx: ValidationContext) -> SecurityResult | None:
    """Command substitution in all forms plus Zsh-specific patterns."""
    content = ctx.fully_unquoted

    if UNESCAPED_BACKTICK.search(content):
        return SecurityResult(
            blocked=True,
            check_id=10,
            behavior="ask",
            message="unescaped backtick — command substitution",
        )

    if CMD_SUBSTITUTION.search(content):
        return SecurityResult(
            blocked=True,
            check_id=10,
            behavior="ask",
            message="command substitution pattern detected",
        )

    if ZSH_EQUALS_EXPANSION.search(content):
        return SecurityResult(
            blocked=True,
            check_id=10,
            behavior="ask",
            message="Zsh =cmd expansion can execute arbitrary commands",
        )

    if ZSH_ALWAYS_BLOCK.search(content):
        return SecurityResult(
            blocked=True,
            check_id=10,
            behavior="ask",
            message="Zsh always block alters control flow",
        )

    return None


def validate_redirections(ctx: ValidationContext) -> SecurityResult | None:
    """Remaining < or > after safe redirection stripping are suspicious."""
    if has_unescaped_char(ctx.fully_unquoted, "<"):
        return SecurityResult(
            blocked=True,
            check_id=11,
            behavior="ask",
            message="input redirection detected",
        )
    if has_unescaped_char(ctx.fully_unquoted, ">"):
        return SecurityResult(
            blocked=True,
            check_id=11,
            behavior="ask",
            message="output redirection detected",
        )
    return None


IFS_ASSIGN = re.compile(r"\bIFS\s*=")
IFS_EXPAND = re.compile(r"\$IFS\b|\$\{[^}]*IFS[^}]*\}")


def validate_ifs_injection(ctx: ValidationContext) -> SecurityResult | None:
    """IFS assignment or expansion — changes word splitting behavior."""
    cmd = ctx.original_command
    if IFS_ASSIGN.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=12,
            behavior="ask",
            message="IFS assignment — changes word splitting behavior",
        )
    if IFS_EXPAND.search(ctx.fully_unquoted):
        return SecurityResult(
            blocked=True,
            check_id=12,
            behavior="ask",
            message="IFS expansion ($IFS/${IFS}) — injects separators",
        )
    return None


GIT_COMMIT_CMD = re.compile(r"\bgit\s+commit\b", re.I)


def validate_git_commit_substitution(ctx: ValidationContext) -> SecurityResult | None:
    """Deeper git commit checks beyond validator 4."""
    cmd = ctx.original_command
    if not GIT_COMMIT_CMD.search(cmd):
        return None

    unquoted = ctx.fully_unquoted
    if "$(" in unquoted or "`" in unquoted:
        return SecurityResult(
            blocked=True,
            check_id=13,
            behavior="ask",
            message="command substitution in git commit arguments",
        )

    if GIT_COMMIT_C_SUBSTITUTION.search(cmd):
        return SecurityResult(
            blocked=True,
            check_id=13,
            behavior="ask",
            message="git commit -C/-c with variable — possible substitution",
        )

    return None


PROC_ENVIRON = re.compile(r"/proc/[^/]*/environ")


def validate_proc_environ(ctx: ValidationContext) -> SecurityResult | None:
    """/proc/*/environ access leaks credentials and environment variables."""
    if PROC_ENVIRON.search(ctx.original_command):
        return SecurityResult(
            blocked=True,
            check_id=14,
            behavior="deny",
            message="/proc/*/environ access leaks credentials",
        )
    return None


def count_unescaped_delimiters(cmd: str) -> dict[str, int]:
    """Count unescaped delimiters respecting quote state."""
    counts: dict[str, int] = {"{": 0, "}": 0, "(": 0, ")": 0, "[": 0, "]": 0}
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n and not in_single:
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if not in_single and not in_double and c in counts:
            counts[c] += 1
        i += 1
    return counts


def has_command_separator(cmd: str) -> bool:
    """Check if command contains shell separators (;, |, &, &&, ||)."""
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n and not in_single:
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if c in (";", "|", "&"):
                return True
        i += 1
    return False


def validate_malformed_tokens(ctx: ValidationContext) -> SecurityResult | None:
    """Unbalanced quotes and delimiters exploit parser differentials.

    Checks: unbalanced quotes, unbalanced {}, (), [].
    Only flags delimiter imbalance when command separators present
    (reduces false positives on single-command JSON-like strings).
    """
    cmd = ctx.original_command

    single_count = 0
    double_count = 0
    i = 0
    n = len(cmd)
    while i < n:
        if cmd[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if cmd[i] == "'":
            single_count += 1
        elif cmd[i] == '"':
            double_count += 1
        i += 1

    if single_count % 2 != 0 or double_count % 2 != 0:
        return SecurityResult(
            blocked=True,
            check_id=15,
            behavior="ask",
            message="unbalanced quotes — possible parser confusion exploit",
            is_misparsing=True,
        )

    if has_command_separator(cmd):
        counts = count_unescaped_delimiters(cmd)
        if counts["{"] != counts["}"]:
            return SecurityResult(
                blocked=True,
                check_id=15,
                behavior="ask",
                message="unbalanced braces with command separator — possible injection",
                is_misparsing=True,
            )
        if counts["("] != counts[")"]:
            return SecurityResult(
                blocked=True,
                check_id=15,
                behavior="ask",
                message="unbalanced parentheses with command separator — possible injection",
                is_misparsing=True,
            )
        if counts["["] != counts["]"]:
            return SecurityResult(
                blocked=True,
                check_id=15,
                behavior="ask",
                message="unbalanced brackets with command separator — possible injection",
                is_misparsing=True,
            )

    return None


def validate_backslash_escaped_whitespace(ctx: ValidationContext) -> SecurityResult | None:
    r"""Backslash-space and backslash-tab outside quotes — path traversal vector.

    Also checks \n and \t literal sequences in unquoted content.
    echo\ test/../../../usr/bin/touch creates unexpected single tokens.
    """
    cmd = ctx.original_command
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)

    while i < n:
        c = cmd[i]
        if c == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if c == "\\" and i + 1 < n and not in_single:
            next_c = cmd[i + 1]
            if not in_double and next_c in (" ", "\t"):
                return SecurityResult(
                    blocked=True,
                    check_id=16,
                    behavior="ask",
                    message="backslash-escaped whitespace — possible path traversal",
                )
            if next_c in ("n", "t"):
                return SecurityResult(
                    blocked=True,
                    check_id=16,
                    behavior="ask",
                    message="backslash-escaped whitespace literal — possible hidden boundary",
                )
            i += 2
            continue
        i += 1

    return None


def find_brace_expansion(content: str) -> bool:
    """Depth-matching brace expansion detection.

    Finds matching } for each {, checking for , or .. at depth 0
    (actual expansion markers). Handles nested braces and escapes.
    """
    i = 0
    n = len(content)
    while i < n:
        if content[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if content[i] == "{" and not is_escaped_at_position(content, i):
            depth = 1
            has_comma = False
            has_dotdot = False
            j = i + 1
            while j < n and depth > 0:
                if content[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if content[j] == "{":
                    depth += 1
                elif content[j] == "}":
                    depth -= 1
                    if depth == 0:
                        if has_comma or has_dotdot:
                            return True
                elif depth == 1:
                    if content[j] == ",":
                        has_comma = True
                    elif content[j : j + 2] == "..":
                        has_dotdot = True
                j += 1
        i += 1
    return False


def has_mismatched_brace_count(original: str, unquoted: str) -> bool:
    """Detect when quoted { was stripped leaving more } than { in unquoted form."""
    open_orig = original.count("{") - original.count("\\{")
    close_orig = original.count("}") - original.count("\\}")
    open_unq = unquoted.count("{") - unquoted.count("\\{")
    close_unq = unquoted.count("}") - unquoted.count("\\}")

    if close_unq > open_unq and open_orig >= close_orig:
        return True
    return False


SENSITIVE_BRACE_PATHS = re.compile(
    r"\{[^}]*/etc/passwd[^}]*\}"
    r"|\{[^}]*/etc/shadow[^}]*\}"
    r"|\{[^}]*/dev/null[^}]*,[^}]*/etc/[^}]*\}"
)


def validate_brace_expansion(ctx: ValidationContext) -> SecurityResult | None:
    """Brace expansion detection with depth matching and mismatch detection."""
    content = ctx.fully_unquoted

    if has_mismatched_brace_count(ctx.original_command, content):
        return SecurityResult(
            blocked=True,
            check_id=17,
            behavior="ask",
            message="mismatched brace count — quoted brace stripped, possible expansion attack",
        )

    if ("'{'" in ctx.original_command or '"{' in ctx.original_command) and "{" in content:
        return SecurityResult(
            blocked=True,
            check_id=17,
            behavior="ask",
            message="quoted brace alongside unquoted brace — possible expansion bypass",
        )

    if SENSITIVE_BRACE_PATHS.search(content):
        return SecurityResult(
            blocked=True,
            check_id=17,
            behavior="deny",
            message="brace expansion targeting sensitive paths",
        )

    if find_brace_expansion(content):
        return SecurityResult(
            blocked=True,
            check_id=17,
            behavior="ask",
            message="brace expansion detected",
        )

    return None


CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_control_characters(ctx: ValidationContext) -> SecurityResult | None:
    """Invisible control characters (0x00-0x1F except 0x09 tab, 0x0A LF, 0x0D CR) + 0x7F DEL."""
    if CONTROL_CHARS.search(ctx.original_command):
        return SecurityResult(
            blocked=True,
            check_id=18,
            behavior="deny",
            message="invisible control character — possible command hiding",
            is_misparsing=True,
        )
    return None


UNICODE_WHITESPACE = re.compile(
    "["
    "\u00a0"
    "\u1680"
    "\u2000-\u200f"
    "\u2028\u2029"
    "\u202a-\u202e"
    "\u202f"
    "\u205f"
    "\u2060"
    "\u3000"
    "\ufeff"
    "]"
)


def validate_unicode_whitespace(ctx: ValidationContext) -> SecurityResult | None:
    """Unicode whitespace and homoglyph attacks."""
    if UNICODE_WHITESPACE.search(ctx.original_command):
        return SecurityResult(
            blocked=True,
            check_id=19,
            behavior="deny",
            message="Unicode whitespace or directional override — possible homoglyph attack",
        )
    return None


MID_WORD_HASH = re.compile(r"\S#")
HASH_LENGTH_SYNTAX = re.compile(r"\$\{#")


def validate_mid_word_hash(ctx: ValidationContext) -> SecurityResult | None:
    """# preceded by non-whitespace — possible comment injection.

    Uses unquoted_keep_quotes to catch quote-adjacent patterns like 'x'#.
    Excludes ${# (string-length syntax).
    """
    content = ctx.unquoted_keep_quotes

    if HASH_LENGTH_SYNTAX.search(ctx.original_command):
        return None

    if MID_WORD_HASH.search(content):
        return SecurityResult(
            blocked=True,
            check_id=20,
            behavior="ask",
            message="mid-word hash — possible comment injection",
        )
    return None


ZSH_DANGEROUS_CMDS = frozenset(ZSH_DANGEROUS_CMDS_TUPLE)


def validate_zsh_dangerous_commands(ctx: ValidationContext) -> SecurityResult | None:
    """Zsh-specific dangerous commands that bypass sandbox restrictions.

    Strips precommand modifiers (command, builtin, noglob, etc.) and env
    assignments before checking. Also blocks fc -e (arbitrary editor execution).
    """
    base = ctx.base_command
    if base in ZSH_DANGEROUS_CMDS:
        return SecurityResult(
            blocked=True,
            check_id=21,
            behavior="deny",
            message=f"Zsh builtin '{base}' bypasses sandbox",
        )

    if FC_E_PATTERN.search(ctx.fully_unquoted):
        return SecurityResult(
            blocked=True,
            check_id=21,
            behavior="deny",
            message="fc -e can execute arbitrary commands via editor",
        )

    return None


def validate_backslash_escaped_operators(ctx: ValidationContext) -> SecurityResult | None:
    r"""Backslash-escaped shell operators (\;, \|, \&, \<, \>) outside quotes.

    Quote-aware scanning. \; is the primary attack vector:
    find . -type f \; cat /etc/shadow
    """
    found, op = has_backslash_escaped_operator(ctx.original_command)
    if found:
        return SecurityResult(
            blocked=True,
            check_id=22,
            behavior="ask",
            message=f"backslash-escaped shell operator '\\{op}' — possible hidden command chaining",
            is_misparsing=True,
        )
    return None


def validate_comment_quote_desync(ctx: ValidationContext) -> SecurityResult | None:
    """# in unquoted position followed by quote chars on same line.

    When a # appears outside quotes followed by ' or " on the same line,
    it can desync quote trackers that don't handle comments correctly.
    """
    cmd = ctx.original_command
    in_single = False
    in_double = False
    i = 0
    n = len(cmd)

    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n and not in_single:
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "#" and not in_single and not in_double:
            rest_of_line = cmd[i + 1 :]
            newline_pos = rest_of_line.find("\n")
            if newline_pos != -1:
                rest_of_line = rest_of_line[:newline_pos]
            if "'" in rest_of_line or '"' in rest_of_line:
                return SecurityResult(
                    blocked=True,
                    check_id=23,
                    behavior="ask",
                    message="unquoted hash followed by quote chars — possible comment/quote desync",
                )
        i += 1

    return None


def validate_carriage_return(ctx: ValidationContext) -> SecurityResult | None:
    r"""Carriage return (\r) outside double quotes — parser differential.

    shell-quote's \s includes \r (treats as token boundary), but bash IFS
    doesn't include CR. This mismatch lets attackers split tokens differently
    than what the security analyzer expects.
    CR inside double quotes is safe (both parsers treat as data).
    """
    cmd = ctx.original_command
    if "\r" not in cmd:
        return None

    in_single = False
    in_double = False
    i = 0
    n = len(cmd)

    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n and not in_single:
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "\r" and not in_double:
            return SecurityResult(
                blocked=True,
                check_id=24,
                behavior="deny",
                message="carriage return outside double quotes — parser differential exploit",
                is_misparsing=True,
            )
        i += 1

    return None


def validate_shell_quote_bug(ctx: ValidationContext) -> SecurityResult | None:
    r"""Detect shell-quote library single-quote backslash bug.

    shell-quote treats \ as escape inside single quotes, but bash treats it
    as literal. '\\' in shell-quote consumes the closing quote, causing
    token merging. Odd trailing backslashes inside single quotes are always
    unsafe. Even trailing backslashes are unsafe if a later ' exists.
    """
    cmd = ctx.original_command
    if "'" not in cmd or "\\" not in cmd:
        return None

    i = 0
    n = len(cmd)
    while i < n:
        if cmd[i] == "'":
            i += 1
            trailing_backslashes = 0
            while i < n and cmd[i] != "'":
                if cmd[i] == "\\":
                    trailing_backslashes += 1
                else:
                    trailing_backslashes = 0
                i += 1
            if i >= n:
                break
            if trailing_backslashes > 0:
                if trailing_backslashes % 2 != 0:
                    return SecurityResult(
                        blocked=True,
                        check_id=25,
                        behavior="deny",
                        message="shell-quote single-quote backslash bug — odd trailing backslashes",
                        is_misparsing=True,
                    )
                if "'" in cmd[i + 1 :]:
                    return SecurityResult(
                        blocked=True,
                        check_id=25,
                        behavior="deny",
                        message="shell-quote single-quote backslash bug — even backslashes with later quote",
                        is_misparsing=True,
                    )
            i += 1
        else:
            i += 1

    return None


def validate_quoted_newline(ctx: ValidationContext) -> SecurityResult | None:
    r"""Newline inside quotes followed by line starting with #.

    stripCommentLines processes line-by-line without quote tracking.
    A newline inside quotes followed by a #-prefixed line hides arguments
    from line-based processing.
    """
    cmd = ctx.original_command
    if "\n" not in cmd:
        return None

    in_single = False
    in_double = False
    i = 0
    n = len(cmd)

    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n and not in_single:
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "\n" and (in_single or in_double):
            rest = cmd[i + 1 :]
            next_line = rest.split("\n", 1)[0].strip()
            if next_line.startswith("#"):
                return SecurityResult(
                    blocked=True,
                    check_id=26,
                    behavior="ask",
                    message="newline inside quotes followed by # — line-based processor bypass",
                    is_misparsing=True,
                )
        i += 1

    return None


EARLY_VALIDATORS: list[tuple[int, Callable[[ValidationContext], SecurityResult | None]]] = [
    (1, validate_empty),
    (2, validate_incomplete_commands),
    (3, validate_safe_heredoc),
    (4, validate_git_commit),
]

MISPARSING_VALIDATORS: list[tuple[int, Callable[[ValidationContext], SecurityResult | None]]] = [
    (18, validate_control_characters),
    (25, validate_shell_quote_bug),
    (24, validate_carriage_return),
    (22, validate_backslash_escaped_operators),
    (15, validate_malformed_tokens),
    (26, validate_quoted_newline),
]

STANDARD_VALIDATORS: list[tuple[int, Callable[[ValidationContext], SecurityResult | None]]] = [
    (5, validate_jq_command),
    (6, validate_obfuscated_flags),
    (7, validate_shell_metacharacters),
    (8, validate_dangerous_variables),
    (9, validate_newlines),
    (10, validate_dangerous_patterns),
    (11, validate_redirections),
    (12, validate_ifs_injection),
    (13, validate_git_commit_substitution),
    (14, validate_proc_environ),
    (16, validate_backslash_escaped_whitespace),
    (17, validate_brace_expansion),
    (19, validate_unicode_whitespace),
    (20, validate_mid_word_hash),
    (21, validate_zsh_dangerous_commands),
    (23, validate_comment_quote_desync),
]


def run_validators(ctx: ValidationContext) -> SecurityResult | None:
    """Run all validators with misparsing-priority deferral.

    1. Early validators run first (can short-circuit with allow)
    2. Misparsing validators run next (results returned immediately)
    3. Standard validators run last
    4. If a misparsing result was found, it takes priority over deferred non-misparsing ask
    """
    for check_id, validator in EARLY_VALIDATORS:
        result = validator(ctx)
        if result is not None:
            return result

    deferred_misparsing: SecurityResult | None = None
    for check_id, validator in MISPARSING_VALIDATORS:
        result = validator(ctx)
        if result is not None and result.blocked:
            if result.is_misparsing:
                return result
            if deferred_misparsing is None:
                deferred_misparsing = result

    deferred_standard: SecurityResult | None = None
    for check_id, validator in STANDARD_VALIDATORS:
        result = validator(ctx)
        if result is not None:
            if result.blocked:
                if deferred_standard is None:
                    deferred_standard = result
            elif result.behavior == "allow":
                return result

    if deferred_misparsing is not None:
        return deferred_misparsing
    if deferred_standard is not None:
        return deferred_standard

    return None


def analyze_bash_security(command: str) -> SecurityResult:
    """Run all security validators against a command.

    Splits compound commands and validates each subcommand independently.
    First block wins. If any subcommand is blocked, the whole command is blocked.
    An early allow on the full command short-circuits everything.
    """
    if not command or not command.strip():
        return SecurityResult(blocked=False, check_id=1, behavior="allow", message="empty command")

    full_ctx = build_context(command)
    full_result = run_validators(full_ctx)
    if full_result is not None:
        if full_result.behavior == "allow" or full_result.blocked:
            return full_result

    subcommands = split_compound(command)
    if len(subcommands) > 1:
        for sub in subcommands:
            sub_stripped = sub.strip()
            if not sub_stripped:
                continue
            sub_ctx = build_context(sub_stripped)
            result = run_validators(sub_ctx)
            if result is not None and result.blocked:
                return result

    return PASSTHROUGH
