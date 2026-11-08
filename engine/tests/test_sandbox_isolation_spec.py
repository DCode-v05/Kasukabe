"""Deterministic isolation + behaviour spec for the sandbox.

The agentic eval (eval_sandbox_agent.py) proves the sandbox is a usable agent
tool. This proves the security/operational PROPERTIES the agent eval can't:
containment (no egress, escape rejected), secret hygiene (trusted/untrusted
split), and operational behaviour (persistence, concurrency, timeouts).

Needs Linux + bwrap, so it runs inside the test container:
    docker compose run --rm test pytest tests/test_sandbox_isolation_spec.py -v

Seccomp runtime enforcement can't be exercised under QEMU (conftest drops the
BPF fd), so that one test is gated on SANDBOX_SECCOMP_ACTIVE=1 — set it when
running on native Linux (real x86_64, or arm64 with the fixed seccomp numbers).
"""

from __future__ import annotations

import asyncio
import os

import pytest

from src.sandbox.executor import SandboxExecutor, DEFAULT_MAX_OUTPUT
from src.sandbox.policy import SandboxPolicy

# Only meaningful where the inner sandbox actually runs.
probe = SandboxExecutor(session_id="isospec-probe")
HAS_BWRAP = probe.uses_bubblewrap
probe.cleanup()

pytestmark = pytest.mark.skipif(
    not HAS_BWRAP,
    reason="needs Linux + bwrap — run via `docker compose run test`",
)


def make_ex(session: str, **kw) -> SandboxExecutor:
    return SandboxExecutor(session_id=session, policy=SandboxPolicy.default(), **kw)


# ── Containment ───────────────────────────────────────────────────────


class TestContainment:
    @pytest.mark.asyncio
    async def test_no_network_egress_by_default(self):
        """Untrusted code with no granted network cannot reach the internet
        (bwrap --unshare-net). The container itself HAS internet, so a block
        here is the netns, not a missing route."""
        ex = make_ex("iso-egress")
        try:
            code = (
                "import socket\n"
                "try:\n"
                "    s = socket.create_connection(('1.1.1.1', 80), timeout=4)\n"
                "    s.close(); print('CONNECTED')\n"
                "except Exception as e:\n"
                "    print('BLOCKED', type(e).__name__)\n"
            )
            res = await ex.run_python(code, timeout=15)
            assert "CONNECTED" not in res.stdout, f"sandbox reached the network: {res.to_str()}"
            assert "BLOCKED" in res.stdout, res.to_str()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_network_command_denied_without_permission(self):
        """A command that needs network, with no permission callback wired,
        is refused (fail-closed) rather than silently allowed."""
        ex = make_ex("iso-netperm", permission_callback=None)
        try:
            res = await ex.run_bash("curl -s https://example.com", network=True, timeout=15)
            assert res.exit_code != 0
            assert "Network access denied" in res.stderr, res.to_str()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_workspace_escape_rejected(self):
        """Reads/writes outside the workspace are rejected — code is confined."""
        ex = make_ex("iso-escape")
        try:
            r_read = await ex.read_file("/etc/passwd")
            assert r_read.exit_code != 0 and "escape" in r_read.stderr.lower(), r_read.to_str()
            r_rel = await ex.read_file("../../../etc/passwd")
            assert r_rel.exit_code != 0, r_rel.to_str()
            r_write = await ex.write_file("../escape.txt", "nope")
            assert r_write.exit_code != 0 and "escape" in r_write.stderr.lower(), r_write.to_str()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        os.environ.get("SANDBOX_SECCOMP_ACTIVE") != "1",
        reason="seccomp BPF not active here (QEMU drops it); set SANDBOX_SECCOMP_ACTIVE=1 on native Linux",
    )
    async def test_seccomp_blocks_dangerous_syscall(self):
        """With seccomp active, a blocked syscall (unshare) fails with EPERM
        from inside the sandbox even though bwrap itself used it to get here."""
        ex = make_ex("iso-seccomp")
        try:
            # unshare(CLONE_NEWUSER=0x10000000) via libc; EPERM(1) when seccomp blocks it.
            code = (
                "import ctypes\n"
                "libc = ctypes.CDLL(None, use_errno=True)\n"
                "rc = libc.unshare(0x10000000)\n"
                "print('RC', rc, 'ERRNO', ctypes.get_errno())\n"
            )
            res = await ex.run_python(code, timeout=15)
            assert "RC 0" not in res.stdout, f"unshare was NOT blocked: {res.to_str()}"
            assert "ERRNO 1" in res.stdout, res.to_str()  # EPERM
        finally:
            ex.cleanup()


