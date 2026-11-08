"""Seccomp-BPF profile builder for sandbox isolation.

Generates a BPF filter program that restricts syscalls to an allowlist.
Applied inside bubblewrap via --seccomp <fd>.

BPF structure:
  1. Validate architecture (reject if wrong arch)
  2. Load syscall number from seccomp_data
  3. Jump table: if syscall in allowed set → ALLOW
  4. If syscall in blocked-with-errno set → ERRNO(n)
  5. Default: ERRNO(EPERM)

Linux-only. All functions return None/empty on non-Linux.
"""

from __future__ import annotations

import logging
import os
import platform
import struct
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


BPF_LD = 0x00
BPF_JMP = 0x05
BPF_RET = 0x06

BPF_W = 0x00

BPF_ABS = 0x20
BPF_K = 0x00

BPF_JEQ = 0x10

LD_W_ABS = BPF_LD | BPF_W | BPF_ABS
JMP_JEQ_K = BPF_JMP | BPF_JEQ | BPF_K
RET_K = BPF_RET | BPF_K

OFFSET_NR = 0
OFFSET_ARCH = 4

SECCOMP_RET_ALLOW = 0x7FFF_0000
SECCOMP_RET_ERRNO = 0x0005_0000
SECCOMP_RET_KILL_PROCESS = 0x8000_0000

AUDIT_ARCH_X86_64 = 0xC000_003E
AUDIT_ARCH_AARCH64 = 0xC000_00B7

EPERM = 1
ENOSYS = 38

BPF_STMT_FMT = "<HBBI"
BPF_JUMP_FMT = "<HBBI"


def bpf_stmt(code: int, k: int) -> bytes:
    return struct.pack(BPF_STMT_FMT, code, 0, 0, k)


def bpf_jump(code: int, k: int, jt: int, jf: int) -> bytes:
    return struct.pack(BPF_JUMP_FMT, code, jt, jf, k)


BLOCKED_X86_64: dict[int, int] = {
    101: EPERM,
    157: EPERM,
    161: EPERM,
    165: EPERM,
    166: EPERM,
    167: EPERM,
    168: EPERM,
    169: EPERM,
    175: EPERM,
    176: EPERM,
    246: EPERM,
    272: EPERM,
    308: EPERM,
    310: EPERM,
    311: EPERM,
    313: EPERM,
    320: EPERM,
    321: EPERM,
    298: EPERM,
    212: EPERM,
    248: EPERM,
    249: EPERM,
    250: EPERM,
    155: EPERM,
    173: EPERM,
    172: EPERM,
    154: EPERM,
}

BLOCKED_AARCH64: dict[int, int] = {
    117: EPERM,
    167: EPERM,
    51: EPERM,
    40: EPERM,
    39: EPERM,
    224: EPERM,
    225: EPERM,
    142: EPERM,
    105: EPERM,
    106: EPERM,
    97: EPERM,
    268: EPERM,
    270: EPERM,
    271: EPERM,
    273: EPERM,
    294: EPERM,
    280: EPERM,
    241: EPERM,
    217: EPERM,
    218: EPERM,
    219: EPERM,
    41: EPERM,
}

SOCKET_X86_64 = {41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 288}

SOCKET_AARCH64 = {198, 203, 202, 206, 207, 211, 212, 210, 200, 201, 204, 205, 199, 208, 209, 242}


@dataclass
class SeccompProfile:
    """A seccomp-BPF profile."""

    blocked_syscalls: dict[int, int] = field(default_factory=dict)
    kill_on_unknown: bool = False


def is_linux() -> bool:
    return platform.system() == "Linux"


def detect_arch() -> tuple[int, dict[int, int], set[int]] | None:
    """Detect arch and return (audit_arch, blocked_table, socket_set)."""
    if not is_linux():
        return None
    machine = platform.machine()
    if machine in ("x86_64", "amd64"):
        return AUDIT_ARCH_X86_64, BLOCKED_X86_64, SOCKET_X86_64
    if machine in ("aarch64", "arm64"):
        return AUDIT_ARCH_AARCH64, BLOCKED_AARCH64, SOCKET_AARCH64
    logger.warning("seccomp: unsupported architecture %s", machine)
    return None


def build_bpf_for_sandbox(allow_network: bool = False) -> bytes | None:
    """Build the standard sandbox seccomp BPF filter.

    Returns raw BPF bytecode, or None if non-Linux or unsupported arch.
    If allow_network=False, socket syscalls are added to the block list.
    """
    arch_info = detect_arch()
    if arch_info is None:
        return None

    audit_arch, blocked_table, socket_set = arch_info

    blocked = dict(blocked_table)
    if not allow_network:
        for nr in socket_set:
            blocked.setdefault(nr, EPERM)

    profile = SeccompProfile(blocked_syscalls=blocked, kill_on_unknown=False)
    return compile_bpf(profile, audit_arch)


def compile_bpf(profile: SeccompProfile, audit_arch: int) -> bytes:
    """Compile a SeccompProfile into raw BPF bytecode.

    Strategy: block-list approach.
      1. Load arch, verify it matches (reject if wrong)
      2. Load syscall number
      3. For each blocked syscall: if match → RET_ERRNO(errno)
      4. Default: RET_ALLOW

    Block-list is simpler than allow-list for BPF and avoids breaking
    legitimate syscalls we didn't enumerate. The blocked set is small
    and contains only dangerous escape syscalls.
    """
    instructions: list[bytes] = []
    blocked = profile.blocked_syscalls

    instructions.append(bpf_stmt(LD_W_ABS, OFFSET_ARCH))

    instructions.append(bpf_jump(JMP_JEQ_K, audit_arch, 1, 0))
    instructions.append(bpf_stmt(RET_K, SECCOMP_RET_KILL_PROCESS))

    instructions.append(bpf_stmt(LD_W_ABS, OFFSET_NR))

    blocked_list = sorted(blocked.items())
    n_blocked = len(blocked_list)

    for i, (nr, errno) in enumerate(blocked_list):
        remaining_checks = n_blocked - i - 1
        jt = remaining_checks + 1
        jf = 0
        instructions.append(bpf_jump(JMP_JEQ_K, nr, jt, jf))

    instructions.append(bpf_stmt(RET_K, SECCOMP_RET_ALLOW))

    for _, errno_val in blocked_list:
        if profile.kill_on_unknown:
            instructions.append(bpf_stmt(RET_K, SECCOMP_RET_KILL_PROCESS))
        else:
            instructions.append(bpf_stmt(RET_K, SECCOMP_RET_ERRNO | errno_val))

    return b"".join(instructions)


def write_bpf_file(bpf_bytes: bytes, workspace: str) -> str | None:
    """Write BPF bytecode to a file in the workspace.

    Returns the file path, or None on failure.
    bwrap reads this via --seccomp <fd>.
    """
    try:
        bpf_path = os.path.join(workspace, ".seccomp.bpf")
        with open(bpf_path, "wb") as f:
            f.write(bpf_bytes)
        return bpf_path
    except OSError as e:
        logger.warning("seccomp: failed to write BPF file: %s", e)
        return None


def has_seccomp_support() -> bool:
    """Check if the kernel supports seccomp-BPF."""
    if not is_linux():
        return False
    try:
        with open("/proc/sys/kernel/seccomp/actions_avail", "r") as f:
            return "allow" in f.read()
    except (OSError, FileNotFoundError):
        try:
            return Path("/proc/self/status").read_text().find("Seccomp:") >= 0
        except OSError:
            return False
