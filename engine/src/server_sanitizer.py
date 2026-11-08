"""Codename scrubber for outbound response bodies.

Prevents internal module paths from leaking into any 4xx/5xx response body,
SSE event data, or error detail field served to the product layer. The
engine's Herald and Raven sub-systems are internal codenames that must never
surface in user-facing artifacts; this module provides the primitives that
ensure it.

Wire-up happens in ``server.py`` at three boundaries: the SSE event
serializer, the global exception handler's JSONResponse body, and
``error_from_exception``'s ``details`` assembly. This module itself is pure
functions with no I/O.
"""

from __future__ import annotations

from typing import Any

FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "src.herald",
    "src.raven",
)

REDACTION_MARKER: str = "[redacted]"


def scrub_text(text: str) -> str:
    """Replace every forbidden substring in ``text`` with the redaction marker.

    Pure, deterministic, case-sensitive. Empty / non-string inputs are
    handled at call sites via ``scrub_mapping``; this function's contract
    assumes ``text`` is a string.
    """
    if not text:
        return text
    result = text
    for banned in FORBIDDEN_SUBSTRINGS:
        if banned in result:
            result = result.replace(banned, REDACTION_MARKER)
    return result


def scrub_mapping(obj: Any) -> Any:
    """Recursively apply ``scrub_text`` to every string leaf in ``obj``.

    Walks dicts (both keys and values), lists, and tuples. Scalar leaves
    that are not strings (int, float, bool, None) are returned unchanged.
    Unknown container types are returned as-is.
    """
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        return {
            (scrub_text(k) if isinstance(k, str) else k): scrub_mapping(v) for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [scrub_mapping(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(scrub_mapping(item) for item in obj)
    return obj


def contains_forbidden(text: str) -> bool:
    """Return True if ``text`` contains any forbidden substring.

    Used by unit tests and CI jobs that grep response bodies for leakage.
    """
    if not text:
        return False
    return any(banned in text for banned in FORBIDDEN_SUBSTRINGS)
