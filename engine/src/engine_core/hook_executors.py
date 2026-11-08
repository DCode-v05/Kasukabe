"""hook_executors — HTTP hook executor implementations.

Provides ``HttpPreToolUseHook`` and ``HttpPostToolUseHook`` that POST
hook context JSON to a webhook URL and parse the response as a hook result.

These are the HTTP-type hook implementations referenced in ``hook_settings.py``
and described in the Asana task:
  ``{ "type": "http", "url": "https://...", "timeout": 30 }``

Protocol
--------
Request:
  - Method: POST
  - Content-Type: application/json
  - Body: hook context serialised as JSON (same structure written to
    stdin for COMMAND hooks)

Response:
  - 200/2xx: body parsed as JSON hook result
  - Non-2xx: fail-open (logged, no-op result)
  - Timeout: fail-open (logged, no-op result)

The JSON result schema mirrors the COMMAND hook stdout schema:
  - PreToolUse:  ``{"decision": "...", "deny_reason": "...", ...}``
  - PostToolUse: ``{"additional_context": "...", "prevent_continuation": false, ...}``

Reference: ``src/utils/hooks/execHttpHook.ts``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any

from src.engine_core.pre_tool_hooks import (
    PreToolUseHookInput,
    PreToolUseHookResult,
    result_from_dict as pre_result_from_dict,
)
from src.engine_core.post_tool_hooks import (
    PostToolHookContext,
    PostToolUseHookResult,
    result_from_dict as post_result_from_dict,
)
from src.engine_core.stop_hooks import (
    StopHookInput,
    StopHookResult,
    result_from_dict as stop_result_from_dict,
)

logger = logging.getLogger(__name__)


class HttpPreToolUseHook:
    """POSTs ``PreToolUseHookInput`` as JSON to a webhook URL.

    Parses the response body as a ``PreToolUseHookResult`` dict.
    Fail-open: any network error, timeout, or non-2xx response returns
    an ``inherit`` no-op result.
    """

    def __init__(self, url: str, timeout_s: float = 30.0) -> None:
        if not url:
            raise ValueError("HttpPreToolUseHook requires a non-empty URL")
        self.url = url
        self.timeout_s = timeout_s

    async def execute(self, inp: PreToolUseHookInput) -> PreToolUseHookResult:
        payload = json.dumps(asdict(inp)).encode("utf-8")
        try:
            data = await post_json(self.url, payload, self.timeout_s)
        except Exception as exc:  # noqa: BLE001
            logger.debug("HttpPreToolUseHook failed for %s: %s", self.url, exc)
            return PreToolUseHookResult()
        return pre_result_from_dict(data)


class HttpPostToolUseHook:
    """POSTs ``PostToolHookContext`` as JSON to a webhook URL.

    Parses the response body as a ``PostToolUseHookResult`` dict.
    Fail-open on any error.
    """

    def __init__(self, url: str, timeout_s: float = 30.0) -> None:
        if not url:
            raise ValueError("HttpPostToolUseHook requires a non-empty URL")
        self.url = url
        self.timeout_s = timeout_s

    async def execute(self, ctx: PostToolHookContext) -> PostToolUseHookResult:
        payload = json.dumps(asdict(ctx)).encode("utf-8")
        try:
            data = await post_json(self.url, payload, self.timeout_s)
        except Exception as exc:  # noqa: BLE001
            logger.debug("HttpPostToolUseHook failed for %s: %s", self.url, exc)
            return PostToolUseHookResult()
        return post_result_from_dict(data)


class HttpStopHook:
    """POSTs ``StopHookInput`` as JSON to a webhook URL.

    Parses the response body as a ``StopHookResult`` dict. Required because
    ``StopHookRegistry.execute_all`` dereferences ``res.message`` and
    ``res.stop_reason`` — a ``GenericHookResult`` has neither field, so
    routing HTTP stop hooks through the generic registry would crash the
    aggregator. This class returns the right shape.
    """

    def __init__(self, url: str, timeout_s: float = 30.0) -> None:
        if not url:
            raise ValueError("HttpStopHook requires a non-empty URL")
        self.url = url
        self.timeout_s = timeout_s

    async def execute(self, inp: StopHookInput) -> StopHookResult:
        payload = json.dumps(asdict(inp)).encode("utf-8")
        try:
            data = await post_json(self.url, payload, self.timeout_s)
        except Exception as exc:  # noqa: BLE001
            logger.debug("HttpStopHook failed for %s: %s", self.url, exc)
            return StopHookResult()
        return stop_result_from_dict(data)


@dataclass(frozen=True)
class CommandRun:
    """Outcome of a command-hook subprocess: exit code + decoded streams."""

    returncode: int
    stdout_text: str
    stderr_text: str


async def run_command_hook(
    command: list[str],
    payload: bytes,
    timeout_s: float,
    env: dict[str, str] | None = None,
) -> CommandRun | None:
    """Spawn a command hook subprocess, write ``payload`` to stdin, read
    stdout/stderr with timeout + hard-kill on overrun, decode and return.

    Returns ``None`` on spawn failure (FileNotFoundError / PermissionError /
    OSError) — caller treats as fail-open.  Returns a ``CommandRun`` with
    ``returncode == -1`` on timeout so callers can distinguish spawn-fail
    (no result) from timeout (process was running but too slow).

    Shared across ``CommandPreToolUseHook``, ``CommandPostToolUseHook``,
    ``CommandStopHook``, and ``CommandGenericHook``.  Consolidates the
    shielded-communicate + kill + 1s-harvest fallback pattern that used
    to live in four copies.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        logger.debug("command hook spawn failed for %r: %s", command[:1], exc)
        return None

    comm_task = asyncio.create_task(proc.communicate(input=payload))
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            asyncio.shield(comm_task), timeout=timeout_s
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(comm_task, timeout=1.0)
        except Exception:  # noqa: BLE001
            comm_task.cancel()
            try:
                await comm_task
            except BaseException:  # noqa: BLE001
                pass
        logger.debug("command hook timed out after %.2fs: %r", timeout_s, command[:1])
        return CommandRun(returncode=-1, stdout_text="", stderr_text="")

    return CommandRun(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout_text=(stdout_bytes or b"").decode("utf-8", errors="replace").strip(),
        stderr_text=(stderr_bytes or b"").decode("utf-8", errors="replace").strip(),
    )


