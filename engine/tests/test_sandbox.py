"""Tests for sandbox executor and tools."""

import platform
import pytest
from src.sandbox.executor import (
    SandboxExecutor,
    DEFAULT_SAFE_BINS,
    validate_safe_bin,
    DEFAULT_TRUSTED_DIRS,
    needs_network,
    MAX_REGEX_LEN,
)

IS_WINDOWS = platform.system() == "Windows"


class TestSafeBinaryAllowlist:
    def test_safe_echo(self):
        assert validate_safe_bin(["echo", "hello"], DEFAULT_SAFE_BINS, DEFAULT_TRUSTED_DIRS)

    def test_safe_grep(self):
        assert validate_safe_bin(["grep", "-e", "pattern"], DEFAULT_SAFE_BINS, DEFAULT_TRUSTED_DIRS)

    def test_unsafe_find_exec(self):
        assert not validate_safe_bin(
            ["find", ".", "-exec", "rm", "{}", ";"], DEFAULT_SAFE_BINS, DEFAULT_TRUSTED_DIRS
        )

    def test_sed_not_in_safe_bins(self):
        # sed is gated by src/sandbox/sed_validation.py (step 4.5 of the
        # permission pipeline), not here. It must not appear in the
        # executor-level safe-bin allowlist. validate_safe_bin therefore
        # rejects every sed invocation by way of "not in safe_bins".
        assert "sed" not in DEFAULT_SAFE_BINS
        assert not validate_safe_bin(
            ["sed", "-i", "s/old/new/"], DEFAULT_SAFE_BINS, DEFAULT_TRUSTED_DIRS
        )
        assert not validate_safe_bin(["sed", "s/a/b/"], DEFAULT_SAFE_BINS, DEFAULT_TRUSTED_DIRS)

    def test_unknown_binary(self):
        assert not validate_safe_bin(
            ["curl", "http://evil.com"], DEFAULT_SAFE_BINS, DEFAULT_TRUSTED_DIRS
        )

    def test_jq_allowed(self):
        assert validate_safe_bin(["jq", ".foo"], DEFAULT_SAFE_BINS, DEFAULT_TRUSTED_DIRS)

    def test_jq_file_denied(self):
        assert not validate_safe_bin(
            ["jq", "-f", "script.jq"], DEFAULT_SAFE_BINS, DEFAULT_TRUSTED_DIRS
        )

    def test_too_many_positional(self):
        assert not validate_safe_bin(
            ["grep"] + [f"arg{i}" for i in range(20)],
            DEFAULT_SAFE_BINS,
            DEFAULT_TRUSTED_DIRS,
        )


class TestNetworkDetection:
    def test_curl_needs_network(self):
        assert needs_network("curl https://example.com")

    def test_git_needs_network(self):
        assert needs_network("git clone https://repo.git")

    def test_pip_needs_network(self):
        assert needs_network("pip install pandas")

    def test_echo_no_network(self):
        assert not needs_network("echo hello")

    def test_python_no_network(self):
        assert not needs_network("python script.py")

    def test_jq_no_network(self):
        assert not needs_network("jq '.foo'")


class TestExecutorSync:
    def test_is_safe_command(self):
        ex = SandboxExecutor(session_id="sync-1")
        try:
            assert ex.is_safe_command("echo hello")
            assert ex.is_safe_command("jq '.foo'")
            assert not ex.is_safe_command("curl http://evil.com")
            assert not ex.is_safe_command("rm -rf /")
        finally:
            ex.cleanup()

    def test_command_needs_network(self):
        ex = SandboxExecutor(session_id="sync-2")
        try:
            assert ex.command_needs_network("curl https://x.com")
            assert ex.command_needs_network("anything", explicit_network=True)
            assert not ex.command_needs_network("echo hello")
        finally:
            ex.cleanup()

    def test_network_not_approved_by_default(self):
        ex = SandboxExecutor(session_id="sync-3")
        try:
            assert not ex.task_network_approved
        finally:
            ex.cleanup()

    def test_revoke_network(self):
        ex = SandboxExecutor(session_id="sync-4")
        try:
            ex.task_network_approved = True
            assert ex.task_network_approved
            ex.revoke_network_permission()
            assert not ex.task_network_approved
        finally:
            ex.cleanup()

    def test_cleanup_revokes_network(self):
        ex = SandboxExecutor(session_id="sync-5")
        ex.task_network_approved = True
        ex.cleanup()
        assert not ex.task_network_approved


