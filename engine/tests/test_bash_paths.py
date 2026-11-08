"""Tests for bash write-target extraction (src/sandbox/bash_paths.py)."""

import pytest

from src.sandbox.bash_paths import (
    collect_sed_in_place_targets,
    extract_written_paths,
    tokenize_command,
)


class TestRedirections:
    def test_simple_redirect_spaced(self):
        assert extract_written_paths("echo hi > /tmp/foo") == ["/tmp/foo"]

    def test_simple_redirect_glued(self):
        assert extract_written_paths("echo hi >/tmp/foo") == ["/tmp/foo"]

    def test_append_redirect_spaced(self):
        assert extract_written_paths("echo hi >> /tmp/log") == ["/tmp/log"]

    def test_append_redirect_glued(self):
        assert extract_written_paths("echo hi >>/tmp/log") == ["/tmp/log"]

    def test_combined_stderr_redirect(self):
        assert extract_written_paths("cmd &> /tmp/all") == ["/tmp/all"]

    def test_stdout_fd_redirect(self):
        assert extract_written_paths("cmd 1> /tmp/out") == ["/tmp/out"]

    def test_stderr_fd_redirect(self):
        assert extract_written_paths("cmd 2> /tmp/err") == ["/tmp/err"]

    def test_dangerous_target_via_redirect(self):
        assert extract_written_paths("echo evil > ~/.bashrc") == ["~/.bashrc"]

    def test_dangerous_target_via_append(self):
        assert extract_written_paths("echo line >> ~/.zshrc") == ["~/.zshrc"]


class TestTee:
    def test_tee_single_file(self):
        assert extract_written_paths("echo hi | tee /tmp/foo") == ["/tmp/foo"]

    def test_tee_append_flag(self):
        assert extract_written_paths("echo hi | tee -a /tmp/log") == ["/tmp/log"]

    def test_tee_dangerous(self):
        assert extract_written_paths("echo evil | tee -a ~/.zshrc") == ["~/.zshrc"]

    def test_tee_multiple_files(self):
        result = extract_written_paths("echo hi | tee /tmp/a /tmp/b /tmp/c")
        assert result == ["/tmp/a", "/tmp/b", "/tmp/c"]

    def test_tee_with_double_dash(self):
        result = extract_written_paths("echo hi | tee -- /tmp/-weird-name")
        assert "/tmp/-weird-name" in result


class TestSedInPlace:
    def test_sed_short_flag(self):
        result = extract_written_paths("sed -i s/a/b/ /tmp/foo.txt")
        assert "/tmp/foo.txt" in result

    def test_sed_long_flag(self):
        result = extract_written_paths("sed --in-place s/a/b/ /tmp/foo.txt")
        assert "/tmp/foo.txt" in result

    def test_sed_inline_backup(self):
        result = extract_written_paths("sed -i.bak s/a/b/ /tmp/foo.txt")
        assert "/tmp/foo.txt" in result

    def test_sed_dangerous(self):
        result = extract_written_paths("sed -i s/a/b/ ~/.gitconfig")
        assert "~/.gitconfig" in result

    def test_sed_without_in_place_returns_empty(self):
        assert extract_written_paths("sed s/a/b/ /tmp/foo.txt") == []

    def test_sed_combined_short_flag_contains_i(self):
        result = extract_written_paths("sed -nEi s/a/b/ /tmp/foo.txt")
        assert "/tmp/foo.txt" in result

    def test_sed_combined_short_flag_dangerous_target(self):
        result = extract_written_paths("sed -nEi s/x/evil/ ~/.gitconfig")
        assert "~/.gitconfig" in result

    def test_sed_combined_short_flag_without_i_returns_empty(self):
        assert extract_written_paths("sed -nE s/a/b/ /tmp/foo.txt") == []


