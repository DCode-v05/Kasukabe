"""Tests for src/sandbox/readonly_allowlist.py"""

import asyncio
from src.sandbox.readonly_allowlist import (
    is_command_read_only,
    tokenize,
    validate_flags,
    CommandConfig,
)
from src.sandbox.permissions import (
    check_permission,
    ToolSource,
    PermissionMode,
)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_simple(self):
        assert tokenize("git status") == ["git", "status"]

    def test_single_quotes(self):
        assert tokenize("echo 'hello world'") == ["echo", "hello world"]

    def test_double_quotes(self):
        assert tokenize('grep "foo bar" file') == ["grep", "foo bar", "file"]

    def test_escaped_in_double_quotes(self):
        assert tokenize('echo "it\\"s"') == ["echo", 'it"s']

    def test_backslash_outside(self):
        assert tokenize("echo hello\\ world") == ["echo", "hello world"]

    def test_empty(self):
        assert tokenize("") == []

    def test_unbalanced_single(self):
        assert tokenize("echo 'unmatched") is None

    def test_unbalanced_double(self):
        assert tokenize('echo "unmatched') is None

    def test_multiple_spaces(self):
        assert tokenize("ls   -la   /tmp") == ["ls", "-la", "/tmp"]


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------


class TestValidateFlags:
    def test_known_no_arg_flag(self):
        cfg = CommandConfig(safe_flags={"-v": "none", "--verbose": "none"})
        assert validate_flags(["-v"], 0, cfg) is True

    def test_unknown_flag_rejected(self):
        cfg = CommandConfig(safe_flags={"-v": "none"})
        assert validate_flags(["--evil"], 0, cfg) is False

    def test_flag_with_string_arg(self):
        cfg = CommandConfig(safe_flags={"--format": "string"})
        assert validate_flags(["--format", "json"], 0, cfg) is True

    def test_flag_with_equals(self):
        cfg = CommandConfig(safe_flags={"--format": "string"})
        assert validate_flags(["--format=json"], 0, cfg) is True

    def test_flag_none_rejects_equals(self):
        cfg = CommandConfig(safe_flags={"-v": "none"})
        assert validate_flags(["-v=bad"], 0, cfg) is False

    def test_number_flag_valid(self):
        cfg = CommandConfig(safe_flags={"-n": "number"})
        assert validate_flags(["-n", "10"], 0, cfg) is True

    def test_number_flag_invalid(self):
        cfg = CommandConfig(safe_flags={"-n": "number"})
        assert validate_flags(["-n", "abc"], 0, cfg) is False

    def test_combined_short_flags(self):
        cfg = CommandConfig(safe_flags={"-n": "none", "-r": "none", "-l": "none"})
        assert validate_flags(["-nrl"], 0, cfg) is True

    def test_combined_with_arg_flag_rejected(self):
        cfg = CommandConfig(safe_flags={"-n": "number", "-r": "none"})
        assert validate_flags(["-rn"], 0, cfg) is False

    def test_dollar_rejected(self):
        cfg = CommandConfig(safe_flags={})
        assert validate_flags(["$HOME"], 0, cfg) is False

    def test_brace_expansion_rejected(self):
        cfg = CommandConfig(safe_flags={})
        assert validate_flags(["{a,b}"], 0, cfg) is False

    def test_double_dash_stops_flags(self):
        cfg = CommandConfig(safe_flags={"-v": "none"}, respects_double_dash=True)
        assert validate_flags(["-v", "--", "--unknown"], 0, cfg) is True

    def test_positional_allowed(self):
        cfg = CommandConfig(safe_flags={"-v": "none"})
        assert validate_flags(["-v", "file.txt"], 0, cfg) is True

    def test_git_numeric_shorthand(self):
        cfg = CommandConfig(safe_flags={"-n": "number"})
        assert validate_flags(["-5"], 0, cfg) is True

    def test_missing_arg_rejected(self):
        cfg = CommandConfig(safe_flags={"--format": "string"})
        assert validate_flags(["--format"], 0, cfg) is False


# ---------------------------------------------------------------------------
# Git commands
# ---------------------------------------------------------------------------


