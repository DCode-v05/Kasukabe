"""Tests for command exit code semantics."""

import pytest

from src.sandbox.command_semantics import extract_base_command, interpret_exit_code
from src.sandbox.executor import SandboxResult


class TestExtractBaseCommand:
    def test_simple(self):
        assert extract_base_command("grep foo bar.txt") == "grep"

    def test_pipeline_last_command(self):
        assert extract_base_command("cat file | grep pattern | head") == "head"

    def test_full_path(self):
        assert extract_base_command("/usr/bin/grep foo") == "grep"

    def test_env_var_prefix(self):
        assert extract_base_command("LC_ALL=C grep foo") == "grep"

    def test_sudo_prefix(self):
        assert extract_base_command("sudo grep foo") == "grep"

    def test_compound_and(self):
        assert extract_base_command("echo hi && grep foo bar") == "grep"

    def test_empty(self):
        assert extract_base_command("") == ""

    def test_env_and_sudo_combined(self):
        assert extract_base_command("FOO=1 sudo env grep pattern") == "grep"

    def test_single_pipe(self):
        assert extract_base_command("cat file | grep pattern") == "grep"

    def test_bracket_command(self):
        assert extract_base_command("[ -f foo.txt ]") == "["

    def test_diff(self):
        assert extract_base_command("diff file1 file2") == "diff"


class TestInterpretExitCode:
    def test_grep_success(self):
        is_error, msg = interpret_exit_code("grep foo bar", 0)
        assert is_error is False
        assert msg is None

    def test_grep_no_match(self):
        is_error, msg = interpret_exit_code("grep foo bar", 1)
        assert is_error is False
        assert msg == "No matches found"

    def test_grep_real_error(self):
        is_error, msg = interpret_exit_code("grep '[bad' bar", 2)
        assert is_error is True
        assert msg is None

    def test_rg_no_match(self):
        is_error, msg = interpret_exit_code("rg pattern", 1)
        assert is_error is False
        assert msg == "No matches found"

    def test_diff_files_differ(self):
        is_error, msg = interpret_exit_code("diff a.txt b.txt", 1)
        assert is_error is False
        assert msg == "Files differ"

    def test_diff_error(self):
        is_error, msg = interpret_exit_code("diff a.txt b.txt", 2)
        assert is_error is True

    def test_test_false(self):
        is_error, msg = interpret_exit_code("test -f missing.txt", 1)
        assert is_error is False
        assert msg == "Condition is false"

    def test_bracket_false(self):
        is_error, msg = interpret_exit_code("[ -f missing.txt ]", 1)
        assert is_error is False
        assert msg == "Condition is false"

    def test_find_partial(self):
        is_error, msg = interpret_exit_code("find / -name '*.py'", 1)
        assert is_error is False
        assert msg == "Some directories were inaccessible"

    def test_unknown_command_exit_1(self):
        is_error, msg = interpret_exit_code("ls /nonexistent", 1)
        assert is_error is True
        assert msg == "Command failed with exit code 1"

    def test_unknown_command_exit_1_with_stderr(self):
        is_error, msg = interpret_exit_code("ls /nonexistent", 1, stderr="No such file")
        assert is_error is True
        assert msg is None

    def test_unknown_command_exit_0(self):
        is_error, msg = interpret_exit_code("ls", 0)
        assert is_error is False
        assert msg is None

    def test_pipeline_grep(self):
        is_error, msg = interpret_exit_code("cat file | grep pattern", 1)
        assert is_error is False
        assert msg == "No matches found"


class TestSandboxResultSemantics:
    def test_ok_grep_no_match(self):
        r = SandboxResult("", "", 1, command="grep foo bar")
        assert r.ok is True

    def test_ok_grep_error(self):
        r = SandboxResult("", "grep: invalid", 2, command="grep '[bad'")
        assert r.ok is False

    def test_ok_no_command_exit_1(self):
        r = SandboxResult("", "", 1)
        assert r.ok is False

    def test_ok_no_command_exit_0(self):
        r = SandboxResult("output", "", 0)
        assert r.ok is True

    def test_ok_timed_out(self):
        r = SandboxResult("", "", 0, timed_out=True, command="grep foo")
        assert r.ok is False

    def test_to_str_grep_no_match_empty_output(self):
        r = SandboxResult("", "", 1, command="grep foo bar")
        assert r.to_str() == "[No matches found]"

    def test_to_str_grep_no_match_with_stderr(self):
        r = SandboxResult("", "some warning", 1, command="grep foo bar")
        result = r.to_str()
        assert "[stderr]" in result
        assert "[No matches found]" in result

    def test_to_str_grep_success_with_output(self):
        r = SandboxResult("matched line", "", 0, command="grep foo bar")
        assert r.to_str() == "matched line"

    def test_to_str_unknown_command_exit_1_no_output(self):
        r = SandboxResult("", "", 1, command="ls /bad")
        assert r.to_str() == "[Command failed with exit code 1]"

    def test_to_str_diff_differ_with_output(self):
        r = SandboxResult("< line1\n> line2", "", 1, command="diff a b")
        result = r.to_str()
        assert "< line1" in result
        assert "[Files differ]" in result

    def test_to_str_no_command_backward_compat(self):
        r = SandboxResult("output", "", 0)
        assert r.to_str() == "output"

    def test_to_str_no_command_error_backward_compat(self):
        r = SandboxResult("", "", 1)
        assert r.to_str() == "[exit code 1]"


class TestEndToEndSemantics:
    """Integration tests that run real bash commands through the full
    bash_handler → run_bash → SandboxResult → to_str() chain."""

    @pytest.mark.asyncio
    async def test_grep_no_match_through_run_bash(self):
        from src.sandbox.executor import SandboxExecutor

        ex = SandboxExecutor(session_id="sem-e2e-1")
        try:
            result = await ex.run_bash("echo 'hello world' | grep ZZZZNOTHERE")
            assert result.ok is True
            assert result.command == "echo 'hello world' | grep ZZZZNOTHERE"
            output = result.to_str()
            assert "No matches found" in output
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_grep_match_through_run_bash(self):
        from src.sandbox.executor import SandboxExecutor

        ex = SandboxExecutor(session_id="sem-e2e-2")
        try:
            result = await ex.run_bash("echo 'hello world' | grep hello")
            assert result.ok is True
            assert result.exit_code == 0
            assert "hello" in result.to_str()
            assert "No matches found" not in result.to_str()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_diff_files_differ_through_run_bash(self):
        from src.sandbox.executor import SandboxExecutor

        ex = SandboxExecutor(session_id="sem-e2e-3")
        try:
            await ex.write_file("a.txt", "line1\n")
            await ex.write_file("b.txt", "line2\n")
            result = await ex.run_bash("diff a.txt b.txt")
            assert result.ok is True
            assert result.exit_code == 1
            output = result.to_str()
            assert "Files differ" in output
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_test_false_through_run_bash(self):
        from src.sandbox.executor import SandboxExecutor

        ex = SandboxExecutor(session_id="sem-e2e-4")
        try:
            result = await ex.run_bash("test -f /nonexistent_file_abc123")
            assert result.ok is True
            assert result.exit_code == 1
            assert "Condition is false" in result.to_str()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_unknown_command_error_through_run_bash(self):
        from src.sandbox.executor import SandboxExecutor

        ex = SandboxExecutor(session_id="sem-e2e-5")
        try:
            result = await ex.run_bash("ls /nonexistent_path_abc123 2>/dev/null")
            assert result.ok is False
            assert result.command != ""
        finally:
            ex.cleanup()