class TestCollectSedInPlaceTargets:
    def test_standalone_dash_i(self):
        tokens = ["sed", "-i", "s/a/b/", "file.txt"]
        assert collect_sed_in_place_targets(tokens, 0) == ["s/a/b/", "file.txt"]

    def test_long_in_place(self):
        tokens = ["sed", "--in-place", "s/a/b/", "file.txt"]
        assert collect_sed_in_place_targets(tokens, 0) == ["s/a/b/", "file.txt"]

    def test_backup_extension(self):
        tokens = ["sed", "-i.bak", "s/a/b/", "file.txt"]
        assert collect_sed_in_place_targets(tokens, 0) == ["s/a/b/", "file.txt"]

    def test_combined_short_flag_nEi(self):
        tokens = ["sed", "-nEi", "s/a/b/", "file.txt"]
        result = collect_sed_in_place_targets(tokens, 0)
        assert "file.txt" in result

    def test_combined_short_flag_in_middle(self):
        tokens = ["sed", "-nzi", "s/a/b/", "file.txt"]
        result = collect_sed_in_place_targets(tokens, 0)
        assert "file.txt" in result

    def test_no_inplace_returns_empty(self):
        tokens = ["sed", "-n", "s/a/b/", "file.txt"]
        assert collect_sed_in_place_targets(tokens, 0) == []

    def test_combined_short_flag_without_i_returns_empty(self):
        tokens = ["sed", "-nEr", "s/a/b/", "file.txt"]
        assert collect_sed_in_place_targets(tokens, 0) == []


class TestTokenizeCommand:
    def test_basic_split(self):
        assert tokenize_command("echo hello world") == ["echo", "hello", "world"]

    def test_quoted_argument(self):
        assert tokenize_command("echo 'hello world'") == ["echo", "hello world"]

    def test_escaped_space(self):
        assert tokenize_command("echo hello\\ world") == ["echo", "hello world"]

    def test_empty_string_returns_empty(self):
        assert tokenize_command("") == []

    def test_whitespace_only_returns_empty(self):
        assert tokenize_command("   ") == []

    @pytest.mark.parametrize("value", [None, 123, b"bytes"])
    def test_non_string_returns_empty(self, value):
        assert tokenize_command(value) == []  # type: ignore[arg-type]

    def test_unbalanced_quote_returns_empty(self):
        assert tokenize_command("echo 'unclosed") == []

    def test_sed_command_tokenized(self):
        result = tokenize_command("sed -i 's/foo/bar/g' file.txt")
        assert result == ["sed", "-i", "s/foo/bar/g", "file.txt"]


class TestFileArgCommands:
    def test_rm_single(self):
        assert extract_written_paths("rm /tmp/foo") == ["/tmp/foo"]

    def test_rm_multiple(self):
        result = extract_written_paths("rm /tmp/a /tmp/b /tmp/c")
        assert result == ["/tmp/a", "/tmp/b", "/tmp/c"]

    def test_rm_rf_dir(self):
        assert extract_written_paths("rm -rf ~/.git") == ["~/.git"]

    def test_mv_dst(self):
        assert extract_written_paths("mv /tmp/old /tmp/new") == ["/tmp/new"]

    def test_mv_dangerous_dst(self):
        result = extract_written_paths("mv /tmp/payload ~/.bashrc")
        assert "~/.bashrc" in result

    def test_cp_dst(self):
        assert extract_written_paths("cp /etc/hosts /tmp/copy") == ["/tmp/copy"]

    def test_cp_dangerous_dst(self):
        result = extract_written_paths("cp /etc/hosts ~/.mcp.json")
        assert "~/.mcp.json" in result

    def test_ln_link_name(self):
        assert extract_written_paths("ln -s /etc/passwd /tmp/link") == ["/tmp/link"]

    def test_touch_single(self):
        assert extract_written_paths("touch /tmp/marker") == ["/tmp/marker"]

    def test_touch_multiple(self):
        result = extract_written_paths("touch /tmp/a /tmp/b")
        assert result == ["/tmp/a", "/tmp/b"]

    def test_dd_of(self):
        assert extract_written_paths("dd if=/dev/zero of=/tmp/blob bs=1M count=1") == ["/tmp/blob"]

    def test_dd_dangerous_of(self):
        result = extract_written_paths("dd if=/dev/zero of=~/.bashrc")
        assert "~/.bashrc" in result


