"""Write guard — two entrypoints over the scanner singleton.

`check_content_for_secrets` is the non-raising variant: returns a list of
human-readable labels when secrets are found, `None` when clean. Callers use
it for conditional control flow (e.g. "skip this write and increment a
counter").

`guard_content` is the raising variant: calls the scanner and raises
`SecretDetectedError` on any match. Callers use it when the natural control
flow is exception-based (e.g. `SignalStore.write` — the caller further up the
stack handles the error).

Both helpers are thin wrappers over the module-level scanner singleton in
`secret_scanner.py`. Non-string or empty input is a no-op.
"""

from __future__ import annotations

from src.security.secret_scanner import (
    SecretDetectedError,
    get_scanner,
)


def check_content_for_secrets(content: str | None) -> list[str] | None:
    """Scan `content` and return labels if any secrets were found.

    Returns `None` for clean content, `None` for empty/non-string input,
    and `list[str]` of human-readable labels on match. Never raises.
    """
    matches = get_scanner().scan(content)
    if not matches:
        return None
    return [m.label for m in matches]


def guard_content(content: str | None, *, where: str) -> None:
    """Scan `content` and raise `SecretDetectedError` on match.

    `where` is a short caller-identifier string included in the exception
    message for debugging (e.g. `"signal-ingest"`, `"lc-knowledge-write"`).
    """
    matches = get_scanner().scan(content)
    if matches:
        raise SecretDetectedError(
            labels=[m.label for m in matches],
            where=where,
        )


__all__ = [
    "check_content_for_secrets",
    "guard_content",
    "SecretDetectedError",
]
