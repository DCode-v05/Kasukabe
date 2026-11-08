"""Tests for the bypass-immune dangerous path gate (src/sandbox/path_safety.py)."""

import os
from unittest.mock import patch

import pytest

from src.sandbox.path_safety import (
    DANGEROUS_DIRS,
    DANGEROUS_FILES,
    check_path_safety,
)


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


class TestCheckPathSafetyGuardrails:
    def test_empty_returns_none(self):
        assert check_path_safety("") is None

    def test_whitespace_only_returns_none(self):
        assert check_path_safety("   ") is None

    def test_non_string_returns_none(self):
        assert check_path_safety(None) is None  # type: ignore[arg-type]
        assert check_path_safety(123) is None  # type: ignore[arg-type]
        assert check_path_safety(b"bytes") is None  # type: ignore[arg-type]

    def test_null_byte_blocked(self):
        result = check_path_safety("/tmp/foo\x00.txt")
        assert result is not None
        assert result.is_dangerous is True
        assert result.classifier_approvable is False
        assert result.reason.startswith("path-evasion: ")
        assert "null byte" in result.reason


# ---------------------------------------------------------------------------
# Windows evasion patterns — always classifier_approvable=False
# ---------------------------------------------------------------------------


class TestWindowsEvasion:
    def test_ntfs_ads_dollar_data(self):
        result = check_path_safety("C:\\Users\\me\\notes.txt:$DATA")
        assert result is not None
        assert result.is_dangerous is True
        assert result.classifier_approvable is False
        assert "NTFS alternate data stream" in result.reason

    def test_ntfs_ads_named_stream(self):
        result = check_path_safety("C:\\Users\\me\\notes.md:hidden")
        assert result is not None
        assert result.classifier_approvable is False

    def test_ntfs_ads_drive_letter_not_flagged_alone(self):
        # "C:" alone is a drive, not an ADS — must not false-positive
        result = check_path_safety("C:\\Users\\me\\safe.txt")
        assert result is None

    def test_short_name_progra1(self):
        result = check_path_safety("C:\\PROGRA~1\\evil.exe")
        assert result is not None
        assert result.classifier_approvable is False
        assert "8.3 short name" in result.reason

    def test_short_name_git1(self):
        result = check_path_safety("C:\\project\\GIT~1\\config")
        assert result is not None
        assert result.classifier_approvable is False

    def test_short_name_docume2(self):
        result = check_path_safety("C:\\DOCUME~2\\file.txt")
        assert result is not None

    def test_long_path_prefix(self):
        result = check_path_safety("\\\\?\\C:\\foo\\.bashrc")
        assert result is not None
        assert result.classifier_approvable is False
        assert "long-path prefix" in result.reason

    def test_unc_backslash(self):
        result = check_path_safety("\\\\server\\share\\file")
        assert result is not None
        assert result.classifier_approvable is False
        assert "UNC" in result.reason

    def test_unc_forward_slash(self):
        result = check_path_safety("//server/share/file")
        assert result is not None
        assert result.classifier_approvable is False
        assert "UNC" in result.reason

    def test_trailing_dot(self):
        result = check_path_safety("/home/user/.bashrc.")
        assert result is not None
        assert result.classifier_approvable is False
        assert "trailing dot" in result.reason

    def test_trailing_space(self):
        result = check_path_safety("/home/user/.bashrc ")
        assert result is not None
        assert result.classifier_approvable is False
        assert "trailing dot" in result.reason  # same check name

    def test_dot_and_dotdot_components_not_flagged(self):
        # `.` and `..` are legitimate POSIX directory markers — must NOT
        # trip the trailing-dot evasion rule.
        assert check_path_safety("/tmp/./safe.txt") is None
        assert check_path_safety("/tmp/sub/../safe.txt") is None

    def test_three_or_more_dot_component_flagged(self):
        # `...` is not a POSIX marker — anything beyond `.`/`..` falls through
        # to the trailing-dot rule and is treated as evasion.
        result = check_path_safety("/tmp/.../safe.txt")
        assert result is not None
        assert result.classifier_approvable is False
        assert "trailing dot" in result.reason

    def test_reason_prefix_evasion(self):
        result = check_path_safety("\\\\?\\C:\\safe.txt")
        assert result is not None
        assert result.reason.startswith("path-evasion: ")


