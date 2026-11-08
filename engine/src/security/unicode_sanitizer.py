"""Unicode sanitization for all inbound user inputs.

Strips invisible Unicode characters that enable hidden prompt injection
("ASCII smuggling"). Attack vector: invisible format characters, tag
characters (U+E0001-E007F), directional overrides, zero-width spaces, and
private-use codepoints embed instructions a human can't see but an LLM
reads and follows.

Reference: HackerOne #3086545 (Claude Desktop MCP), embracethered.com
blog on Unicode tag-based hidden prompt injection.

Applied as ASGI middleware so every HTTP endpoint is protected
automatically — no per-handler wiring required.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_ITERATIONS: int = 10
MAX_RECURSION_DEPTH: int = 64

DANGEROUS_CATEGORIES: frozenset[str] = frozenset({"Cf", "Co", "Cn"})

# Explicit ranges as defense-in-depth against unicodedata version skew.
# Overlaps with the category check intentionally.
EXPLICIT_DANGEROUS: re.Pattern[str] = re.compile(
    "[\u200b-\u200f]"  # zero-width spaces, LTR/RTL marks
    "|[\u202a-\u202e]"  # directional formatting (LRE, RLE, PDF, LRO, RLO)
    "|[\u2066-\u2069]"  # directional isolates (LRI, RLI, FSI, PDI)
    "|\ufeff"  # byte order mark
    "|[\ue000-\uf8ff]"  # BMP private use area
    "|[\U000e0000-\U000e007f]"  # tag characters — THE ASCII Smuggling vector (HackerOne #3086545)
    "|[\U000f0000-\U000ffffd]"  # supplementary private use area-A (plane 15)
    "|[\U00100000-\U0010fffd]"  # supplementary private use area-B (plane 16)
)


# ---------------------------------------------------------------------------
# Core sanitizer
# ---------------------------------------------------------------------------


def sanitize_unicode(text: str) -> str:
    """Strip dangerous invisible Unicode from *text*.

    Algorithm (direct port of src's ``partiallySanitizeUnicode``):
    1. If pure ASCII, return immediately (fast path).
    2. Iteratively: NFKC normalize → strip null bytes → strip Cf/Co/Cn
       category chars → strip explicit dangerous ranges.
    3. Repeat until stable (max ``MAX_ITERATIONS`` rounds).
    4. Raise ``ValueError`` if the loop doesn't converge.
    """
    if not text:
        return text

    # Null bytes are Cc category (not caught by Cf/Co/Cn) and
    # "\x00".isascii() is True — must strip before the ASCII fast path.
    if "\x00" in text:
        text = text.replace("\x00", "")
        if not text:
            return text

    if text.isascii():
        return text

    current = text
    previous = ""
    iterations = 0

    while current != previous and iterations < MAX_ITERATIONS:
        previous = current
        current = unicodedata.normalize("NFKC", current)
        current = current.replace("\x00", "")
        current = "".join(
            ch for ch in current if unicodedata.category(ch) not in DANGEROUS_CATEGORIES
        )
        current = EXPLICIT_DANGEROUS.sub("", current)
        iterations += 1

    if iterations >= MAX_ITERATIONS:
        raise ValueError(
            f"Unicode sanitization did not converge after {MAX_ITERATIONS} "
            f"iterations on input: {text[:100]!r}"
        )

    return current


# ---------------------------------------------------------------------------
# Recursive walker
# ---------------------------------------------------------------------------


def sanitize_value(value: Any, depth: int = 0) -> Any:
    """Recursively sanitize every string leaf in a nested structure.

    Walks dicts (keys AND values), lists, and tuples. Primitives (int,
    float, bool, None) pass through unchanged.

    Raises ``ValueError`` if recursion exceeds ``MAX_RECURSION_DEPTH``
    (defense against stack overflow from deeply nested payloads).
    """
    if depth > MAX_RECURSION_DEPTH:
        raise ValueError(
            f"Recursion depth {depth} exceeds limit {MAX_RECURSION_DEPTH} "
            "during Unicode sanitization"
        )

    if isinstance(value, str):
        return sanitize_unicode(value)

    if isinstance(value, dict):
        return {
            (sanitize_unicode(k) if isinstance(k, str) else k): sanitize_value(v, depth + 1)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [sanitize_value(item, depth + 1) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_value(item, depth + 1) for item in value)

    return value


# ---------------------------------------------------------------------------
# ASGI middleware
# ---------------------------------------------------------------------------


async def read_body(receive: Any) -> bytes:
    """Buffer the complete request body from ASGI receive calls."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        chunk = message.get("body", b"")
        if chunk:
            chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def get_header(scope: dict, name: bytes) -> bytes | None:
    """Extract a header value from ASGI scope (case-insensitive)."""
    for header_name, header_value in scope.get("headers", []):
        if header_name.lower() == name:
            return header_value
    return None


def update_content_length(scope: dict, new_length: int) -> None:
    """Replace Content-Length header in scope with *new_length*."""
    headers = scope.get("headers", [])
    scope["headers"] = [
        (name, value) for name, value in headers if name.lower() != b"content-length"
    ] + [(b"content-length", str(new_length).encode("ascii"))]


class UnicodeSanitizationMiddleware:
    """Raw ASGI middleware that sanitizes Unicode in all inbound requests.

    Uses raw ASGI (not Starlette's BaseHTTPMiddleware) to avoid breaking
    SSE streaming on endpoints like /execute.

    Sanitizes: URL path, query string, and JSON request bodies
    (POST/PUT/PATCH with application/json).
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    # Paths that authenticate via HMAC over the raw request body. Mutating
    # the body here would invalidate the signature, so we skip them
    # entirely. They run their own input validation (HMAC + timestamp
    # freshness + signature compare) which is stricter than this layer.
    HMAC_BYPASS_PATHS: frozenset[str] = frozenset()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        raw_path = scope.get("path", "")
        if raw_path in self.HMAC_BYPASS_PATHS:
            return await self.app(scope, receive, send)
        if raw_path and not raw_path.isascii():
            scope["path"] = sanitize_unicode(raw_path)

        raw_qs = scope.get("query_string", b"")
        if raw_qs:
            try:
                decoded_qs = raw_qs.decode("utf-8")
                if not decoded_qs.isascii():
                    scope["query_string"] = sanitize_unicode(decoded_qs).encode("utf-8")
            except UnicodeDecodeError:
                pass  # non-UTF-8 query strings pass through

        if scope.get("method", "") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)

        content_type = get_header(scope, b"content-type")
        if not content_type or b"application/json" not in content_type:
            return await self.app(scope, receive, send)

        body = await read_body(receive)
        if not body:

            async def empty_receive() -> dict:
                return {"type": "http.request", "body": b"", "more_body": False}

            return await self.app(scope, empty_receive, send)

        # Cannot skip parse for ASCII bodies — JSON escape sequences
        # (\u200b) are ASCII bytes but decode to dangerous Unicode.
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            sanitized_body = body
        else:
            try:
                sanitized = sanitize_value(parsed)
                sanitized_body = json.dumps(sanitized, ensure_ascii=False).encode("utf-8")
            except ValueError as exc:
                logger.warning("Unicode sanitization failed: %s", exc)
                error_body = json.dumps(
                    {
                        "error": {
                            "code": "invalid_params",
                            "message": str(exc),
                            "retryable": False,
                        }
                    }
                ).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 400,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (
                                b"content-length",
                                str(len(error_body)).encode("ascii"),
                            ),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": error_body,
                        "more_body": False,
                    }
                )
                return

        if get_header(scope, b"content-length") is not None:
            update_content_length(scope, len(sanitized_body))

        # After body is delivered, delegate to original receive so downstream
        # handlers can still detect http.disconnect from the client.
        body_sent = False

        async def sanitized_receive() -> dict:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": sanitized_body,
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, sanitized_receive, send)