class TestGitCommands:
    def test_git_status(self):
        assert is_command_read_only("git status") is True

    def test_git_status_short(self):
        assert is_command_read_only("git status -s -b") is True

    def test_git_log(self):
        assert is_command_read_only("git log --oneline -10") is True

    def test_git_log_graph(self):
        assert is_command_read_only("git log --oneline --graph --all") is True

    def test_git_diff(self):
        assert is_command_read_only("git diff HEAD") is True

    def test_git_diff_cached(self):
        assert is_command_read_only("git diff --cached --stat") is True

    def test_git_show(self):
        assert is_command_read_only("git show HEAD --stat") is True

    def test_git_branch_list(self):
        assert is_command_read_only("git branch -a") is True

    def test_git_branch_create_rejected(self):
        assert is_command_read_only("git branch new-feature") is False

    def test_git_branch_list_explicit(self):
        assert is_command_read_only("git branch --list 'feature*'") is True

    def test_git_remote(self):
        assert is_command_read_only("git remote -v") is True

    def test_git_remote_add_rejected(self):
        assert is_command_read_only("git remote add origin url") is False

    def test_git_remote_show(self):
        assert is_command_read_only("git remote show origin") is True

    def test_git_tag_list(self):
        assert is_command_read_only("git tag -l") is True

    def test_git_tag_create_rejected(self):
        assert is_command_read_only("git tag v1.0.0") is False

    def test_git_describe(self):
        assert is_command_read_only("git describe --tags --always") is True

    def test_git_rev_parse(self):
        assert is_command_read_only("git rev-parse HEAD") is True

    def test_git_rev_parse_toplevel(self):
        assert is_command_read_only("git rev-parse --show-toplevel") is True

    def test_git_blame(self):
        assert is_command_read_only("git blame -L 10,20 file.py") is True

    def test_git_ls_files(self):
        assert is_command_read_only("git ls-files --modified") is True

    def test_git_ls_tree(self):
        assert is_command_read_only("git ls-tree -r HEAD") is True

    def test_git_shortlog(self):
        assert is_command_read_only("git shortlog -sn") is True

    def test_git_rev_list(self):
        assert is_command_read_only("git rev-list --count HEAD") is True

    def test_git_merge_base(self):
        assert is_command_read_only("git merge-base --is-ancestor abc def") is True

    def test_git_cat_file(self):
        assert is_command_read_only("git cat-file -p HEAD") is True

    def test_git_for_each_ref(self):
        assert (
            is_command_read_only("git for-each-ref --format='%(refname)' --sort=-creatordate")
            is True
        )

    def test_git_grep(self):
        assert is_command_read_only("git grep -n pattern") is True

    def test_git_stash_show(self):
        assert is_command_read_only("git stash show --stat") is True

    def test_git_stash_list(self):
        assert is_command_read_only("git stash list") is True

    def test_git_reflog(self):
        assert is_command_read_only("git reflog --oneline -10") is True

    def test_git_reflog_expire_rejected(self):
        assert is_command_read_only("git reflog expire --all") is False

    def test_git_reflog_delete_rejected(self):
        assert is_command_read_only("git reflog delete HEAD@{0}") is False

    def test_git_ls_remote(self):
        assert is_command_read_only("git ls-remote --tags origin") is True

    def test_git_config_get(self):
        assert is_command_read_only("git config --get user.email") is True

    def test_git_config_set_rejected(self):
        assert is_command_read_only("git config user.email foo@bar.com") is False

    def test_git_config_list(self):
        assert is_command_read_only("git config --list") is True

    def test_git_worktree_list(self):
        assert is_command_read_only("git worktree list --porcelain") is True

    def test_git_commit_rejected(self):
        assert is_command_read_only("git commit -m 'msg'") is False

    def test_git_push_rejected(self):
        assert is_command_read_only("git push origin main") is False

    def test_git_add_rejected(self):
        assert is_command_read_only("git add .") is False

    def test_git_checkout_rejected(self):
        assert is_command_read_only("git checkout -b new") is False


# ---------------------------------------------------------------------------
# Search tools
# ---------------------------------------------------------------------------


class TestSearchTools:
    def test_grep_basic(self):
        assert is_command_read_only("grep -rn pattern dir/") is True

    def test_grep_context(self):
        assert is_command_read_only("grep -C 5 -i pattern file") is True

    def test_rg_basic(self):
        assert is_command_read_only("rg --type py pattern") is True

    def test_rg_glob(self):
        assert is_command_read_only("rg -g '*.ts' pattern src/") is True

    def test_find_basic(self):
        assert is_command_read_only("find . -name '*.py'") is True

    def test_find_exec_rejected(self):
        assert is_command_read_only("find . -exec rm {} \\;") is False

    def test_find_delete_rejected(self):
        assert is_command_read_only("find . -name '*.tmp' -delete") is False

    def test_fd_basic(self):
        assert is_command_read_only("fd -e py pattern") is True

    def test_fd_exec_rejected(self):
        assert is_command_read_only("fd -e py --exec cat") is False

    def test_ag_basic(self):
        assert is_command_read_only("ag -i pattern") is True


