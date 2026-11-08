"""Unit tests for ToolResultBuilder.

Pure logic — no LLM, no I/O, no mocks. Tests the builder's accumulation,
size accounting, and the str-vs-list build() collapse semantics.
"""

from src.engine_core.tool_result_builder import ToolResultBuilder
from src.herald.types import (
    ImageBlock,
    PersistedBlock,
    TextBlock,
    ToolResultBlock,
)


class TestBuilderBasics:
    def test_only_base_text_builds_to_plain_string(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="hello")
        result = b.build()
        assert isinstance(result, ToolResultBlock)
        assert result.tool_use_id == "t1"
        assert result.content == "hello"  # legacy str shape
        assert result.is_error is False

    def test_is_error_propagates(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="oops", is_error=True)
        result = b.build()
        assert result.is_error is True
        assert result.content == "oops"

    def test_empty_base_text_substitutes_marker(self):
        # Empty content guard: a builder with empty base_result and
        # no appends must NOT produce an empty tool_result.content,
        # because some models hit a stop-sequence regex on that
        # shape (src toolResultStorage.ts inc-4586).
        b = ToolResultBuilder(tool_use_id="t1", base_result="")
        result = b.build()
        assert result.content == "(tool completed with no output)"

    def test_empty_base_text_with_tool_name_uses_name_in_marker(self):
        b = ToolResultBuilder(
            tool_use_id="t1",
            base_result="",
            tool_name="Bash",
        )
        result = b.build()
        assert result.content == "(Bash completed with no output)"

    def test_tool_use_id_property(self):
        b = ToolResultBuilder(tool_use_id="my_id", base_result="x")
        assert b.tool_use_id == "my_id"

    def test_is_error_property(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="x", is_error=True)
        assert b.is_error is True


class TestAppendText:
    def test_unlabeled_append_makes_list(self):
        # Two unlabeled text blocks must produce list content (cannot
        # be collapsed to a string because they're separate blocks).
        b = ToolResultBuilder(tool_use_id="t1", base_result="base")
        b.append_text("more")
        result = b.build()
        assert isinstance(result.content, list)
        assert len(result.content) == 2
        assert result.content[0] == TextBlock(text="base")
        assert result.content[1] == TextBlock(text="more")

    def test_labeled_append_renders_label_inline(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="base")
        b.append_text("allowed by rule: Bash(*)", label="permission")
        result = b.build()
        assert isinstance(result.content, list)
        assert len(result.content) == 2
        assert result.content[1] == TextBlock(text="[permission]\nallowed by rule: Bash(*)")

    def test_labeled_first_append_still_lists(self):
        # Even if the base block is unlabeled and there's only one
        # additional labeled block, we still emit list-content because
        # the label carries information that a flat join would lose.
        b = ToolResultBuilder(tool_use_id="t1", base_result="base")
        b.append_text("hook says hi", label="hook")
        result = b.build()
        assert isinstance(result.content, list)

    def test_empty_text_append_is_ignored(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="base")
        b.append_text("")
        b.append_text("", label="permission")
        # Builder should still collapse to plain string — empty appends
        # didn't add anything.
        result = b.build()
        assert result.content == "base"

    def test_multiple_labeled_appends(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="base")
        b.append_text("perm note", label="permission")
        b.append_text("hook note", label="hook")
        b.append_text("another hook note", label="hook")
        result = b.build()
        assert isinstance(result.content, list)
        assert len(result.content) == 4
        texts = [blk.text for blk in result.content if isinstance(blk, TextBlock)]
        assert texts == [
            "base",
            "[permission]\nperm note",
            "[hook]\nhook note",
            "[hook]\nanother hook note",
        ]


class TestAppendImage:
    def test_image_append_lists(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="see image")
        src = {"type": "base64", "media_type": "image/png", "data": "deadbeef"}
        b.append_image(src)
        result = b.build()
        assert isinstance(result.content, list)
        assert len(result.content) == 2
        assert result.content[0] == TextBlock(text="see image")
        assert result.content[1] == ImageBlock(source=src)


class TestAppendPersisted:
    def test_persisted_append_lists(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="big output truncated")
        b.append_persisted(path="/tmp/results/t1.txt", summary="head excerpt...")
        result = b.build()
        assert isinstance(result.content, list)
        assert len(result.content) == 2
        assert result.content[1] == PersistedBlock(
            path="/tmp/results/t1.txt", summary="head excerpt..."
        )


class TestTotalChars:
    def test_only_base(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="hello")
        assert b.total_chars() == 5

    def test_text_appends_accumulate(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="abc")  # 3
        b.append_text("defg")  # +4 = 7
        b.append_text("hi", label="x")  # "[x]\nhi" = 6, total 13
        assert b.total_chars() == 13

    def test_image_blocks_contribute_zero(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="abc")
        b.append_image({"type": "base64", "media_type": "image/png", "data": "x" * 1000})
        assert b.total_chars() == 3  # image data not counted

    def test_persisted_block_counts_summary_only(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="abc")
        b.append_persisted(path="/x", summary="12345")
        assert b.total_chars() == 8  # "abc" + "12345"


