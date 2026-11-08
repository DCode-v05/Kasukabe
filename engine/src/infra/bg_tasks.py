"""Background task registration — shared `add_done_callback` eviction pattern.

Two places in the codebase track background asyncio.Tasks and hand-roll
the same register+evict-on-done boilerplate:

- `src/engine_core/coordinator.py::Coordinator.background_tasks` (set)
- `src/engine_core/sub_agent.py::ASYNC_TASKS` (dict)

`track_background` is the shared helper. Works with either a `set[Task]` or a
`dict[Key, Task]`; the done-callback removes the entry so nothing leaks.

Usage:
    task = asyncio.create_task(coro)
    track_background(task, my_set)                      # set-backed registry
    track_background(task, my_dict, key="bg-abc123")    # dict-backed registry
"""

from __future__ import annotations

import asyncio
from typing import Any, Hashable, MutableMapping, MutableSet


TaskRegistrySet = MutableSet[asyncio.Task[Any]]
TaskRegistryDict = MutableMapping[Any, asyncio.Task[Any]]


def track_background(
    task: asyncio.Task,
    registry: TaskRegistrySet | TaskRegistryDict,
    *,
    key: Hashable | None = None,
) -> asyncio.Task:
    """Register `task` in `registry` and add a done-callback that evicts it.

    For a set-backed registry, `key` is ignored and the task itself is the
    entry (symmetric with `asyncio.Task.__hash__`). For a dict-backed
    registry, `key` is required and identifies the slot.

    The done-callback's eviction is idempotent (set.discard / dict.pop with
    default) so callers that pop the entry manually — e.g. an explicit
    `collect(thread_id)` path — don't race the callback.
    """
    if isinstance(registry, dict):
        if key is None:
            raise ValueError("track_background: dict registry requires key=")
        registry[key] = task

        def evict_dict(t: asyncio.Task, k: Hashable = key) -> None:
            registry.pop(k, None)

        task.add_done_callback(evict_dict)
    elif isinstance(registry, set):
        registry.add(task)

        def evict_set(t: asyncio.Task) -> None:
            registry.discard(t)

        task.add_done_callback(evict_set)
    else:
        raise TypeError(
            f"track_background: unsupported registry type {type(registry)!r}; "
            "pass a set[Task] or dict[Key, Task]."
        )
    return task


__all__ = ["track_background"]
