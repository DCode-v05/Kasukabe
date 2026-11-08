"""Sandbox tools — engine-level tools that execute inside bubblewrap isolation.

NOT catalog tools. Registered directly in thread.py alongside engine tools
like ask_user, spawn_sub_agent, request_plan.

Tools:
  - bash: execute shell commands (foreground or background, optional PTY)
  - python: execute Python code
  - read_file: read workspace files
  - write_file: write workspace files
  - file_edit: edit workspace files in place
  - list_directory: list workspace contents
  - search_files: regex search across workspace files
  - process: monitor/manage background processes (list, poll, log, kill, write, send-keys)
"""

from __future__ import annotations

import json
from typing import Any

from src.sandbox.provider import ExecBackend
from src.sandbox.readonly_allowlist import is_command_read_only
from src.utility_directory.tool_runtime import ToolDef, ALWAYS_SAFE


def build_sandbox_tools(executor: ExecBackend) -> list[ToolDef]:
    """Build all sandbox-backed ToolDefs for direct registration in the thread.

    `executor` is any ExecBackend (the provider-agnostic exec surface): a raw
    SandboxExecutor in tests, or a provisioned provider session in the engine.
    """

    async def bash_handler(params: dict[str, Any]) -> str:
        command = params.get("command", "")
        if not command:
            return "Error: command is required"
        timeout = min(params.get("timeout", 30), 120)
        background = params.get("background", False)
        pty = params.get("pty", False)
        yield_ms = params.get("yield_ms", 10000)
        network = params.get("network", False)

        try:
            result = await executor.run_bash(
                command,
                timeout=timeout,
                background=background,
                yield_ms=yield_ms,
                pty=pty,
                network=network,
            )
            return result.to_str()
        finally:
            if not background:
                try:
                    from src.sandbox.orphan_detector import orphan_cleanup_enabled

                    if orphan_cleanup_enabled():
                        await executor.kill_orphaned_children()
                except Exception:
                    pass

    async def python_handler(params: dict[str, Any]) -> str:
        code = params.get("code", "")
        if not code:
            return "Error: code is required"
        timeout = min(params.get("timeout", 30), 120)
        result = await executor.run_python(code, timeout=timeout)
        return result.to_str()

    async def read_file_handler(params: dict[str, Any]) -> str:
        path = params.get("path", "")
        if not path:
            return "Error: path is required"
        result = await executor.read_file(path)
        return result.to_str()

    async def write_file_handler(params: dict[str, Any]) -> str:
        path = params.get("path", "")
        content = params.get("content", "")
        if not path:
            return "Error: path is required"
        result = await executor.write_file(path, content)
        return result.to_str()

    async def file_edit_handler(params: dict[str, Any]) -> str:
        path = params.get("path", "")
        old_string = params.get("old_string", "")
        new_string = params.get("new_string", "")
        replace_all = params.get("replace_all", False)
        if not path:
            return "Error: path is required"
        if not old_string:
            return "Error: old_string is required"
        result = await executor.edit_file(path, old_string, new_string, replace_all=replace_all)
        return result.to_str()

    async def list_directory_handler(params: dict[str, Any]) -> str:
        path = params.get("path", ".")
        result = await executor.list_directory(path)
        return result.to_str()

    async def search_content_handler(params: dict[str, Any]) -> str:
        pattern = params.get("pattern", "")
        if not pattern:
            return "Error: pattern is required"
        path = params.get("path", ".")
        result = await executor.search_content(pattern, path)
        return result.to_str()

    async def process_handler(params: dict[str, Any]) -> str:
        action = params.get("action", "")
        session_id = params.get("session_id", "")

        valid_actions = {"list", "poll", "log", "kill", "write", "send-keys", "remove"}
        if action not in valid_actions:
            return f"Error: unknown action '{action}'. Valid: {', '.join(sorted(valid_actions))}"

        if action == "list":
            sessions = executor.process_list()
            if not sessions:
                return "No active or recent sessions"
            return json.dumps(sessions, indent=2)

        if not session_id:
            return "Error: session_id is required for this action"

        if action == "poll":
            timeout_ms = params.get("timeout_ms", 2000)
            result = await executor.process_poll(session_id, timeout_ms)
            return json.dumps(result, indent=2)

        if action == "log":
            offset = params.get("offset", 0)
            limit = params.get("limit", 200)
            result = executor.process_log(session_id, offset, limit)
            return json.dumps(result, indent=2)

        if action == "kill":
            result = await executor.process_kill(session_id)
            return json.dumps(result)

        if action == "write":
            data = params.get("data", "")
            result = await executor.process_write(session_id, data)
            return json.dumps(result)

        if action == "send-keys":
            keys = params.get("keys", "")
            result = await executor.process_send_keys(session_id, keys)
            return json.dumps(result)

        if action == "remove":
            result = executor.process_remove(session_id)
            return json.dumps(result)

        return f"Error: unknown action '{action}'. Valid: list, poll, log, kill, write, send-keys, remove"

    def bash_is_read_only(inp: dict[str, Any]) -> bool:
        return is_command_read_only(inp.get("command", ""))

    return [
        ToolDef(
            name="bash",
            description="Execute a shell command in the sandbox workspace. Supports background mode (background:true) for long-running commands and PTY mode (pty:true) for interactive programs. Network access requires user permission (set network:true for commands needing internet like curl, git, pip).",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (max 120, ignored for background)",
                        "default": 30,
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Run in background. Use process tool to monitor.",
                        "default": False,
                    },
                    "yield_ms": {
                        "type": "integer",
                        "description": "For background: ms to wait before yielding (default 10000)",
                        "default": 10000,
                    },
                    "pty": {
                        "type": "boolean",
                        "description": "Use pseudo-terminal for interactive programs",
                        "default": False,
                    },
                    "network": {
                        "type": "boolean",
                        "description": "Set true if command needs internet (curl, git, pip). Triggers user permission prompt. Auto-detected for known network binaries.",
                        "default": False,
                    },
                },
                "required": ["command"],
            },
            handler=bash_handler,
            is_concurrency_safe=bash_is_read_only,
            is_read_only=bash_is_read_only,
            is_command_execution=True,
        ),
        ToolDef(
            name="python",
            description="Execute Python code in the sandbox workspace. For data analysis, computation, file processing. No network access.",
            input_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (max 120)",
                        "default": 30,
                    },
                },
                "required": ["code"],
            },
            handler=python_handler,
            is_command_execution=True,
        ),
        ToolDef(
            name="read_file",
            description="Read a file from the sandbox workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                },
                "required": ["path"],
            },
            handler=read_file_handler,
            is_concurrency_safe=ALWAYS_SAFE,
            is_read_only=ALWAYS_SAFE,
        ),
        ToolDef(
            name="write_file",
            description="Write content to a file in the sandbox workspace. Creates parent directories.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
            handler=write_file_handler,
            is_destructive=lambda _: True,
        ),
        ToolDef(
            name="file_edit",
            description="Edit a file in the sandbox workspace by replacing old_string with new_string. old_string must match exactly once in the file unless replace_all is true; if it matches multiple times, the call errors and you must add surrounding context to make the match unique (or pass replace_all=true to replace every occurrence).",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to find and replace. Must be unique in the file unless replace_all is true — include enough surrounding context to disambiguate.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Text to substitute in place of old_string",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence (default: false, which requires old_string to be unique)",
                        "default": False,
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=file_edit_handler,
        ),
        ToolDef(
            name="list_directory",
            description="List files and directories in the sandbox workspace with sizes.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to workspace",
                        "default": ".",
                    },
                },
            },
            handler=list_directory_handler,
            is_concurrency_safe=ALWAYS_SAFE,
            is_read_only=ALWAYS_SAFE,
        ),
        ToolDef(
            name="search_files",
            description="Search file contents in the sandbox workspace using regex patterns.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {
                        "type": "string",
                        "description": "File or directory to search",
                        "default": ".",
                    },
                },
                "required": ["pattern"],
            },
            handler=search_content_handler,
            is_concurrency_safe=ALWAYS_SAFE,
            is_read_only=ALWAYS_SAFE,
        ),
        ToolDef(
            name="process",
            description="Monitor and manage background processes. Actions: list (show all), poll (wait for output), log (paginated output), kill (terminate), write (send stdin), send-keys (Ctrl+C etc.), remove (clean up finished).",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: list, poll, log, kill, write, send-keys, remove",
                        "enum": ["list", "poll", "log", "kill", "write", "send-keys", "remove"],
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Session ID (from bash background output). Required for all actions except list.",
                    },
                    "timeout_ms": {
                        "type": "integer",
                        "description": "For poll: max ms to wait for output (default 2000)",
                        "default": 2000,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "For log: line offset (default 0)",
                        "default": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "For log: max lines (default 200)",
                        "default": 200,
                    },
                    "data": {"type": "string", "description": "For write: data to send to stdin"},
                    "keys": {
                        "type": "string",
                        "description": "For send-keys: key name (ctrl-c, ctrl-d, enter, tab, escape, up, down, left, right)",
                    },
                },
                "required": ["action"],
            },
            handler=process_handler,
        ),
    ]