class TestReplaceWithPersisted:
    def test_replace_collapses_to_single_block(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="huge output")
        b.append_text("more huge output")
        b.append_text("even more", label="hook")
        b.replace_with_persisted(path="/tmp/r.txt", summary="head...")
        result = b.build()
        # After replace_with_persisted: list content with exactly one
        # PersistedBlock (NOT collapsed to string — PersistedBlock is
        # not a TextBlock so the legacy-shape branch doesn't apply).
        assert isinstance(result.content, list)
        assert len(result.content) == 1
        assert result.content[0] == PersistedBlock(path="/tmp/r.txt", summary="head...")

    def test_replace_drops_total_chars(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="x" * 1000)
        b.append_text("y" * 500)
        assert b.total_chars() == 1500
        b.replace_with_persisted(path="/tmp/r.txt", summary="hint")
        assert b.total_chars() == 4  # only "hint"


class TestEmptyContentGuard:
    """The empty-content guard substitutes a marker when no block
    contributes meaningful content. Protects against a documented
    model bug where empty tool_result.content matches a stop-sequence
    regex (src toolResultStorage.ts inc-4586). The guard fires AFTER
    all post-tool injections (permission feedback, hook attachments,
    size cap), so a builder is "effectively empty" only when nothing
    in the entire pipeline contributed text or non-text content."""

    def test_whitespace_only_base_substitutes_marker(self):
        b = ToolResultBuilder(
            tool_use_id="t1",
            base_result="   \n\t  ",
            tool_name="Bash",
        )
        assert b.build().content == "(Bash completed with no output)"

    def test_empty_base_with_only_empty_appends_substitutes_marker(self):
        b = ToolResultBuilder(
            tool_use_id="t1",
            base_result="",
            tool_name="Bash",
        )
        # append_text("") is silently ignored by the builder, so
        # these don't add anything to fend off the guard.
        b.append_text("")
        b.append_text("", label="permission")
        assert b.build().content == "(Bash completed with no output)"

    def test_image_block_prevents_empty_marker(self):
        # Image blocks always count as content — even with empty text
        # the builder is not "empty" if it has an image.
        b = ToolResultBuilder(
            tool_use_id="t1",
            base_result="",
            tool_name="Bash",
        )
        src = {"type": "base64", "media_type": "image/png", "data": "x"}
        b.append_image(src)
        result = b.build()
        # List content with the image, no marker substitution.
        assert isinstance(result.content, list)
        assert len(result.content) == 2  # empty TextBlock + ImageBlock

    def test_persisted_block_prevents_empty_marker(self):
        b = ToolResultBuilder(
            tool_use_id="t1",
            base_result="",
            tool_name="Bash",
        )
        b.append_persisted(path="/tmp/r.txt", summary="head")
        result = b.build()
        assert isinstance(result.content, list)

    def test_non_empty_base_does_not_trigger_guard(self):
        b = ToolResultBuilder(
            tool_use_id="t1",
            base_result="real output",
            tool_name="Bash",
        )
        assert b.build().content == "real output"

    def test_replace_with_persisted_does_not_trigger_guard(self):
        b = ToolResultBuilder(
            tool_use_id="t1",
            base_result="huge",
            tool_name="Bash",
        )
        b.replace_with_persisted(path="/tmp/r.txt", summary="head")
        result = b.build()
        # Persisted block always counts as content.
        assert isinstance(result.content, list)
        assert "Bash completed" not in str(result.content)

    def test_marker_label_falls_back_when_tool_name_empty(self):
        # No tool_name provided → generic "tool" label.
        b = ToolResultBuilder(tool_use_id="t1", base_result="")
        assert b.build().content == "(tool completed with no output)"

    def test_marker_preserves_is_error_flag(self):
        b = ToolResultBuilder(
            tool_use_id="t1",
            base_result="",
            tool_name="Bash",
            is_error=True,
        )
        result = b.build()
        assert result.is_error is True
        assert result.content == "(Bash completed with no output)"


class TestBuildShapeCollapse:
    """Verify the str-vs-list collapse rule precisely.

    Rule: build() returns content as plain str if and only if the
    builder holds exactly one unlabeled TextBlock. Any other state
    returns list content.
    """

    def test_single_unlabeled_text_collapses(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="solo")
        assert b.build().content == "solo"

    def test_two_text_blocks_no_collapse(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="a")
        b.append_text("b")
        assert isinstance(b.build().content, list)

    def test_one_labeled_block_no_collapse(self):
        # Builder starts with the unlabeled base. If we append a
        # labeled block we end up with 2 blocks → list. There's no
        # way to construct a single labeled block from outside (the
        # base is always unlabeled), so this test exercises 2 blocks.
        b = ToolResultBuilder(tool_use_id="t1", base_result="base")
        b.append_text("note", label="hook")
        assert isinstance(b.build().content, list)

    def test_image_only_appended_no_collapse(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="see")
        b.append_image({"type": "base64", "media_type": "image/png", "data": "x"})
        assert isinstance(b.build().content, list)

    def test_persisted_only_after_replace_no_collapse(self):
        b = ToolResultBuilder(tool_use_id="t1", base_result="huge")
        b.replace_with_persisted(path="/p", summary="s")
        # One block, but it's a PersistedBlock not a TextBlock → list.
        assert isinstance(b.build().content, list)
