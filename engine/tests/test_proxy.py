"""Tests for trusted-zone HTTP CONNECT proxy."""

import asyncio
import os
import platform
import sys
from pathlib import Path

import pytest

from src.sandbox.policy import NetworkRule, ProxyPolicy
from src.sandbox.proxy import (
    TrustedZoneProxy,
    evaluate_policy,
    inject_credentials,
    match_host_pattern,
)


class TestHostPatternMatching:
    def test_wildcard_matches_all(self):
        assert match_host_pattern("*", "anything.com") is True

    def test_exact_match(self):
        assert match_host_pattern("api.stripe.com", "api.stripe.com") is True

    def test_exact_no_match(self):
        assert match_host_pattern("api.stripe.com", "api.github.com") is False

    def test_glob_subdomain(self):
        assert match_host_pattern("*.github.com", "api.github.com") is True

    def test_glob_no_match_root(self):
        assert match_host_pattern("*.github.com", "github.com") is False

    def test_case_insensitive(self):
        assert match_host_pattern("*.GitHub.COM", "api.github.com") is True

    def test_double_star(self):
        assert match_host_pattern("*.*.com", "deep.sub.com") is True


class TestPolicyEvaluation:
    def policy(self, rules, default="deny"):
        return ProxyPolicy(
            enabled=True,
            rules=[NetworkRule(**r) for r in rules],
            default_action=default,
        )

    def test_allow_rule_matches(self):
        policy = self.policy(
            [
                {"binary": "node", "host_pattern": "*.npmjs.org", "action": "allow"},
            ]
        )
        action, rule = evaluate_policy(policy, "node", "registry.npmjs.org")
        assert action == "allow"
        assert rule is not None

    def test_deny_rule_matches(self):
        policy = self.policy(
            [
                {"binary": "curl", "host_pattern": "*", "action": "deny"},
            ]
        )
        action, rule = evaluate_policy(policy, "curl", "evil.com")
        assert action == "deny"

    def test_default_deny_when_no_match(self):
        policy = self.policy(
            [
                {"binary": "node", "host_pattern": "*.npmjs.org", "action": "allow"},
            ],
            default="deny",
        )
        action, rule = evaluate_policy(policy, "curl", "evil.com")
        assert action == "deny"
        assert rule is None

    def test_default_allow_when_no_match(self):
        policy = self.policy([], default="allow")
        action, rule = evaluate_policy(policy, "node", "anything.com")
        assert action == "allow"
        assert rule is None

    def test_first_match_wins(self):
        policy = self.policy(
            [
                {"binary": "node", "host_pattern": "evil.com", "action": "deny"},
                {"binary": "node", "host_pattern": "*", "action": "allow"},
            ]
        )
        action, _ = evaluate_policy(policy, "node", "evil.com")
        assert action == "deny"

    def test_wildcard_binary_matches_any(self):
        policy = self.policy(
            [
                {"binary": "*", "host_pattern": "pypi.org", "action": "allow"},
            ]
        )
        action, _ = evaluate_policy(policy, "pip", "pypi.org")
        assert action == "allow"

    def test_none_binary_only_matches_wildcard(self):
        policy = self.policy(
            [
                {"binary": "node", "host_pattern": "*", "action": "allow"},
            ],
            default="deny",
        )
        action, _ = evaluate_policy(policy, None, "anything.com")
        assert action == "deny"

    def test_inject_credentials_action(self):
        policy = self.policy(
            [
                {
                    "binary": "node",
                    "host_pattern": "api.stripe.com",
                    "action": "inject_credentials",
                    "credential_ref": "stripe_key",
                },
            ]
        )
        action, rule = evaluate_policy(policy, "node", "api.stripe.com")
        assert action == "inject_credentials"
        assert rule.credential_ref == "stripe_key"


class TestCredentialInjection:
    @pytest.mark.asyncio
    async def test_bearer_token(self):
        rule = NetworkRule(
            binary="node",
            host_pattern="api.stripe.com",
            action="inject_credentials",
            credential_ref="stripe",
        )

        async def loader(ref):
            return "sk_live_abc123"

        headers = await inject_credentials(rule, loader)
        assert headers is not None
        assert headers["Authorization"] == "Bearer sk_live_abc123"

    @pytest.mark.asyncio
    async def test_bearer_prefix_preserved(self):
        rule = NetworkRule(
            binary="node",
            host_pattern="api.openai.com",
            action="inject_credentials",
            credential_ref="openai",
        )

        async def loader(ref):
            return "Bearer sk-abc"

        headers = await inject_credentials(rule, loader)
        assert headers["Authorization"] == "Bearer sk-abc"

    @pytest.mark.asyncio
    async def test_no_credential_ref(self):
        rule = NetworkRule(binary="node", host_pattern="*", action="allow")
        headers = await inject_credentials(rule, lambda ref: None)
        assert headers is None

    @pytest.mark.asyncio
    async def test_loader_returns_none(self):
        rule = NetworkRule(
            binary="node", host_pattern="*", action="inject_credentials", credential_ref="missing"
        )

        async def loader(ref):
            return None

        headers = await inject_credentials(rule, loader)
        assert headers is None

    @pytest.mark.asyncio
    async def test_loader_raises(self):
        rule = NetworkRule(
            binary="node", host_pattern="*", action="inject_credentials", credential_ref="broken"
        )

        async def loader(ref):
            raise RuntimeError("db error")

        headers = await inject_credentials(rule, loader)
        assert headers is None


