"""Regression tests: sandbox executor spawn sites must not leak secrets.

Seeds os.environ with sentinel secrets, runs a simple `env`-emitting command
through each of SandboxExecutor's public code paths, and asserts the child
process could not observe the secret. GITHUB_TOKEN is allowlisted and must
survive.
"""

from __future__ import annotations

import platform

import pytest

from src.sandbox.executor import SandboxExecutor
from src.utility_directory import subprocess_env as se_mod

IS_WINDOWS = platform.system() == "Windows"


@pytest.fixture(autouse=True)
def reset_state():
    yield
    se_mod.session_mode = None


@pytest.fixture
def seeded_env(monkeypatch):
    # engine_core (stripped in default + strict)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-sentinel")
    monkeypatch.setenv("DATABASE_URL", "postgres://sentinel")
    # strict-only (stripped in strict, since sandbox uses strict regardless)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-sentinel")
    monkeypatch.setenv("ACTIONS_RUNTIME_TOKEN", "runtime-sentinel")
    # allowlist (preserved in all tiers)
    monkeypatch.setenv("GITHUB_TOKEN", "gh-sentinel")
    # GHA INPUT_ duplicate (stripped because base is scrubbed)
    monkeypatch.setenv("INPUT_ANTHROPIC_API_KEY", "input-sentinel")
    # user's own INPUT_ var (preserved — base not in scrub set)
    monkeypatch.setenv("INPUT_MY_CUSTOM_FLAG", "true")


CHECK_CMD = (
    "echo ANTHROPIC=${ANTHROPIC_API_KEY:-missing}; "
    "echo AWS=${AWS_SECRET_ACCESS_KEY:-missing}; "
    "echo GH=${GITHUB_TOKEN:-missing}; "
    "echo INPUT_ANT=${INPUT_ANTHROPIC_API_KEY:-missing}; "
    "echo INPUT_FLAG=${INPUT_MY_CUSTOM_FLAG:-missing}"
)


def assert_scrubbed(stdout: str) -> None:
    assert "ANTHROPIC=missing" in stdout, f"ANTHROPIC leaked: {stdout}"
    assert "AWS=missing" in stdout, f"AWS leaked: {stdout}"
    assert "GH=gh-sentinel" in stdout, f"GITHUB_TOKEN stripped: {stdout}"
    assert "INPUT_ANT=missing" in stdout, f"INPUT_ duplicate leaked: {stdout}"
    assert "INPUT_FLAG=true" in stdout, f"legitimate INPUT_ var stripped: {stdout}"


@pytest.mark.asyncio
@pytest.mark.skipif(IS_WINDOWS, reason="sandbox executor spawns posix shell")
async def test_sandbox_foreground_exec_strips_secrets(seeded_env):
    """The main `exec` path (foreground shell) must not leak secrets."""
    ex = SandboxExecutor(session_id="scrub-fg")
    try:
        result = await ex.run_bash(CHECK_CMD)
        assert_scrubbed(result.stdout)
    finally:
        ex.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(IS_WINDOWS, reason="sandbox executor spawns posix shell")
async def test_sandbox_background_exec_strips_secrets(seeded_env):
    """The `exec_background` path must not leak secrets."""
    ex = SandboxExecutor(session_id="scrub-bg")
    try:
        # Long yield_ms so the short echo completes within the yield window
        # and we get the captured output back instead of a session id.
        result = await ex.run_bash(CHECK_CMD, background=True, yield_ms=5000)
        assert_scrubbed(result.stdout)
    finally:
        ex.cleanup()


@pytest.mark.asyncio
@pytest.mark.skipif(IS_WINDOWS, reason="sandbox executor spawns posix shell")
async def test_parent_retains_secrets_after_sandbox_spawn(seeded_env):
    """Running a sandbox command must NOT mutate the parent's os.environ."""
    import os

    before = dict(os.environ)
    ex = SandboxExecutor(session_id="scrub-parent")
    try:
        await ex.run_bash("echo hello")
    finally:
        ex.cleanup()

    # Parent env is unchanged — secrets still readable for lazy credential reads.
    assert os.environ.get("ANTHROPIC_API_KEY") == "anthropic-sentinel"
    assert os.environ.get("AWS_SECRET_ACCESS_KEY") == "aws-sentinel"
    assert dict(os.environ) == before