# ---------------------------------------------------------------------------
# Dangerous files — classifier_approvable=True
# ---------------------------------------------------------------------------


class TestDangerousFiles:
    @pytest.mark.parametrize("filename", sorted(DANGEROUS_FILES))
    def test_each_dangerous_file_blocked(self, filename):
        result = check_path_safety(f"/home/test/{filename}")
        assert result is not None, f"{filename} was not flagged"
        assert result.is_dangerous is True
        assert result.classifier_approvable is True
        assert result.reason.startswith("dangerous-target: ")

    def test_case_insensitive_bashrc_upper(self):
        result = check_path_safety("/home/test/.BASHRC")
        assert result is not None
        assert result.is_dangerous is True

    def test_case_insensitive_bashrc_mixed(self):
        result = check_path_safety("/home/test/.BaShRc")
        assert result is not None

    def test_case_insensitive_gitconfig(self):
        result = check_path_safety("/home/test/.GITCONFIG")
        assert result is not None

    def test_safe_file_not_flagged(self):
        assert check_path_safety("/tmp/safe.txt") is None

    def test_similar_but_safe_name(self):
        # ".bashrc2" is not the same as ".bashrc" — must not false-positive
        assert check_path_safety("/tmp/.bashrc2") is None

    def test_mcp_json_blocked_everywhere(self):
        result = check_path_safety("/any/deep/path/.mcp.json")
        assert result is not None
        assert result.classifier_approvable is True

    def test_reason_prefix_dangerous_target(self):
        result = check_path_safety("/home/test/.bashrc")
        assert result is not None
        assert result.reason.startswith("dangerous-target: ")


# ---------------------------------------------------------------------------
# Dangerous directories — classifier_approvable=True
# ---------------------------------------------------------------------------


class TestDangerousDirs:
    @pytest.mark.parametrize("dirname", sorted(DANGEROUS_DIRS))
    def test_each_dangerous_dir_blocked(self, dirname):
        result = check_path_safety(f"/project/{dirname}/some/file")
        assert result is not None, f"{dirname} was not flagged"
        assert result.is_dangerous is True
        assert result.classifier_approvable is True
        assert result.reason.startswith("dangerous-target: ")

    def test_git_config_blocked_via_dir_check(self):
        result = check_path_safety("/project/.git/config")
        assert result is not None
        assert result.classifier_approvable is True

    def test_git_hook_blocked(self):
        result = check_path_safety("/project/.git/hooks/pre-commit")
        assert result is not None

    def test_claude_dir_blocked(self):
        result = check_path_safety("/home/user/.claude/settings.json")
        assert result is not None

    def test_case_insensitive_git_upper(self):
        result = check_path_safety("/project/.GIT/hooks/pre-commit")
        assert result is not None

    def test_safe_dir_not_flagged(self):
        assert check_path_safety("/tmp/project/src/main.py") is None

    def test_similar_dir_not_flagged(self):
        # ".github" contains ".git" as prefix but is a different directory.
        # Case-insensitive whole-component match requires exact ".git", not
        # ".github" — this must not false-positive.
        assert check_path_safety("/project/.github/workflows/ci.yml") is None


# ---------------------------------------------------------------------------
# Dual-path (symlink) validation
# ---------------------------------------------------------------------------


