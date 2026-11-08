"""Tests for the two-level sed command validator (src/sandbox/sed_validation.py)."""

from __future__ import annotations

import pytest

from src.sandbox.sed_validation import (
    SAFE_PRINT_FLAGS,
    SAFE_SUB_FLAGS,
    SedBehavior,
    check_sed_constraints,
    parse_sed_args,
)


def tokens(*args: str) -> list[str]:
    """Build a tokenised command with `sed` as the head."""
    return ["sed", *args]


# ---------------------------------------------------------------------------
# parse_sed_args
# ---------------------------------------------------------------------------


class TestParseSedArgs:
    def test_positional_expression_and_files(self):
        p = parse_sed_args(["s/foo/bar/", "a.txt", "b.txt"])
        assert p.parse_ok is True
        assert p.expressions == ("s/foo/bar/",)
        assert p.files == ("a.txt", "b.txt")
        assert p.has_inplace is False
        assert p.flags == ()

    def test_dash_e_expression(self):
        p = parse_sed_args(["-e", "s/foo/bar/", "a.txt"])
        assert p.parse_ok is True
        assert p.expressions == ("s/foo/bar/",)
        assert p.files == ("a.txt",)

    def test_long_expression_equals(self):
        p = parse_sed_args(["--expression=s/foo/bar/", "a.txt"])
        assert p.parse_ok is True
        assert p.expressions == ("s/foo/bar/",)

    def test_dash_e_equals_form(self):
        p = parse_sed_args(["-e=s/foo/bar/"])
        assert p.parse_ok is True
        assert p.expressions == ("s/foo/bar/",)

    def test_multiple_dash_e(self):
        p = parse_sed_args(["-e", "s/a/1/", "-e", "s/b/2/", "file.txt"])
        assert p.parse_ok is True
        assert p.expressions == ("s/a/1/", "s/b/2/")
        assert p.files == ("file.txt",)

    def test_long_in_place(self):
        p = parse_sed_args(["--in-place", "s/a/b/", "f"])
        assert p.parse_ok is True
        assert p.has_inplace is True

    def test_short_in_place(self):
        p = parse_sed_args(["-i", "s/a/b/", "f"])
        assert p.parse_ok is True
        assert p.has_inplace is True

    def test_gnu_in_place_with_backup_ext(self):
        p = parse_sed_args(["-i.bak", "s/a/b/", "f"])
        assert p.parse_ok is True
        assert p.has_inplace is True

    def test_combined_flags_split(self):
        p = parse_sed_args(["-nEi", "s/a/b/", "f"])
        assert p.parse_ok is True
        assert "-n" in p.flags
        assert "-E" in p.flags
        assert p.has_inplace is True

    def test_combined_ne_then_expr(self):
        # -ne means -n + -e, and the expression is the NEXT token.
        p = parse_sed_args(["-ne", "1p", "file.txt"])
        assert p.parse_ok is True
        assert "-n" in p.flags
        assert p.expressions == ("1p",)
        assert p.files == ("file.txt",)

    def test_dangling_dash_e(self):
        p = parse_sed_args(["-e"])
        assert p.parse_ok is False
        assert "dangling" in p.parse_error

    def test_dangling_long_expression(self):
        p = parse_sed_args(["--expression"])
        assert p.parse_ok is False
        assert "dangling" in p.parse_error

    @pytest.mark.parametrize(
        "combo",
        ["-ew", "-eW", "-we", "-wE", "-ee"],
    )
    def test_dangerous_flag_combo_rejected(self, combo):
        p = parse_sed_args([combo, "s/a/b/"])
        assert p.parse_ok is False
        assert "dangerous flag" in p.parse_error

    def test_Ee_is_not_a_dangerous_combo(self):
        # `-Ee` splits into `-E` (extended regex) + `-e` (expression follows).
        # Both are legitimate sed flags — not a dangerous combination.
        p = parse_sed_args(["-Ee", "s/a/b/"])
        assert p.parse_ok is True
        assert "-E" in p.flags
        assert p.expressions == ("s/a/b/",)

    def test_no_dash_e_positional_is_expression(self):
        p = parse_sed_args(["s/a/b/"])
        assert p.parse_ok is True
        assert p.expressions == ("s/a/b/",)
        assert p.files == ()


