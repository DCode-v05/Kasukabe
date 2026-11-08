"""M1 — resume_block resolves a paused turn's future LOCK-FREE.

The crux of M1: a turn that pauses on a waiting block holds thread.turn_lock
the whole time it waits. resume_block must resolve the waiting future WITHOUT
acquiring that lock or re-entering thread.execute() — otherwise the answer would
deadlock against the held lock. These tests prove the lock-free path and the
no-waiter fallback.
"""

import asyncio

import pytest

from src.engine_core.coordinator import Coordinator, current_thread_id_var


def make_coordinator():
    class FakeEmitter:
        def __init__(self):
            self.events = []

        async def emit(self, ev):
            self.events.append(ev)

    return Coordinator(adapter=None, emitter=FakeEmitter(), memory=None, db=None)


@pytest.mark.asyncio
async def test_resume_resolves_paused_future_without_handle_input(monkeypatch):
    coord = make_coordinator()

    called = {"handle_input": False}

    async def boom(*a, **k):
        called["handle_input"] = True
        raise AssertionError("resume_block must not call handle_input when a waiter exists")

    monkeypatch.setattr(coord, "handle_input", boom)

    current_thread_id_var.set("thr_lock")
    pause = asyncio.create_task(
        coord.await_block_input(
            {
                "question": "continue?",
                "format": "confirm",
                "options": [{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}],
            }
        )
    )
    for _ in range(5):
        await asyncio.sleep(0)
        if coord.pending_inputs:
            break
    assert coord.pending_inputs, "the turn must be parked with a registered waiter"
    block_id = next(iter(coord.pending_inputs))

    owner, resumed = await coord.resume_block("thr_lock", block_id, value="yes", text="")
    assert owner == "thr_lock"
    assert resumed is True
    assert called["handle_input"] is False
    reply = await pause
    assert reply == "yes"
    assert block_id not in coord.pending_inputs


@pytest.mark.asyncio
async def test_resume_no_waiter_falls_back_to_handle_input(monkeypatch):
    coord = make_coordinator()
    seen = {}

    async def fake_handle_input(message, thread_id=None, **k):
        seen["message"] = message
        seen["thread_id"] = thread_id
        return thread_id or "thr_new"

    monkeypatch.setattr(coord, "handle_input", fake_handle_input)

    result, resumed = await coord.resume_block(
        "thr_x", "craft:thr_x:doesnotexist", value="hello", text=""
    )
    assert seen["message"] == "hello"
    assert seen["thread_id"] == "thr_x"
    assert result == "thr_x"
    assert resumed is False


@pytest.mark.asyncio
async def test_double_resume_is_safe(monkeypatch):
    coord = make_coordinator()
    monkeypatch.setattr(coord, "handle_input", lambda *a, **k: None)

    current_thread_id_var.set("thr_dbl")
    pause = asyncio.create_task(coord.await_block_input("answer me?"))
    for _ in range(5):
        await asyncio.sleep(0)
        if coord.pending_inputs:
            break
    block_id = next(iter(coord.pending_inputs))

    await coord.resume_block("thr_dbl", block_id, value="first", text="")
    await coord.resume_block("thr_dbl", block_id, value="second", text="")
    reply = await pause
    assert reply == "first"
