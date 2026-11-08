"""Tests for the sandbox provider seam + fail-closed execution.

These run cross-platform (no bwrap required): the fail-closed tests force
use_bwrap=False, and the provider tests only provision/destroy (no exec).
"""

from __future__ import annotations

import asyncio

import pytest

from src.sandbox.executor import SandboxExecutor
from src.sandbox.provider import (
    ExecBackend,
    LocalBwrapProvider,
    SandboxConfig,
    SessionHandle,
    SandboxSessionManager,
)
from src.sandbox.tools import build_sandbox_tools


EXPECTED_TOOLS = {
    "bash",
    "python",
    "read_file",
    "write_file",
    "file_edit",
    "list_directory",
    "search_files",
    "process",
}


@pytest.mark.asyncio
async def test_provision_returns_session_handle():
    provider = LocalBwrapProvider()
    assert provider.name == "local-bwrap"
    handle = await provider.provision(SandboxConfig(session_id="prov-1"))
    try:
        assert isinstance(handle, SessionHandle)
        assert handle.session_id
        assert handle.provider is provider
        # the backend is the provider-agnostic exec surface tools bind to
        assert isinstance(handle.backend, SandboxExecutor)
        assert isinstance(handle.backend, ExecBackend)  # runtime_checkable Protocol
    finally:
        await provider.destroy(handle)


@pytest.mark.asyncio
async def test_build_tools_from_provider_session():
    provider = LocalBwrapProvider()
    handle = await provider.provision(SandboxConfig(session_id="prov-2"))
    try:
        tools = {t.name: t for t in build_sandbox_tools(handle.backend)}
        assert set(tools) == EXPECTED_TOOLS
        # the workspace-search tool must NOT collide with the content-store tool
        assert "search_content" not in tools
    finally:
        await provider.destroy(handle)


@pytest.mark.asyncio
async def test_destroy_removes_workspace():
    provider = LocalBwrapProvider()
    handle = await provider.provision(SandboxConfig(session_id="prov-3"))
    ws = handle.backend.workspace  # property creates the dir
    assert ws.exists()
    await provider.destroy(handle)
    assert not ws.exists()


@pytest.mark.asyncio
async def test_pause_resume_are_noops():
    provider = LocalBwrapProvider()
    handle = await provider.provision(SandboxConfig(session_id="prov-4"))
    try:
        assert await provider.pause(handle) is None
        assert await provider.resume(handle) is None
    finally:
        await provider.destroy(handle)


@pytest.mark.asyncio
async def test_run_bash_fail_closed_without_bwrap(tmp_path):
    """Code execution refuses to run unsandboxed — never falls back to the host."""
    ex = SandboxExecutor(session_id="failclosed-1")
    ex.use_bwrap = False  # simulate bwrap unavailable (prod seccomp block / non-Linux)
    sentinel = tmp_path / "should_not_exist"
    try:
        result = await ex.run_bash(f"echo escaped > {sentinel}")
        assert result.exit_code != 0
        assert "Refusing to run unsandboxed" in result.stderr
        assert not sentinel.exists()  # nothing actually ran
    finally:
        ex.cleanup()


@pytest.mark.asyncio
async def test_run_python_fail_closed_without_bwrap():
    ex = SandboxExecutor(session_id="failclosed-2")
    ex.use_bwrap = False
    try:
        result = await ex.run_python("print('escaped')")
        assert result.exit_code != 0
        assert "bubblewrap" in result.stderr.lower()
    finally:
        ex.cleanup()


def test_build_cmd_raises_without_bwrap():
    """Defense in depth: build_cmd never returns an unwrapped command."""
    ex = SandboxExecutor(session_id="failclosed-3")
    ex.use_bwrap = False
    try:
        with pytest.raises(RuntimeError, match="refusing to build an unsandboxed command"):
            ex.build_cmd(["echo", "hi"])
    finally:
        ex.cleanup()


# ── SandboxSessionManager: ephemeral lifetime (reap after idle, re-provision) ──
# Uses read_file/write_file only (no bwrap needed), so these run cross-platform.


async def new_manager(idle_ttl_s: float):
    provider = LocalBwrapProvider()
    cfg = SandboxConfig()
    session = await provider.provision(cfg)
    return SandboxSessionManager(
        provider, cfg, session, idle_ttl_s=idle_ttl_s, sweep_interval_s=9999
    )


@pytest.mark.asyncio
async def test_idle_reap_discards_workspace_and_reprovisions():
    mgr = await new_manager(idle_ttl_s=0.05)
    try:
        await mgr.write_file("marker.txt", "v1")
        assert "v1" in (await mgr.read_file("marker.txt")).stdout
        ws = mgr.session.backend.workspace
        assert ws.exists()

        await asyncio.sleep(0.1)  # go idle past TTL
        assert await mgr.reap_if_idle() is True
        assert mgr.session is None
        assert not ws.exists()  # workspace discarded (ephemeral)

        # next use re-provisions a FRESH, empty sandbox — the marker is gone
        again = await mgr.read_file("marker.txt")
        assert again.exit_code != 0
        assert mgr.session is not None
        assert mgr.session.backend.workspace != ws  # different workspace
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_activity_resets_idle_clock():
    mgr = await new_manager(idle_ttl_s=0.2)
    try:
        for _ in range(3):
            await mgr.write_file("a.txt", "x")  # activity → resets the clock
            await asyncio.sleep(0.1)  # < TTL since last use
            assert await mgr.reap_if_idle() is False  # still active → not reaped
        assert mgr.session is not None
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_not_reaped_while_busy():
    mgr = await new_manager(idle_ttl_s=0.0)  # "always idle" by time
    try:
        await mgr.write_file("a.txt", "x")
        # Force a busy backend: pretend a background process is live.
        mgr.session.backend.sessions["fake"] = object()
        assert mgr.is_busy is True
        assert await mgr.reap_if_idle() is False  # idle but busy → not reaped
        mgr.session.backend.sessions.clear()
        assert await mgr.reap_if_idle() is True  # now free → reaped
    finally:
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_shutdown_destroys_session():
    mgr = await new_manager(idle_ttl_s=9999)
    await mgr.write_file("x.txt", "y")
    ws = mgr.session.backend.workspace
    assert ws.exists()
    await mgr.shutdown()
    assert mgr.session is None
    assert not ws.exists()


@pytest.mark.asyncio
async def test_manager_is_an_exec_backend_for_tools():
    mgr = await new_manager(idle_ttl_s=9999)
    try:
        assert isinstance(mgr, ExecBackend)
        tools = {t.name for t in build_sandbox_tools(mgr)}
        assert tools == EXPECTED_TOOLS
    finally:
        await mgr.shutdown()
