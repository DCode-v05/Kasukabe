"""Per-request usage accumulator using contextvars.

Providers call record_usage() after every LLM call. When a tracker is
active in the current context, tokens accumulate; otherwise the call is
a no-op. This lets thread.execute() capture total usage from all adapter
calls (agent loop, generate(), embeddings) without changing the LLM Protocol.

Usage:
    tracker = UsageAccumulator()
    token = usage_tracker_var.set(tracker)
    try:
        ...  # any LLM / embedding calls in this async context
    finally:
        usage_tracker_var.reset(token)
        totals = tracker.total()
"""

from __future__ import annotations

import contextvars
import threading

from src.herald.types import Usage

usage_tracker_var: contextvars.ContextVar[UsageAccumulator | None] = contextvars.ContextVar(
    "usage_tracker", default=None
)


class UsageAccumulator:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cache_read: int = 0
        self.cache_create: int = 0
        self.embedding_tokens: int = 0
        self.call_count: int = 0
        self.latest_quota_value: dict | None = None

    def record(self, usage: Usage) -> None:
        with self.lock:
            self.input_tokens += usage.input_tokens
            self.output_tokens += usage.output_tokens
            self.cache_read += usage.cache_read_input_tokens
            self.cache_create += usage.cache_creation_input_tokens
            self.call_count += 1

    def record_embedding(self, tokens: int) -> None:
        with self.lock:
            self.embedding_tokens += tokens
            self.call_count += 1

    def record_quota(self, snapshot: dict | None) -> None:
        with self.lock:
            if snapshot is not None:
                self.latest_quota_value = snapshot

    def latest_quota(self) -> dict | None:
        with self.lock:
            return self.latest_quota_value

    def total(self) -> Usage:
        with self.lock:
            return Usage(
                input_tokens=self.input_tokens,
                output_tokens=self.output_tokens,
                cache_read_input_tokens=self.cache_read,
                cache_creation_input_tokens=self.cache_create,
            )


on_usage_hooks: list = []


def add_on_usage_hook(fn) -> None:
    """Register a callback fired on every record_usage call.

    The hook receives (usage: Usage, model_id: str). If the hook returns
    a coroutine, it's scheduled as a fire-and-forget task on the running
    event loop. Exceptions in hooks are swallowed (logged, not propagated).
    Duplicate registrations are silently ignored.
    """
    if fn in on_usage_hooks:
        return
    on_usage_hooks.append(fn)


def record_usage(usage: Usage, model_id: str = "") -> None:
    tracker = usage_tracker_var.get(None)
    if tracker is not None:
        tracker.record(usage)
    for hook in on_usage_hooks:
        try:
            import asyncio

            result = hook(usage, model_id)
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(result)
                except RuntimeError:
                    pass
        except Exception:
            pass


def record_embedding_usage(tokens: int) -> None:
    tracker = usage_tracker_var.get(None)
    if tracker is not None:
        tracker.record_embedding(tokens)


def record_quota_snapshot(snapshot: dict | None) -> None:
    tracker = usage_tracker_var.get(None)
    if tracker is not None:
        tracker.record_quota(snapshot)
