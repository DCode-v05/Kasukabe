"""Tests for the dangerous removal path detector (src/sandbox/dangerous_removal.py).

Unit tests for the three public surfaces:

* ``is_dangerous_removal_path`` — pure check on a resolved path
* ``is_dangerous_removal_raw_path`` — pure check on a user-typed path
* ``check_dangerous_removal`` — integration helper that combines the
  two with the argv extractor

Integration tests against ``check_permission()`` live in
``test_permissions_dangerous_removal.py``.
"""

from __future__ import annotations

import os

import pytest

from src.sandbox.dangerous_removal import (
    check_dangerous_removal,
    is_dangerous_removal_path,
    is_dangerous_removal_raw_path,
)


# ---------------------------------------------------------------------------
# is_dangerous_removal_path — the seven shapes
# ---------------------------------------------------------------------------


class TestIsDangerousRemovalPath:
    # --- Shape 1: wildcard alone -------------------------------------------
    def test_wildcard_alone_blocked(self):
        assert is_dangerous_removal_path("*") is True

    # --- Shape 2: path ending in /* ----------------------------------------
    def test_wildcard_suffix_blocked(self):
        assert is_dangerous_removal_path("/home/*") is True

    def test_wildcard_suffix_under_tmp_blocked(self):
        assert is_dangerous_removal_path("/tmp/*") is True

    def test_wildcard_suffix_backslash_blocked(self):
        # Backslash form collapses to forward slash before the check.
        assert is_dangerous_removal_path(r"C:\Users\*") is True

    # --- Shape 3: POSIX root -----------------------------------------------
    def test_root_blocked(self):
        assert is_dangerous_removal_path("/") is True

    def test_root_double_slash_blocked(self):
        # // collapses to / via normalize_slashes.
        assert is_dangerous_removal_path("//") is True

    def test_root_trailing_slash_is_still_root(self):
        # "/" survives rstrip because the == "/" branch runs first.
        assert is_dangerous_removal_path("/") is True

    # --- Shape 4: Windows drive root ---------------------------------------
    def test_windows_drive_root_bare_blocked(self):
        assert is_dangerous_removal_path("C:") is True

    def test_windows_drive_root_with_slash_blocked(self):
        assert is_dangerous_removal_path("C:/") is True

    def test_windows_drive_root_backslash_blocked(self):
        assert is_dangerous_removal_path("C:\\") is True

    def test_windows_drive_root_lowercase_blocked(self):
        assert is_dangerous_removal_path("d:/") is True

    # --- Shape 5: home directory -------------------------------------------
    def test_expanded_home_blocked(self):
        home = os.path.expanduser("~")
        assert is_dangerous_removal_path(home) is True

    def test_expanded_home_with_trailing_slash_blocked(self):
        home = os.path.expanduser("~")
        assert is_dangerous_removal_path(home + "/") is True

    # --- Shape 6: direct child of root -------------------------------------
    @pytest.mark.parametrize(
        "path",
        [
            "/etc",
            "/usr",
            "/opt",
            "/home",
            "/root",
            "/var",
            "/boot",
            "/lib",
            "/lib64",
            "/srv",
            "/mnt",
            "/media",
            "/dev",
            "/proc",
            "/sys",
            "/tmp",
            "/bin",
            "/sbin",
        ],
    )
    def test_direct_root_child_blocked(self, path: str):
        assert is_dangerous_removal_path(path) is True

    def test_direct_root_child_trailing_slash_blocked(self):
        # rstrip brings /etc/ back to /etc before the dirname check fires.
        assert is_dangerous_removal_path("/etc/") is True

    # --- Shape 7: Windows drive child --------------------------------------
    def test_windows_drive_child_windows_blocked(self):
        assert is_dangerous_removal_path("C:/Windows") is True

    def test_windows_drive_child_backslash_blocked(self):
        assert is_dangerous_removal_path("C:\\Users") is True

    def test_windows_drive_child_program_files_blocked(self):
        # `D:/Program Files` is a single component under `D:/` — the
        # regex `^[A-Za-z]:/[^/]+$` matches any non-slash sequence so
        # spaces are fine.
        assert is_dangerous_removal_path("D:/Program Files") is True

    def test_windows_drive_child_single_component_blocked(self):
        assert is_dangerous_removal_path("C:/ProgramFiles") is True

    # --- Negative cases: safe subpaths -------------------------------------
    def test_subdir_of_etc_allowed(self):
        assert is_dangerous_removal_path("/etc/nginx/sites-available/mine") is False

    def test_subdir_of_tmp_allowed(self):
        assert is_dangerous_removal_path("/tmp/build-output") is False

    def test_user_home_subdir_allowed(self):
        home = os.path.expanduser("~")
        assert is_dangerous_removal_path(home + "/projects/foo") is False

    def test_deep_user_path_allowed(self):
        assert is_dangerous_removal_path("/home/user/projects/node_modules") is False

    def test_hidden_subdir_under_etc_allowed(self):
        assert is_dangerous_removal_path("/etc/systemd/system/my.service") is False

    def test_windows_drive_child_subdir_allowed(self):
        assert is_dangerous_removal_path("C:/Windows/System32") is False

    # --- Edge cases --------------------------------------------------------
    def test_empty_string_allowed(self):
        assert is_dangerous_removal_path("") is False

    def test_relative_path_not_dangerous(self):
        # Relative paths fall through the root/drive checks. The only
        # dangerous relative shape is bare `*`, which is tested above.
        assert is_dangerous_removal_path("foo/bar") is False

    def test_dot_allowed(self):
        assert is_dangerous_removal_path(".") is False

    def test_dotdot_allowed(self):
        # `..` is not one of the 7 shapes. The caller is responsible for
        # deciding whether to allow traversal — this function only detects
        # the catastrophic shapes.
        assert is_dangerous_removal_path("..") is False