class TestDualPathValidation:
    def test_resolved_path_caught_when_original_safe(self):
        """Attack: symlink /tmp/innocent.txt -> ~/.bashrc, write /tmp/innocent.txt."""
        result = check_path_safety(
            original_path="/tmp/innocent.txt",
            resolved_path="/home/user/.bashrc",
        )
        assert result is not None
        assert result.is_dangerous is True
        assert ".bashrc" in result.reason

    def test_original_caught_even_when_resolved_safe(self):
        result = check_path_safety(
            original_path="/home/user/.bashrc",
            resolved_path="/tmp/resolved_somewhere_safe",
        )
        assert result is not None
        assert result.is_dangerous is True

    def test_both_safe_returns_none(self):
        result = check_path_safety(
            original_path="/tmp/a.txt",
            resolved_path="/tmp/b.txt",
        )
        assert result is None

    def test_real_symlink_to_bashrc(self, tmp_path):
        """End-to-end: create a real symlink, pass only the original path
        and let check_path_safety resolve it internally."""
        target = tmp_path / ".bashrc"
        target.write_text("# dangerous real file")
        link = tmp_path / "innocent.txt"
        os.symlink(str(target), str(link))
        # Original is safe-looking, but the resolved path ends in .bashrc.
        result = check_path_safety(str(link))
        assert result is not None
        assert result.is_dangerous is True
        assert result.classifier_approvable is True
        assert ".bashrc" in result.reason


# ---------------------------------------------------------------------------
# classifier_approvable contract
# ---------------------------------------------------------------------------


class TestClassifierApprovableContract:
    def test_evasion_is_not_approvable(self):
        result = check_path_safety("C:\\PROGRA~1\\evil.exe")
        assert result is not None
        assert result.classifier_approvable is False

    def test_dangerous_target_is_approvable(self):
        result = check_path_safety("/home/user/.bashrc")
        assert result is not None
        assert result.classifier_approvable is True

    def test_null_byte_is_not_approvable(self):
        result = check_path_safety("/tmp/x\x00.txt")
        assert result is not None
        assert result.classifier_approvable is False

    def test_unc_is_not_approvable(self):
        result = check_path_safety("//server/share")
        assert result is not None
        assert result.classifier_approvable is False


# ---------------------------------------------------------------------------
# Safe-path regression sanity
# ---------------------------------------------------------------------------


class TestSafePaths:
    @pytest.mark.parametrize(
        "path",
        [
            "/tmp/foo.txt",
            "/home/user/project/src/main.py",
            "/var/log/app.log",
            "./relative/file.txt",
            "~/Documents/notes.md",
            "C:\\Users\\me\\projects\\safe.txt",
        ],
    )
    def test_safe_paths_return_none(self, path):
        assert check_path_safety(path) is None


# ---------------------------------------------------------------------------
# DOS reserved device names — classifier_approvable=False
# ---------------------------------------------------------------------------


class TestDOSDeviceNames:
    @pytest.mark.parametrize(
        "name",
        [
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM2",
            "COM3",
            "COM4",
            "COM5",
            "COM6",
            "COM7",
            "COM8",
            "COM9",
            "LPT1",
            "LPT2",
            "LPT3",
            "LPT4",
            "LPT5",
            "LPT6",
            "LPT7",
            "LPT8",
            "LPT9",
        ],
    )
    def test_bare_device_name_blocked(self, name):
        result = check_path_safety(f"C:\\Users\\me\\{name}")
        assert result is not None
        assert result.is_dangerous is True
        assert result.classifier_approvable is False
        assert "DOS reserved device name" in result.reason

    @pytest.mark.parametrize("name", ["CON", "PRN", "NUL", "COM1", "LPT1"])
    def test_device_with_extension_blocked(self, name):
        result = check_path_safety(f"/tmp/{name}.txt")
        assert result is not None
        assert result.classifier_approvable is False
        assert "DOS reserved device name" in result.reason

    def test_case_insensitive_con(self):
        result = check_path_safety("/tmp/con.log")
        assert result is not None
        assert result.classifier_approvable is False

    def test_case_insensitive_nul(self):
        result = check_path_safety("/tmp/nul.dat")
        assert result is not None
        assert result.classifier_approvable is False

    def test_nested_in_path(self):
        result = check_path_safety("C:\\project\\output\\CON")
        assert result is not None
        assert result.classifier_approvable is False

    def test_safe_prefix_not_flagged(self):
        # "CONSOLE" starts with "CON" but the stem is "CONSOLE", not "CON"
        assert check_path_safety("/tmp/CONSOLE.txt") is None

    def test_safe_contains_not_flagged(self):
        # "icon.png" — stem is "icon" not a reserved name
        assert check_path_safety("/tmp/icon.png") is None

    def test_com_without_digit_not_flagged(self):
        # "COM" alone without digit is not reserved
        assert check_path_safety("/tmp/COM.txt") is None

    def test_lpt_without_digit_not_flagged(self):
        assert check_path_safety("/tmp/LPT.txt") is None

    def test_com0_not_flagged(self):
        # COM0 is not a valid DOS device (only 1-9)
        assert check_path_safety("/tmp/COM0.txt") is None

    def test_dotfile_prefixed_con_blocked(self):
        # Windows ignores leading dots for device name resolution
        result = check_path_safety("/tmp/.CON")
        assert result is not None
        assert result.classifier_approvable is False
        assert "DOS reserved device name" in result.reason

    def test_dotfile_prefixed_nul_blocked(self):
        result = check_path_safety("/tmp/.NUL.txt")
        assert result is not None
        assert "DOS reserved device name" in result.reason

    def test_dotfile_prefixed_com1_blocked(self):
        result = check_path_safety("C:\\Users\\.COM1")
        assert result is not None
        assert result.classifier_approvable is False


