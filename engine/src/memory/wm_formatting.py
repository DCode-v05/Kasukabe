"""Shared formatting for Working Memory context summaries.

Used by both InMemoryWorkingMemory and SqliteWorkingMemory to produce
structured, token-budgeted summaries from LogEntry lists.

Deterministic — no LLM calls. Groups tool calls with results, extracts
file paths, highlights errors, surfaces checkpoints.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.memory.working_memory import LogEntry, EntryType

MAX_CHARS_PER_SECTION = 8_000
NOISE_EVENTS = {"thread_state_change"}


def safe_json(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return {}


def truncate(text: str, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def extract_file_path(data: dict) -> str | None:
    inp = data.get("input")
    nested = inp if isinstance(inp, dict) else {}
    for key in ("file_path", "path", "filename"):
        val = data.get(key) or nested.get(key)
        if val and isinstance(val, str) and "/" in val:
            return val
    if isinstance(inp, str):
        m = re.search(r'(/[^\s"\',:)\]]{3,})', inp)
        if m:
            return m.group(1)
    return None


def format_context_summary(
    items: list[LogEntry],
    max_items: int = 10,
    max_tokens: int | None = None,
) -> str:
    if not items:
        return ""

    recent = items[-max_items:]

    current_state: list[str] = []
    task_activity: list[str] = []
    tool_ops: list[str] = []
    files_touched: set[str] = set()
    errors: list[str] = []
    decisions: list[str] = []
    events: list[str] = []

    i = 0
    last_user_msg = ""
    last_assistant_msg = ""

    PLAIN_TEXT_TYPES = {EntryType.MESSAGE_USER, EntryType.MESSAGE_ASSISTANT, EntryType.ACTION_USER}

    while i < len(recent):
        entry = recent[i]
        data = {} if entry.type in PLAIN_TEXT_TYPES else safe_json(entry.content)

        if entry.type == EntryType.MESSAGE_USER:
            last_user_msg = truncate(entry.content, 300)
            task_activity.append(f"User: {last_user_msg}")

        elif entry.type == EntryType.MESSAGE_ASSISTANT:
            last_assistant_msg = truncate(entry.content, 300)

        elif entry.type == EntryType.TOOL_CALL:
            tool_name = data.get("tool", "unknown")
            tool_input = truncate(data.get("input", ""), 150)
            fp = extract_file_path(data)
            if fp:
                files_touched.add(fp)

            result_line = ""
            if i + 1 < len(recent) and recent[i + 1].type == EntryType.TOOL_RESULT:
                rdata = safe_json(recent[i + 1].content)
                if rdata.get("tool") == tool_name:
                    success = rdata.get("success", True)
                    duration = rdata.get("duration_ms", "")
                    dur_str = f" ({duration}ms)" if duration else ""
                    status = "ok" if success else "FAILED"
                    result_line = f"  → {tool_name}: {tool_input}{dur_str} [{status}]"

                    if not success:
                        err_text = truncate(rdata.get("result", ""), 300)
                        errors.append(f"Tool {tool_name} failed: {err_text}")

                    rfp = extract_file_path(rdata)
                    if rfp:
                        files_touched.add(rfp)
                    i += 1

            if result_line:
                tool_ops.append(result_line)
            else:
                tool_ops.append(f"  → {tool_name}: {tool_input}")

        elif entry.type == EntryType.TOOL_RESULT:
            tool_name = data.get("tool", "unknown")
            success = data.get("success", True)
            result_text = truncate(data.get("result", entry.content), 200)
            dur = data.get("duration_ms", "")
            dur_str = f" ({dur}ms)" if dur else ""
            status = "ok" if success else "FAILED"
            tool_ops.append(f"  → {tool_name}: {result_text}{dur_str} [{status}]")
            if not success:
                errors.append(f"Tool {tool_name} failed: {result_text}")
            rfp = extract_file_path(data)
            if rfp:
                files_touched.add(rfp)

        elif entry.type == EntryType.CHECKPOINT:
            label = data.get("label", "")
            status = data.get("status", "")
            output = truncate(data.get("output", ""), 200)
            decisions.append(f"Checkpoint: {label} [{status}] {output}".strip())

        elif entry.type in (EntryType.USER_INPUT_REQUEST, EntryType.USER_INPUT_RESPONSE):
            if entry.type == EntryType.USER_INPUT_REQUEST:
                q = data.get("question", entry.content)
                decisions.append(f"Asked user: {truncate(q, 200)}")
            else:
                r = data.get("response", entry.content)
                decisions.append(f"User answered: {truncate(r, 200)}")

        elif entry.type == EntryType.SYSTEM_EVENT:
            event_name = data.get("event", "")
            if event_name in NOISE_EVENTS:
                i += 1
                continue
            if "user_input" in data:
                task_activity.append(f"User: {truncate(data['user_input'], 300)}")
            elif "charter_id" in data:
                events.append(f"Charter update: {data.get('reason', '')}")
            else:
                events.append(truncate(entry.content, 200))

        elif entry.type == EntryType.ACTION_USER:
            task_activity.append(f"User action: {truncate(entry.content, 200)}")

        i += 1

    if last_user_msg:
        current_state.append(f"Last user request: {last_user_msg}")
    if last_assistant_msg:
        current_state.append(f"Last assistant response: {last_assistant_msg}")
    if decisions:
        current_state.append(decisions[-1])

    sections: list[tuple[str, list[str]]] = [
        ("Current State", current_state),
        ("Task Activity", task_activity),
        ("Tool Operations", tool_ops),
        ("Files Touched", sorted(files_touched)),
        ("Errors & Corrections", errors),
        ("Decisions & Checkpoints", decisions),
        ("Session Events", events),
    ]

    parts = ["Previous context from this session:"]
    was_truncated = False

    for title, lines in sections:
        if not lines:
            continue
        parts.append(f"\n# {title}")
        section_chars = 0
        for line in lines:
            if section_chars + len(line) > MAX_CHARS_PER_SECTION:
                parts.append(f"[... {title} truncated ...]")
                was_truncated = True
                break
            parts.append(line)
            section_chars += len(line)

    result = "\n".join(parts)

    if max_tokens is not None:
        char_budget = max_tokens * 4
        if len(result) > char_budget:
            cut = result.rfind("\n", 0, char_budget)
            if cut <= 0:
                cut = char_budget
            result = result[:cut] + "\n[Working memory summary truncated — exceeded token budget]"
            was_truncated = True

    if was_truncated:
        result += "\n[Some sections were truncated]"

    return result