class TestProxyServer:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        policy = ProxyPolicy(enabled=True, default_action="deny")
        proxy = TrustedZoneProxy(policy, listen_port=0)
        await proxy.start()
        assert proxy.port > 0
        await proxy.stop()

    @pytest.mark.asyncio
    async def test_deny_returns_403(self):
        policy = ProxyPolicy(enabled=True, default_action="deny")
        proxy = TrustedZoneProxy(policy, listen_port=0)
        await proxy.start()

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(b"CONNECT evil.com:443 HTTP/1.1\r\nHost: evil.com\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5)
            assert b"403" in response

            writer.close()
        finally:
            await proxy.stop()

    @pytest.mark.asyncio
    async def test_deny_creates_audit_entry(self):
        policy = ProxyPolicy(enabled=True, default_action="deny")
        proxy = TrustedZoneProxy(policy, listen_port=0)
        await proxy.start()

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(b"CONNECT blocked.com:443 HTTP/1.1\r\n\r\n")
            await writer.drain()
            await asyncio.wait_for(reader.read(4096), timeout=5)
            writer.close()

            await asyncio.sleep(0.1)
            assert len(proxy.audit_log) == 1
            assert proxy.audit_log[0].host == "blocked.com"
            assert proxy.audit_log[0].action == "deny"
        finally:
            await proxy.stop()

    @pytest.mark.asyncio
    async def test_bad_request_returns_400(self):
        policy = ProxyPolicy(enabled=True, default_action="allow")
        proxy = TrustedZoneProxy(policy, listen_port=0)
        await proxy.start()

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(b"GET / HTTP/1.1\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5)
            assert b"400" in response

            writer.close()
        finally:
            await proxy.stop()

    @pytest.mark.asyncio
    async def test_allow_with_rules(self):
        policy = ProxyPolicy(
            enabled=True,
            default_action="deny",
            rules=[NetworkRule(binary="*", host_pattern="*.example.com", action="allow")],
        )
        audit_entries = []
        proxy = TrustedZoneProxy(policy, listen_port=0, audit_callback=audit_entries.append)
        await proxy.start()

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(b"CONNECT api.example.com:443 HTTP/1.1\r\n\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.readline(), timeout=5)
            assert b"200" in response or b"502" in response

            writer.close()
        finally:
            await proxy.stop()

        assert len(audit_entries) >= 1
        assert audit_entries[0].action == "allow"


CONNECT_CLIENT = """
import socket, sys
s = socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=10)
s.sendall(b"CONNECT blocked.example:443 HTTP/1.1\\r\\n\\r\\n")
sys.stdout.write(s.recv(4096).decode("utf-8", "replace").splitlines()[0])
s.close()
"""


@pytest.mark.skipif(
    platform.system() != "Linux",
    reason="peer identity is resolved through /proc, which only exists on Linux",
)
class TestPerBinaryPolicy:
    """The binary dimension end to end: a real child process, a real CONNECT."""

    async def child_binary_name(self) -> str:
        """The binary name an outside observer sees for a child process.

        Ground truth for what the proxy must resolve, read straight from the
        kernel with `os.readlink` rather than through `process_identity`, so
        the assertions below are not circular. It is not always the basename
        of `sys.executable`: under CPU emulation the kernel execs the
        emulator, and the emulator is genuinely the process holding the
        socket, which is what a policy rule has to name.
        """
        probe = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys, time; print('ready', flush=True); time.sleep(30)",
            stdout=asyncio.subprocess.PIPE,
        )
        try:
            assert probe.stdout is not None
            await asyncio.wait_for(probe.stdout.readline(), timeout=30)
            return Path(os.readlink(f"/proc/{probe.pid}/exe")).name
        finally:
            probe.kill()
            await probe.wait()

    async def connect_from_subprocess(self, port: int) -> tuple[str, int]:
        """Issue a CONNECT from a child process. Returns (status line, child pid)."""
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            CONNECT_CLIENT,
            str(port),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        assert proc.returncode == 0, f"client failed: {stderr.decode()}"
        return stdout.decode(), proc.pid

    @pytest.mark.asyncio
    async def test_rule_naming_the_caller_binary_matches(self):
        binary = await self.child_binary_name()
        policy = ProxyPolicy(
            enabled=True,
            default_action="allow",
            rules=[NetworkRule(binary=binary, host_pattern="*", action="deny")],
        )
        proxy = TrustedZoneProxy(policy, listen_port=0)
        await proxy.start()
        try:
            status, child_pid = await self.connect_from_subprocess(proxy.port)
        finally:
            await proxy.stop()

        assert "403" in status
        assert len(proxy.audit_log) == 1
        entry = proxy.audit_log[0]
        assert entry.binary == binary
        assert entry.pid == child_pid
        assert entry.action == "deny"
        assert entry.rule_matched == f"{binary}:*"

    @pytest.mark.asyncio
    async def test_rule_naming_another_binary_does_not_match(self):
        binary = await self.child_binary_name()
        policy = ProxyPolicy(
            enabled=True,
            default_action="deny",
            rules=[NetworkRule(binary="some-other-binary", host_pattern="*", action="allow")],
        )
        proxy = TrustedZoneProxy(policy, listen_port=0)
        await proxy.start()
        try:
            status, _ = await self.connect_from_subprocess(proxy.port)
        finally:
            await proxy.stop()

        assert "403" in status
        assert proxy.audit_log[0].binary == binary
        assert proxy.audit_log[0].rule_matched == "default"