# ---------------------------------------------------------------------------
# is_dangerous_removal_raw_path — pre-realpath user-typed form
# ---------------------------------------------------------------------------


class TestIsDangerousRemovalRawPath:
    def test_raw_tilde_alone_blocked(self):
        assert is_dangerous_removal_raw_path("~") is True

    def test_raw_tilde_slash_blocked(self):
        # `~/` expands to the home directory.
        assert is_dangerous_removal_raw_path("~/") is True

    def test_raw_double_quoted_root_blocked(self):
        assert is_dangerous_removal_raw_path('"/"') is True

    def test_raw_single_quoted_etc_blocked(self):
        assert is_dangerous_removal_raw_path("'/etc'") is True

    def test_raw_whitespace_wrapped_root_blocked(self):
        assert is_dangerous_removal_raw_path("  /  ") is True

    def test_raw_double_slash_root_blocked(self):
        assert is_dangerous_removal_raw_path("//") is True

    def test_raw_backslash_windows_blocked(self):
        assert is_dangerous_removal_raw_path("C:\\Windows") is True

    def test_raw_safe_subdir_allowed(self):
        assert is_dangerous_removal_raw_path("/tmp/build/output") is False

    def test_raw_user_project_allowed(self):
        assert is_dangerous_removal_raw_path("~/projects/node_modules") is False

    def test_raw_empty_allowed(self):
        assert is_dangerous_removal_raw_path("") is False

    def test_raw_only_quotes_allowed(self):
        # Empty after quote strip — falls through to empty check.
        assert is_dangerous_removal_raw_path('""') is False

    def test_raw_tilde_with_home_unset(self, monkeypatch):
        """When HOME is unset, ``~`` stays literal — still hard-deny.

        Without the explicit raw ``~`` branch in
        ``is_dangerous_removal_raw_path``, the literal ``~`` string would
        fail every shape check (not `/`, not `/*`, not a drive root,
        etc.) and sail through silently.
        """
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.delenv("USERPROFILE", raising=False)
        assert is_dangerous_removal_raw_path("~") is True


# ---------------------------------------------------------------------------
# check_dangerous_removal — the integration helper
# ---------------------------------------------------------------------------


