"""Sandbox — isolated execution environment for LLM-generated code.

Defense-in-depth layers (Linux):
  Layer 1: Bubblewrap namespace isolation (PID, network, mount, IPC)
  Layer 2: seccomp-BPF syscall filtering (blocks ptrace, mount, unshare, bpf)
  Layer 3: Landlock filesystem ACL (restricts paths even if bwrap has a bug)
  Layer 4: Trusted-zone proxy (per-binary network policy + credential injection)
  Layer 5: Permission pipeline (rule-based gate before every tool call)
"""

from src.sandbox.executor import SandboxExecutor, SandboxResult
from src.sandbox.permissions import (
    ToolSource,
    ToolAction,
    PermissionMode,
    DenyRule,
    PermissionResult,
    check_permission,
)
from src.sandbox.policy import SandboxPolicy
from src.sandbox.proxy import TrustedZoneProxy
from src.sandbox.shadow_detection import ShadowedRule, detect_shadowed_rules, is_shared_source
from src.sandbox.sandbox_auto_allow import is_sandbox_auto_allowed
from src.sandbox.process_tree import (
    tree_kill,
    get_child_pids,
    get_descendant_pids,
    is_process_alive,
)
from src.sandbox.cleanup_registry import register_cleanup, run_all_cleanup, clear_registry
from src.sandbox.orphan_detector import start_orphan_scanner, stop_orphan_scanner

__all__ = [
    "SandboxExecutor",
    "SandboxResult",
    "SandboxPolicy",
    "TrustedZoneProxy",
    "ToolSource",
    "ToolAction",
    "PermissionMode",
    "DenyRule",
    "PermissionResult",
    "check_permission",
    "ShadowedRule",
    "detect_shadowed_rules",
    "is_shared_source",
    "is_sandbox_auto_allowed",
    "tree_kill",
    "get_child_pids",
    "get_descendant_pids",
    "is_process_alive",
    "register_cleanup",
    "run_all_cleanup",
    "clear_registry",
    "start_orphan_scanner",
    "stop_orphan_scanner",
]
