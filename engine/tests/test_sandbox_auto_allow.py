"""Tests for sandbox auto-allow (src/sandbox/sandbox_auto_allow.py)."""

import pytest

from src.sandbox.sandbox_auto_allow import is_sandbox_auto_allowed, sandbox_auto_allow_enabled


class TestSafeReadCommands:
    @pytest.mark.parametrize(
        "cmd",
        [
            "ls",
            "ls -la",
            "ls /tmp",
            "cat file.txt",
            "cat /etc/hosts",
            "head -n 10 file.txt",
            "tail -f log.txt",
            "wc -l file.txt",
            "stat file.txt",
            "file image.png",
            "pwd",
            "uname -a",
            "whoami",
            "id",
            "hostname",
            "date",
            "printenv",
            "grep pattern file.txt",
            "rg pattern",
            "sort file.txt",
            "uniq file.txt",
            "cut -d: -f1 file.txt",
            "tr a-z A-Z",
            "jq .name file.json",
            "diff a.txt b.txt",
            "du -sh .",
            "df -h",
            "basename /path/to/file",
            "dirname /path/to/file",
            "readlink symlink",
            "realpath .",
            "which python",
            "ps aux",
            "pgrep python",
            "md5sum file.txt",
            "sha256sum file.txt",
            "echo hello",
            "tree",
            "less file.txt",
        ],
    )
    def test_safe_command_allowed(self, cmd):
        assert is_sandbox_auto_allowed(cmd) is True


class TestVersionChecks:
    @pytest.mark.parametrize(
        "cmd",
        [
            "python --version",
            "node --version",
            "npm --version",
            "pip --version",
            "gcc --version",
            "rustc --version",
            "go version",
            "java -version",
            "ruby -v",
        ],
    )
    def test_version_check_allowed(self, cmd):
        assert is_sandbox_auto_allowed(cmd) is True


class TestGitReadOps:
    @pytest.mark.parametrize(
        "cmd",
        [
            "git status",
            "git log",
            "git log --oneline -10",
            "git diff",
            "git diff HEAD~1",
            "git branch",
            "git branch -a",
            "git tag",
            "git remote -v",
            "git rev-parse HEAD",
            "git show HEAD",
            "git ls-files",
            "git blame file.py",
        ],
    )
    def test_git_read_op_allowed(self, cmd):
        assert is_sandbox_auto_allowed(cmd) is True


class TestSubcommandMatch:
    @pytest.mark.parametrize(
        "cmd",
        [
            "pip list",
            "pip show requests",
            "pip freeze",
            "npm list",
            "npm ls",
            "npm info express",
            "npm outdated",
            "docker ps",
            "docker images",
            "docker logs container_name",
        ],
    )
    def test_safe_subcommand_allowed(self, cmd):
        assert is_sandbox_auto_allowed(cmd) is True


class TestUnsafeCommands:
    @pytest.mark.parametrize(
        "cmd",
        [
            "rm file.txt",
            "rm -rf /",
            "mv old.txt new.txt",
            "cp src dst",
            "curl https://example.com",
            "wget https://example.com",
            "pip install requests",
            "npm install express",
            "git push",
            "git commit -m 'msg'",
            "git checkout main",
            "python script.py",
            "python -c 'import os'",
            "node -e 'process.exit(1)'",
            "chmod 777 file",
            "chown root file",
            "mkdir dir",
            "touch file",
            "sed -i 's/a/b/' file",
            "awk '{print}' file",
            "docker run image",
            "docker exec container cmd",
            "kill -9 1234",
            "sudo anything",
            "ssh host",
            "scp file host:",
        ],
    )
    def test_unsafe_command_rejected(self, cmd):
        assert is_sandbox_auto_allowed(cmd) is False


class TestCompoundCommands:
    @pytest.mark.parametrize(
        "cmd",
        [
            "ls && rm file",
            "ls || echo fail",
            "ls; rm file",
            "cat file | grep pattern",
            "echo hello > file.txt",
            "echo hello >> file.txt",
            "cat < file.txt",
        ],
    )
    def test_compound_rejected(self, cmd):
        assert is_sandbox_auto_allowed(cmd) is False

    def test_backtick_substitution_rejected(self):
        assert is_sandbox_auto_allowed("echo `rm -rf /`") is False

    def test_dollar_paren_substitution_rejected(self):
        assert is_sandbox_auto_allowed("echo $(rm -rf /)") is False

    def test_nested_subshell_rejected(self):
        assert is_sandbox_auto_allowed("echo $(cat $(whoami))") is False

    def test_quoted_pipe_not_rejected(self):
        assert is_sandbox_auto_allowed("echo 'hello | world'") is True

    def test_quoted_semicolon_not_rejected(self):
        assert is_sandbox_auto_allowed('echo "hello; world"') is True

    def test_quoted_backtick_not_rejected(self):
        assert is_sandbox_auto_allowed("echo 'hello `world`'") is True


class TestSecurityRegressions:
    def test_env_executes_arbitrary_commands(self):
        assert is_sandbox_auto_allowed("env bash") is False
        assert is_sandbox_auto_allowed("env -i bash") is False
        assert is_sandbox_auto_allowed("env VAR=val sh -c 'evil'") is False

    def test_git_config_can_write(self):
        assert is_sandbox_auto_allowed("git config --global user.email evil") is False
        assert is_sandbox_auto_allowed("git config core.hooksPath /tmp/evil") is False

    def test_git_stash_modifies_worktree(self):
        assert is_sandbox_auto_allowed("git stash") is False
        assert is_sandbox_auto_allowed("git stash pop") is False
        assert is_sandbox_auto_allowed("git stash drop") is False


class TestFeatureFlag:
    def test_disabled_rejects_everything(self, monkeypatch):
        monkeypatch.setenv("FEATURE_SANDBOX_AUTO_ALLOW", "false")
        assert is_sandbox_auto_allowed("ls") is False
        assert is_sandbox_auto_allowed("pwd") is False

    def test_enabled_allows_safe(self, monkeypatch):
        monkeypatch.setenv("FEATURE_SANDBOX_AUTO_ALLOW", "true")
        assert is_sandbox_auto_allowed("ls") is True

    def test_default_is_enabled(self, monkeypatch):
        monkeypatch.delenv("FEATURE_SANDBOX_AUTO_ALLOW", raising=False)
        assert sandbox_auto_allow_enabled() is True

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("FEATURE_SANDBOX_AUTO_ALLOW", "FALSE")
        assert sandbox_auto_allow_enabled() is False
        monkeypatch.setenv("FEATURE_SANDBOX_AUTO_ALLOW", "True")
        assert sandbox_auto_allow_enabled() is True


class TestEdgeCases:
    def test_empty_command(self):
        assert is_sandbox_auto_allowed("") is False

    def test_whitespace_only(self):
        assert is_sandbox_auto_allowed("   ") is False

    def test_unknown_command(self):
        assert is_sandbox_auto_allowed("foobar --help") is False

    def test_path_prefix_stripped(self):
        assert is_sandbox_auto_allowed("/usr/bin/ls") is True
        assert is_sandbox_auto_allowed("/bin/cat file.txt") is True

    def test_git_write_op_rejected(self):
        assert is_sandbox_auto_allowed("git push origin main") is False
        assert is_sandbox_auto_allowed("git commit -m 'msg'") is False
        assert is_sandbox_auto_allowed("git reset --hard") is False
