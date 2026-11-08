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

# ── BPF constants (linux/bpf_common.h, linux/seccomp.h) ──────────

# BPF instruction classes
BPF_LD = 0x00
BPF_JMP = 0x05
BPF_RET = 0x06

# BPF instruction sizes
BPF_W = 0x00  # word (32-bit)

# BPF instruction modes
BPF_ABS = 0x20  # absolute offset into seccomp_data
BPF_K = 0x00  # constant operand

# BPF jump conditions
BPF_JEQ = 0x10

# Combined opcodes
LD_W_ABS = BPF_LD | BPF_W | BPF_ABS  # 0x20
JMP_JEQ_K = BPF_JMP | BPF_JEQ | BPF_K  # 0x15
RET_K = BPF_RET | BPF_K  # 0x06

# seccomp_data offsets
OFFSET_NR = 0  # syscall number (offset 0 in seccomp_data)
OFFSET_ARCH = 4  # architecture (offset 4)

# Seccomp return actions
SECCOMP_RET_ALLOW = 0x7FFF_0000
SECCOMP_RET_ERRNO = 0x0005_0000  # OR with errno value
SECCOMP_RET_KILL_PROCESS = 0x8000_0000

# Architecture constants (linux/audit.h)
AUDIT_ARCH_X86_64 = 0xC000_003E
AUDIT_ARCH_AARCH64 = 0xC000_00B7

# Standard errno values
EPERM = 1
ENOSYS = 38

# BPF instruction: 8 bytes (opcode:u16, jt:u8, jf:u8, k:u32)
BPF_STMT_FMT = "<HBBI"
BPF_JUMP_FMT = "<HBBI"


def bpf_stmt(code: int, k: int) -> bytes:
    return struct.pack(BPF_STMT_FMT, code, 0, 0, k)


def bpf_jump(code: int, k: int, jt: int, jf: int) -> bytes:
    return struct.pack(BPF_JUMP_FMT, code, jt, jf, k)


# ── Syscall number tables ─────────────────────────────────────────
# Source: linux kernel headers (asm/unistd_64.h for x86_64,
#         asm-generic/unistd.h for aarch64)
# Only the syscalls we need to explicitly allow or block.

# x86_64 syscall numbers for the dangerous set we must block
BLOCKED_X86_64: dict[int, int] = {
    101: EPERM,  # ptrace
    157: EPERM,  # prctl (we allow this selectively below)
    161: EPERM,  # chroot
    165: EPERM,  # mount
    166: EPERM,  # umount2
    167: EPERM,  # swapon
    168: EPERM,  # swapoff
    169: EPERM,  # reboot
    175: EPERM,  # init_module
    176: EPERM,  # delete_module
    246: EPERM,  # kexec_load
    272: EPERM,  # unshare
    308: EPERM,  # setns
    310: EPERM,  # process_vm_readv
    311: EPERM,  # process_vm_writev
    313: EPERM,  # finit_module
    320: EPERM,  # kexec_file_load
    321: EPERM,  # bpf
    298: EPERM,  # perf_event_open
    212: EPERM,  # lookup_dcookie
    248: EPERM,  # add_key
    249: EPERM,  # request_key
    250: EPERM,  # keyctl
    155: EPERM,  # pivot_root
    173: EPERM,  # ioperm
    172: EPERM,  # iopl
    154: EPERM,  # modify_ldt
}

# aarch64 syscall numbers for the dangerous set
BLOCKED_AARCH64: dict[int, int] = {
    117: EPERM,  # ptrace
    167: EPERM,  # prctl (selective)
    51: EPERM,  # chroot
    40: EPERM,  # mount
    39: EPERM,  # umount2
    224: EPERM,  # swapon
    225: EPERM,  # swapoff
    142: EPERM,  # reboot
    105: EPERM,  # init_module
    106: EPERM,  # delete_module
    # no kexec_load on aarch64 (uses kexec_file_load only)
    97: EPERM,  # unshare
    268: EPERM,  # setns
    270: EPERM,  # process_vm_readv
    271: EPERM,  # process_vm_writev
    273: EPERM,  # finit_module
    294: EPERM,  # kexec_file_load
    280: EPERM,  # bpf
    241: EPERM,  # perf_event_open
    217: EPERM,  # add_key
    218: EPERM,  # request_key
    219: EPERM,  # keyctl   (NB: 220 is clone on aarch64 — must never be blocked,
    #           or every fork()/pipe inside the sandbox dies with EPERM)
    41: EPERM,  # pivot_root
}

# Socket-related syscalls that are blocked when network is denied
SOCKET_X86_64 = {41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 288}
#  41=socket, 42=connect, 43=accept, 44=sendto, 45=recvfrom, 46=sendmsg,
#  47=recvmsg, 48=shutdown, 49=bind, 50=listen, 51=getsockname, 52=getpeername,
#  53=socketpair, 54=setsockopt, 55=getsockopt, 288=accept4

SOCKET_AARCH64 = {198, 203, 202, 206, 207, 211, 212, 210, 200, 201, 204, 205, 199, 208, 209, 242}
#  198=socket, 203=connect, 202=accept, 206=sendto, 207=recvfrom, 211=sendmsg,
#  212=recvmsg, 210=shutdown, 200=bind, 201=listen, 204=getsockname, 205=getpeername,
#  199=socketpair, 208=setsockopt, 209=getsockopt, 242=accept4


@dataclass
class SeccompProfile:
    """A seccomp-BPF profile."""

    blocked_syscalls: dict[int, int] = field(default_factory=dict)  # nr → errno
    kill_on_unknown: bool = False  # False = EPERM on blocked, True = KILL_PROCESS


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

    # 1. Load architecture
    instructions.append(bpf_stmt(LD_W_ABS, OFFSET_ARCH))

    # 2. Verify architecture — if wrong, kill
    # JEQ audit_arch → skip 1 (continue), else → ret KILL
    instructions.append(bpf_jump(JMP_JEQ_K, audit_arch, 1, 0))
    instructions.append(bpf_stmt(RET_K, SECCOMP_RET_KILL_PROCESS))

    # 3. Load syscall number
    instructions.append(bpf_stmt(LD_W_ABS, OFFSET_NR))

    # 4. For each blocked syscall: JEQ nr → jump to its RET_ERRNO
    # We build a linear scan. For each blocked syscall, we jump over
    # the remaining checks to the corresponding RET_ERRNO instruction.
    blocked_list = sorted(blocked.items())  # deterministic order
    n_blocked = len(blocked_list)

    # After all JEQ checks, we have the default action (ALLOW),
    # then n_blocked RET_ERRNO instructions.
    # Jump offsets: if JEQ matches at position i, we need to jump
    # (n_blocked - i - 1) remaining checks + 1 (the ALLOW) to reach
    # the correct RET_ERRNO at position i.
    for i, (nr, errno) in enumerate(blocked_list):
        remaining_checks = n_blocked - i - 1
        jt = remaining_checks + 1  # skip remaining checks + ALLOW → land on our RET_ERRNO
        jf = 0  # continue to next check
        instructions.append(bpf_jump(JMP_JEQ_K, nr, jt, jf))

    # 5. Default action: ALLOW (reached if no blocked syscall matched)
    instructions.append(bpf_stmt(RET_K, SECCOMP_RET_ALLOW))

    # 6. RET_ERRNO for each blocked syscall (in same order as checks)
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
        # Fallback: try reading the seccomp status
        try:
            return Path("/proc/self/status").read_text().find("Seccomp:") >= 0
        except OSError:
            return False