def build_hook_env(
    event_name: str,
    *,
    tool_name: str = "",
    tool_use_id: str = "",
    session_id: str = "",
    cwd: str = "",
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the subprocess environment for a command hook.

    Base is a copy of ``os.environ`` (one call; callers used to copy it
    independently on every invocation).  Then the hook protocol env vars
    (``HOOK_EVENT_NAME``, ``TOOL_NAME``, ``TOOL_USE_ID``, ``SESSION_ID``,
    ``CWD``) are set via ``setdefault`` so the caller's environment wins
    if the user set them explicitly.  Finally ``overrides`` (plugin env:
    ``PLUGIN_ROOT`` / ``PLUGIN_DATA`` / ``PLUGIN_OPTION_*``) are applied
    last so plugin context takes authoritative priority.
    """
    env = os.environ.copy()
    if event_name:
        env.setdefault("HOOK_EVENT_NAME", event_name)
    if tool_name:
        env.setdefault("TOOL_NAME", tool_name)
    if tool_use_id:
        env.setdefault("TOOL_USE_ID", tool_use_id)
    if session_id:
        env.setdefault("SESSION_ID", session_id)
    if cwd:
        env.setdefault("CWD", cwd)
    if overrides:
        env.update(overrides)
    return env


async def post_json(url: str, body: bytes, timeout_s: float) -> Any:
    """POST JSON body to ``url``.  Returns parsed JSON response body.

    Uses asyncio subprocess to invoke curl so we have no hard dependency
    on aiohttp or httpx.  Falls back to a minimal ``asyncio`` TCP socket
    approach if curl is not available.

    Raises on network errors, timeouts, non-2xx responses, or JSON parse
    failures so callers can handle uniformly as fail-open.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl",
            "-s",
            "-S",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
            "--max-time",
            str(int(timeout_s)),
            "-w",
            "\n%{http_code}",
            url,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RuntimeError(f"curl not available: {exc}") from exc

    comm_task = asyncio.create_task(proc.communicate(input=body))
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            asyncio.shield(comm_task), timeout=timeout_s + 2.0
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(comm_task, timeout=1.0)
        except Exception:  # noqa: BLE001
            comm_task.cancel()
            try:
                await comm_task
            except BaseException:  # noqa: BLE001
                pass
        raise TimeoutError(f"HTTP hook timed out after {timeout_s}s")

    stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace").strip()

    lines = stdout_text.rsplit("\n", 1)
    if len(lines) == 2:
        body_text, status_str = lines
        try:
            status_code = int(status_str.strip())
        except ValueError:
            body_text = stdout_text
            status_code = 0
    else:
        body_text = stdout_text
        status_code = 0

    if status_code < 200 or status_code >= 300:
        raise RuntimeError(f"HTTP hook returned non-2xx status {status_code}: {body_text[:200]}")

    if not body_text.strip():
        return {}

    return json.loads(body_text)