# ---------------------------------------------------------------------------
# File inspection
# ---------------------------------------------------------------------------


class TestFileInspection:
    def test_cat(self):
        assert is_command_read_only("cat file.txt") is True

    def test_cat_number(self):
        assert is_command_read_only("cat -n file.txt") is True

    def test_head(self):
        assert is_command_read_only("head -n 20 file.txt") is True

    def test_tail(self):
        assert is_command_read_only("tail -f log.txt") is True

    def test_wc(self):
        assert is_command_read_only("wc -l file.txt") is True

    def test_file(self):
        assert is_command_read_only("file --mime-type image.png") is True

    def test_stat(self):
        assert is_command_read_only("stat file.txt") is True

    def test_du(self):
        assert is_command_read_only("du -sh .") is True

    def test_df(self):
        assert is_command_read_only("df -h") is True

    def test_ls(self):
        assert is_command_read_only("ls -la /tmp") is True


# ---------------------------------------------------------------------------
# Process / system
# ---------------------------------------------------------------------------


class TestProcessSystem:
    def test_ps_aux(self):
        assert is_command_read_only("ps aux") is True

    def test_ps_bsd_e_rejected(self):
        assert is_command_read_only("ps auxe") is False

    def test_ps_ef(self):
        assert is_command_read_only("ps -ef") is True

    def test_hostname(self):
        assert is_command_read_only("hostname") is True

    def test_hostname_fqdn(self):
        assert is_command_read_only("hostname -f") is True

    def test_hostname_set_rejected(self):
        assert is_command_read_only("hostname evil.com") is False

    def test_date_format(self):
        assert is_command_read_only("date '+%Y-%m-%d'") is True

    def test_date_set_rejected(self):
        assert is_command_read_only("date 0101120025") is False

    def test_date_display(self):
        assert is_command_read_only("date -u") is True

    def test_which(self):
        assert is_command_read_only("which python3") is True

    def test_type(self):
        assert is_command_read_only("type ls") is True

    def test_echo(self):
        assert is_command_read_only("echo hello") is True


# ---------------------------------------------------------------------------
# Dev version checks
# ---------------------------------------------------------------------------


class TestDevVersionChecks:
    def test_python_version(self):
        assert is_command_read_only("python --version") is True

    def test_python3_version(self):
        assert is_command_read_only("python3 --version") is True

    def test_python_code_rejected(self):
        assert is_command_read_only("python -c 'import os'") is False

    def test_node_version(self):
        assert is_command_read_only("node --version") is True

    def test_node_v(self):
        assert is_command_read_only("node -v") is True

    def test_node_run_rejected(self):
        assert is_command_read_only("node app.js") is False

    def test_npm_version(self):
        assert is_command_read_only("npm --version") is True


# ---------------------------------------------------------------------------
# Checksums
# ---------------------------------------------------------------------------


class TestChecksums:
    def test_sha256sum(self):
        assert is_command_read_only("sha256sum file.txt") is True

    def test_sha256sum_check(self):
        assert is_command_read_only("sha256sum -c checksums.txt") is True

    def test_md5sum(self):
        assert is_command_read_only("md5sum file.txt") is True


# ---------------------------------------------------------------------------
# Other safe
# ---------------------------------------------------------------------------


class TestOtherSafe:
    def test_sort(self):
        assert is_command_read_only("sort -n file.txt") is True

    def test_sort_output_rejected(self):
        assert is_command_read_only("sort -o output.txt file.txt") is False

    def test_base64(self):
        assert is_command_read_only("base64 -d file.txt") is True

    def test_tree(self):
        assert is_command_read_only("tree -L 3") is True

    def test_tree_output_rejected(self):
        assert is_command_read_only("tree -o output.html") is False

    def test_man(self):
        assert is_command_read_only("man git") is True

    def test_tput_cols(self):
        assert is_command_read_only("tput cols") is True

    def test_tput_init_rejected(self):
        assert is_command_read_only("tput init") is False

    def test_tput_reset_rejected(self):
        assert is_command_read_only("tput reset") is False


