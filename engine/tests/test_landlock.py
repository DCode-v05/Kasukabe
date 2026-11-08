"""Tests for Landlock filesystem access control."""

from unittest.mock import patch


from src.sandbox.landlock import (
    FS_READ_ONLY,
    FS_READ_WRITE,
    FS_ALL_V1,
    LANDLOCK_ACCESS_FS_READ_FILE,
    LandlockRule,
    LandlockProfile,
    default_sandbox_profile,
    generate_landlock_wrapper,
    landlock_bwrap_integration,
    has_landlock_support,
    detect_landlock_abi_version,
)


class TestLandlockProfile:
    """Test profile construction."""

    def test_default_profile_has_workspace_rw(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        (tmp_path / "workspace").mkdir()
        profile = default_sandbox_profile(workspace)
        ws_rules = [r for r in profile.rules if r.path == workspace]
        assert len(ws_rules) == 1
        assert ws_rules[0].access == FS_READ_WRITE

    def test_default_profile_has_usr_ro(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        (tmp_path / "workspace").mkdir()
        profile = default_sandbox_profile(workspace)
        usr_rules = [r for r in profile.rules if r.path == "/usr"]
        for rule in usr_rules:
            assert rule.access == FS_READ_ONLY

    def test_default_profile_has_tmp_rw(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        (tmp_path / "workspace").mkdir()
        profile = default_sandbox_profile(workspace)
        tmp_rules = [r for r in profile.rules if r.path == "/tmp"]
        assert len(tmp_rules) == 1
        assert tmp_rules[0].access == FS_READ_WRITE

    def test_default_profile_has_dev_read_only(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        (tmp_path / "workspace").mkdir()
        profile = default_sandbox_profile(workspace)
        dev_rules = [r for r in profile.rules if r.path == "/dev"]
        assert len(dev_rules) == 1
        assert dev_rules[0].access == LANDLOCK_ACCESS_FS_READ_FILE

    def test_runtimes_path_included_when_exists(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        runtimes = str(tmp_path / "runtimes")
        (tmp_path / "workspace").mkdir()
        (tmp_path / "runtimes").mkdir()
        profile = default_sandbox_profile(workspace, runtimes_path=runtimes)
        rt_rules = [r for r in profile.rules if r.path == runtimes]
        assert len(rt_rules) == 1
        assert rt_rules[0].access == FS_READ_ONLY

    def test_runtimes_path_skipped_when_missing(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        (tmp_path / "workspace").mkdir()
        profile = default_sandbox_profile(workspace, runtimes_path="/nonexistent")
        rt_rules = [r for r in profile.rules if r.path == "/nonexistent"]
        assert len(rt_rules) == 0

    def test_handled_access_covers_all_rule_bits(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        (tmp_path / "workspace").mkdir()
        profile = default_sandbox_profile(workspace)
        union = 0
        for rule in profile.rules:
            union |= rule.access
        assert profile.handled_access == union
        for rule in profile.rules:
            assert (rule.access & profile.handled_access) == rule.access


class TestLandlockWrapper:
    """Test wrapper script generation."""

    def test_wrapper_is_valid_python(self, tmp_path):
        profile = LandlockProfile(
            rules=[LandlockRule(path="/workspace", access=FS_READ_WRITE)],
            handled_access=FS_ALL_V1,
        )
        source = generate_landlock_wrapper(profile)
        compile(source, "<wrapper>", "exec")

    def test_wrapper_contains_rule_paths(self):
        profile = LandlockProfile(
            rules=[
                LandlockRule(path="/workspace", access=FS_READ_WRITE),
                LandlockRule(path="/usr", access=FS_READ_ONLY),
            ],
            handled_access=FS_ALL_V1,
        )
        source = generate_landlock_wrapper(profile)
        assert "'/workspace'" in source
        assert "'/usr'" in source

    def test_wrapper_contains_execvp(self):
        profile = LandlockProfile(
            rules=[LandlockRule(path="/workspace", access=FS_READ_WRITE)],
            handled_access=FS_ALL_V1,
        )
        source = generate_landlock_wrapper(profile)
        assert "os.execvp" in source

    def test_wrapper_contains_restrict_self(self):
        profile = LandlockProfile(
            rules=[LandlockRule(path="/workspace", access=FS_READ_WRITE)],
            handled_access=FS_ALL_V1,
        )
        source = generate_landlock_wrapper(profile)
        assert "_restrict_self" in source

    def test_wrapper_contains_prctl_no_new_privs(self):
        profile = LandlockProfile(
            rules=[LandlockRule(path="/workspace", access=FS_READ_WRITE)],
            handled_access=FS_ALL_V1,
        )
        source = generate_landlock_wrapper(profile)
        assert "prctl" in source


class TestLandlockIntegration:
    """Test bwrap integration."""

    @patch("src.sandbox.landlock.has_landlock_support", return_value=False)
    def test_returns_none_when_unavailable(self, _, tmp_path):
        profile = LandlockProfile(rules=[], handled_access=FS_ALL_V1)
        result = landlock_bwrap_integration(profile, str(tmp_path), ["echo", "hello"])
        assert result is None

    @patch("src.sandbox.landlock.has_landlock_support", return_value=True)
    def test_returns_modified_command(self, _, tmp_path):
        profile = LandlockProfile(
            rules=[LandlockRule(path=str(tmp_path), access=FS_READ_WRITE)],
            handled_access=FS_ALL_V1,
        )
        result = landlock_bwrap_integration(profile, str(tmp_path), ["echo", "hello"])
        assert result is not None
        assert result[0] == "python3"
        assert ".landlock_wrapper.py" in result[1]
        assert result[2:] == ["echo", "hello"]

    @patch("src.sandbox.landlock.has_landlock_support", return_value=True)
    def test_writes_wrapper_file(self, _, tmp_path):
        profile = LandlockProfile(
            rules=[LandlockRule(path=str(tmp_path), access=FS_READ_WRITE)],
            handled_access=FS_ALL_V1,
        )
        landlock_bwrap_integration(profile, str(tmp_path), ["echo", "hello"])
        wrapper_path = tmp_path / ".landlock_wrapper.py"
        assert wrapper_path.exists()
        content = wrapper_path.read_text()
        compile(content, "<wrapper>", "exec")


class TestDetection:
    """Test Landlock detection."""

    @patch("src.sandbox.landlock.is_linux", return_value=False)
    def test_abi_zero_on_non_linux(self, _):
        from src.sandbox import landlock

        landlock.landlock_abi_cached = None
        assert detect_landlock_abi_version() == 0

    @patch("src.sandbox.landlock.is_linux", return_value=False)
    def test_has_support_false_on_non_linux(self, _):
        from src.sandbox import landlock

        landlock.landlock_abi_cached = None
        assert has_landlock_support() is False
