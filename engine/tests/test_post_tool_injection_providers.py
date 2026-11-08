"""Provider serializer tests for list-form ToolResultBlock content.

Pure translation logic — no API calls, no mocks. Verifies the Anthropic
serializer correctly handles the ``str | list[ContentBlock]`` shape of
``ToolResultBlock.content`` produced by the post-tool injection
pipeline. The serializer emits the API-native array shape so
multimodal attachments reach the model with full fidelity.
"""

from src.herald.provider import (
    blocks_to_wire,
    tool_result_to_wire,
)
from src.herald.types import (
    ImageBlock,
    PersistedBlock,
    TextBlock,
    ToolResultBlock,
)


class TestAnthropicToolResultContent:
    def test_string_content_passes_through(self):
        out = tool_result_to_wire("hello world")
        assert out == "hello world"

    def test_single_text_block_lists_to_array(self):
        out = tool_result_to_wire([TextBlock(text="hello")])
        assert out == [{"type": "text", "text": "hello"}]

    def test_multiple_text_blocks_emit_array(self):
        out = tool_result_to_wire(
            [
                TextBlock(text="base"),
                TextBlock(text="[permission]\nallowed"),
            ]
        )
        assert out == [
            {"type": "text", "text": "base"},
            {"type": "text", "text": "[permission]\nallowed"},
        ]

    def test_image_block_emits_native_image(self):
        src = {"type": "base64", "media_type": "image/png", "data": "deadbeef"}
        out = tool_result_to_wire(
            [
                TextBlock(text="see image"),
                ImageBlock(source=src),
            ]
        )
        assert out == [
            {"type": "text", "text": "see image"},
            {"type": "image", "source": src},
        ]

    def test_persisted_block_emits_xml_hint(self):
        out = tool_result_to_wire(
            [
                PersistedBlock(path="/tmp/r/t1.txt", summary="head excerpt..."),
            ]
        )
        assert len(out) == 1
        assert out[0]["type"] == "text"
        assert "<persisted-output" in out[0]["text"]
        assert "/tmp/r/t1.txt" in out[0]["text"]
        assert "head excerpt..." in out[0]["text"]
        assert "</persisted-output>" in out[0]["text"]

    def test_blocks_to_wire_legacy_string_unchanged(self):
        blocks = [ToolResultBlock(tool_use_id="t1", content="legacy")]
        out = blocks_to_wire(blocks)
        assert out == [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "legacy",
                "is_error": False,
            }
        ]

    def test_blocks_to_wire_list_content(self):
        blocks = [
            ToolResultBlock(
                tool_use_id="t1",
                content=[
                    TextBlock(text="base"),
                    TextBlock(text="[hook]\nattach"),
                ],
            ),
        ]
        out = blocks_to_wire(blocks)
        assert out == [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": [
                    {"type": "text", "text": "base"},
                    {"type": "text", "text": "[hook]\nattach"},
                ],
                "is_error": False,
            }
        ]

    def test_blocks_to_wire_is_error_flag_preserved(self):
        blocks = [
            ToolResultBlock(
                tool_use_id="t1",
                content=[TextBlock(text="oops")],
                is_error=True,
            ),
        ]
        out = blocks_to_wire(blocks)
        assert out[0]["is_error"] is True


class TestPermissionFeedbackEndToEnd:
    """Integration: check_permission → ToolResultBuilder → provider payload.

    Exercises the full post-tool injection path a real agent turn uses:
    a rule auto-allow populates PermissionResult.feedback, thread.py's
    injection logic appends it to the ToolResultBuilder, and the
    Anthropic provider serializer emits the feedback text as a native
    content block alongside the tool's base output.
    """

    def run(self, coro):
        import asyncio

        return asyncio.run(coro)

    def build_result_block(self, perm_result, base_output: str):
        """Replicate thread.py.run_single_tool's injection sequence."""
        from src.engine_core.tool_result_builder import ToolResultBuilder
        from src.engine_core.thread import append_permission_content_block

        builder = ToolResultBuilder(
            tool_use_id="t-end2end",
            base_result=base_output,
            is_error=False,
            tool_name="ad:create_issue",
            source_assistant_id="asst_e2e",
        )
        if perm_result.feedback:
            builder.append_text(perm_result.feedback, label="permission")
        for cb in perm_result.content_blocks:
            append_permission_content_block(builder, cb)
        return builder.build()

    def test_rule_allow_feedback_reaches_anthropic_payload(self):
        from src.sandbox.permissions import (
            check_permission,
            PermissionRule,
            PermissionBehavior,
            ToolSource,
        )

        rules = [
            PermissionRule(
                tool="ad:create_issue",
                behavior=PermissionBehavior.ALLOW,
                source="operator_global",
            )
        ]
        perm = self.run(
            check_permission(
                "ad:create_issue",
                {"title": "bug"},
                ToolSource.MCP,
                rules=rules,
            )
        )
        assert perm.allowed and perm.feedback

        block = self.build_result_block(perm, base_output="issue-created:42")
        payload = blocks_to_wire([block])
        assert len(payload) == 1
        content = payload[0]["content"]
        assert isinstance(content, list)
        texts = [b["text"] for b in content if b.get("type") == "text"]
        assert any(
            t.startswith("[permission]") and "allowed by rule: ad:create_issue" in t for t in texts
        )
        assert any("operator_global" in t and "persistent" in t for t in texts)
        assert any(t == "issue-created:42" for t in texts)

    def test_user_paste_image_reaches_anthropic_payload(self):
        from src.sandbox.permissions import check_permission, ToolSource

        img_src = {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}

        async def ask(params):
            return {
                "response": "allow",
                "feedback": "screenshot attached",
                "content_blocks": [{"type": "image", "source": img_src}],
            }

        perm = self.run(
            check_permission(
                "ad:create_issue",
                {"title": "bug"},
                ToolSource.MCP,
                ask_user_fn=ask,
            )
        )
        assert perm.allowed
        assert perm.feedback == "screenshot attached"
        assert perm.content_blocks and perm.content_blocks[0]["type"] == "image"

        block = self.build_result_block(perm, base_output="ok")
        payload = blocks_to_wire([block])
        content = payload[0]["content"]
        assert isinstance(content, list)
        image_entries = [b for b in content if b.get("type") == "image"]
        assert len(image_entries) == 1
        assert image_entries[0]["source"] == img_src
        text_entries = [b for b in content if b.get("type") == "text"]
        assert any(
            "[permission]" in t["text"] and "screenshot attached" in t["text"] for t in text_entries
        )

    def test_sandbox_auto_allow_feedback_serialized(self):
        """Step 2.5 SYSTEM_ALLOW allowlist → feedback visible in payload."""
        from src.sandbox.permissions import check_permission, ToolSource

        perm = self.run(
            check_permission(
                "Bash",
                {"command": "ls -la"},
                ToolSource.SANDBOXED,
            )
        )
        assert perm.allowed
        assert "sandbox safe-command allowlist" in perm.feedback

        block = self.build_result_block(perm, base_output="total 0")
        payload = blocks_to_wire([block])
        content = payload[0]["content"]
        texts = [b["text"] for b in content if b.get("type") == "text"]
        assert any("[permission]" in t and "sandbox safe-command allowlist" in t for t in texts)
