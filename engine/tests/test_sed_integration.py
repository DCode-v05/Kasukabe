"""End-to-end integration tests for the sed validator.

These tests drive the full path: `check_permission` (step 4.5) → real sed
invocation through `SandboxExecutor.run_bash` → observable file-system
change. They close the "integration with real execution" acceptance
criterion on the sed validator.

The bash_security AST analyser, step 1.6 dangerous-path gate, and the
permission matrix are all exercised along the way — if any of them
accidentally blocks the sed command, these tests fail.
"""

from __future__ import annotations

import pytest

from src.sandbox.executor import SandboxExecutor
from src.sandbox.permissions import (
    PermissionMode,
    ToolSource,
    check_permission,
)


class TestSedValidatorEndToEnd:
    @pytest.mark.asyncio
    async def test_safe_substitution_runs_through_executor(self):
        """`echo foo | sed 's/foo/bar/g'` — safe substitution from stdin.
        Validator PASSTHROUGH in default mode (no file args, no -i),
        executor runs sed for real.

        Note: in DEFAULT mode the validator rejects file arguments for
        substitution commands; the user has to explicitly opt in via
        ACCEPT_EDITS to get file-arg reads. This mirrors src exactly.
        """
        ex = SandboxExecutor(session_id="sed-e2e-1")
        try:
            perm = await check_permission(
                "bash",
                {"command": "echo 'foo line' | sed 's/foo/bar/g'"},
                ToolSource.SANDBOXED,
            )
            assert perm.allowed is True, f"step 4.5 blocked: {perm.reason}"
            result = await ex.run_bash("echo 'foo line' | sed 's/foo/bar/g'")
            assert result.ok is True, f"executor failed: {result.stderr}"
            assert "bar line" in result.stdout
            assert "foo" not in result.stdout
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_safe_print_runs_through_executor(self):
        """`sed -n '1,2p' file` — safe print allowlist, read-only."""
        ex = SandboxExecutor(session_id="sed-e2e-2")
        try:
            await ex.write_file("lines.txt", "one\ntwo\nthree\nfour\n")
            perm = await check_permission(
                "bash",
                {"command": "sed -n '1,2p' lines.txt"},
                ToolSource.SANDBOXED,
            )
            assert perm.allowed is True, f"step 4.5 blocked: {perm.reason}"
            result = await ex.run_bash("sed -n '1,2p' lines.txt")
            assert result.ok is True, f"executor failed: {result.stderr}"
            assert "one" in result.stdout
            assert "two" in result.stdout
            assert "three" not in result.stdout
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_sed_in_place_accept_edits_mode(self):
        """`sed -i 's/foo/bar/g' file` — in-place edit, ACCEPT_EDITS mode.

        Verifies the full chain: step 4.5 approves with attribution,
        executor runs sed -i for real, file content is physically changed,
        and the attribution dataclass matches the actual edit.
        """
        ex = SandboxExecutor(session_id="sed-e2e-3")
        try:
            await ex.write_file("edit.txt", "foo one\nfoo two\n")
            perm = await check_permission(
                "bash",
                {"command": "sed -i 's/foo/bar/g' edit.txt"},
                ToolSource.SANDBOXED,
                permission_mode=PermissionMode.ACCEPT_EDITS,
            )
            assert perm.allowed is True, f"step 4.5 blocked: {perm.reason}"
            assert perm.sed_edit_info is not None
            assert perm.sed_edit_info.file_path == "edit.txt"
            assert perm.sed_edit_info.pattern == "foo"
            assert perm.sed_edit_info.flags == "g"
            # Now actually run the edit through the sandbox executor.
            result = await ex.run_bash("sed -i 's/foo/bar/g' edit.txt")
            assert result.ok is True, f"executor failed: {result.stderr}"
            # Read the file back and verify the physical change.
            after = await ex.read_file("edit.txt")
            assert after.ok is True
            assert "bar one" in after.stdout
            assert "bar two" in after.stdout
            assert "foo" not in after.stdout
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_dangerous_sed_denied_before_execution(self):
        """`sed 's/foo/bar/w outfile'` — denylist hit. Validator ASKs, and
        without an ask_user_fn the pipeline fails closed. The executor
        must NEVER run this command because the permission check returned
        denied.
        """
        perm = await check_permission(
            "bash",
            {"command": "sed 's/foo/bar/w /tmp/evil'"},
            ToolSource.SANDBOXED,
        )
        assert perm.allowed is False
        assert "sed-denylist:" in (perm.reason or "")

    @pytest.mark.asyncio
    async def test_sed_in_place_default_mode_denied(self):
        """`sed -i` in DEFAULT mode is not auto-allowed; the pipeline fails
        closed without an ask_user_fn. The executor must never run it.
        """
        perm = await check_permission(
            "bash",
            {"command": "sed -i 's/foo/bar/g' file.txt"},
            ToolSource.SANDBOXED,
            permission_mode=PermissionMode.DEFAULT,
        )
        assert perm.allowed is False

    @pytest.mark.asyncio
    async def test_compound_safe_sed_in_pipeline(self):
        """`echo foo | sed 's/foo/bar/g'` — compound command with safe sed.
        Validator clears the sed gate (doesn't auto-allow compound), the
        rest of the pipeline classifies as safe, executor runs it.
        """
        ex = SandboxExecutor(session_id="sed-e2e-6")
        try:
            perm = await check_permission(
                "bash",
                {"command": "echo foo | sed 's/foo/bar/g'"},
                ToolSource.SANDBOXED,
            )
            assert perm.allowed is True, f"step 4.5 blocked: {perm.reason}"
            result = await ex.run_bash("echo foo | sed 's/foo/bar/g'")
            assert result.ok is True, f"executor failed: {result.stderr}"
            assert "bar" in result.stdout
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_compound_dangerous_sed_blocked_end_to_end(self):
        """`echo x | sed 's/foo/bar/w /tmp/evil'` — dangerous sed hiding in
        a pipeline. The validator catches it and the executor must never
        run it. Regression for the compound-command gap fix.
        """
        perm = await check_permission(
            "bash",
            {"command": "echo x | sed 's/foo/bar/w /tmp/evil'"},
            ToolSource.SANDBOXED,
        )
        assert perm.allowed is False
        assert "sed-denylist:" in (perm.reason or "")
