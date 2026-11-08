"""Unit tests for tool_result_storage.

Real file I/O via pytest's tmp_path fixture — no mocks, no stubs.
The disk persistence behavior is the whole point of the module, so
mocking it would test nothing.
"""

from src.engine_core.tool_result_builder import ToolResultBuilder
from src.engine_core.tool_result_storage import (
    DEFAULT_MAX_RESULT_SIZE_CHARS,
    PERSISTED_SUMMARY_CHARS,
    cap_builder_size,
    get_tool_cap,
    persist_oversized_result,
)
from src.herald.types import PersistedBlock


class TestGetToolCap:
    def test_unknown_tool_uses_default(self):
        assert get_tool_cap("some_random_tool") == DEFAULT_MAX_RESULT_SIZE_CHARS

    def test_known_overrides_apply(self):
        assert get_tool_cap("Bash") == 100_000
        assert get_tool_cap("Read") == 30_000
        assert get_tool_cap("web_fetch") == 80_000

    def test_lowercase_aliases(self):
        assert get_tool_cap("bash") == 100_000
        assert get_tool_cap("read") == 30_000
        assert get_tool_cap("file_read") == 30_000


class TestPersistOversizedResult:
    def test_writes_full_text_to_disk(self, tmp_path):
        text = "x" * 5000
        block = persist_oversized_result(
            text=text,
            tool_use_id="toolu_01",
            results_dir=tmp_path,
        )
        assert isinstance(block, PersistedBlock)
        from pathlib import Path

        assert Path(block.path).read_text(encoding="utf-8") == text

    def test_summary_is_truncated_with_ellipsis(self, tmp_path):
        text = "a" * (PERSISTED_SUMMARY_CHARS + 100)
        block = persist_oversized_result(
            text=text,
            tool_use_id="toolu_01",
            results_dir=tmp_path,
        )
        assert block.summary == "a" * PERSISTED_SUMMARY_CHARS + "..."

    def test_summary_short_text_no_ellipsis(self, tmp_path):
        text = "short"
        block = persist_oversized_result(
            text=text,
            tool_use_id="toolu_01",
            results_dir=tmp_path,
        )
        assert block.summary == "short"
        assert "..." not in block.summary

    def test_results_dir_created_if_missing(self, tmp_path):
        nested = tmp_path / "results" / "nested"
        assert not nested.exists()
        block = persist_oversized_result(
            text="hello",
            tool_use_id="t1",
            results_dir=nested,
        )
        assert nested.exists()
        from pathlib import Path

        assert Path(block.path).exists()

    def test_tool_use_id_sanitized_for_filesystem(self, tmp_path):
        block = persist_oversized_result(
            text="hello",
            tool_use_id="../weird/id with spaces/and?chars",
            results_dir=tmp_path,
        )
        from pathlib import Path

        assert Path(block.path).resolve().is_relative_to(tmp_path.resolve())
        assert Path(block.path).exists()


class TestCapBuilderSizeNoOp:
    def test_under_cap_does_nothing(self, tmp_path):
        b = ToolResultBuilder(tool_use_id="t1", base_result="small output")
        cap_builder_size(b, tool_name="Bash", results_dir=tmp_path)
        result = b.build()
        assert result.content == "small output"
        assert list(tmp_path.iterdir()) == []

    def test_under_default_cap_does_nothing(self, tmp_path):
        b = ToolResultBuilder(tool_use_id="t1", base_result="x" * 100)
        cap_builder_size(b, tool_name="unknown_tool", results_dir=tmp_path)
        assert b.build().content == "x" * 100


class TestCapBuilderSizeOverflow:
    def test_over_cap_triggers_persist_and_collapse(self, tmp_path):
        big = "x" * 35_000
        b = ToolResultBuilder(tool_use_id="toolu_01", base_result=big)
        cap_builder_size(b, tool_name="Read", results_dir=tmp_path)
        result = b.build()
        assert isinstance(result.content, list)
        assert len(result.content) == 1
        block = result.content[0]
        assert isinstance(block, PersistedBlock)
        from pathlib import Path

        assert Path(block.path).read_text(encoding="utf-8") == big
        assert block.summary.startswith("x" * 100)
        assert block.summary.endswith("...")

    def test_concatenates_all_text_blocks_to_disk(self, tmp_path):
        b = ToolResultBuilder(tool_use_id="t1", base_result="x" * 20_000)
        b.append_text("y" * 15_000, label="hook")
        cap_builder_size(b, tool_name="Read", results_dir=tmp_path)
        result = b.build()
        assert isinstance(result.content, list)
        block = result.content[0]
        assert isinstance(block, PersistedBlock)
        from pathlib import Path

        on_disk = Path(block.path).read_text(encoding="utf-8")
        assert "x" * 20_000 in on_disk
        assert "y" * 15_000 in on_disk
        assert "[hook]" in on_disk

    def test_exactly_at_cap_does_not_trip(self, tmp_path):
        cap = get_tool_cap("Read")
        b = ToolResultBuilder(tool_use_id="t1", base_result="x" * cap)
        cap_builder_size(b, tool_name="Read", results_dir=tmp_path)
        assert b.build().content == "x" * cap
        assert list(tmp_path.iterdir()) == []

    def test_one_over_cap_does_trip(self, tmp_path):
        cap = get_tool_cap("Read")
        b = ToolResultBuilder(tool_use_id="t1", base_result="x" * (cap + 1))
        cap_builder_size(b, tool_name="Read", results_dir=tmp_path)
        result = b.build()
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], PersistedBlock)

    def test_unknown_tool_uses_default_cap(self, tmp_path):
        b = ToolResultBuilder(tool_use_id="t1", base_result="z" * 60_000)
        cap_builder_size(b, tool_name="my_custom_tool", results_dir=tmp_path)
        result = b.build()
        assert isinstance(result.content, list)
        assert isinstance(result.content[0], PersistedBlock)


class TestCapBuilderImagesPreserved:
    def test_image_blocks_dont_block_persistence(self, tmp_path):
        b = ToolResultBuilder(tool_use_id="t1", base_result="x" * 100)
        b.append_image({"type": "base64", "media_type": "image/png", "data": "y" * 1000})
        cap_builder_size(b, tool_name="Read", results_dir=tmp_path)
        result = b.build()
        assert isinstance(result.content, list)
        assert len(result.content) == 2
