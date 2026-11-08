"""Tests for the sed edit parser (src/sandbox/sed_edit_parser.py)."""

from __future__ import annotations

import pytest

from src.sandbox.sed_edit_parser import parse_sed_edit


def tokens(*args: str) -> list[str]:
    return ["sed", *args]


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------


class TestParseSedEditBasic:
    def test_simple_in_place_substitution(self):
        info = parse_sed_edit(tokens("-i", "s/foo/bar/", "file.txt"))
        assert info is not None
        assert info.file_path == "file.txt"
        assert info.pattern == "foo"
        assert info.replacement == "bar"
        assert info.flags == ""
        assert info.extended_regex is False

    def test_global_substitution(self):
        info = parse_sed_edit(tokens("-i", "s/foo/bar/g", "file.txt"))
        assert info is not None
        assert info.flags == "g"

    def test_in_place_long_form(self):
        info = parse_sed_edit(tokens("--in-place", "s/foo/bar/g", "a.txt"))
        assert info is not None
        assert info.file_path == "a.txt"
        assert info.flags == "g"

    def test_gnu_backup_inplace(self):
        info = parse_sed_edit(tokens("-i.bak", "s/foo/bar/", "a.txt"))
        assert info is not None
        assert info.file_path == "a.txt"

    def test_returns_none_without_inplace(self):
        info = parse_sed_edit(tokens("-e", "s/foo/bar/", "a.txt"))
        assert info is None

    def test_returns_none_without_files(self):
        info = parse_sed_edit(tokens("-i", "s/foo/bar/"))
        assert info is None

    def test_returns_none_on_empty_tokens(self):
        assert parse_sed_edit([]) is None

    def test_returns_none_on_parse_error(self):
        assert parse_sed_edit(tokens("-e")) is None

    def test_returns_none_on_multiple_expressions(self):
        info = parse_sed_edit(tokens("-i", "-e", "s/a/1/", "-e", "s/b/2/", "file.txt"))
        assert info is None

    def test_returns_none_on_multiple_files(self):
        info = parse_sed_edit(tokens("-i", "s/a/b/", "f1.txt", "f2.txt"))
        assert info is None

    def test_alternate_delimiter_returns_none(self):
        info = parse_sed_edit(tokens("-i", "s|foo|bar|", "a.txt"))
        assert info is None


# ---------------------------------------------------------------------------
# BRE → ERE conversion
# ---------------------------------------------------------------------------


class TestBreToEreConversion:
    def test_bre_parens_become_ere_groups(self):
        # BRE: `\(abc\)+` means literal parens around "abc" with literal "+".
        # Wait — `+` is also a metacharacter in ERE that must be escaped in
        # BRE. BRE `\+` means ERE `+`. BRE `+` means ERE `\+`.
        info = parse_sed_edit(tokens("-i", "s/\\(abc\\)/X/", "f.txt"))
        assert info is not None
        assert info.pattern == "(abc)"

    def test_bre_literal_paren_becomes_escaped(self):
        info = parse_sed_edit(tokens("-i", "s/(abc)/X/", "f.txt"))
        assert info is not None
        assert info.pattern == "\\(abc\\)"

    def test_bre_plus_escape(self):
        info = parse_sed_edit(tokens("-i", "s/a\\+b/X/", "f.txt"))
        assert info is not None
        assert info.pattern == "a+b"

    def test_bre_literal_plus_becomes_escaped(self):
        info = parse_sed_edit(tokens("-i", "s/a+b/X/", "f.txt"))
        assert info is not None
        assert info.pattern == "a\\+b"

    def test_bre_question_mark_escape(self):
        info = parse_sed_edit(tokens("-i", "s/a\\?b/X/", "f.txt"))
        assert info is not None
        assert info.pattern == "a?b"

    def test_bre_pipe_alternation(self):
        info = parse_sed_edit(tokens("-i", "s/a\\|b/X/", "f.txt"))
        assert info is not None
        assert info.pattern == "a|b"

    def test_bre_braces_escape(self):
        info = parse_sed_edit(tokens("-i", "s/a\\{2\\}/X/", "f.txt"))
        assert info is not None
        assert info.pattern == "a{2}"


class TestEreFlagLeavesPatternAlone:
    def test_capital_E_ere_mode(self):
        info = parse_sed_edit(tokens("-i", "-E", "s/(abc)+/X/", "f.txt"))
        assert info is not None
        assert info.extended_regex is True
        assert info.pattern == "(abc)+"

    def test_lowercase_r_ere_mode(self):
        info = parse_sed_edit(tokens("-i", "-r", "s/(abc)+/X/", "f.txt"))
        assert info is not None
        assert info.extended_regex is True
        assert info.pattern == "(abc)+"

    def test_long_regexp_extended(self):
        info = parse_sed_edit(tokens("-i", "--regexp-extended", "s/(abc)+/X/", "f.txt"))
        assert info is not None
        assert info.extended_regex is True
        assert info.pattern == "(abc)+"


# ---------------------------------------------------------------------------
# Replacement ampersand handling
# ---------------------------------------------------------------------------


class TestAmpersandSalt:
    def test_bare_ampersand_becomes_backref(self):
        info = parse_sed_edit(tokens("-i", "s/foo/[&]/", "f.txt"))
        assert info is not None
        # Bare `&` normalises to `\0` (the whole-match backreference).
        assert info.replacement == "[\\0]"

    def test_literal_backslash_ampersand_preserved(self):
        info = parse_sed_edit(tokens("-i", "s/foo/[\\&]/", "f.txt"))
        assert info is not None
        # Literal `\&` stays as `&` in the output.
        assert info.replacement == "[&]"

    def test_both_forms_in_same_replacement(self):
        info = parse_sed_edit(tokens("-i", "s/foo/\\&=&/", "f.txt"))
        assert info is not None
        assert info.replacement == "&=\\0"


# ---------------------------------------------------------------------------
# Flag normalisation
# ---------------------------------------------------------------------------


class TestFlagNormalisation:
    @pytest.mark.parametrize(
        "input_flags,expected",
        [
            ("g", "g"),
            ("gi", "gi"),
            ("ig", "gi"),
            ("giM", "Mgi"),
            ("", ""),
        ],
    )
    def test_flag_canonicalisation(self, input_flags, expected):
        info = parse_sed_edit(tokens("-i", f"s/foo/bar/{input_flags}", "f.txt"))
        assert info is not None
        assert info.flags == expected
