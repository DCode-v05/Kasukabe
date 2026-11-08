"""Sandbox policy engine — declarative YAML policies for sandbox behavior.

Loads sandbox-policy.yaml, validates, produces typed objects that executor.py
and proxy.py consume. Replaces hardcoded DEFAULT_SAFE_BINS with a policy file
that users and skill authors can customize.

Policy merge: custom policies can restrict (never expand) the base policy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_POLICY_PATH = Path(__file__).parent / "default-policy.yaml"


@dataclass
class BinaryPolicy:
    """Policy for a single binary inside the sandbox."""

    name: str
    max_positional: int = 10
    allowed_flags: set[str] = field(default_factory=set)
    denied_flags: set[str] = field(default_factory=set)
    stdin_only: bool = False


@dataclass
class FilesystemRule:
    """A filesystem bind rule for bwrap."""

    host_path: str
    sandbox_path: str
    writable: bool = False


@dataclass
class NetworkRule:
    """A per-binary network access rule for the proxy."""

    binary: str
    host_pattern: str
    methods: list[str] = field(default_factory=lambda: ["*"])
    action: str = "allow"
    credential_ref: str = ""


@dataclass
class ProxyPolicy:
    """Policy for the trusted-zone proxy."""

    enabled: bool = False
    listen_port: int = 3128
    rules: list[NetworkRule] = field(default_factory=list)
    default_action: str = "deny"
    audit_log: bool = True


@dataclass
class SandboxPolicy:
    """Complete sandbox policy — parsed from YAML."""

    version: int = 1
    binaries: dict[str, BinaryPolicy] = field(default_factory=dict)
    filesystem: list[FilesystemRule] = field(default_factory=list)
    proxy: ProxyPolicy = field(default_factory=ProxyPolicy)
    seccomp_enabled: bool = True
    landlock_enabled: bool = True

    @classmethod
    def from_yaml(cls, path: Path) -> SandboxPolicy:
        """Parse a YAML policy file. Uses yaml.safe_load (never yaml.load)."""
        import yaml

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                logger.warning("Policy file %s is not a dict, using defaults", path)
                return cls.default()
            return cls.from_dict(data)
        except FileNotFoundError:
            logger.warning("Policy file %s not found, using defaults", path)
            return cls.default()
        except Exception as e:
            logger.warning("Policy file %s parse error: %s, using defaults", path, e)
            return cls.default()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SandboxPolicy:
        """Parse a dict into a SandboxPolicy."""
        policy = cls()
        policy.version = data.get("version", 1)
        policy.seccomp_enabled = data.get("seccomp_enabled", True)
        policy.landlock_enabled = data.get("landlock_enabled", True)

        for name, conf in data.get("binaries", {}).items():
            if not name or not isinstance(name, str):
                continue
            bp = BinaryPolicy(name=name)
            if isinstance(conf, dict):
                bp.max_positional = conf.get("max_positional", 10)
                bp.allowed_flags = set(conf.get("allowed_flags", []))
                bp.denied_flags = set(conf.get("denied_flags", []))
                bp.stdin_only = conf.get("stdin_only", False)
            policy.binaries[name] = bp

        for rule_data in data.get("filesystem", []):
            if not isinstance(rule_data, dict):
                continue
            host_path = str(rule_data.get("host_path", "") or "")
            sandbox_path = str(rule_data.get("sandbox_path", host_path) or host_path)
            if not host_path:
                continue
            policy.filesystem.append(
                FilesystemRule(
                    host_path=host_path,
                    sandbox_path=sandbox_path,
                    writable=rule_data.get("writable", False),
                )
            )

        proxy_data = data.get("proxy", {})
        if isinstance(proxy_data, dict):
            policy.proxy = ProxyPolicy(
                enabled=proxy_data.get("enabled", False),
                listen_port=proxy_data.get("listen_port", 3128),
                default_action=proxy_data.get("default_action", "deny"),
                audit_log=proxy_data.get("audit_log", True),
            )
            for rule_data in proxy_data.get("rules", []):
                if not isinstance(rule_data, dict):
                    continue
                policy.proxy.rules.append(
                    NetworkRule(
                        binary=rule_data.get("binary", "*"),
                        host_pattern=rule_data.get("host_pattern", "*"),
                        methods=rule_data.get("methods", ["*"]),
                        action=rule_data.get("action", "allow"),
                        credential_ref=rule_data.get("credential_ref", ""),
                    )
                )

        return policy

    @classmethod
    def default(cls) -> SandboxPolicy:
        """Return the default policy (matches current hardcoded behavior)."""
        if DEFAULT_POLICY_PATH.exists():
            return cls.from_yaml(DEFAULT_POLICY_PATH)
        return cls.hardcoded_default()

    @classmethod
    def hardcoded_default(cls) -> SandboxPolicy:
        """Fallback if default-policy.yaml is missing."""
        policy = cls()
        bins = {
            "jq": BinaryPolicy(
                "jq",
                max_positional=1,
                allowed_flags={"--arg", "--argjson", "--argstr"},
                denied_flags={"-f", "--fromfile", "--slurpfile"},
            ),
            "grep": BinaryPolicy(
                "grep",
                stdin_only=True,
                max_positional=0,
                allowed_flags={"-e", "-m", "-A", "-B", "-C", "--regexp", "--max-count"},
            ),
            "cut": BinaryPolicy(
                "cut", stdin_only=True, max_positional=0, allowed_flags={"-d", "-f", "-c"}
            ),
            "sort": BinaryPolicy(
                "sort", stdin_only=True, max_positional=0, allowed_flags={"-k", "-t"}
            ),
            "uniq": BinaryPolicy("uniq", stdin_only=True, max_positional=0),
            "head": BinaryPolicy("head", stdin_only=True, max_positional=0, allowed_flags={"-n"}),
            "tail": BinaryPolicy(
                "tail", stdin_only=True, max_positional=0, allowed_flags={"-n", "-f"}
            ),
            "wc": BinaryPolicy("wc", stdin_only=True, max_positional=0),
            "tr": BinaryPolicy("tr", stdin_only=True, max_positional=2),
            "cat": BinaryPolicy("cat", max_positional=5),
            "echo": BinaryPolicy("echo", max_positional=20),
            "ls": BinaryPolicy("ls", max_positional=5),
            "find": BinaryPolicy(
                "find", max_positional=5, denied_flags={"-exec", "-execdir", "-delete"}
            ),
            "awk": BinaryPolicy("awk", stdin_only=True, max_positional=1, denied_flags={"-f"}),
        }
        policy.binaries = bins
        return policy

    def to_safe_bins(self) -> dict[str, Any]:
        """Convert binary policies to SafeBinProfile dict for SandboxExecutor.

        Imports SafeBinProfile here to avoid circular imports at module level.
        """
        from src.sandbox.executor import SafeBinProfile

        result: dict[str, SafeBinProfile] = {}
        for name, bp in self.binaries.items():
            result[name] = SafeBinProfile(
                min_positional=0,
                max_positional=bp.max_positional,
                allowed_value_flags=set(bp.allowed_flags),
                denied_flags=set(bp.denied_flags),
                stdin_only=bp.stdin_only,
            )
        return result

    def to_filesystem_bwrap_args(self) -> list[str]:
        """Convert filesystem rules to bwrap --bind/--ro-bind arguments."""
        args: list[str] = []
        for rule in self.filesystem:
            if rule.writable:
                args += ["--bind", rule.host_path, rule.sandbox_path]
            else:
                args += ["--ro-bind", rule.host_path, rule.sandbox_path]
        return args


def merge_policies(base: SandboxPolicy, custom: SandboxPolicy) -> SandboxPolicy:
    """Merge custom over base. Custom can only restrict, never expand.

    - Can remove binaries (not present in custom = removed)
    - Can add denied_flags (union of both sets)
    - Can remove allowed_flags (intersection)
    - Can reduce max_positional
    - Cannot add binaries not in base
    - Cannot expand allowed_flags beyond base
    - Cannot increase max_positional
    - Proxy rules: custom deny rules are added, custom allow rules ignored if not in base
    """
    merged = SandboxPolicy()
    merged.version = base.version
    merged.seccomp_enabled = base.seccomp_enabled and custom.seccomp_enabled
    merged.landlock_enabled = base.landlock_enabled and custom.landlock_enabled

    for name, base_bp in base.binaries.items():
        if name not in custom.binaries:
            continue
        custom_bp = custom.binaries[name]
        merged.binaries[name] = BinaryPolicy(
            name=name,
            max_positional=min(base_bp.max_positional, custom_bp.max_positional),
            allowed_flags=base_bp.allowed_flags & custom_bp.allowed_flags,
            denied_flags=base_bp.denied_flags | custom_bp.denied_flags,
            stdin_only=base_bp.stdin_only or custom_bp.stdin_only,
        )

    merged.filesystem = list(base.filesystem)

    merged.proxy = ProxyPolicy(
        enabled=base.proxy.enabled or custom.proxy.enabled,
        listen_port=base.proxy.listen_port,
        default_action="deny"
        if base.proxy.default_action == "deny" or custom.proxy.default_action == "deny"
        else "allow",
        audit_log=base.proxy.audit_log or custom.proxy.audit_log,
        rules=list(base.proxy.rules) + [r for r in custom.proxy.rules if r.action == "deny"],
    )

    return merged


def validate_policy(policy: SandboxPolicy) -> list[str]:
    """Return list of validation errors (empty = valid)."""
    errors: list[str] = []

    for name, bp in policy.binaries.items():
        if not name:
            errors.append("Binary policy has empty name")
        if bp.max_positional < 0:
            errors.append(f"Binary {name}: max_positional cannot be negative")

    for rule in policy.filesystem:
        if not rule.host_path:
            errors.append("Filesystem rule has empty host_path")

    for net_rule in policy.proxy.rules:
        if net_rule.action not in ("allow", "deny", "inject_credentials"):
            errors.append(f"Network rule for {net_rule.binary}: invalid action '{net_rule.action}'")
        if not net_rule.host_pattern:
            errors.append(f"Network rule for {net_rule.binary}: empty host_pattern")
        if net_rule.action == "inject_credentials" and not net_rule.credential_ref:
            errors.append(
                f"Network rule for {net_rule.binary}: inject_credentials requires credential_ref"
            )

    return errors