# ── Secret hygiene (trusted/untrusted split) ──────────────────────────


class TestSecretHygiene:
    @pytest.mark.asyncio
    async def test_engine_secret_not_visible_in_sandbox(self):
        """A secret-looking env var set in the trusted engine process must not
        leak into the untrusted sandbox env."""
        marker = "DETERMINISTIC_SECRET_TOKEN_a1b2c3d4e5"
        os.environ["AWS_SECRET_ACCESS_KEY"] = marker
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-" + marker
        try:
            ex = make_ex("iso-secret")
            try:
                res = await ex.run_bash(
                    "env; echo '---'; cat /proc/self/environ 2>/dev/null | tr '\\0' '\\n'",
                    timeout=15,
                )
                assert marker not in res.stdout, "secret leaked into the sandbox env!"
            finally:
                ex.cleanup()
        finally:
            os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
            os.environ.pop("ANTHROPIC_API_KEY", None)


# ── Operational behaviour: persistence, concurrency, timeout ──────────


class TestOperationalBehaviour:
    @pytest.mark.asyncio
    async def test_workspace_persists_across_execs(self):
        """One session = one workspace: files written by one exec are visible to
        the next (the 'long-lived box' behaviour, within a session)."""
        ex = make_ex("iso-persist")
        try:
            w = await ex.run_bash("echo persisted-value > marker.txt && echo ok")
            assert "ok" in w.stdout, w.to_str()
            r = await ex.run_bash("cat marker.txt")
            assert "persisted-value" in r.stdout, r.to_str()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_concurrent_execs_all_succeed(self):
        """Many threads share ONE sandbox: concurrent execs run cleanly."""
        ex = make_ex("iso-concurrent")
        try:
            results = await asyncio.gather(*(ex.run_bash(f"echo task-{i}") for i in range(6)))
            for i, r in enumerate(results):
                assert f"task-{i}" in r.stdout, f"task {i}: {r.to_str()}"
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_command_timeout_is_graceful(self):
        """A command that exceeds its timeout is killed and reported, not hung."""
        ex = make_ex("iso-timeout")
        try:
            res = await ex.run_bash("sleep 30", timeout=2)
            assert res.timed_out or res.exit_code != 0, res.to_str()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_file_edit_uniqueness_invariant(self):
        """file_edit refuses an ambiguous match and accepts a disambiguated one —
        so the agent never silently mutates the wrong site."""
        ex = make_ex("iso-edit")
        try:
            await ex.write_file("notes.txt", "TODO\nTODO\nTODO\n")
            ambiguous = await ex.edit_file("notes.txt", "TODO", "DONE")
            assert ambiguous.exit_code != 0 and "unique" in ambiguous.stderr.lower(), (
                ambiguous.to_str()
            )
            disambiguated = await ex.edit_file("notes.txt", "TODO\nTODO\nTODO", "TODO\nDONE\nTODO")
            assert disambiguated.exit_code == 0, disambiguated.to_str()
            back = await ex.read_file("notes.txt")
            assert back.stdout.count("TODO") == 2 and "DONE" in back.stdout, back.to_str()
        finally:
            ex.cleanup()

    @pytest.mark.asyncio
    async def test_large_output_is_bounded_not_crashing(self):
        """Huge command output is truncated by to_str rather than dumped whole —
        the sandbox stays usable and context doesn't blow up."""
        ex = make_ex("iso-bigout")
        try:
            # ~30k lines would be ~330KB; the executor must bound what it captures.
            res = await ex.run_bash("for i in $(seq 1 30000); do echo line-$i; done", timeout=30)
            assert "line-1\n" in res.stdout  # produced output
            assert len(res.stdout) <= DEFAULT_MAX_OUTPUT  # bounded at capture
            assert "line-30000" not in res.stdout  # tail truncated, not all 30k kept
        finally:
            ex.cleanup()
