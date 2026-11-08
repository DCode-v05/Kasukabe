"""ToolResultBuilder — composer for tool execution results.

Replaces the bare ``ToolResultBlock(content=str)`` construction with a
composable pipeline that accumulates the tool's base output, then any
additional content (permission feedback, post-tool hook attachments,
images, persisted-output hints) before the result reaches the message
array.

Used by ``Thread.run_single_tool`` after a tool handler returns. The
builder is single-use: instantiate, append, build, discard.

Backward compatibility: when a builder holds exactly one unlabeled
text block, ``build()`` returns a ``ToolResultBlock`` with ``content``
as a plain string — the same shape every legacy call site produces.
Multi-block / labeled / image / persisted contents return ``content``
as a ``list[ContentBlock]``.

The src reference for this pipeline is ``toolExecution.ts:1418-1438``
in the Claude Code TypeScript codebase, which builds the same kind of
content-block accumulation around the tool result.
"""

from __future__ import annotations

from src.herald.types import (
    ContentBlock,
    ImageBlock,
    PersistedBlock,
    TextBlock,
    ToolResultBlock,
)


class ToolResultBuilder:
    """Compose a ToolResultBlock from multiple content sources.

    The builder starts with the tool handler's raw text output as its
    base, then accepts subsequent appends from the post-tool injection
    pipeline. Call ``build()`` once at the end to get the immutable
    ``ToolResultBlock``.
    """

    def __init__(
        self,
        tool_use_id: str,
        base_result: str,
        is_error: bool = False,
        tool_name: str = "",
        source_assistant_id: str = "",
    ) -> None:
        self.tool_use_id = tool_use_id
        self.is_error = is_error
        self.source_assistant_id = source_assistant_id
        self.tool_name = tool_name
        self.blocks: list[ContentBlock] = [TextBlock(text=base_result)]
        self.labels: list[str | None] = [None]

    def append_text(self, text: str, label: str | None = None) -> None:
        """Append a text block with an optional label.

        A label is a short tag (e.g. ``"permission"``, ``"hook"``)
        rendered inline so the model can tell apart attachments from
        the tool's own output. When ``label`` is set, the rendered
        block becomes ``"[<label>]\\n<text>"``. When ``label`` is
        ``None``, the text is appended as-is.

        Empty text is silently ignored — no point in adding an empty
        block to the message array.
        """
        if not text:
            return
        if label:
            text = f"[{label}]\n{text}"
        self.blocks.append(TextBlock(text=text))
        self.labels.append(label)

    def append_image(self, source: dict) -> None:
        """Append an image block. ``source`` is a provider-neutral dict
        (e.g. ``{"type": "base64", "media_type": "image/png", "data": ...}``)
        that the Anthropic provider serializes natively. OpenAI and
        Gemini providers currently drop image blocks (see provider
        serializer comments).
        """
        self.blocks.append(ImageBlock(source=source))
        self.labels.append(None)

    def append_persisted(self, path: str, summary: str) -> None:
        """Append a ``PersistedBlock`` pointing to an oversized result
        that has been spilled to disk. ``summary`` is a short head
        excerpt the model sees as a retrieval hint."""
        self.blocks.append(PersistedBlock(path=path, summary=summary))
        self.labels.append(None)

    def replace_with_persisted(self, path: str, summary: str) -> None:
        """Replace **all** current blocks with a single ``PersistedBlock``.

        Used by the size-cap enforcement layer when the accumulated
        builder content exceeds the per-tool cap: the full text gets
        written to disk, and the in-memory builder is collapsed to a
        single retrieval hint.
        """
        self.blocks = [PersistedBlock(path=path, summary=summary)]
        self.labels = [None]

    def total_chars(self) -> int:
        """Sum of text lengths across all blocks.

        Image blocks contribute zero. Persisted blocks contribute the
        length of their summary (the actual file is on disk and is not
        counted against the in-memory budget).
        """
        total = 0
        for block in self.blocks:
            if isinstance(block, TextBlock):
                total += len(block.text)
            elif isinstance(block, PersistedBlock):
                total += len(block.summary)
        return total

    def is_effectively_empty(self) -> bool:
        """True when no block contributes meaningful content.

        A builder is "effectively empty" when every text block has
        empty or whitespace-only text AND there are no image or
        persisted blocks. The empty-content guard in ``build()`` uses
        this to substitute a marker so the model never sees a
        completely empty ``tool_result.content``.
        """
        from src.herald.types import ImageBlock, PersistedBlock

        for block in self.blocks:
            if isinstance(block, TextBlock):
                if block.text and block.text.strip():
                    return False
            elif isinstance(block, (ImageBlock, PersistedBlock)):
                return False
        return True

    def build(self) -> ToolResultBlock:
        """Assemble and return the immutable ``ToolResultBlock``.

        Empty-content guard: if every block is effectively empty
        (whitespace-only text, no images, no persisted blocks), the
        builder substitutes a single text block with a marker like
        ``"(Bash completed with no output)"``. This protects against
        a documented model bug where empty ``tool_result.content``
        matches a stop-sequence regex and the model ends its turn
        with zero output (src toolResultStorage.ts inc-4586). The
        marker uses ``tool_name`` if set, otherwise the generic
        ``"tool"`` label.

        Returns the legacy plain-string shape when there's exactly one
        unlabeled text block — this preserves backward compatibility
        with every existing call site. Returns the list-content shape
        otherwise (multiple blocks, or any non-text block, or any
        labeled block).
        """
        if self.is_effectively_empty():
            label = self.tool_name or "tool"
            marker = f"({label} completed with no output)"
            return ToolResultBlock(
                tool_use_id=self.tool_use_id,
                content=marker,
                is_error=self.is_error,
                source_assistant_id=self.source_assistant_id,
            )

        is_single_unlabeled_text = (
            len(self.blocks) == 1
            and isinstance(self.blocks[0], TextBlock)
            and self.labels[0] is None
        )
        content: str | list[ContentBlock]
        if is_single_unlabeled_text:
            content = self.blocks[0].text  # type: ignore[union-attr]
        else:
            content = list(self.blocks)
        return ToolResultBlock(
            tool_use_id=self.tool_use_id,
            content=content,
            is_error=self.is_error,
            source_assistant_id=self.source_assistant_id,
        )
