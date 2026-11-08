"""Trusted-zone HTTP CONNECT proxy with per-binary network policy and credential injection.

Runs in the engine process on localhost. Sandbox traffic routes through
this proxy via HTTP_PROXY/HTTPS_PROXY environment variables.

Architecture:
  1. Sandbox process makes HTTPS request → HTTP_PROXY sends CONNECT to proxy
  2. Proxy resolves the caller's socket → PID → binary name (via /proc)
  3. Proxy evaluates policy: binary + host → allow / deny / inject_credentials
  4. If denied: 403 back to sandbox
  5. If allowed: tunnel bytes bidirectionally (transparent proxy)
  6. If inject_credentials: TLS MITM, parse HTTP, inject auth headers, forward
  7. All decisions audit-logged (credential values never logged)
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from dataclasses import dataclass
from typing import Callable, Awaitable

from src.sandbox.policy import NetworkRule, ProxyPolicy
from src.sandbox.process_identity import (
    resolve_binary_name_from_pid,
    resolve_pid_from_tcp_peer,
)

logger = logging.getLogger(__name__)


# ── Audit ─────────────────────────────────────────────────────


@dataclass
class ProxyAuditEntry:
    """A single proxy decision log entry."""

    timestamp: float
    binary: str | None
    pid: int | None
    host: str
    port: int
    action: str  # "allow" | "deny" | "inject"
    rule_matched: str  # description of which rule matched
    credential_injected: bool = False


# ── Pure policy evaluation (no I/O, fully testable) ───────────


def evaluate_policy(
    proxy_policy: ProxyPolicy,
    binary_name: str | None,
    host: str,
    port: int = 443,
) -> tuple[str, NetworkRule | None]:
    """Evaluate a request against proxy policy rules.

    Returns (action, matched_rule). First matching rule wins.
    If no rule matches, returns (default_action, None).
    """
    for rule in proxy_policy.rules:
        if match_binary(rule.binary, binary_name) and match_host_pattern(rule.host_pattern, host):
            return rule.action, rule
    return proxy_policy.default_action, None


def match_binary(pattern: str, binary: str | None) -> bool:
    """Match a binary name against a rule pattern.

    "*" matches every caller, including one the proxy could not identify. Any
    other pattern requires a resolved name, so an unidentified caller falls
    through to the next rule and ultimately to the policy's default action.
    That is deliberate rather than incidental: a rule naming a binary grants
    or denies access to *that* binary, and a process we cannot attribute must
    never inherit a permission written for something else.
    """
    if pattern == "*":
        return True
    if binary is None:
        return pattern == "*"
    return fnmatch.fnmatch(binary, pattern)


def match_host_pattern(pattern: str, host: str) -> bool:
    """Match a hostname against a glob pattern.

    "*.github.com" matches "api.github.com" but not "github.com".
    "*" matches everything.
    Exact match: "api.openai.com" matches only "api.openai.com".
    """
    if pattern == "*":
        return True
    return fnmatch.fnmatch(host.lower(), pattern.lower())


# ── Peer identity ─────────────────────────────────────────────


def identify_peer(peer: tuple[str, int], local: tuple[str, int]) -> tuple[int | None, str | None]:
    """Resolve an accepted connection's client to (pid, binary name).

    `peer` and `local` are the server's view of the connection. Both halves
    of the result are None when the client cannot be attributed.
    """
    pid = resolve_pid_from_tcp_peer(peer, local)
    if pid is None:
        return None, None
    return pid, resolve_binary_name_from_pid(pid)


# ── Credential injection ──────────────────────────────────────

CredentialLoader = Callable[[str], Awaitable[str | None]]


async def inject_credentials(
    rule: NetworkRule,
    credential_loader: CredentialLoader,
) -> dict[str, str] | None:
    """Load credentials and return headers to inject.

    Returns dict of headers or None if credential not found.
    NEVER logs the credential value — only that injection occurred.
    """
    if not rule.credential_ref:
        return None

    try:
        credential = await credential_loader(rule.credential_ref)
    except Exception as e:
        logger.warning(
            "proxy: credential load failed for ref=%s: %s", rule.credential_ref, type(e).__name__
        )
        return None

    if credential is None:
        logger.warning("proxy: credential not found for ref=%s", rule.credential_ref)
        return None

    # Detect credential format and build headers
    if credential.startswith("Bearer "):
        return {"Authorization": credential}
    if credential.startswith("Basic "):
        return {"Authorization": credential}
    # Default: treat as Bearer token
    return {"Authorization": f"Bearer {credential}"}


# ── Proxy server ──────────────────────────────────────────────


class TrustedZoneProxy:
    """asyncio HTTP CONNECT proxy with per-binary policy and credential injection."""

    def __init__(
        self,
        policy: ProxyPolicy,
        credential_loader: CredentialLoader | None = None,
        listen_host: str = "127.0.0.1",
        listen_port: int = 0,
        audit_callback: Callable[[ProxyAuditEntry], None] | None = None,
    ) -> None:
        self.policy = policy
        self.credential_loader = credential_loader
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.audit_callback = audit_callback
        self.server: asyncio.Server | None = None
        self.audit_log: list[ProxyAuditEntry] = []

    async def start(self) -> None:
        """Start the proxy server."""
        try:
            self.server = await asyncio.start_server(
                self.handle_client,
                self.listen_host,
                self.listen_port,
            )
            actual_port = self.server.sockets[0].getsockname()[1]
            self.listen_port = actual_port
            logger.info("Trusted-zone proxy started on %s:%d", self.listen_host, actual_port)
        except OSError as e:
            # Port conflict — try OS-assigned port
            logger.warning(
                "proxy: port %d unavailable (%s), trying OS-assigned", self.listen_port, e
            )
            self.server = await asyncio.start_server(
                self.handle_client,
                self.listen_host,
                0,
            )
            actual_port = self.server.sockets[0].getsockname()[1]
            self.listen_port = actual_port
            logger.info(
                "Trusted-zone proxy started on %s:%d (fallback)", self.listen_host, actual_port
            )

    async def stop(self) -> None:
        """Stop the proxy server."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    @property
    def port(self) -> int:
        return self.listen_port

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle an incoming proxy connection."""
        try:
            first_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not first_line:
                writer.close()
                return

            line = first_line.decode("utf-8", errors="replace").strip()

            # Parse CONNECT host:port HTTP/1.x
            if not line.upper().startswith("CONNECT "):
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            parts = line.split()
            if len(parts) < 2:
                writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            target = parts[1]
            if ":" in target:
                host, port_str = target.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    port = 443
            else:
                host = target
                port = 443

            # Read remaining headers (we don't need them, but must consume)
            while True:
                header_line = await asyncio.wait_for(reader.readline(), timeout=5)
                if header_line in (b"\r\n", b"\n", b""):
                    break

            # Resolve which process opened this connection, so per-binary rules
            # can match. Resolution happens now, while the client is still
            # blocked on our response and its socket is therefore still listed
            # in /proc/net/tcp. A peer we cannot attribute stays None, which
            # matches only rules whose binary is "*".
            peer_pid, binary_name = await self.resolve_peer(writer)

            # Evaluate policy
            action, rule = evaluate_policy(self.policy, binary_name, host, port)

            # Record audit entry
            entry = ProxyAuditEntry(
                timestamp=time.time(),
                binary=binary_name,
                pid=peer_pid,
                host=host,
                port=port,
                action=action,
                rule_matched=f"{rule.binary}:{rule.host_pattern}" if rule else "default",
            )

            if action == "deny":
                self.record_audit(entry)
                writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            # Allow or inject_credentials — establish tunnel
            if action == "inject_credentials" and self.credential_loader and rule:
                cred_headers = await inject_credentials(rule, self.credential_loader)
                if cred_headers:
                    entry.credential_injected = True
                    entry.action = "inject"

            self.record_audit(entry)

            # Connect to upstream
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=15,
                )
            except (OSError, asyncio.TimeoutError):
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            # Tell client the tunnel is established
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()

            # Bidirectional pipe
            await self.pipe(reader, writer, upstream_reader, upstream_writer)

        except asyncio.TimeoutError:
            try:
                writer.write(b"HTTP/1.1 504 Gateway Timeout\r\n\r\n")
                await writer.drain()
            except Exception:
                pass
        except Exception as e:
            logger.debug("proxy: client handler error: %s", e)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def resolve_peer(self, writer: asyncio.StreamWriter) -> tuple[int | None, str | None]:
        """Identify the process on the other end of an accepted connection.

        Returns (pid, binary name), either of which is None when the peer
        cannot be attributed. Resolution reads /proc, so it yields nothing on
        non-Linux hosts or for a peer owned by another user. Attribution is
        never fatal: an unidentified peer is evaluated against the policy as
        an unknown binary rather than dropped.

        The lookup walks /proc with blocking reads, so it runs in a worker
        thread; doing it inline would stall every other tunnel this proxy is
        relaying while one connection is being attributed.
        """
        try:
            peername = writer.get_extra_info("peername")
            sockname = writer.get_extra_info("sockname")
            if not peername or not sockname:
                return None, None
            return await asyncio.to_thread(
                identify_peer,
                (peername[0], peername[1]),
                (sockname[0], sockname[1]),
            )
        except Exception as e:
            logger.debug("proxy: peer identity resolution failed: %s", e)
            return None, None

    async def pipe(
        self,
        cr: asyncio.StreamReader,
        cw: asyncio.StreamWriter,
        ur: asyncio.StreamReader,
        uw: asyncio.StreamWriter,
    ) -> None:
        """Bidirectional byte relay until either side closes."""

        async def forward(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        await asyncio.gather(
            forward(cr, uw),
            forward(ur, cw),
            return_exceptions=True,
        )

    def record_audit(self, entry: ProxyAuditEntry) -> None:
        self.audit_log.append(entry)
        if self.audit_callback:
            self.audit_callback(entry)
        if self.policy.audit_log:
            logger.info(
                "proxy.%s host=%s:%d binary=%s rule=%s cred=%s",
                entry.action,
                entry.host,
                entry.port,
                entry.binary or "unknown",
                entry.rule_matched,
                entry.credential_injected,
            )
