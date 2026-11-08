"""Tests for seccomp-BPF profile builder."""

import struct
from unittest.mock import patch


from src.sandbox.seccomp import (
    AUDIT_ARCH_X86_64,
    AUDIT_ARCH_AARCH64,
    SECCOMP_RET_ALLOW,
    SECCOMP_RET_ERRNO,
    SECCOMP_RET_KILL_PROCESS,
    EPERM,
    LD_W_ABS,
    JMP_JEQ_K,
    RET_K,
    OFFSET_ARCH,
    OFFSET_NR,
    SeccompProfile,
    compile_bpf,
    build_bpf_for_sandbox,
    has_seccomp_support,
    write_bpf_file,
    BLOCKED_X86_64,
    BLOCKED_AARCH64,
    SOCKET_X86_64,
)

BPF_INSN_SIZE = 8


def decode_insn(data: bytes, index: int) -> tuple[int, int, int, int]:
    """Decode a BPF instruction at the given index."""
    offset = index * BPF_INSN_SIZE
    return struct.unpack("<HBBI", data[offset : offset + BPF_INSN_SIZE])


class TestBPFStructure:
    """Test that compiled BPF has correct structure."""

    def test_empty_blocklist_produces_minimal_filter(self):
        profile = SeccompProfile(blocked_syscalls={})
        bpf = compile_bpf(profile, AUDIT_ARCH_X86_64)
        assert len(bpf) == 5 * BPF_INSN_SIZE

    def test_first_instruction_loads_arch(self):
        profile = SeccompProfile(blocked_syscalls={101: EPERM})
        bpf = compile_bpf(profile, AUDIT_ARCH_X86_64)
        code, jt, jf, k = decode_insn(bpf, 0)
        assert code == LD_W_ABS
        assert k == OFFSET_ARCH

    def test_second_instruction_checks_arch(self):
        profile = SeccompProfile(blocked_syscalls={101: EPERM})
        bpf = compile_bpf(profile, AUDIT_ARCH_X86_64)
        code, jt, jf, k = decode_insn(bpf, 1)
        assert code == JMP_JEQ_K
        assert k == AUDIT_ARCH_X86_64
        assert jt == 1
        assert jf == 0

    def test_wrong_arch_kills_process(self):
        profile = SeccompProfile(blocked_syscalls={101: EPERM})
        bpf = compile_bpf(profile, AUDIT_ARCH_X86_64)
        code, jt, jf, k = decode_insn(bpf, 2)
        assert code == RET_K
        assert k == SECCOMP_RET_KILL_PROCESS

    def test_third_instruction_loads_syscall_nr(self):
        profile = SeccompProfile(blocked_syscalls={101: EPERM})
        bpf = compile_bpf(profile, AUDIT_ARCH_X86_64)
        code, jt, jf, k = decode_insn(bpf, 3)
        assert code == LD_W_ABS
        assert k == OFFSET_NR

    def test_blocked_syscall_returns_errno(self):
        profile = SeccompProfile(blocked_syscalls={101: EPERM})
        bpf = compile_bpf(profile, AUDIT_ARCH_X86_64)
        n_insns = len(bpf) // BPF_INSN_SIZE
        assert n_insns == 7
        code, jt, jf, k = decode_insn(bpf, 6)
        assert code == RET_K
        assert k == (SECCOMP_RET_ERRNO | EPERM)

    def test_default_action_is_allow(self):
        profile = SeccompProfile(blocked_syscalls={101: EPERM})
        bpf = compile_bpf(profile, AUDIT_ARCH_X86_64)
        code, jt, jf, k = decode_insn(bpf, 5)
        assert code == RET_K
        assert k == SECCOMP_RET_ALLOW

    def test_multiple_blocked_syscalls(self):
        blocked = {101: EPERM, 165: EPERM, 169: EPERM}
        profile = SeccompProfile(blocked_syscalls=blocked)
        bpf = compile_bpf(profile, AUDIT_ARCH_X86_64)
        assert len(bpf) == 11 * BPF_INSN_SIZE

    def test_aarch64_architecture(self):
        profile = SeccompProfile(blocked_syscalls={117: EPERM})
        bpf = compile_bpf(profile, AUDIT_ARCH_AARCH64)
        code, jt, jf, k = decode_insn(bpf, 1)
        assert k == AUDIT_ARCH_AARCH64