# ---------------------------------------------------------------------------
# Safe-print allowlist
# ---------------------------------------------------------------------------


class TestSafePrintAllowlist:
    @pytest.mark.parametrize(
        "expr",
        ["p", "1p", "123p", "1,5p", "10,200p", "1p;5p", "1p;5p;10p"],
    )
    def test_allowed_when_quiet_flag_present(self, expr):
        result = check_sed_constraints(
            tokens("-n", "-e", expr, "file.txt"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.PASSTHROUGH, (expr, result.reason)

    @pytest.mark.parametrize(
        "expr",
        ["p", "1p", "1,5p"],
    )
    def test_rejected_without_quiet_flag(self, expr):
        result = check_sed_constraints(
            tokens("-e", expr, "file.txt"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    def test_safe_print_files_allowed(self):
        result = check_sed_constraints(
            tokens("-n", "-e", "1,5p", "a.txt", "b.txt"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.PASSTHROUGH

    def test_rejects_mixed_with_substitution_in_print_shape(self):
        result = check_sed_constraints(
            tokens("-n", "-e", "s/foo/bar/"),
            allow_file_writes=False,
        )
        # Not a safe print (has substitution), but is a safe substitution.
        assert result.behavior is SedBehavior.PASSTHROUGH

    def test_safe_print_rejects_unknown_flag(self):
        # `-x` is not a recognized short flag; parser splits it into `-x`,
        # which then fails the SAFE_PRINT_FLAGS membership check.
        result = check_sed_constraints(
            tokens("-n", "-x", "-e", "1p"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    def test_safe_print_rejects_empty_segment_from_trailing_semicolon(self):
        result = check_sed_constraints(
            tokens("-n", "-e", "1p;"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK


# ---------------------------------------------------------------------------
# Safe-substitution allowlist
# ---------------------------------------------------------------------------


class TestSafeSubstitutionAllowlist:
    @pytest.mark.parametrize(
        "expr",
        [
            "s/foo/bar/",
            "s/foo/bar/g",
            "s/foo/bar/gi",
            "s/foo/bar/gI",
            "s/foo/bar/1",
            "s/foo/bar/g2",
        ],
    )
    def test_allowed_simple_substitution(self, expr):
        result = check_sed_constraints(
            tokens("-e", expr),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.PASSTHROUGH, (expr, result.reason)

    @pytest.mark.parametrize(
        "expr",
        [
            "s|foo|bar|",
            "s#foo#bar#",
            "s%foo%bar%",
            "s@foo@bar@",
        ],
    )
    def test_alternate_delimiter_rejected(self, expr):
        result = check_sed_constraints(
            tokens("-e", expr),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK, (expr, result.reason)

    def test_file_args_rejected_without_writes_permission(self):
        result = check_sed_constraints(
            tokens("-e", "s/foo/bar/", "file.txt"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    def test_in_place_rejected_without_writes_permission(self):
        result = check_sed_constraints(
            tokens("-i", "-e", "s/foo/bar/", "file.txt"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    def test_in_place_allowed_with_writes_permission(self):
        result = check_sed_constraints(
            tokens("-i", "-e", "s/foo/bar/", "file.txt"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.PASSTHROUGH

    def test_files_allowed_with_writes_permission_even_without_i(self):
        result = check_sed_constraints(
            tokens("-e", "s/foo/bar/", "file.txt"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.PASSTHROUGH

    def test_safe_sub_flag_set_complete(self):
        assert "g" in SAFE_SUB_FLAGS
        assert "p" in SAFE_SUB_FLAGS
        assert "i" in SAFE_SUB_FLAGS
        assert "m" in SAFE_SUB_FLAGS
        assert "I" in SAFE_SUB_FLAGS
        assert "M" in SAFE_SUB_FLAGS
        for d in "123456789":
            assert d in SAFE_SUB_FLAGS


# ---------------------------------------------------------------------------
# Denylist — one test per dangerous pattern
# ---------------------------------------------------------------------------


class TestDenylist:
    @pytest.mark.parametrize(
        "expr,label",
        [
            ("s/f\u00f3o/bar/", "non-ASCII"),
            ("s/foo/bar/{3}", "curly-braces"),
            ("1{p}", "curly-braces-address"),
            ("#comment", "top-comment"),
            ("!p", "leading-negation"),
            ("/foo/!d", "post-pattern-negation"),
            ("1~2p", "tilde-step"),
            ("$~2p", "dollar-tilde-step"),
            (",p", "bare-comma"),
            (",+5p", "gnu-offset"),
            ("s\\foo\\bar\\", "backslash-delimiter"),
            ("s/foo\\|bar/x/", "alternate-escape"),
            ("w outfile", "top-write-command"),
            ("1w outfile", "numeric-write-command"),
            ("$w outfile", "dollar-write-command"),
            ("/pat/w outfile", "pattern-write-command"),
            ("e echo hi", "top-execute-command"),
            ("1e echo hi", "numeric-execute-command"),
            ("$e echo hi", "dollar-execute-command"),
            ("/pat/e echo hi", "pattern-execute-command"),
        ],
    )
    def test_rejects_dangerous_pattern(self, expr, label):
        result = check_sed_constraints(
            tokens("-e", expr),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK, (label, expr, result.reason)
        assert result.reason.startswith("sed-denylist:") or result.reason.startswith("sed-parse:")

    def test_substitution_with_w_flag_blocked(self):
        # `s/foo/bar/w outfile` hits multiple denylist rules — the exact rule
        # that catches it is an implementation detail; the important thing is
        # that it is rejected.
        result = check_sed_constraints(
            tokens("-e", "s/foo/bar/w outfile"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK
        assert result.reason.startswith("sed-denylist:")

    def test_substitution_with_W_flag_blocked(self):
        result = check_sed_constraints(
            tokens("-e", "s/foo/bar/gW"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK

    def test_substitution_with_e_flag_blocked(self):
        result = check_sed_constraints(
            tokens("-e", "s/foo/echo pwned/e"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK

    def test_substitution_with_E_flag_blocked(self):
        result = check_sed_constraints(
            tokens("-e", "s/foo/bar/gE"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK

    def test_y_transliterate_with_w_blocked(self):
        result = check_sed_constraints(
            tokens("-e", "y/abc/xyz/;w outfile"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK

    def test_newline_in_expression_blocked(self):
        result = check_sed_constraints(
            tokens("-e", "s/foo\nbar/x/"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK


# ---------------------------------------------------------------------------
# Defense-in-depth — allowlist match AND denylist hit still rejected
# ---------------------------------------------------------------------------


class TestSrcParityTightenings:
    """Parity with src sedValidation.ts strictness — tighter than our earlier
    first-pass implementation.
    """

    # --- Gap #3: Pattern 2 rejects any semicolon ---

    def test_semicolon_literal_in_replacement_rejected(self):
        # Even a literal `;` in the replacement is unsafe — src refuses to
        # parse substitution expressions that contain any semicolon.
        result = check_sed_constraints(
            tokens("-e", "s/foo/bar;baz/g"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    def test_semicolon_chained_substitutions_rejected(self):
        result = check_sed_constraints(
            tokens("-e", "s/a/b/g;s/c/d/g"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    # --- Gap #5: broader y transliterate detection ---

    def test_y_transliterate_with_w_in_address_prefix_rejected(self):
        # src's y detection is positional-agnostic: any `y<char>` anywhere
        # combined with any w/W/e/E anywhere rejects the expression.
        result = check_sed_constraints(
            tokens("-e", "1,10 y/abc/def/ w outfile"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK

    def test_y_transliterate_alone_still_allowed_as_unknown(self):
        # A bare `y/abc/def/` with no w/W/e/E is not in the denylist but also
        # not in the allowlist — it falls through to ASK as "unknown pattern"
        # rather than as a denylist hit. Either way, the behavior is ASK.
        result = check_sed_constraints(
            tokens("-e", "y/abc/def/"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    # --- Gap #6: Pattern 2 requires exactly one expression ---

    def test_multiple_dash_e_substitutions_rejected(self):
        # Even if both expressions are individually safe, src allows only a
        # single expression for Pattern 2.
        result = check_sed_constraints(
            tokens("-e", "s/a/1/g", "-e", "s/b/2/g"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    def test_multiple_dash_e_with_writes_also_rejected(self):
        result = check_sed_constraints(
            tokens("-i", "-e", "s/a/1/g", "-e", "s/b/2/g", "file.txt"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK


class TestDefenseInDepth:
    def test_safe_sub_shape_plus_dangerous_flag(self):
        # Regex shape looks like s/a/b/flags, but `w outfile` has a dangerous
        # flag pattern that the denylist must catch first.
        result = check_sed_constraints(
            tokens("-e", "s/foo/bar/gw outfile"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK
        assert result.reason.startswith("sed-denylist:")

    def test_safe_sub_shape_plus_non_ascii(self):
        result = check_sed_constraints(
            tokens("-e", "s/foo/b\u00e1r/g"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.ASK
        assert result.reason.startswith("sed-denylist:")

    def test_safe_print_shape_plus_curly_brace(self):
        result = check_sed_constraints(
            tokens("-n", "-e", "1{p}"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK


# ---------------------------------------------------------------------------
# check_sed_constraints entry point semantics
# ---------------------------------------------------------------------------


class TestCheckSedConstraintsEntry:
    def test_empty_tokens_asks(self):
        result = check_sed_constraints([], allow_file_writes=False)
        assert result.behavior is SedBehavior.ASK
        assert result.reason.startswith("sed-parse:")

    def test_parse_error_becomes_ask(self):
        result = check_sed_constraints(tokens("-e"), allow_file_writes=False)
        assert result.behavior is SedBehavior.ASK
        assert result.reason.startswith("sed-parse:")

    def test_unknown_shape_asks(self):
        # `1,10d` deletes lines. Not in either allowlist, not in denylist.
        # Falls through to the unknown bucket.
        result = check_sed_constraints(
            tokens("-e", "1,10d"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK
        assert result.reason.startswith("sed-unknown:")

    def test_sed_only_with_nothing_is_ask(self):
        result = check_sed_constraints(tokens(), allow_file_writes=False)
        assert result.behavior is SedBehavior.ASK


# ---------------------------------------------------------------------------
# Combined flag split + mode gating
# ---------------------------------------------------------------------------


class TestCombinedFlagAndMode:
    def test_nEi_with_writes_allowed(self):
        # `-nEi 's/foo/bar/g' file.txt` splits into `-n`, `-E`, `-i`.
        # Safe-substitution allowlist doesn't care about extra read/quiet flags
        # — they are benign add-ons — so this is a legitimate in-place
        # substitution when writes are allowed.
        result = check_sed_constraints(
            tokens("-nEi", "s/foo/bar/g", "file.txt"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.PASSTHROUGH

    def test_nEi_without_writes_blocked(self):
        result = check_sed_constraints(
            tokens("-nEi", "s/foo/bar/g", "file.txt"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    def test_i_with_safe_sub_and_writes_allowed(self):
        result = check_sed_constraints(
            tokens("-i", "s/foo/bar/g", "file.txt"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.PASSTHROUGH

    def test_i_with_safe_sub_blocked_without_writes(self):
        result = check_sed_constraints(
            tokens("-i", "s/foo/bar/g", "file.txt"),
            allow_file_writes=False,
        )
        assert result.behavior is SedBehavior.ASK

    def test_in_place_long_form_with_writes(self):
        result = check_sed_constraints(
            tokens("--in-place", "s/foo/bar/g", "file.txt"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.PASSTHROUGH

    def test_gnu_backup_i_with_writes(self):
        result = check_sed_constraints(
            tokens("-i.bak", "s/foo/bar/g", "file.txt"),
            allow_file_writes=True,
        )
        assert result.behavior is SedBehavior.PASSTHROUGH


# ---------------------------------------------------------------------------
# Safe flag set / constants sanity
# ---------------------------------------------------------------------------


class TestConstants:
    def test_safe_print_flags_include_expected(self):
        for f in [
            "-n",
            "-E",
            "-r",
            "-z",
            "--quiet",
            "--silent",
            "--regexp-extended",
            "--zero-terminated",
            "--posix",
        ]:
            assert f in SAFE_PRINT_FLAGS

    def test_safe_sub_flags_exact_membership(self):
        expected = set("gpimIM") | {"1", "2", "3", "4", "5", "6", "7", "8", "9"}
        assert set(SAFE_SUB_FLAGS) == expected
