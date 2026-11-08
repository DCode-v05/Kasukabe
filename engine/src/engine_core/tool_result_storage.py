"""tool_result_storage — disk-spill cap for oversized tool results.

When a tool produces output larger than its per-tool cap, the full
text is written to a file in the thread's results directory and the
``ToolResultBuilder`` is collapsed to a single ``PersistedBlock`` that
points the model at the file with a short head excerpt.

This protects the context window from one large ``Bash`` or ``Read``
result blowing the budget. The src reference is
``toolResultStorage.ts`` in the Claude Code TypeScript codebase, which
uses the same default 50K-char cap with per-tool overrides and an
inline retrieval hint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from src.engine_core.tool_result_builder import ToolResultBuilder
from src.herald.types import PersistedBlock, TextBlock, format_persisted_block_xml


# Default cap if a tool name has no override below. ~50K chars =
# ~25K tokens at the dense estimation rate, which is roughly 12% of
# a 200K context window for a single tool result — already on the
# edge of "too much."
DEFAULT_MAX_RESULT_SIZE_CHARS: int = 50_000

# Per-tool overrides. Bash gets a higher cap because compile/build
# logs and test runs are often legitimately large. Read gets a lower
# cap because it's typically used for source files where 30K is
# already a 700+ line file. Web fetch sits between.
PER_TOOL_RESULT_CAPS: dict[str, int] = {
    "Bash": 100_000,
    "bash": 100_000,
    "run_bash": 100_000,
    "Read": 30_000,
    "read": 30_000,
    "file_read": 30_000,
    "web_fetch": 80_000,
    "WebFetch": 80_000,
}

# Length of the head excerpt that rides on the PersistedBlock as a
# retrieval hint for the model. Long enough that the model can usually
# answer follow-up questions from the excerpt alone; short enough that
# spilling 1MB of output costs <1KB of context.
PERSISTED_SUMMARY_CHARS: int = 500


def get_tool_cap(tool_name: str) -> int:
    """Return the per-tool char cap, or the default."""
    return PER_TOOL_RESULT_CAPS.get(tool_name, DEFAULT_MAX_RESULT_SIZE_CHARS)


def persist_oversized_result(
    text: str,
    tool_use_id: str,
    results_dir: Path,
) -> PersistedBlock:
    """Write the full ``text`` to ``results_dir/<tool_use_id>.txt`` and
    return a ``PersistedBlock`` carrying the path and a short summary.

    The results directory is created if it doesn't exist. ``tool_use_id``
    is sanitized for filesystem use (it's normally a model-generated
    string like ``"toolu_01ABC..."`` so this is mostly defensive).
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_use_id)
    out_path = results_dir / f"{safe_id}.txt"
    out_path.write_text(text, encoding="utf-8")

    summary = text[:PERSISTED_SUMMARY_CHARS]
    if len(text) > PERSISTED_SUMMARY_CHARS:
        summary += "..."

    return PersistedBlock(path=str(out_path), summary=summary)


def cap_builder_size(
    builder: ToolResultBuilder,
    tool_name: str,
    results_dir: "Path | Callable[[], Path]",
) -> None:
    """Enforce the per-tool size cap on ``builder``.

    If ``builder.total_chars()`` exceeds ``get_tool_cap(tool_name)``,
    persist the concatenated text content to disk and replace the
    builder's blocks with a single ``PersistedBlock``. The full text
    written to disk is the concatenation of every text-bearing block
    in the builder, in order — this preserves the post-tool injection
    pipeline's accumulated content (base + permission feedback + hook
    attachments) so the model can still retrieve everything from the
    file even after the inline view collapses to a hint.

    ``results_dir`` may be a ``Path`` or a zero-arg callable returning
    a ``Path``. The callable form lets hot callers (e.g. Thread) defer
    creating a temp directory until the cap is actually exceeded —
    Python's eager argument evaluation would otherwise materialize a
    lazy ``results_dir`` property on every tool call regardless of
    whether persistence triggers.

    No-op if the builder is under cap. The caller is responsible for
    providing the directory; this module never owns directories.
    """
    cap = get_tool_cap(tool_name)
    if builder.total_chars() <= cap:
        return

    if callable(results_dir):
        results_dir = results_dir()

    # Concatenate all text-bearing blocks for the on-disk record.
    # We reach into the builder's internal block list deliberately —
    # this is the only place outside ToolResultBuilder that needs raw
    # block access, and we keep it private by living in the same
    # package.
    parts: list[str] = []
    for block in builder.blocks:  # noqa: SLF001 — same-package access
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, PersistedBlock):
            # Already persisted — preserve the hint inline so the
            # final on-disk record points back to the prior file.
            parts.append(format_persisted_block_xml(block))
        # ImageBlock contributes nothing to the on-disk text record.
    full_text = "\n\n".join(parts)

    persisted = persist_oversized_result(
        text=full_text,
        tool_use_id=builder.tool_use_id,
        results_dir=results_dir,
    )
    builder.replace_with_persisted(path=persisted.path, summary=persisted.summary)
