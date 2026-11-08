"""Secret scanner — lazy-compiled regex engine with label-only exposure.

The scanner compiles all rules on first use (lazy) and caches the compiled
`re.Pattern` objects in a dict keyed by rule ID. `scan()` returns a list of
`SecretMatch` containing only the rule ID and human-readable label — the
matched text is NEVER returned, logged, or stored. This label-only contract
is the scanner's primary safety invariant.

`redact()` is provided for logging/debug contexts where content must be
displayed but sanitized; it replaces matched text with `[REDACTED]`.

`SecretDetectedError` is raised by higher-level guard helpers (see
`write_guard.py`) when a scan produces any matches and the caller wants
exception-based control flow.
"""

from __future__ import annotations

import re
from typing import Literal, NamedTuple

from src.security.secret_rules import RULES, SecretRule


# Field-name-bounded JSON value redaction. Catches opaque tokens (internal
# session IDs, custom auth headers) that don't match any format-based rule
# in RULES but appear inside a JSON object with a sensitive field name.
# Complements the format scan — field regex catches unknown-shape secrets,
# format regex catches known-shape secrets anywhere in free text.
JSON_FIELD_NAMES = (
    "access_token",
    "api_key",
    "authorization",
    "bearer_token",
    "client_secret",
    "environment_secret",
    "password",
    "refresh_token",
    "secret",
    "session_ingress_token",
    "token",
)
JSON_FIELD_RE = re.compile(
    r'"(' + "|".join(JSON_FIELD_NAMES) + r')"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
    re.IGNORECASE,
)

# Minimum captured-value length for tail-preserving redaction. Shorter
# values are fully masked because the tail preview would leak a large
# fraction of the secret.
TAIL_MIN_LENGTH = 16
TAIL_PREFIX = 8
TAIL_SUFFIX = 4

RedactMode = Literal["full", "tail"]


def render_redaction(value: str, mode: RedactMode) -> str:
    """Return the replacement text for a captured secret value."""
    if mode == "tail" and len(value) >= TAIL_MIN_LENGTH:
        return f"{value[:TAIL_PREFIX]}...{value[-TAIL_SUFFIX:]}"
    return "[REDACTED]"


class SecretMatch(NamedTuple):
    rule_id: str
    label: str


class SecretDetectedError(Exception):
    """Raised when a scan detects one or more secrets in content.

    The `labels` attribute is the only source of truth for what was found;
    the matched text is never captured here. `str(exc)` renders a concise
    human-readable summary listing the labels (no values).
    """

    def __init__(self, labels: list[str], *, where: str = "") -> None:
        self.labels = labels
        self.where = where
        detail = ", ".join(labels)
        location = f" [{where}]" if where else ""
        super().__init__(f"Secret(s) detected{location}: {detail}")


class SecretScanner:
    """Lazy-compiled regex scanner over the `RULES` catalog."""

    def __init__(self, rules: tuple[SecretRule, ...] = RULES) -> None:
        self.rules = rules
        self.compiled: dict[str, re.Pattern[str]] | None = None

    def compile(self) -> dict[str, re.Pattern[str]]:
        if self.compiled is None:
            self.compiled = {rule.id: re.compile(rule.pattern, rule.flags) for rule in self.rules}
        return self.compiled

    def scan(self, content: str | None) -> list[SecretMatch]:
        """Return one match per rule that fired. Never returns matched text."""
        if not content or not isinstance(content, str):
            return []
        compiled = self.compile()
        return [
            SecretMatch(rule_id=rule.id, label=rule.label)
            for rule in self.rules
            if compiled[rule.id].search(content)
        ]

    def redact(self, content: str | None, *, mode: RedactMode = "full") -> str:
        """Replace matched secret values with `[REDACTED]`. Safe for logs.

        For rules with a capture group, only the captured secret value is
        replaced — the surrounding boundary characters (trailing space,
        quote, semicolon, etc.) are preserved so the surrounding context
        stays readable. For rules without a capture group, the whole match
        is replaced.

        `mode="tail"` preserves the first 8 and last 4 chars of captured
        values of length >= 16 (e.g. `abcd1234...wxyz`), which lets an
        operator distinguish between different leaked credentials in a
        log without exposing the full secret. Values shorter than 16
        chars, and matches without a capture group, still redact fully.
        """
        if not content or not isinstance(content, str):
            return content or ""
        compiled = self.compile()

        def replace(match: re.Match[str]) -> str:
            # Patterns with a capture group: preserve boundary chars outside
            # the group via span arithmetic (avoids an edge-case bug where
            # the captured value coincidentally reoccurs in the boundary).
            if match.lastindex:
                full_start, full_end = match.span(0)
                cap_start, cap_end = match.span(1)
                return (
                    match.string[full_start:cap_start]
                    + render_redaction(match.group(1), mode)
                    + match.string[cap_end:full_end]
                )
            return "[REDACTED]"

        result = content
        for rule in self.rules:
            result = compiled[rule.id].sub(replace, result)
        return result

    def redact_json_fields(self, content: str | None, *, mode: RedactMode = "full") -> str:
        """Redact values of known sensitive JSON field names.

        Complements `redact()`: the format scanner only catches values that
        match a gitleaks-shaped pattern, so opaque/custom tokens (e.g.
        internal session IDs, proxy bearer tokens) in serialized HTTP
        bodies would slip through. This pass redacts the value of any
        JSON key whose name is in `JSON_FIELD_NAMES`, regardless of the
        value's shape. Case-insensitive on the field name so
        `"Authorization"` and `"authorization"` both match.

        Escaped quotes inside the value are handled — the inner regex
        consumes `\\"` sequences without closing the string.
        """
        if not content or not isinstance(content, str):
            return content or ""

        def sub(m: re.Match[str]) -> str:
            field = m.group(1)
            value = m.group(2)
            return f'"{field}":"{render_redaction(value, mode)}"'

        return JSON_FIELD_RE.sub(sub, content)


# Module-level singleton. Import-time cost is zero; the first scan() call
# compiles the full rule catalog into the per-instance cache.
scanner = SecretScanner()


def get_scanner() -> SecretScanner:
    """Return the module-level singleton scanner."""
    return scanner