class TestCheckDangerousRemoval:
    def test_rm_root_blocked(self, tmp_path):
        reason = check_dangerous_removal("rm", ["-rf", "/"], str(tmp_path))
        assert reason is not None
        assert "rm" in reason

    def test_rm_etc_blocked(self, tmp_path):
        reason = check_dangerous_removal("rm", ["/etc"], str(tmp_path))
        assert reason is not None
        assert "rm" in reason

    def test_rm_etc_passwd_blocked(self, tmp_path):
        reason = check_dangerous_removal("rm", ["/etc/passwd"], str(tmp_path))
        # /etc/passwd is not one of the 7 shapes — its parent is /etc, not
        # /, so dirname check does NOT fire. The per-file protection
        # lives at step 1.6 (check_path_safety + SENSITIVE_PATHS), not
        # here. This function is for catastrophic *directory* removal.
        assert reason is None

    def test_rmdir_root_blocked(self, tmp_path):
        reason = check_dangerous_removal("rmdir", ["/"], str(tmp_path))
        assert reason is not None
        assert "rmdir" in reason

    def test_rmdir_etc_blocked(self, tmp_path):
        reason = check_dangerous_removal("rmdir", ["/etc"], str(tmp_path))
        assert reason is not None

    def test_unlink_etc_blocked(self, tmp_path):
        reason = check_dangerous_removal("unlink", ["/etc"], str(tmp_path))
        assert reason is not None
        assert "unlink" in reason

    def test_rm_wildcard_alone_blocked(self, tmp_path):
        reason = check_dangerous_removal("rm", ["*"], str(tmp_path))
        assert reason is not None

    def test_rm_tilde_blocked(self, tmp_path):
        reason = check_dangerous_removal("rm", ["-rf", "~"], str(tmp_path))
        assert reason is not None

    def test_rm_case_insensitive_command(self, tmp_path):
        reason = check_dangerous_removal("RM", ["/etc"], str(tmp_path))
        assert reason is not None

    def test_non_removal_command_short_circuits(self, tmp_path):
        assert check_dangerous_removal("ls", ["/etc"], str(tmp_path)) is None
        assert check_dangerous_removal("cat", ["/etc/passwd"], str(tmp_path)) is None
        assert check_dangerous_removal("", [], str(tmp_path)) is None

    def test_rm_empty_args_allowed(self, tmp_path):
        # No paths extracted → no check → None.
        assert check_dangerous_removal("rm", [], str(tmp_path)) is None

    def test_rm_only_flags_allowed(self, tmp_path):
        # All args are flags, nothing positional to check.
        assert check_dangerous_removal("rm", ["-rf"], str(tmp_path)) is None

    def test_rm_safe_subdir_allowed(self, tmp_path):
        safe_target = tmp_path / "build"
        safe_target.mkdir()
        reason = check_dangerous_removal("rm", ["-rf", str(safe_target)], str(tmp_path))
        assert reason is None

    def test_rm_relative_safe_path_allowed(self, tmp_path):
        (tmp_path / "out").mkdir()
        reason = check_dangerous_removal("rm", ["-rf", "out"], str(tmp_path))
        assert reason is None

    def test_rm_posix_double_dash_attack_blocked(self, tmp_path):
        # rm -- /etc : `--` stops option parsing, `/etc` is a positional
        # path. The extractor must preserve it.
        reason = check_dangerous_removal("rm", ["--", "/etc"], str(tmp_path))
        assert reason is not None

    def test_rm_posix_double_dash_after_flags_blocked(self, tmp_path):
        reason = check_dangerous_removal("rm", ["-rf", "--", "/etc"], str(tmp_path))
        assert reason is not None

    def test_rm_symlink_attack_blocked(self, tmp_path):
        """``ln -s /etc tmp/foo; rm -rf tmp/foo`` must be caught.

        The raw check sees ``tmp/foo`` (safe). The resolved check
        follows the symlink to ``/etc`` (dangerous). The dual-check
        pattern exists specifically for this attack.
        """
        link = tmp_path / "foo"
        try:
            os.symlink("/etc", str(link))
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation unsupported on this filesystem")
        reason = check_dangerous_removal("rm", ["-rf", str(link)], str(tmp_path))
        assert reason is not None
        assert "/etc" in reason or "resolves to" in reason

    @pytest.mark.skipif(
        not os.path.exists("/private/tmp"),
        reason="macOS-specific /tmp → /private/tmp canonicalisation",
    )
    def test_rm_macos_tmp_caught_by_raw_check(self, tmp_path):
        # On macOS, /tmp realpaths to /private/tmp. The raw check sees
        # /tmp and catches it via the direct-root-child rule before
        # realpath has a chance to canonicalise.
        reason = check_dangerous_removal("rm", ["-rf", "/tmp"], str(tmp_path))
        assert reason is not None

    def test_rm_quoted_path_blocked(self, tmp_path):
        # The extractor upstream (shlex) normally strips quotes, but the
        # belt-and-suspenders strip inside check_dangerous_removal must
        # handle any that leak through.
        reason = check_dangerous_removal("rm", ['"/etc"'], str(tmp_path))
        assert reason is not None

    def test_rm_realpath_error_falls_back_gracefully(self, tmp_path, monkeypatch):
        """A realpath failure must not crash the pipeline."""

        def raise_(path):  # noqa: ANN001
            raise OSError("simulated realpath failure")

        monkeypatch.setattr(os.path, "realpath", raise_)
        # Raw check still runs and catches /etc.
        reason = check_dangerous_removal("rm", ["/etc"], str(tmp_path))
        assert reason is not None

    def test_rm_realpath_error_safe_path_returns_none(self, tmp_path, monkeypatch):
        """Realpath failure on a safe path still returns None."""
        (tmp_path / "safe").mkdir()

        def raise_(path):  # noqa: ANN001
            raise OSError("simulated realpath failure")

        monkeypatch.setattr(os.path, "realpath", raise_)
        reason = check_dangerous_removal("rm", ["safe"], str(tmp_path))
        assert reason is None

    def test_absolute_path_does_not_use_cwd(self, tmp_path):
        # An absolute dangerous path ignores the cwd entirely.
        reason = check_dangerous_removal("rm", ["/etc"], "/some/other/cwd")
        assert reason is not None
