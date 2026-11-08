"""Tests for the bypass-immune dangerous path gate (src/sandbox/path_safety.py)."""

import os
from unittest.mock import patch

import pytest

from src.sandbox.path_safety import (
    DANGEROUS_DIRS,
    DANGEROUS_FILES,
    check_path_safety,
)


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
        assert "trailing dot" in result.reason

    def test_dot_and_dotdot_components_not_flagged(self):
        assert check_path_safety("/tmp/./safe.txt") is None
        assert check_path_safety("/tmp/sub/../safe.txt") is None

    def test_three_or_more_dot_component_flagged(self):
        result = check_path_safety("/tmp/.../safe.txt")
        assert result is not None
        assert result.classifier_approvable is False
        assert "trailing dot" in result.reason

    def test_reason_prefix_evasion(self):
        result = check_path_safety("\\\\?\\C:\\safe.txt")
        assert result is not None
        assert result.reason.startswith("path-evasion: ")


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
        assert check_path_safety("/tmp/.bashrc2") is None

    def test_mcp_json_blocked_everywhere(self):
        result = check_path_safety("/any/deep/path/.mcp.json")
        assert result is not None
        assert result.classifier_approvable is True

    def test_reason_prefix_dangerous_target(self):
        result = check_path_safety("/home/test/.bashrc")
        assert result is not None
        assert result.reason.startswith("dangerous-target: ")


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
        assert check_path_safety("/project/.github/workflows/ci.yml") is None


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
        result = check_path_safety(str(link))
        assert result is not None
        assert result.is_dangerous is True
        assert result.classifier_approvable is True
        assert ".bashrc" in result.reason


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
        assert check_path_safety("/tmp/CONSOLE.txt") is None

    def test_safe_contains_not_flagged(self):
        assert check_path_safety("/tmp/icon.png") is None

    def test_com_without_digit_not_flagged(self):
        assert check_path_safety("/tmp/COM.txt") is None

    def test_lpt_without_digit_not_flagged(self):
        assert check_path_safety("/tmp/LPT.txt") is None

    def test_com0_not_flagged(self):
        assert check_path_safety("/tmp/COM0.txt") is None

    def test_dotfile_prefixed_con_blocked(self):
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
        assert check_path_safety("C:\\file.txt") is None

    def test_absolute_windows_forward_slash_not_flagged(self):
        assert check_path_safety("C:/file.txt") is None

    def test_drive_letter_alone_not_flagged(self):
        assert check_path_safety("C:\\Users\\me\\safe.txt") is None


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
        result = check_path_safety("C:\\server@Ssl@8443\\path")
        assert result is not None
        assert result.classifier_approvable is False
        assert "WebDAV" in result.reason

    def test_webdav_mixed_case_sSL(self):
        result = check_path_safety("C:\\server@8443@sSL\\path")
        assert result is not None
        assert result.classifier_approvable is False

    def test_davwwwroot_unc(self):
        result = check_path_safety("\\\\server\\DavWWWRoot\\path\\file")
        assert result is not None
        assert result.classifier_approvable is False
        assert "UNC" in result.reason

    def test_davwwwroot_case_insensitive(self):
        result = check_path_safety("\\\\server\\davwwwroot\\path")
        assert result is not None
        assert result.classifier_approvable is False

    def test_davwwwroot_mid_path(self):
        result = check_path_safety("C:\\mount\\DavWWWRoot\\docs\\file.txt")
        assert result is not None
        assert result.classifier_approvable is False

    def test_single_slash_not_flagged(self):
        assert check_path_safety("/tmp/file.txt") is None

    def test_single_backslash_not_flagged(self):
        assert check_path_safety("\\tmp\\file.txt") is None


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
        assert check_path_safety("/proc/1/fd/3") is None

    def test_dev_null_not_blocked(self):
        assert check_path_safety("/dev/null") is None

    def test_dev_sda_not_blocked(self):
        assert check_path_safety("/dev/sda") is None

    def test_dev_pts_not_blocked(self):
        assert check_path_safety("/dev/pts/0") is None

    def test_normalized_path_still_caught(self):
        result = check_path_safety("/dev/./zero")
        assert result is not None
        assert result.classifier_approvable is False

    def test_traversal_to_device_caught(self):
        result = check_path_safety("/dev/null/../zero")
        assert result is not None
        assert result.classifier_approvable is False

    def test_double_slash_prefix_caught(self):
        result = check_path_safety("//dev/zero")
        assert result is not None
        assert result.classifier_approvable is False

    def test_double_slash_stdin_caught(self):
        result = check_path_safety("//dev/stdin")
        assert result is not None
        assert result.classifier_approvable is False


class TestUnicodeNormalization:
    def test_fullwidth_bashrc(self):
        fullwidth = "\uff0e\uff42\uff41\uff53\uff48\uff52\uff43"
        result = check_path_safety(f"/home/user/{fullwidth}")
        assert result is not None
        assert result.is_dangerous is True
        assert ".bashrc" in result.reason

    def test_fullwidth_git_dir(self):
        fullwidth_git = "\uff0e\uff47\uff49\uff54"
        result = check_path_safety(f"/project/{fullwidth_git}/config")
        assert result is not None
        assert result.is_dangerous is True

    def test_normal_ascii_unaffected(self):
        assert check_path_safety("/tmp/safe.txt") is None

    def test_legitimate_unicode_path_unaffected(self):
        assert check_path_safety("/tmp/données.txt") is None
        assert check_path_safety("/tmp/日本語.txt") is None

    def test_fullwidth_colon_no_false_positive_ads(self):
        fullwidth_colon = "\uff1a"
        result = check_path_safety(f"/tmp/foo{fullwidth_colon}bar.txt")
        assert result is None


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
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            result = check_path_safety("C:file.txt")
            assert result is not None
            assert "NTFS alternate data stream" in result.reason

    def test_existing_checks_unaffected_when_disabled(self):
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            assert check_path_safety("\\\\?\\C:\\foo").is_dangerous is True
            assert check_path_safety("\\\\server\\share").is_dangerous is True
            assert check_path_safety("C:\\GIT~1\\config").is_dangerous is True
            assert check_path_safety("/tmp/file.txt:$DATA").is_dangerous is True
            assert check_path_safety("/tmp/file.txt.").is_dangerous is True
            assert check_path_safety("/tmp/x\x00.txt").is_dangerous is True

    def test_unicode_normalization_disabled_when_flag_off(self):
        fullwidth = "\uff0e\uff42\uff41\uff53\uff48\uff52\uff43"
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            result = check_path_safety(f"/home/user/{fullwidth}")
            assert result is None

    def test_enhanced_unc_disabled_when_flag_off(self):
        with patch.dict(os.environ, {"FEATURE_WINDOWS_PATH_EVASION": "false"}):
            result = check_path_safety("///server/share")
            result = check_path_safety("\\\\server@SSL\\share")
            assert result is not None