class TestFilesystem:
    @pytest.mark.asyncio
    async def test_write_read(self):
        ex = SandboxExecutor(session_id="fs-1")
        try:
            await ex.write_file("test.txt", "content here")
            result = await ex.read_file("test.txt")
            assert result.ok
            assert "content here" in result.stdout
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_nested_write(self):
        ex = SandboxExecutor(session_id="fs-2")
        try:
            await ex.write_file("deep/nested/file.txt", "nested")
            result = await ex.read_file("deep/nested/file.txt")
            assert result.ok
            assert "nested" in result.stdout
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_read_nonexistent(self):
        ex = SandboxExecutor(session_id="fs-3")
        try:
            result = await ex.read_file("nope.txt")
            assert not result.ok
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_path_escape_blocked(self):
        ex = SandboxExecutor(session_id="fs-4")
        try:
            result = await ex.read_file("../../etc/passwd")
            assert not result.ok
            assert "escape" in result.stderr.lower()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_list_directory(self):
        ex = SandboxExecutor(session_id="fs-5")
        try:
            await ex.write_file("a.txt", "a")
            await ex.write_file("sub/c.txt", "c")
            result = await ex.list_directory(".")
            assert result.ok
            assert "a.txt" in result.stdout
            assert "sub/" in result.stdout
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_search_content(self):
        ex = SandboxExecutor(session_id="fs-6")
        try:
            await ex.write_file("code.py", "def hello():\n    print('world')")
            result = await ex.search_content("hello")
            assert result.ok
            assert "hello" in result.stdout
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_workspace_path_resolve(self):
        ex = SandboxExecutor(session_id="fs-7")
        try:
            await ex.write_file("test.txt", "data")
            result = await ex.read_file("/workspace/test.txt")
            assert result.ok
        finally:
            ex.cleanup()


class TestToolDefinitions:
    def test_all_tools_present(self):
        ex = SandboxExecutor(session_id="tools-1")
        try:
            from src.sandbox.tools import build_sandbox_tools

            tools = {t.name: t for t in build_sandbox_tools(ex)}
            expected = {
                "bash",
                "python",
                "read_file",
                "write_file",
                "file_edit",
                "list_directory",
                "search_files",
                "process",
            }
            assert set(tools.keys()) == expected
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_write_then_read_tool(self):
        ex = SandboxExecutor(session_id="tools-2")
        try:
            from src.sandbox.tools import build_sandbox_tools

            tools = {t.name: t for t in build_sandbox_tools(ex)}
            await tools["write_file"].handler({"path": "demo.txt", "content": "demo data"})
            result = await tools["read_file"].handler({"path": "demo.txt"})
            assert "demo data" in result
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_empty_command_errors(self):
        ex = SandboxExecutor(session_id="tools-3")
        try:
            from src.sandbox.tools import build_sandbox_tools

            tools = {t.name: t for t in build_sandbox_tools(ex)}
            assert "Error" in await tools["bash"].handler({"command": ""})
            assert "Error" in await tools["python"].handler({"code": ""})
            assert "Error" in await tools["read_file"].handler({"path": ""})
            assert "Error" in await tools["search_files"].handler({"pattern": ""})
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_process_list_empty(self):
        ex = SandboxExecutor(session_id="tools-4")
        try:
            from src.sandbox.tools import build_sandbox_tools

            tools = {t.name: t for t in build_sandbox_tools(ex)}
            result = await tools["process"].handler({"action": "list"})
            assert "No active" in result
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_process_unknown_action(self):
        ex = SandboxExecutor(session_id="tools-5")
        try:
            from src.sandbox.tools import build_sandbox_tools

            tools = {t.name: t for t in build_sandbox_tools(ex)}
            result = await tools["process"].handler({"action": "unknown_xyz"})
            assert "unknown" in result.lower()
        finally:
            ex.cleanup()


class TestNetworkPermission:
    @pytest.mark.asyncio
    async def test_denied_without_callback(self):
        ex = SandboxExecutor(session_id="net-1", permission_callback=None)
        try:
            result = await ex.run_bash("curl https://example.com")
            assert not result.ok
            assert "denied" in result.stderr.lower()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_denied_by_callback(self):
        async def deny(commands):
            return False

        ex = SandboxExecutor(session_id="net-2", permission_callback=deny)
        try:
            result = await ex.run_bash("curl https://example.com")
            assert not result.ok
            assert "denied" in result.stderr.lower()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_approved_doesnt_deny(self):
        async def approve(commands):
            return True

        ex = SandboxExecutor(session_id="net-3", permission_callback=approve)
        try:
            result = await ex.run_bash("echo approved", network=True)
            assert "denied" not in result.stderr.lower()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_per_task_persists(self):
        call_count = 0

        async def approve_once(commands):
            nonlocal call_count
            call_count += 1
            return True

        ex = SandboxExecutor(session_id="net-4", permission_callback=approve_once)
        try:
            await ex.run_bash("curl https://example.com")
            await ex.run_bash("git status", network=True)
            assert call_count == 1
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_safe_commands_no_permission(self):
        call_count = 0

        async def counter(commands):
            nonlocal call_count
            call_count += 1
            return True

        ex = SandboxExecutor(session_id="net-5", permission_callback=counter)
        try:
            cmd = "Write-Output 'safe'" if IS_WINDOWS else "echo safe"
            await ex.run_bash(cmd)
            assert call_count == 0
        finally:
            ex.cleanup()


