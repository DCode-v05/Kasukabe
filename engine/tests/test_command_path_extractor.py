"""Tests for the command path extractor (src/sandbox/command_path_extractor.py)."""

from __future__ import annotations

import pytest

from src.sandbox.command_path_extractor import (
    PATH_EXTRACTORS,
    filter_positional,
    extract_removal_paths,
    resolve_removal_command,
)


# ---------------------------------------------------------------------------
# filter_positional — POSIX `--` handling is the security-critical path
# ---------------------------------------------------------------------------


class TestFilterPositional:
    def test_empty_args(self):
        assert filter_positional([]) == []

    def test_single_positional(self):
        assert filter_positional(["foo.txt"]) == ["foo.txt"]

    def test_only_flags(self):
        assert filter_positional(["-r", "-f", "-v"]) == []

    def test_combined_flags(self):
        assert filter_positional(["-rf"]) == []

    def test_flags_then_positional(self):
        assert filter_positional(["-rf", "file1", "file2"]) == ["file1", "file2"]

    def test_positional_then_flags(self):
        # Unusual but legal ordering. Without a real parser table we
        # treat every non-flag token as positional regardless of place.
        assert filter_positional(["file1", "-rf", "file2"]) == ["file1", "file2"]

    def test_double_dash_alone(self):
        assert filter_positional(["--"]) == []

    def test_double_dash_then_positional(self):
        assert filter_positional(["--", "foo.txt"]) == ["foo.txt"]

    def test_double_dash_then_dash_prefixed_path(self):
        """The POSIX `--` attack payload MUST keep the dash-prefixed arg.

        `rm -- -/../.claude/settings.local.json` — a naive
        ``not arg.startswith("-")`` filter drops the path, validation
        silently skips, file is deleted. The explicit after-double-dash
        flag catches this.
        """
        got = filter_positional(["--", "-/../.claude/settings.local.json"])
        assert got == ["-/../.claude/settings.local.json"]

    def test_flags_double_dash_then_path(self):
        assert filter_positional(["-rf", "--", "/etc"]) == ["/etc"]

    def test_flags_double_dash_then_dash_path(self):
        assert filter_positional(["-rf", "--", "-weird"]) == ["-weird"]

    def test_double_dash_preserves_empty_trailing(self):
        # `--` with no following tokens means no positional args.
        assert filter_positional(["-r", "--"]) == []

    def test_double_dash_only_first_occurrence_matters(self):
        # After the first `--`, subsequent `--` is just a positional.
        assert filter_positional(["--", "--", "foo"]) == ["--", "foo"]

    def test_empty_string_arg_dropped(self):
        # An empty arg (shouldn't normally occur post-shlex) is dropped.
        assert filter_positional(["", "foo"]) == ["foo"]

    def test_multiple_mixed(self):
        assert filter_positional(["-r", "a", "-f", "b", "--", "-c", "d"]) == ["a", "b", "-c", "d"]


# ---------------------------------------------------------------------------
# extract_rm_paths / extract_rmdir_paths / extract_unlink_paths
# ---------------------------------------------------------------------------


class TestExtractRemovalPaths:
    """All three removal commands share the extractor."""

    def test_single_file(self):
        assert extract_removal_paths(["foo.txt"]) == ["foo.txt"]

    def test_multiple_files_with_flags(self):
        assert extract_removal_paths(["-rf", "a", "b", "c"]) == ["a", "b", "c"]

    def test_posix_double_dash_attack(self):
        assert extract_removal_paths(["--", "-/../.claude/settings.local.json"]) == [
            "-/../.claude/settings.local.json"
        ]

    def test_dash_r_then_dash_dash_then_etc(self):
        assert extract_removal_paths(["-r", "--", "/etc"]) == ["/etc"]

    def test_root_with_dash_dash(self):
        assert extract_removal_paths(["--", "/"]) == ["/"]

    def test_etc_passwd_with_dash_dash(self):
        assert extract_removal_paths(["--", "/etc/passwd"]) == ["/etc/passwd"]

    def test_empty_args(self):
        assert extract_removal_paths([]) == []

    def test_no_positional(self):
        assert extract_removal_paths(["-rf"]) == []


# ---------------------------------------------------------------------------
# PATH_EXTRACTORS — the dispatcher table
# ---------------------------------------------------------------------------


class TestPathExtractorsTable:
    def test_all_removal_commands_registered(self):
        assert PATH_EXTRACTORS["rm"] is extract_removal_paths
        assert PATH_EXTRACTORS["rmdir"] is extract_removal_paths
        assert PATH_EXTRACTORS["unlink"] is extract_removal_paths

    def test_unknown_command_not_registered(self):
        assert "mv" not in PATH_EXTRACTORS
        assert "cp" not in PATH_EXTRACTORS

    def test_dispatch_by_name(self):
        extractor = PATH_EXTRACTORS["rm"]
        assert extractor(["-rf", "/etc"]) == ["/etc"]


# ---------------------------------------------------------------------------
# resolve_removal_command — canonical alias lookup
# ---------------------------------------------------------------------------


class TestResolveRemovalCommand:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("rm", "rm"),
            ("rmdir", "rmdir"),
            ("unlink", "unlink"),
        ],
    )
    def test_basic_aliases(self, name: str, expected: str):
        assert resolve_removal_command(name) == expected

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("RM", "rm"),
            ("Rm", "rm"),
            ("RMDIR", "rmdir"),
            ("Unlink", "unlink"),
        ],
    )
    def test_case_insensitive(self, name: str, expected: str):
        assert resolve_removal_command(name) == expected

    def test_unknown_returns_none(self):
        assert resolve_removal_command("mv") is None
        assert resolve_removal_command("cp") is None
        assert resolve_removal_command("cat") is None
        assert resolve_removal_command("") is None
