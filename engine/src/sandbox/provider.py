"""Sandbox provider — the isolation seam.

Lets the isolation primitive change (Docker now -> managed Firecracker -> owned
Firecracker) WITHOUT touching engine-core code. Engine code (bootstrap, threads)
depends only on this interface, never on SandboxExecutor directly.

Today's implementation is LocalBwrapProvider: it runs bwrap + seccomp + Landlock
inside the per-user Docker container. A future FirecrackerProvider / FlyProvider /
E2BProvider implements the same surface against a remote microVM, and engine code
is unchanged on the swap.

See architecture/sections/50_sandbox.md -> the isolation seam.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from src.config import SANDBOX_IDLE_TTL_DEFAULT_S
from src.sandbox.executor import SandboxExecutor, SandboxResult
from src.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)

DEFAULT_IDLE_TTL_S = SANDBOX_IDLE_TTL_DEFAULT_S
DEFAULT_SWEEP_INTERVAL_S = 600

PermissionCallback = Any


@runtime_checkable
class ExecBackend(Protocol):
    """The execution surface a provisioned sandbox session exposes.

    SandboxExecutor already satisfies this, so build_sandbox_tools(backend)
    accepts either a raw executor (tests) or a provider session backend.
    """

    async def run_bash(
        self,
        command: str,
        timeout: int = ...,
        background: bool = ...,
        yield_ms: int = ...,
        pty: bool = ...,
        network: bool = ...,
    ) -> SandboxResult: ...

    async def run_python(self, code: str, timeout: int = ...) -> SandboxResult: ...

    async def read_file(self, path: str) -> SandboxResult: ...

    async def write_file(self, path: str, content: str) -> SandboxResult: ...

    async def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = ...,
    ) -> SandboxResult: ...

    async def list_directory(self, path: str) -> SandboxResult: ...

    async def search_content(self, pattern: str, path: str) -> SandboxResult: ...

    def cleanup(self) -> None: ...

    @property
    def uses_bubblewrap(self) -> bool: ...

    async def kill_orphaned_children(self) -> int: ...

    def process_list(self) -> list[dict[str, Any]]: ...

    async def process_poll(self, session_id: str, timeout_ms: int = ...) -> dict[str, Any]: ...

    def process_log(
        self, session_id: str, offset: int = ..., limit: int = ...
    ) -> dict[str, Any]: ...

    async def process_kill(self, session_id: str) -> dict[str, Any]: ...

    async def process_write(self, session_id: str, data: str) -> dict[str, Any]: ...

    async def process_send_keys(self, session_id: str, keys: str) -> dict[str, Any]: ...

    def process_remove(self, session_id: str) -> dict[str, Any]: ...


@dataclass
class SandboxConfig:
    """How to provision one sandbox session."""

    session_id: str | None = None
    allow_network: bool = False
    policy: SandboxPolicy | None = None
    permission_callback: PermissionCallback = None


@dataclass
class SessionHandle:
    """A provisioned sandbox session. `backend` is what build_sandbox_tools binds to."""

    session_id: str
    backend: ExecBackend
    provider: "SandboxProvider"


class SandboxProvider(Protocol):
    """The isolation seam. Implementations: LocalBwrapProvider (now), Firecracker/Fly/E2B (later)."""

    name: str

    async def provision(self, config: SandboxConfig) -> SessionHandle: ...

    async def destroy(self, handle: SessionHandle) -> None: ...

    async def pause(self, handle: SessionHandle) -> None: ...

    async def resume(self, handle: SessionHandle) -> None: ...


class LocalBwrapProvider:
    """Docker-stage provider: bwrap + seccomp + Landlock inside the per-user container.

    The sandbox is the bwrap environment managed by SandboxExecutor. provision()
    builds one executor; exec/file ops go through its ExecBackend surface; destroy()
    tears it down. pause/resume are no-ops here — warm-pool/snapshot semantics belong
    to the Firecracker provider, never to engine core.
    """

    name = "local-bwrap"

    async def provision(self, config: SandboxConfig) -> SessionHandle:
        policy = config.policy or SandboxPolicy.default()
        executor = SandboxExecutor(
            session_id=config.session_id,
            policy=policy,
            permission_callback=config.permission_callback,
        )
        logger.info(
            "sandbox provisioned: provider=%s session=%s bwrap=%s allow_network=%s",
            self.name,
            executor.session_id,
            executor.uses_bubblewrap,
            config.allow_network,
        )
        return SessionHandle(
            session_id=executor.session_id,
            backend=executor,
            provider=self,
        )

    async def destroy(self, handle: SessionHandle) -> None:
        try:
            handle.backend.cleanup()
        except Exception as e:
            logger.warning("sandbox destroy failed for session=%s: %s", handle.session_id, e)

    async def pause(self, handle: SessionHandle) -> None:
        return None

    async def resume(self, handle: SessionHandle) -> None:
        return None


class SandboxSessionManager:
    """Owns the sandbox SESSION lifetime — ephemeral, like Codex.

    The TASK state is durable elsewhere (SQLite/data). The sandbox is NOT: after
    `idle_ttl_s` of inactivity the session is reaped via `provider.destroy()`,
    which discards the ephemeral workspace and kills its processes. The next use
    lazily re-provisions a FRESH session via `provider.provision()`. Any activity
    resets the idle clock.

    Implements ExecBackend, so `build_sandbox_tools(manager)` binds the agent's
    tools to the manager — they keep working transparently across reap/re-provision.
    The reap/re-provision goes through the SandboxProvider seam, so at the
    Firecracker stage the same flow destroys/restores a microVM with no engine change.
    """

    name = "session-manager"

    def __init__(
        self,
        provider: "SandboxProvider",
        config: SandboxConfig,
        session: SessionHandle | None = None,
        idle_ttl_s: int = DEFAULT_IDLE_TTL_S,
        sweep_interval_s: int = DEFAULT_SWEEP_INTERVAL_S,
    ) -> None:
        self.provider = provider
        self.config = config
        self.session = session
        self.idle_ttl = idle_ttl_s
        self.sweep = sweep_interval_s
        self.last_used = time.monotonic()
        self.lock = asyncio.Lock()
        self.reaper: asyncio.Task | None = None
        self.stopped = False

    async def ensure(self) -> ExecBackend:
        """Return the live backend, provisioning a fresh session if reaped/absent."""
        async with self.lock:
            if self.session is None:
                self.session = await self.provider.provision(self.config)
                logger.info(
                    "sandbox re-provisioned after reap: session=%s", self.session.session_id
                )
            self.last_used = time.monotonic()
            return self.session.backend

    def live(self) -> ExecBackend | None:
        return self.session.backend if self.session else None

    @property
    def session_id(self) -> str | None:
        return self.session.session_id if self.session else None

    @property
    def uses_bubblewrap(self) -> bool:
        be = self.live()
        return bool(be.uses_bubblewrap) if be is not None else False

    @property
    def is_busy(self) -> bool:
        be = self.live()
        return bool(getattr(be, "is_busy", False)) if be is not None else False

    async def run_bash(self, *a, **k) -> SandboxResult:
        return await (await self.ensure()).run_bash(*a, **k)

    async def run_python(self, *a, **k) -> SandboxResult:
        return await (await self.ensure()).run_python(*a, **k)

    async def read_file(self, *a, **k) -> SandboxResult:
        return await (await self.ensure()).read_file(*a, **k)

    async def write_file(self, *a, **k) -> SandboxResult:
        return await (await self.ensure()).write_file(*a, **k)

    async def edit_file(self, *a, **k) -> SandboxResult:
        return await (await self.ensure()).edit_file(*a, **k)

    async def list_directory(self, *a, **k) -> SandboxResult:
        return await (await self.ensure()).list_directory(*a, **k)

    async def search_content(self, *a, **k) -> SandboxResult:
        return await (await self.ensure()).search_content(*a, **k)

    async def kill_orphaned_children(self, *a, **k):
        be = self.live()
        if be is not None and hasattr(be, "kill_orphaned_children"):
            return await be.kill_orphaned_children(*a, **k)

    def process_list(self):
        be = self.live()
        return be.process_list() if be is not None else []

    async def process_poll(self, *a, **k):
        be = self.live()
        return await be.process_poll(*a, **k) if be is not None else {"error": "no active sandbox"}

    def process_log(self, *a, **k):
        be = self.live()
        return be.process_log(*a, **k) if be is not None else {"error": "no active sandbox"}

    async def process_kill(self, *a, **k):
        be = self.live()
        return await be.process_kill(*a, **k) if be is not None else {"error": "no active sandbox"}

    async def process_write(self, *a, **k):
        be = self.live()
        return await be.process_write(*a, **k) if be is not None else {"error": "no active sandbox"}

    async def process_send_keys(self, *a, **k):
        be = self.live()
        return (
            await be.process_send_keys(*a, **k)
            if be is not None
            else {"error": "no active sandbox"}
        )

    def process_remove(self, *a, **k):
        be = self.live()
        return be.process_remove(*a, **k) if be is not None else {"error": "no active sandbox"}

    def cleanup(self) -> None:
        be = self.live()
        if be is not None:
            be.cleanup()

    async def reap_if_idle(self) -> bool:
        """Destroy the session if idle past TTL and not busy. Returns True if reaped."""
        async with self.lock:
            if self.session is None:
                return False
            idle = time.monotonic() - self.last_used
            if idle < self.idle_ttl:
                return False
            if self.is_busy:
                logger.debug("sandbox idle %.0fs but busy — deferring reap", idle)
                return False
            session = self.session
            self.session = None
        await self.provider.destroy(session)
        logger.info("sandbox reaped after %.0fs idle: session=%s", idle, session.session_id)
        return True

    def start_reaper(self) -> None:
        if self.reaper is None:
            self.reaper = asyncio.create_task(self.reaper_loop())

    async def reaper_loop(self) -> None:
        try:
            while not self.stopped:
                await asyncio.sleep(self.sweep)
                try:
                    await self.reap_if_idle()
                except Exception as e:  # noqa: BLE001
                    logger.warning("sandbox reaper error: %s", e)
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        self.stopped = True
        if self.reaper is not None:
            self.reaper.cancel()
            try:
                await self.reaper
            except (asyncio.CancelledError, Exception):
                pass
            self.reaper = None
        if self.session is not None:
            await self.provider.destroy(self.session)
            self.session = None