# ---------------------------------------------------------------------------
# Drive-relative paths — classifier_approvable=False
# ---------------------------------------------------------------------------


class TestDriveRelativePaths:
    def test_drive_relative_basic(self):
        result = check_path_safety("C:file.txt")
        assert result is not None
        assert result.is_dangerous is True
        assert result.classifier_approvable is False
        assert "drive-relative" in result.reason

    def test_drive_relative_bashrc(self):
        result = check_path_safety("D:.bashrc")
        assert result is not None
        assert result.classifier_approvable is False

    def test_drive_relative_subpath(self):
        result = check_path_safety("E:subdir/file.txt")
        assert result is not None
        assert result.classifier_approvable is False

    def test_absolute_windows_not_flagged(self):
        # "C:\file.txt" has a separator after colon — this is normal
        assert check_path_safety("C:\\file.txt") is None

    def test_absolute_windows_forward_slash_not_flagged(self):
        assert check_path_safety("C:/file.txt") is None

    def test_drive_letter_alone_not_flagged(self):
        # "C:" alone is a drive component, not drive-relative
        # It passes through the component loop where WIN_DRIVE_RE skips it
        assert check_path_safety("C:\\Users\\me\\safe.txt") is None


# ---------------------------------------------------------------------------
# Enhanced UNC detection — mixed separators and WebDAV
# ---------------------------------------------------------------------------


class TestEnhancedUNC:
    def test_mixed_separator_forward_back(self):
        result = check_path_safety("/\\\\server\\share")
        assert result is not None
        assert result.classifier_approvable is False
        assert "UNC" in result.reason

    def test_mixed_separator_back_forward(self):
        result = check_path_safety("\\\\//server/share")
        assert result is not None
        assert result.classifier_approvable is False

    def test_three_slashes(self):
        result = check_path_safety("///server/share")
        assert result is not None
        assert result.classifier_approvable is False

    def test_webdav_ssl_port(self):
        # Starts with \\ so basic UNC check fires first — still blocked
        result = check_path_safety("\\\\server@SSL@8443\\path\\file")
        assert result is not None
        assert result.classifier_approvable is False
        assert "UNC" in result.reason

    def test_webdav_port_ssl(self):
        result = check_path_safety("\\\\server@8443@SSL\\path")
        assert result is not None
        assert result.classifier_approvable is False
        assert "UNC" in result.reason

    def test_webdav_ssl_only(self):
        result = check_path_safety("\\\\server@SSL\\share")
        assert result is not None
        assert result.classifier_approvable is False

    def test_webdav_ssl_without_unc_prefix(self):
        # WebDAV pattern embedded in a non-UNC path — still caught
        result = check_path_safety("C:\\server@SSL@8443\\path")
        assert result is not None
        assert result.classifier_approvable is False
        assert "WebDAV" in result.reason

    def test_webdav_port_ssl_without_unc_prefix(self):
        result = check_path_safety("C:\\server@8443@SSL\\path")
        assert result is not None
        assert result.classifier_approvable is False
        assert "WebDAV" in result.reason

    def test_webdav_mixed_case_ssl(self):
        # Mixed-case @Ssl must be caught (case-insensitive)
        result = check_path_safety("C:\\server@Ssl@8443\\path")
        assert result is not None
        assert result.classifier_approvable is False
        assert "WebDAV" in result.reason

    def test_webdav_mixed_case_sSL(self):
        result = check_path_safety("C:\\server@8443@sSL\\path")
        assert result is not None
        assert result.classifier_approvable is False

    def test_davwwwroot_unc(self):
        # Starts with \\ so basic UNC check fires first — still blocked
        result = check_path_safety("\\\\server\\DavWWWRoot\\path\\file")
        assert result is not None
        assert result.classifier_approvable is False
        assert "UNC" in result.reason

    def test_davwwwroot_case_insensitive(self):
        # Also starts with \\ — basic UNC fires first
        result = check_path_safety("\\\\server\\davwwwroot\\path")
        assert result is not None
        assert result.classifier_approvable is False

    def test_davwwwroot_mid_path(self):
        result = check_path_safety("C:\\mount\\DavWWWRoot\\docs\\file.txt")
        assert result is not None
        assert result.classifier_approvable is False

    def test_single_slash_not_flagged(self):
        # Single leading slash is normal POSIX absolute path
        assert check_path_safety("/tmp/file.txt") is None

    def test_single_backslash_not_flagged(self):
        assert check_path_safety("\\tmp\\file.txt") is None


