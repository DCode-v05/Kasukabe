"""Landlock filesystem access control for defense-in-depth.

Landlock (Linux 5.13+) restricts filesystem access at the thread level.
Applied as a pre-exec step INSIDE the bwrap namespace: the sandboxed process
restricts itself before exec'ing the user command.

Why inside, not outside? bwrap can't apply Landlock externally. The kernel
requires the process to call landlock_restrict_self() on itself. So we
generate a tiny Python wrapper that:
  1. Creates a Landlock ruleset via ctypes → kernel syscalls
  2. Adds path rules (workspace=rw, /usr=ro, etc.)
  3. Enforces the ruleset (irreversible)
  4. os.execvp() the real command

If Landlock is unavailable (kernel < 5.13), everything degrades gracefully —
bwrap's mount namespace still provides filesystem isolation.

Linux-only. All functions return None/empty on non-Linux.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14

FS_READ_ONLY = (
    LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR
)
FS_READ_WRITE = (
    FS_READ_ONLY
    | LANDLOCK_ACCESS_FS_WRITE_FILE
    | LANDLOCK_ACCESS_FS_REMOVE_DIR
    | LANDLOCK_ACCESS_FS_REMOVE_FILE
    | LANDLOCK_ACCESS_FS_MAKE_DIR
    | LANDLOCK_ACCESS_FS_MAKE_REG
    | LANDLOCK_ACCESS_FS_MAKE_SYM
    | LANDLOCK_ACCESS_FS_TRUNCATE
)
FS_ALL_V1 = (1 << 13) - 1

SYSCALL_LANDLOCK_CREATE_RULESET = {"x86_64": 444, "aarch64": 444}
SYSCALL_LANDLOCK_ADD_RULE = {"x86_64": 445, "aarch64": 445}
SYSCALL_LANDLOCK_RESTRICT_SELF = {"x86_64": 446, "aarch64": 446}

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0


@dataclass
class LandlockRule:
    """A filesystem access rule."""

    path: str
    access: int


@dataclass
class LandlockProfile:
    """A set of Landlock rules."""

    rules: list[LandlockRule] = field(default_factory=list)
    handled_access: int = FS_ALL_V1


def is_linux() -> bool:
    return platform.system() == "Linux"


def arch_key() -> str | None:
    if not is_linux():
        return None
    machine = platform.machine()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    return None


landlock_abi_cached: int | None = None


def detect_landlock_abi_version() -> int:
    """Detect the Landlock ABI version (0 = not supported, 1+ = version)."""
    global landlock_abi_cached
    if landlock_abi_cached is not None:
        return landlock_abi_cached

    if not is_linux():
        landlock_abi_cached = 0
        return 0

    arch = arch_key()
    if arch is None:
        landlock_abi_cached = 0
        return 0

    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        nr = SYSCALL_LANDLOCK_CREATE_RULESET[arch]
        abi = libc.syscall(nr, 0, 0, LANDLOCK_CREATE_RULESET_VERSION)
        if abi < 0:
            landlock_abi_cached = 0
            return 0
        landlock_abi_cached = abi
        return abi
    except (OSError, KeyError):
        landlock_abi_cached = 0
        return 0


def has_landlock_support() -> bool:
    """Check if Landlock is available (kernel 5.13+)."""
    return detect_landlock_abi_version() >= 1


def default_sandbox_profile(workspace: str, runtimes_path: str | None = None) -> LandlockProfile:
    """Build default Landlock rules for sandbox execution.

    Rules:
      <workspace>           → read + write + execute + create + remove
      /usr, /lib, /lib64, /bin → read + execute only
      /tmp                  → read + write + create + remove
      /dev                  → read only
      /proc                 → read only
      /runtimes (if provided) → read + execute only
      Everything else       → denied (implicit)

    Note on the workspace path: this should be the path the *sandboxed
    process* sees, not the host path. When called from SandboxExecutor
    via bwrap, that's "/workspace" (the bwrap bind-mount target), not
    the host-side tempdir. Passing the host path here makes the wrapper
    skip the rule silently (path doesn't exist in the bwrap namespace),
    which causes Landlock to deny execution of scripts inside the
    workspace.
    """
    rules: list[LandlockRule] = []

    rules.append(LandlockRule(path=workspace, access=FS_READ_WRITE))

    for path in ["/usr", "/lib", "/bin"]:
        if os.path.exists(path):
            rules.append(LandlockRule(path=path, access=FS_READ_ONLY))
    if os.path.exists("/lib64"):
        rules.append(LandlockRule(path="/lib64", access=FS_READ_ONLY))

    if runtimes_path and os.path.exists(runtimes_path):
        rules.append(LandlockRule(path=runtimes_path, access=FS_READ_ONLY))

    rules.append(LandlockRule(path="/tmp", access=FS_READ_WRITE))

    rules.append(
        LandlockRule(
            path="/dev",
            access=LANDLOCK_ACCESS_FS_READ_FILE,
        )
    )

    rules.append(
        LandlockRule(
            path="/proc",
            access=LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR,
        )
    )

    handled = 0
    for rule in rules:
        handled |= rule.access

    return LandlockProfile(rules=rules, handled_access=handled)


def generate_landlock_wrapper(profile: LandlockProfile) -> str:
    """Generate a Python script that applies Landlock then exec's the command.

    The script uses ctypes to call landlock syscalls, adds rules for each
    path in the profile, enforces the ruleset, then os.execvp() the real
    command passed as sys.argv[1:].

    Returns the Python source code as a string.

    Note: this function used to wrap the template in textwrap.dedent(), but
    that interacted badly with the multi-line `{rules_code}` interpolation —
    only the first line of rules_code picked up the template's indent, so
    dedent computed the wrong common-prefix and produced a script with
    inconsistent leading whitespace (IndentationError on the docstring).
    The template below uses no leading whitespace and rules_code emits the
    exact 4-space indent expected inside main()'s body.
    """
    rules_code_lines = [
        f"    _add_rule(ruleset_fd, {rule.path!r}, {rule.access})" for rule in profile.rules
    ]
    rules_code = "\n".join(rules_code_lines)

    return f'''#!/usr/bin/env python3
"""Landlock wrapper — applies filesystem restrictions then exec's the command."""
import ctypes
import ctypes.util
import os
import struct
import sys

_libc = ctypes.CDLL("libc.so.6", use_errno=True)

# Landlock syscall numbers (same on x86_64 and aarch64)
_NR_CREATE = 444
_NR_ADD_RULE = 445
_NR_RESTRICT = 446

# Landlock rule type
_LANDLOCK_RULE_PATH_BENEATH = 1

class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]

