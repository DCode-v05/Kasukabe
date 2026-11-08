"""Tests for sandbox policy engine."""

from src.sandbox.policy import (
    SandboxPolicy,
    merge_policies,
    validate_policy,
)
from src.sandbox.executor import SafeBinProfile, DEFAULT_SAFE_BINS


class TestPolicyParsing:
    """Test YAML parsing and dict parsing."""

    def test_default_policy_loads(self):
        policy = SandboxPolicy.default()
        assert policy.version == 1
        assert len(policy.binaries) >= 14

    def test_default_policy_has_all_binaries(self):
        policy = SandboxPolicy.default()
        expected = {
            "jq",
            "grep",
            "cut",
            "sort",
            "uniq",
            "head",
            "tail",
            "wc",
            "tr",
            "cat",
            "echo",
            "ls",
            "find",
            "awk",
        }
        assert expected.issubset(set(policy.binaries.keys()))

    def test_sed_not_in_default_policy(self):
        # sed is gated by src/sandbox/sed_validation.py at permission time,
        # not by the executor-level binary policy. It must not be present in
        # the default binary allowlist.
        policy = SandboxPolicy.default()
        assert "sed" not in policy.binaries

    def test_from_dict_minimal(self):
        data = {"version": 1, "binaries": {"echo": {"max_positional": 5}}}
        policy = SandboxPolicy.from_dict(data)
        assert "echo" in policy.binaries
        assert policy.binaries["echo"].max_positional == 5

    def test_from_dict_empty(self):
        policy = SandboxPolicy.from_dict({})
        assert policy.version == 1
        assert len(policy.binaries) == 0

    def test_from_yaml_missing_file_returns_default(self, tmp_path):
        policy = SandboxPolicy.from_yaml(tmp_path / "nonexistent.yaml")
        assert len(policy.binaries) >= 14

    def test_from_yaml_invalid_yaml_returns_default(self, tmp_path):
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(": : : invalid yaml {{[")
        policy = SandboxPolicy.from_yaml(bad_yaml)
        assert len(policy.binaries) >= 14

    def test_from_yaml_non_dict_returns_default(self, tmp_path):
        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text("- item1\n- item2\n")
        policy = SandboxPolicy.from_yaml(list_yaml)
        assert len(policy.binaries) >= 14

    def test_stdin_only_parsed(self):
        data = {"binaries": {"grep": {"stdin_only": True, "max_positional": 0}}}
        policy = SandboxPolicy.from_dict(data)
        assert policy.binaries["grep"].stdin_only is True

    def test_denied_flags_parsed(self):
        data = {"binaries": {"find": {"denied_flags": ["-delete", "-exec"]}}}
        policy = SandboxPolicy.from_dict(data)
        assert "-delete" in policy.binaries["find"].denied_flags
        assert "-exec" in policy.binaries["find"].denied_flags

    def test_filesystem_rules_parsed(self):
        data = {
            "filesystem": [
                {"host_path": "/usr", "sandbox_path": "/usr", "writable": False},
                {"host_path": "/workspace", "writable": True},
            ]
        }
        policy = SandboxPolicy.from_dict(data)
        assert len(policy.filesystem) == 2
        assert policy.filesystem[0].writable is False
        assert policy.filesystem[1].writable is True

    def test_proxy_policy_parsed(self):
        data = {
            "proxy": {
                "enabled": True,
                "listen_port": 4000,
                "default_action": "deny",
                "rules": [
                    {"binary": "node", "host_pattern": "*.npmjs.org", "action": "allow"},
                ],
            }
        }
        policy = SandboxPolicy.from_dict(data)
        assert policy.proxy.enabled is True
        assert policy.proxy.listen_port == 4000
        assert len(policy.proxy.rules) == 1
        assert policy.proxy.rules[0].binary == "node"


class TestToSafeBins:
    """Test conversion to SafeBinProfile dict."""

    def test_matches_default_safe_bins(self):
        policy = SandboxPolicy.default()
        safe_bins = policy.to_safe_bins()
        # Every binary in DEFAULT_SAFE_BINS should have a matching policy
        for name, profile in DEFAULT_SAFE_BINS.items():
            assert name in safe_bins, f"Missing {name}"
            sb = safe_bins[name]
            assert sb.max_positional == profile.max_positional, f"{name} max_positional"
            assert sb.stdin_only == profile.stdin_only, f"{name} stdin_only"
            assert sb.denied_flags == profile.denied_flags, f"{name} denied_flags"

    def test_returns_safe_bin_profile_instances(self):
        policy = SandboxPolicy.from_dict({"binaries": {"echo": {"max_positional": 20}}})
        safe_bins = policy.to_safe_bins()
        assert isinstance(safe_bins["echo"], SafeBinProfile)

    def test_empty_policy_returns_empty(self):
        policy = SandboxPolicy.from_dict({})
        assert len(policy.to_safe_bins()) == 0