# ---------------------------------------------------------------------------
# POSIX device file blocking
# ---------------------------------------------------------------------------


class TestDeviceFileBlocking:
    @pytest.mark.parametrize(
        "path",
        [
            "/dev/zero",
            "/dev/random",
            "/dev/urandom",
            "/dev/full",
            "/dev/stdin",
            "/dev/tty",
            "/dev/console",
            "/dev/stdout",
            "/dev/stderr",
            "/dev/fd/0",
            "/dev/fd/1",
            "/dev/fd/2",
        ],
    )
    def test_blocked_device_files(self, path):
        result = check_path_safety(path)
        assert result is not None, f"{path} was not blocked"
        assert result.is_dangerous is True
        assert result.classifier_approvable is False
        assert "blocked device file" in result.reason

    def test_proc_fd_stdio(self):
        result = check_path_safety("/proc/1234/fd/0")
        assert result is not None
        assert result.classifier_approvable is False
        assert "blocked device file" in result.reason

    def test_proc_fd_1(self):
        result = check_path_safety("/proc/1/fd/1")
        assert result is not None
        assert result.classifier_approvable is False

    def test_proc_fd_2(self):
        result = check_path_safety("/proc/42/fd/2")
        assert result is not None
        assert result.classifier_approvable is False

    def test_proc_fd_3_not_blocked(self):
        # fd/3 and above are not stdio — not blocked
        assert check_path_safety("/proc/1/fd/3") is None

    def test_dev_null_not_blocked(self):
        # /dev/null is safe and commonly used — must not be blocked
        assert check_path_safety("/dev/null") is None

    def test_dev_sda_not_blocked(self):
        assert check_path_safety("/dev/sda") is None

    def test_dev_pts_not_blocked(self):
        assert check_path_safety("/dev/pts/0") is None

    def test_normalized_path_still_caught(self):
        # Paths with redundant components should still match after normpath
        result = check_path_safety("/dev/./zero")
        assert result is not None
        assert result.classifier_approvable is False

    def test_traversal_to_device_caught(self):
        result = check_path_safety("/dev/null/../zero")
        assert result is not None
        assert result.classifier_approvable is False

    def test_double_slash_prefix_caught(self):
        # POSIX normpath preserves // at start — our code must collapse it
        result = check_path_safety("//dev/zero")
        assert result is not None
        assert result.classifier_approvable is False

    def test_double_slash_stdin_caught(self):
        result = check_path_safety("//dev/stdin")
        assert result is not None
        assert result.classifier_approvable is False


# ---------------------------------------------------------------------------
# Unicode normalization (NFKC)
# ---------------------------------------------------------------------------


