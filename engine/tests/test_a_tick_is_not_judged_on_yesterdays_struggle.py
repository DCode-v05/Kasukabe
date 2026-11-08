"""ENG-22: the struggle counters carried across ticks, so the struggle
injection fired on a resident's whole morning rather than on its turn.

From `docs/review/10-engine-seam-and-mind.md`: "`thread.py:3399-3406,
4436-4443`: `recent_tool_sigs` and `turns_without_text` carry across ticks
for a pinned thread, so ENG-07(b) fires on cumulative behaviour, not on
one turn. Seam mitigation: write a zeroed `{"_struggle": ...}`
`SYSTEM_EVENT` at `send_tick` start (WM is the seam's `working_memory`
object; a write is composition, not an engine edit)."

The mechanism, both halves of which are in frozen code and correct for the
product that engine was written for. At the top of every agent loop
`thread.py` restores `state.struggle` from the LAST working-memory
`SYSTEM_EVENT` whose content begins `{"_struggle":`, and after every tool
step it writes the updated counters back. For an assistant thread that
spans one conversation this is exactly right. For a Kasukabe resident the
thread is PINNED FOR THE LIFE OF THE RUN, so there is no boundary at which
those counters were ever cleared: `recent_tool_sigs` keeps a rolling
twelve signatures across every tick since genesis and `turns_without_text`
only resets when the model speaks. Three `say` calls with the same
argument keys - one an hour ago, one at noon, one now - trip
`STRUGGLE_REPEATED_CALLS`, and the resident is handed "[System: Execution
was struggling ...]" plus an auto-plan for behaviour that was not
repetitive within any turn it took. That injection is ENG-07(b), the
assistant-shaped scaffolding the seam is otherwise removing.

The seam cannot change what `thread.py` restores. It can decide what is
there to be restored, which is what `send_tick` now does: one zeroed entry
per tick, written into the same working-memory object the thread reads,
before the turn starts. Within a tick the counters still accumulate, which
is the behaviour that was always wanted - a resident spinning on one
perception should still be caught.

The first test is the wiring one: it plants a poisoned entry, drives the
real `send_tick`, and watches the frozen engine's own `StrugglingDetected`
event. Nothing in this file inspects the seam's helper directly, because
a helper that writes a perfect entry nothing calls is this project's most
common defect.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.engine_core.events import StrugglingDetected
from src.engine_core.loop_state import (
    STRUGGLE_REPEATED_CALLS,
    STRUGGLE_TURNS_NO_TEXT,
    StrugglingState,
)
from src.memory.working_memory import EntryType

STRUGGLE_PREFIX = '{"_struggle":'

# What a pinned resident's counters actually looked like by mid-morning:
# eight turns since it last said anything out loud, and the same `say`
# signature three times across three different ticks.
A_MORNINGS_ACCUMULATION = {
    "consecutive_errors": 0,
    "turns_since_checkpoint": 4,
    "turns_without_text": STRUGGLE_TURNS_NO_TEXT + 1,
    "recent_tool_sigs": ["world:say:['content']"] * (STRUGGLE_REPEATED_CALLS + 1),
    "struggle_triggered": False,
}


def world_client(agent_id: str):
    from src.sim.world_tools import WorldClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=[])
        if request.url.path == "/tools/registry/version":
            return httpx.Response(200, json={"version": 0})
        if request.url.path == f"/agents/{agent_id}/memories":
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected world call: {request.url.path}")

    return WorldClient(
        "http://world.test",
        agent_id=agent_id,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def booted(tmp_path, monkeypatch):
    from src.sim.config import SimProfile
    from src.sim.pinned_boot import boot_sim_engine
    from tests.sim_fake_citadel import FakeBroker, install_fake_broker

    monkeypatch.setenv("FORK_SUBAGENT", "false")
    broker = FakeBroker()
    install_fake_broker(monkeypatch, broker)
    profile = SimProfile(
        agent_id="agent-boot",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(
        profile, "http://world.test", world_client=world_client("agent-boot")
    )
    return engine, broker


async def plant(engine, struggle: dict) -> None:
    """Put a struggle entry in working memory the way a previous tick's
    turn would have left one - through the thread's own memory object,
    which is the one `thread.py` reads back."""
    await engine.thread.memory.write(
        engine.thread_id,
        EntryType.SYSTEM_EVENT,
        json.dumps({"_struggle": struggle}),
    )


async def struggle_entries(engine) -> list[dict]:
    entries = await engine.thread.memory.read_by_type(
        engine.thread_id, EntryType.SYSTEM_EVENT
    )
    return [
        json.loads(e.content)["_struggle"]
        for e in entries
        if e.content.startswith(STRUGGLE_PREFIX)
    ]


def is_zeroed(struggle: dict) -> bool:
    return StrugglingState.from_dict(struggle) == StrugglingState()


# ── the wiring test ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_fresh_tick_is_not_told_it_is_struggling(tmp_path, monkeypatch):
    """THE WIRING TEST. A poisoned entry from an earlier tick, a real
    `send_tick`, and the frozen engine's own `StrugglingDetected` event as
    the verdict - so this fails if the zeroing helper exists and
    `send_tick` does not call it, and fails if it is called anywhere other
    than before `coordinator.handle_input`.
    """
    from src.sim.model_profile import MODEL_ID
    from tests.sim_fake_citadel import text_turn_sse

    engine, broker = await booted(tmp_path, monkeypatch)
    try:
        await plant(engine, A_MORNINGS_ACCUMULATION)

        struggles: list[StrugglingDetected] = []

        async def watch(event) -> None:
            if isinstance(event, StrugglingDetected):
                struggles.append(event)

        engine.emitter.on(watch)

        for _ in range(4):
            broker.queue(MODEL_ID, "sse", text_turn_sse("Morning, Halcyon."))
        await engine.send_tick("It is 09:10 on day 0. You are at the well.")

        assert struggles == [], (
            "ENG-22: the resident was told it was struggling on counters "
            "that belong to earlier ticks. The pinned thread never clears "
            f"them, so this fires on the whole morning: {struggles}"
        )
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_the_same_counters_still_catch_a_resident_spinning_now(
    tmp_path, monkeypatch
):
    """The control, and the reason this is a zeroing and not a removal.
    The detector must keep working on what happens INSIDE a tick, or the
    fix has bought quiet by disabling the mechanism.

    Planted state that is already over the line is restored by
    `thread.py` unless something clears it - so with the tick's own zeroed
    entry written AFTER this one, the poison is what an intra-tick
    accumulation would look like, and it must still fire.
    """
    from src.sim.model_profile import MODEL_ID
    from tests.sim_fake_citadel import text_turn_sse

    engine, broker = await booted(tmp_path, monkeypatch)
    try:
        struggles: list[StrugglingDetected] = []

        async def watch(event) -> None:
            if isinstance(event, StrugglingDetected):
                struggles.append(event)

        engine.emitter.on(watch)

        for _ in range(4):
            broker.queue(MODEL_ID, "sse", text_turn_sse("Morning, Halcyon."))
        # Planted INSIDE the tick, after send_tick has written its zero:
        # a hook on the emitter fires while the turn is being set up.
        await engine.send_tick("It is 09:10 on day 0.")
        await plant(engine, A_MORNINGS_ACCUMULATION)

        for _ in range(4):
            broker.queue(MODEL_ID, "sse", text_turn_sse("Still here."))
        # Drive the thread directly, bypassing send_tick's zeroing, which
        # is exactly the state an accumulation within one tick reaches.
        await engine.coordinator.handle_input(
            "You are still at the well.", thread_id=engine.thread_id
        )

        assert struggles, (
            "the struggle detector no longer fires at all - the fix "
            "disabled the mechanism instead of scoping it to a tick"
        )
        assert struggles[0].turns_without_text >= STRUGGLE_TURNS_NO_TEXT
    finally:
        await engine.shutdown()


# ── what is written, and how often ───────────────────────────────────────


@pytest.mark.asyncio
async def test_the_tick_writes_one_zeroed_entry_after_the_stale_one(
    tmp_path, monkeypatch
):
    """Order is the whole mechanism: `thread.py` takes the LAST matching
    entry, so a zero written before the stale one would change nothing."""
    from src.sim.model_profile import MODEL_ID
    from tests.sim_fake_citadel import text_turn_sse

    engine, broker = await booted(tmp_path, monkeypatch)
    try:
        await plant(engine, A_MORNINGS_ACCUMULATION)
        for _ in range(4):
            broker.queue(MODEL_ID, "sse", text_turn_sse("Morning."))
        await engine.send_tick("It is 09:10 on day 0.")

        found = await struggle_entries(engine)
        assert len(found) >= 2
        assert found[0] == A_MORNINGS_ACCUMULATION, "the planted entry moved"
        assert is_zeroed(found[1]), (
            "ENG-22: the entry the tick wrote is not a zeroed one, so the "
            f"turn is still judged on what came before it: {found[1]}"
        )
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_one_zero_per_tick_and_not_one_per_model_turn(tmp_path, monkeypatch):
    """A tick is several agent-loop turns. Zeroing at every one of them
    would mean a resident could call the same tool ten times within one
    perception and never be caught."""
    from src.sim.model_profile import MODEL_ID
    from tests.sim_fake_citadel import text_turn_sse, tool_use_turn_sse

    engine, broker = await booted(tmp_path, monkeypatch)
    try:
        # One tick, two agent-loop turns: a tool call, then an answer.
        broker.queue(MODEL_ID, "sse", tool_use_turn_sse("recall_session_log", {"turns": 2}))
        for _ in range(4):
            broker.queue(MODEL_ID, "sse", text_turn_sse("Right, I remember."))
        await engine.send_tick("What were you doing?")

        found = await struggle_entries(engine)
        zeroed = [s for s in found if is_zeroed(s)]
        assert len(zeroed) == 1, (
            f"expected exactly one zeroed entry for one tick, got {len(zeroed)} "
            f"of {len(found)} entries"
        )
        assert any(not is_zeroed(s) for s in found), (
            "the engine's own post-tool writes are gone - this test is no "
            "longer measuring what it thinks it is"
        )
    finally:
        await engine.shutdown()


def test_the_entry_matches_the_prefix_the_engine_looks_for():
    """`thread.py:3403` matches on `content.startswith('{\"_struggle\":')`.
    A json.dumps separator change, or an extra key, and the seam's entry
    is skipped silently and this finding comes back with no test red."""
    entry = json.dumps({"_struggle": StrugglingState().to_dict()})
    assert entry.startswith(STRUGGLE_PREFIX)
    restored = StrugglingState.from_dict(json.loads(entry)["_struggle"])
    assert restored == StrugglingState()
    assert restored.check(has_charter=False) is None
    assert restored.check(has_charter=True) is None