class TestPipelines:
    def test_pipeline_with_tee(self):
        result = extract_written_paths("cat /etc/hosts | tee /tmp/copy")
        assert "/tmp/copy" in result

    def test_pipeline_with_redirect_only_at_end(self):
        result = extract_written_paths("ls -la | grep foo > /tmp/out")
        assert "/tmp/out" in result

    def test_chained_with_semicolon(self):
        result = extract_written_paths("ls /tmp ; touch /tmp/marker")
        assert "/tmp/marker" in result

    def test_chained_with_and(self):
        result = extract_written_paths("test -d /tmp && touch /tmp/marker")
        assert "/tmp/marker" in result


class TestNoWrites:
    def test_ls_returns_empty(self):
        assert extract_written_paths("ls -la /tmp") == []

    def test_cat_returns_empty(self):
        assert extract_written_paths("cat /etc/hosts") == []

    def test_grep_returns_empty(self):
        assert extract_written_paths("grep foo /tmp/file") == []

    def test_pipe_with_no_writes(self):
        assert extract_written_paths("cat /etc/hosts | grep foo | wc -l") == []

    def test_empty_command(self):
        assert extract_written_paths("") == []

    def test_whitespace_command(self):
        assert extract_written_paths("   ") == []

    def test_non_string(self):
        assert extract_written_paths(None) == []  # type: ignore[arg-type]
        assert extract_written_paths(123) == []  # type: ignore[arg-type]


class TestEdgeCases:
    def test_unbalanced_quote_returns_empty(self):
        assert extract_written_paths("echo 'unclosed > /tmp/foo") == []

    def test_dedupe_preserves_order(self):
        result = extract_written_paths("echo a > /tmp/foo ; echo b >> /tmp/foo")
        assert result == ["/tmp/foo"]

    def test_absolute_command_path(self):
        result = extract_written_paths("echo hi | /usr/bin/tee /tmp/foo")
        assert "/tmp/foo" in result

    def test_unknown_command_returns_empty(self):
        assert extract_written_paths("frobnicate /tmp/foo") == []


class TestPrecommandWrappers:
    def test_sudo_rm_dangerous(self):
        result = extract_written_paths("sudo rm ~/.bashrc")
        assert "~/.bashrc" in result

    def test_sudo_tee_append_dangerous(self):
        result = extract_written_paths("echo evil | sudo tee -a ~/.zshrc")
        assert "~/.zshrc" in result

    def test_doas_mv_to_dangerous(self):
        result = extract_written_paths("doas mv /tmp/payload ~/.bashrc")
        assert "~/.bashrc" in result

    def test_env_assignment_then_tee(self):
        result = extract_written_paths("env FOO=bar tee ~/.zshrc")
        assert "~/.zshrc" in result

    def test_bare_env_assignment_then_tee(self):
        result = extract_written_paths("FOO=bar tee ~/.zshrc")
        assert "~/.zshrc" in result

    def test_sudo_env_assignment_sed_in_place(self):
        result = extract_written_paths("sudo env FOO=bar sed -i s/x/y/ ~/.gitconfig")
        assert "~/.gitconfig" in result

    def test_nohup_nice_cp_dangerous(self):
        result = extract_written_paths("nohup nice cp /tmp/payload ~/.bashrc")
        assert "~/.bashrc" in result

    def test_strace_dd_dangerous(self):
        result = extract_written_paths("strace dd if=/dev/zero of=~/.bashrc")
        assert "~/.bashrc" in result

    def test_sudo_with_safe_target_unaffected(self):
        result = extract_written_paths("sudo touch /tmp/marker")
        assert result == ["/tmp/marker"]