class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]

def _create_ruleset(handled: int) -> int:
    attr = _LandlockRulesetAttr(handled_access_fs=handled)
    fd = _libc.syscall(_NR_CREATE, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if fd < 0:
        err = ctypes.get_errno()
        print(f"landlock: create_ruleset failed (errno={{err}})", file=sys.stderr)
        return -1
    return fd

def _add_rule(ruleset_fd: int, path: str, access: int) -> None:
    try:
        parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return  # path doesn't exist in this namespace, skip
    attr = _LandlockPathBeneathAttr(allowed_access=access, parent_fd=parent_fd)
    ret = _libc.syscall(_NR_ADD_RULE, ruleset_fd, _LANDLOCK_RULE_PATH_BENEATH,
                        ctypes.byref(attr), 0)
    os.close(parent_fd)
    if ret < 0:
        err = ctypes.get_errno()
        print(f"landlock: add_rule failed for {{path}} (errno={{err}})", file=sys.stderr)

def _restrict_self(ruleset_fd: int) -> bool:
    # Drop ambient capabilities so Landlock can restrict
    _libc.prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS
    ret = _libc.syscall(_NR_RESTRICT, ruleset_fd, 0)
    os.close(ruleset_fd)
    if ret < 0:
        err = ctypes.get_errno()
        print(f"landlock: restrict_self failed (errno={{err}})", file=sys.stderr)
        return False
    return True

def main():
    if len(sys.argv) < 2:
        print("landlock wrapper: no command specified", file=sys.stderr)
        sys.exit(1)

    ruleset_fd = _create_ruleset({profile.handled_access})
    if ruleset_fd < 0:
        # Landlock unavailable — exec without restrictions
        os.execvp(sys.argv[1], sys.argv[1:])
        return

{rules_code}
    if not _restrict_self(ruleset_fd):
        # Failed — exec without restrictions rather than crash
        pass

    os.execvp(sys.argv[1], sys.argv[1:])

if __name__ == "__main__":
    main()
'''


def landlock_bwrap_integration(
    profile: LandlockProfile,
    workspace: str,
    original_cmd: list[str],
    python_bin: str = "python3",
) -> list[str] | None:
    """Write Landlock wrapper to workspace and return modified command.

    The modified command runs the wrapper script first, which applies
    Landlock rules then exec's the original command.

    Returns None if Landlock is not supported.
    """
    if not has_landlock_support():
        return None

    wrapper_source = generate_landlock_wrapper(profile)
    wrapper_path = os.path.join(workspace, ".landlock_wrapper.py")

    try:
        with open(wrapper_path, "w") as f:
            f.write(wrapper_source)
        os.chmod(wrapper_path, 0o755)
    except OSError as e:
        logger.warning("landlock: failed to write wrapper: %s", e)
        return None

    sandbox_wrapper = "/workspace/.landlock_wrapper.py"
    return [python_bin, sandbox_wrapper] + original_cmd