# ---------------------------------------------------------------------------
# Compound commands
# ---------------------------------------------------------------------------


class TestCompoundCommands:
    def test_all_safe(self):
        assert is_command_read_only("git status && git log --oneline -5") is True

    def test_pipe_safe(self):
        assert is_command_read_only("cat file | grep pattern | wc -l") is True

    def test_mixed_rejected(self):
        assert is_command_read_only("git status && rm -rf .") is False

    def test_semicolon_safe(self):
        assert is_command_read_only("ls -la ; git status") is True

    def test_semicolon_unsafe(self):
        assert is_command_read_only("ls -la ; git push origin main") is False


# ---------------------------------------------------------------------------
# Security edge cases
# ---------------------------------------------------------------------------


class TestSecurityEdgeCases:
    def test_dollar_variable_rejected(self):
        assert is_command_read_only("cat $HOME/.ssh/id_rsa") is False

    def test_backtick_rejected(self):
        assert is_command_read_only("echo `whoami`") is False

    def test_brace_expansion_rejected(self):
        assert is_command_read_only("cat {a,b}.txt") is False

    def test_brace_range_rejected(self):
        assert is_command_read_only("echo {1..10}") is False

    def test_unknown_command_rejected(self):
        assert is_command_read_only("curl http://evil.com") is False

    def test_empty_rejected(self):
        assert is_command_read_only("") is False

    def test_whitespace_rejected(self):
        assert is_command_read_only("   ") is False

    def test_git_output_flag_rejected(self):
        assert is_command_read_only("git diff --output=/tmp/evil") is False

    def test_git_config_env_rejected(self):
        assert is_command_read_only("git --config-env=foo status") is False


# ---------------------------------------------------------------------------
# Integration with permissions.py READ_ONLY branch
# ---------------------------------------------------------------------------


class TestReadOnlyIntegration:
    def run(self, coro):
        return asyncio.run(coro)

    def test_git_rev_parse_allowed_in_readonly(self):
        """git rev-parse is UNKNOWN by classifier but should be rescued by allowlist."""
        result = self.run(
            check_permission(
                "bash",
                {"command": "git rev-parse HEAD"},
                ToolSource.SANDBOXED,
                permission_mode=PermissionMode.READ_ONLY,
            )
        )
        assert result.allowed is True

    def test_python_version_allowed_in_readonly(self):
        result = self.run(
            check_permission(
                "bash",
                {"command": "python --version"},
                ToolSource.SANDBOXED,
                permission_mode=PermissionMode.READ_ONLY,
            )
        )
        assert result.allowed is True

    def test_rm_denied_in_readonly(self):
        result = self.run(
            check_permission(
                "bash",
                {"command": "rm -rf /tmp/foo"},
                ToolSource.SANDBOXED,
                permission_mode=PermissionMode.READ_ONLY,
            )
        )
        assert result.allowed is False

    def test_git_push_denied_in_readonly(self):
        result = self.run(
            check_permission(
                "bash",
                {"command": "git push origin main"},
                ToolSource.SANDBOXED,
                permission_mode=PermissionMode.READ_ONLY,
            )
        )
        assert result.allowed is False

    def test_sort_allowed_in_readonly(self):
        result = self.run(
            check_permission(
                "bash",
                {"command": "sort -n file.txt"},
                ToolSource.SANDBOXED,
                permission_mode=PermissionMode.READ_ONLY,
            )
        )
        assert result.allowed is True

    def test_grep_already_allowed_in_readonly(self):
        """grep is SEARCH by classifier — allowed without needing rescue."""
        result = self.run(
            check_permission(
                "bash",
                {"command": "grep -r pattern ."},
                ToolSource.SANDBOXED,
                permission_mode=PermissionMode.READ_ONLY,
            )
        )
        assert result.allowed is True

    def test_compound_safe_in_readonly(self):
        result = self.run(
            check_permission(
                "bash",
                {"command": "git rev-parse HEAD && git log --oneline -5"},
                ToolSource.SANDBOXED,
                permission_mode=PermissionMode.READ_ONLY,
            )
        )
        assert result.allowed is True

    def test_non_sandboxed_not_rescued(self):
        """MCP tools don't get the allowlist rescue."""
        result = self.run(
            check_permission(
                "ad:run_script",
                {"command": "git status"},
                ToolSource.MCP,
                permission_mode=PermissionMode.READ_ONLY,
            )
        )
        assert result.allowed is False