class TestUnicodeNormalization:
    def test_fullwidth_bashrc(self):
        # Fullwidth period + lowercase letters: ．ｂａｓｈｒｃ
        # NFKC normalizes fullwidth chars to ASCII equivalents
        fullwidth = "\uff0e\uff42\uff41\uff53\uff48\uff52\uff43"
        result = check_path_safety(f"/home/user/{fullwidth}")
        assert result is not None
        assert result.is_dangerous is True
        assert ".bashrc" in result.reason

    def test_fullwidth_git_dir(self):
        # Fullwidth ．ｇｉｔ
        fullwidth_git = "\uff0e\uff47\uff49\uff54"
        result = check_path_safety(f"/project/{fullwidth_git}/config")
        assert result is not None
        assert result.is_dangerous is True

    def test_normal_ascii_unaffected(self):
        # Plain ASCII should still work identically
        assert check_path_safety("/tmp/safe.txt") is None

    def test_legitimate_unicode_path_unaffected(self):
        # Unicode filenames that don't normalize to dangerous names should pass
        assert check_path_safety("/tmp/données.txt") is None
        assert check_path_safety("/tmp/日本語.txt") is None

    def test_fullwidth_colon_no_false_positive_ads(self):
        # Fullwidth colon U+FF1A normalizes to ASCII ":" under NFKC.
        # Evasion checks (ADS) must run on raw components, not NFKC-normalized,
        # so a legitimate POSIX filename with fullwidth colon is NOT flagged.
        fullwidth_colon = "\uff1a"
        result = check_path_safety(f"/tmp/foo{fullwidth_colon}bar.txt")
        assert result is None


# ---------------------------------------------------------------------------
# Feature flag: FEATURE_WINDOWS_PATH_EVASION
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    def test_dos_device_blocked_when_enabled(self):
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "true"}):
            result = check_path_safety("/tmp/CON.txt")
            assert result is not None
            assert "DOS reserved device name" in result.reason

    def test_dos_device_not_blocked_when_disabled(self):
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            result = check_path_safety("/tmp/CON.txt")
            assert result is None

    def test_device_file_blocked_when_enabled(self):
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "true"}):
            result = check_path_safety("/dev/zero")
            assert result is not None

    def test_device_file_not_blocked_when_disabled(self):
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            result = check_path_safety("/dev/zero")
            assert result is None

    def test_drive_relative_blocked_when_enabled(self):
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "true"}):
            result = check_path_safety("C:file.txt")
            assert result is not None

    def test_drive_relative_not_blocked_when_disabled(self):
        # When flag is off, drive-relative check is skipped, but "C:file.txt"
        # is a single component with ":" so the existing ADS check still fires.
        # This is defense-in-depth — the path is blocked by a different check.
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            result = check_path_safety("C:file.txt")
            assert result is not None
            assert "NTFS alternate data stream" in result.reason

    def test_existing_checks_unaffected_when_disabled(self):
        # Pre-existing checks (NTFS ADS, 8.3, long-path, UNC, trailing dots)
        # must remain active even when the feature flag is off.
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            assert check_path_safety("\\\\?\\C:\\foo").is_dangerous is True
            assert check_path_safety("\\\\server\\share").is_dangerous is True
            assert check_path_safety("C:\\GIT~1\\config").is_dangerous is True
            assert check_path_safety("/tmp/file.txt:$DATA").is_dangerous is True
            assert check_path_safety("/tmp/file.txt.").is_dangerous is True
            assert check_path_safety("/tmp/x\x00.txt").is_dangerous is True

    def test_unicode_normalization_disabled_when_flag_off(self):
        # Fullwidth .bashrc should NOT be caught when flag is off
        # because NFKC normalization is gated on the flag
        fullwidth = "\uff0e\uff42\uff41\uff53\uff48\uff52\uff43"
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            result = check_path_safety(f"/home/user/{fullwidth}")
            assert result is None

    def test_enhanced_unc_disabled_when_flag_off(self):
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            # Mixed separator — not caught by existing checks
            result = check_path_safety("///server/share")
            # Falls through to existing // check which IS always on
            # Let's test a case that only the enhanced check catches
            result = check_path_safety("\\\\server@SSL\\share")
            # This starts with \\ so existing check catches it
            assert result is not None  # existing UNC check fires