class TestDangerousSyscalls:
    """Verify dangerous syscalls are in the block tables."""

    def test_ptrace_blocked_x86_64(self):
        assert 101 in BLOCKED_X86_64

    def test_mount_blocked_x86_64(self):
        assert 165 in BLOCKED_X86_64

    def test_unshare_blocked_x86_64(self):
        assert 272 in BLOCKED_X86_64

    def test_bpf_blocked_x86_64(self):
        assert 321 in BLOCKED_X86_64

    def test_kexec_blocked_x86_64(self):
        assert 246 in BLOCKED_X86_64

    def test_pivot_root_blocked_x86_64(self):
        assert 155 in BLOCKED_X86_64

    def test_init_module_blocked_x86_64(self):
        assert 175 in BLOCKED_X86_64

    def test_setns_blocked_x86_64(self):
        assert 308 in BLOCKED_X86_64

    def test_ptrace_blocked_aarch64(self):
        assert 117 in BLOCKED_AARCH64

    def test_mount_blocked_aarch64(self):
        assert 40 in BLOCKED_AARCH64

    def test_bpf_blocked_aarch64(self):
        assert 280 in BLOCKED_AARCH64

    def test_keyctl_group_aarch64_numbers(self):
        assert 217 in BLOCKED_AARCH64
        assert 218 in BLOCKED_AARCH64
        assert 219 in BLOCKED_AARCH64
        assert 220 not in BLOCKED_AARCH64

    def test_execution_syscalls_never_blocked(self):
        """Regression guard: blocking clone/execve/exit breaks every sandboxed
        process. Guard both arch tables against such a collision (name, x86_64, aarch64)."""
        critical = [
            ("clone", 56, 220),
            ("clone3", 435, 435),
            ("execve", 59, 221),
            ("execveat", 322, 281),
            ("exit", 60, 93),
            ("exit_group", 231, 94),
        ]
        for name, x86, arm in critical:
            assert x86 not in BLOCKED_X86_64, f"{name} ({x86}) must not be blocked on x86_64"
            assert arm not in BLOCKED_AARCH64, f"{name} ({arm}) must not be blocked on aarch64"


class TestBuildBpfForSandbox:
    """Test the convenience builder."""

    @patch("src.sandbox.seccomp.is_linux", return_value=False)
    def test_returns_none_on_non_linux(self, _):
        assert build_bpf_for_sandbox() is None

    @patch("src.sandbox.seccomp.detect_arch", return_value=None)
    def test_returns_none_on_unsupported_arch(self, _):
        assert build_bpf_for_sandbox() is None

    @patch("src.sandbox.seccomp.detect_arch")
    def test_includes_socket_block_when_network_denied(self, mock_arch):
        mock_arch.return_value = (AUDIT_ARCH_X86_64, BLOCKED_X86_64, SOCKET_X86_64)
        bpf = build_bpf_for_sandbox(allow_network=False)
        assert bpf is not None
        bpf_with_net = build_bpf_for_sandbox(allow_network=True)
        assert len(bpf) > len(bpf_with_net)

    @patch("src.sandbox.seccomp.detect_arch")
    def test_excludes_socket_block_when_network_allowed(self, mock_arch):
        mock_arch.return_value = (AUDIT_ARCH_X86_64, BLOCKED_X86_64, SOCKET_X86_64)
        bpf = build_bpf_for_sandbox(allow_network=True)
        assert bpf is not None


class TestWriteBpfFile:
    """Test BPF file writing."""

    def test_writes_to_workspace(self, tmp_path):
        bpf = b"\x00" * 40
        path = write_bpf_file(bpf, str(tmp_path))
        assert path is not None
        assert path.endswith(".seccomp.bpf")
        with open(path, "rb") as f:
            assert f.read() == bpf

    def test_returns_none_on_bad_path(self):
        path = write_bpf_file(b"\x00", "/nonexistent/dir/that/does/not/exist")
        assert path is None


class TestHasSeccompSupport:
    """Test seccomp detection."""

    @patch("src.sandbox.seccomp.is_linux", return_value=False)
    def test_false_on_non_linux(self, _):
        assert has_seccomp_support() is False