class TestToFilesystemArgs:
    """Test conversion to bwrap args."""

    def test_readonly_produces_ro_bind(self):
        policy = SandboxPolicy.from_dict(
            {
                "filesystem": [
                    {"host_path": "/usr", "sandbox_path": "/usr", "writable": False},
                ]
            }
        )
        args = policy.to_filesystem_bwrap_args()
        assert args == ["--ro-bind", "/usr", "/usr"]

    def test_writable_produces_bind(self):
        policy = SandboxPolicy.from_dict(
            {
                "filesystem": [
                    {"host_path": "/workspace", "sandbox_path": "/workspace", "writable": True},
                ]
            }
        )
        args = policy.to_filesystem_bwrap_args()
        assert args == ["--bind", "/workspace", "/workspace"]

    def test_multiple_rules(self):
        policy = SandboxPolicy.from_dict(
            {
                "filesystem": [
                    {"host_path": "/usr", "sandbox_path": "/usr"},
                    {"host_path": "/lib", "sandbox_path": "/lib"},
                ]
            }
        )
        args = policy.to_filesystem_bwrap_args()
        assert len(args) == 6  # 2 rules x 3 args each


class TestMerge:
    """Test policy merge — restrict only, never expand."""

    def test_merge_removes_binary(self):
        base = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "echo": {"max_positional": 20},
                    "cat": {"max_positional": 5},
                }
            }
        )
        custom = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "echo": {"max_positional": 20},
                    # cat not in custom = removed
                }
            }
        )
        merged = merge_policies(base, custom)
        assert "echo" in merged.binaries
        assert "cat" not in merged.binaries

    def test_merge_adds_denied_flags(self):
        base = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "grep": {"denied_flags": ["-r"]},
                }
            }
        )
        custom = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "grep": {"denied_flags": ["-r", "--include"]},
                }
            }
        )
        merged = merge_policies(base, custom)
        assert "-r" in merged.binaries["grep"].denied_flags
        assert "--include" in merged.binaries["grep"].denied_flags

    def test_merge_restricts_allowed_flags(self):
        base = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "grep": {"allowed_flags": ["-e", "-m", "-A"]},
                }
            }
        )
        custom = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "grep": {"allowed_flags": ["-e"]},
                }
            }
        )
        merged = merge_policies(base, custom)
        assert merged.binaries["grep"].allowed_flags == {"-e"}

    def test_merge_reduces_max_positional(self):
        base = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "cat": {"max_positional": 5},
                }
            }
        )
        custom = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "cat": {"max_positional": 2},
                }
            }
        )
        merged = merge_policies(base, custom)
        assert merged.binaries["cat"].max_positional == 2

    def test_merge_cannot_expand_max_positional(self):
        base = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "cat": {"max_positional": 5},
                }
            }
        )
        custom = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "cat": {"max_positional": 100},
                }
            }
        )
        merged = merge_policies(base, custom)
        assert merged.binaries["cat"].max_positional == 5

    def test_merge_cannot_add_new_binary(self):
        base = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "echo": {"max_positional": 20},
                }
            }
        )
        custom = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "echo": {"max_positional": 20},
                    "rm": {"max_positional": 5},  # not in base
                }
            }
        )
        merged = merge_policies(base, custom)
        assert "rm" not in merged.binaries

    def test_merge_stdin_only_is_sticky(self):
        base = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "grep": {"stdin_only": True},
                }
            }
        )
        custom = SandboxPolicy.from_dict(
            {
                "binaries": {
                    "grep": {"stdin_only": False},
                }
            }
        )
        merged = merge_policies(base, custom)
        assert merged.binaries["grep"].stdin_only is True


class TestValidation:
    """Test policy validation."""

    def test_valid_policy_no_errors(self):
        policy = SandboxPolicy.default()
        assert validate_policy(policy) == []

    def test_negative_max_positional(self):
        policy = SandboxPolicy.from_dict({"binaries": {"x": {"max_positional": -1}}})
        errors = validate_policy(policy)
        assert any("negative" in e for e in errors)

    def test_invalid_network_action(self):
        policy = SandboxPolicy.from_dict(
            {
                "proxy": {
                    "rules": [{"binary": "curl", "host_pattern": "*", "action": "yolo"}],
                }
            }
        )
        errors = validate_policy(policy)
        assert any("invalid action" in e for e in errors)

    def test_inject_credentials_needs_ref(self):
        policy = SandboxPolicy.from_dict(
            {
                "proxy": {
                    "rules": [
                        {
                            "binary": "node",
                            "host_pattern": "api.stripe.com",
                            "action": "inject_credentials",
                        }
                    ],
                }
            }
        )
        errors = validate_policy(policy)
        assert any("credential_ref" in e for e in errors)

    def test_empty_host_pattern(self):
        policy = SandboxPolicy.from_dict(
            {
                "proxy": {
                    "rules": [{"binary": "curl", "host_pattern": "", "action": "allow"}],
                }
            }
        )
        errors = validate_policy(policy)
        assert any("empty host_pattern" in e for e in errors)