# ============================================================
# SECURITY HARDENING TESTS
# ============================================================


class TestSecurityHardening:
    def test_pip_not_in_safe_bins(self):
        """pip removed from safe binary allowlist."""
        assert "pip" not in DEFAULT_SAFE_BINS

    def test_node_not_in_safe_bins(self):
        """node removed from safe binary allowlist."""
        assert "node" not in DEFAULT_SAFE_BINS

    def test_node_validate_returns_false(self):
        """node -e command rejected by safe binary check."""
        assert not validate_safe_bin(
            ["node", "-e", "console.log(1)"],
            DEFAULT_SAFE_BINS,
            DEFAULT_TRUSTED_DIRS,
        )

    @pytest.mark.asyncio
    async def test_run_python_blocked_without_bwrap(self):
        """run_python() blocked on non-bwrap systems."""
        ex = SandboxExecutor(session_id="py-gate-1")
        if not ex.uses_bubblewrap:
            result = await ex.run_python("print(1)")
            assert result.exit_code == 1
            assert "bubblewrap" in result.stderr.lower()
        ex.cleanup()

    def test_python_not_in_default_safe_bins(self):
        """python/python3 not in DEFAULT_SAFE_BINS (only added when bubblewrap available)."""
        assert "python3" not in DEFAULT_SAFE_BINS
        assert "python" not in DEFAULT_SAFE_BINS

    @pytest.mark.asyncio
    async def test_redos_rejects_long_pattern(self):
        """Regex patterns longer than MAX_REGEX_LEN are rejected."""
        ex = SandboxExecutor(session_id="redos-1")
        try:
            # Write a file to search
            await ex.write_file("test.txt", "hello world")
            long_pattern = "a" * (MAX_REGEX_LEN + 1)
            result = await ex.search_content(long_pattern)
            assert result.exit_code == 1
            assert "too long" in result.stderr.lower()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_redos_allows_normal_pattern(self):
        """Normal-length regex patterns still work."""
        ex = SandboxExecutor(session_id="redos-2")
        try:
            await ex.write_file("test.txt", "hello world\nfoo bar")
            result = await ex.search_content("hello")
            assert result.exit_code == 0
            assert "hello" in result.stdout
        finally:
            ex.cleanup()


class TestPolicyIntegration:
    """Test that policy wires into SandboxExecutor correctly."""

    def test_policy_derives_safe_bins(self):
        from src.sandbox.policy import SandboxPolicy

        policy = SandboxPolicy.default()
        executor = SandboxExecutor(policy=policy)
        # Policy-derived bins should match the policy's binary count + python if bwrap
        assert len(executor.safe_bins) >= len(policy.binaries)
        assert "jq" in executor.safe_bins
        assert "grep" in executor.safe_bins
        assert executor.safe_bins["jq"].max_positional == 1

    def test_policy_overrides_explicit_safe_bins(self):
        from src.sandbox.policy import SandboxPolicy

        policy = SandboxPolicy.from_dict({"binaries": {"echo": {"max_positional": 3}}})
        executor = SandboxExecutor(policy=policy)
        assert executor.safe_bins["echo"].max_positional == 3
        assert "jq" not in executor.safe_bins  # policy only has echo

    def test_no_policy_uses_defaults(self):
        executor = SandboxExecutor()
        assert "jq" in executor.safe_bins
        assert executor.safe_bins["jq"].max_positional == 1

    def test_cleanup_closes_seccomp_fd(self):
        executor = SandboxExecutor()
        # Simulate an open inheritable fd (the same shape build_cmd creates).
        import tempfile
        import os

        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            fd = os.open(tmp.name, os.O_RDONLY)
            os.set_inheritable(fd, True)
            executor.seccomp_fd = fd
            executor.cleanup()
            assert executor.seccomp_fd is None
            # cleanup() should have closed the fd; reusing it should now fail.
            try:
                os.fstat(fd)
                raise AssertionError("expected fd to be closed by cleanup()")
            except OSError:
                pass  # expected — fd is closed
        finally:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


class TestProxyIntegration:
    """Test that proxy wires into SandboxExecutor correctly."""

    def test_proxy_stored(self):
        from src.sandbox.proxy import TrustedZoneProxy
        from src.sandbox.policy import ProxyPolicy

        proxy = TrustedZoneProxy(ProxyPolicy(enabled=True), listen_port=0)
        executor = SandboxExecutor(proxy=proxy)
        assert executor.proxy is proxy

    def test_no_proxy_by_default(self):
        executor = SandboxExecutor()
        assert executor.proxy is None
