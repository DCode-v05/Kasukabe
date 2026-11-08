"""ENG-21: a monologue that hits the response cap must not spend a second
model call out of the world's two-call floor.

THE BUG, in the two files that make it. `sim/config.py` caps one response
at 768 output tokens. `app/world/call_budget.py` caps a tick at
`MIN_CALLS_PER_TICK = 2` calls minimum, and `send_tick` passes that in as
`RequestOptions.max_turns`, which is literally
`for turn in range(state.max_turns)` in `thread.py`. Inside that loop,
frozen MAX_TOKENS recovery answers a truncated response by `continue`-ing
the SAME loop with a "Resume directly" prompt - so:

    call 1  the resident starts writing -> stop_reason=max_tokens
    call 2  the continuation -> the resident keeps writing
    ----->  the for-loop is exhausted; exit_reason = max_turns, turns = 2

`pinned_boot.send_tick` reads `exit_reason == "max_turns"` back into
`last_turn_hit_budget`, `agent_adapter` returns it on /tick, and
`world/retrieval_depth.py` prints `call_budget.spent_line(2)` into the next
perception: "You ran out of energy for thinking last turn after 2 steps."
Nothing was attempted. Both "steps" were one uninterrupted sentence.

WHY THE FIRST TEST HERE IS THE ONE THAT MATTERS. The three below it are
about the wrapper. The first drives `boot_sim_engine` + `send_tick` - the
real production entry point, the real Coordinator, the real
ExecutionThread, the real herald Provider, with only the TCP socket
replaced - and counts what reached the wire. A test that only exercised
`_NoContinuationAdapter` would stay green if nobody ever wrapped the
adapter with it, which is this codebase's most common defect.
"""

from __future__ import annotations

import httpx
import pytest

from src.herald import catalog_sync
from src.herald.types import StopReason, StreamEvent
from src.sim.bounded_monologue import end_the_turn_on_a_cut_monologue
from src.sim.config import SimProfile
from src.sim.model_profile import MODEL_ID
from src.sim.pinned_boot import boot_sim_engine

from tests.sim_fake_citadel import (
    FakeBroker,
    cut_off_after_a_tool_call_sse,
    cut_off_monologue_sse,
    install_fake_broker,
    text_turn_sse,
)


def empty_world_client(agent_id: str) -> httpx.AsyncClient:
    """A world with no tools of its own. ENG-21 is about the stop reason
    crossing the seam, not about tool translation."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"result": "ok"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    b = FakeBroker()
    install_fake_broker(monkeypatch, b)
    return b


async def boot(tmp_path, agent_id: str = "agent-cut"):
    from src.sim.world_tools import WorldClient

    catalog_sync.runtime_overlay.clear()
    profile = SimProfile(
        agent_id=agent_id,
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    world = WorldClient("http://world.test", agent_id=agent_id, client=empty_world_client(agent_id))
    return await boot_sim_engine(profile, "http://world.test", world_client=world)


@pytest.mark.asyncio
async def test_a_cut_off_monologue_ends_the_tick_instead_of_eating_the_budget(
    tmp_path, monkeypatch, broker
):
    """One truncated sentence must cost ONE call, not the whole floor."""
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    engine = await boot(tmp_path)
    try:
        broker.queue(MODEL_ID, "sse", cut_off_monologue_sse("I have been thinking about the"))
        broker.queue(MODEL_ID, "sse", text_turn_sse("...and so I conclude."))

        reply = await engine.send_tick("look around", call_budget=2)

        streamed = [b for b in broker.request_bodies if b.get("stream")]
        assert len(streamed) == 1, (
            "the truncated monologue bought a continuation: the frozen "
            "MAX_TOKENS recovery in thread.py re-entered the same bounded "
            f"agent loop, so {len(streamed)} model calls were billed for one "
            "uninterrupted sentence (ENG-21)"
        )
        assert engine.last_turn_steps == 1, (
            f"the world will be told this tick took {engine.last_turn_steps} "
            "steps when the resident spoke once"
        )
        assert engine.last_turn_hit_budget is False, (
            "exit_reason came back max_turns, so the next perception tells "
            "this resident 'You ran out of energy for thinking last turn "
            "after 2 steps' having attempted nothing at all "
            "(world/call_budget.py::spent_line)"
        )
        assert "I have been thinking about the" in reply
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_a_cap_that_interrupts_a_real_action_still_gets_its_continuation(
    tmp_path, monkeypatch, broker
):
    """The other half of the fix: only a MONOLOGUE loses its continuation.

    A stream that produced a tool call and then hit the cap is the engine
    doing real work - it tombstones the interrupted results and lets the
    model finish the act it started - and a call spent finishing an action
    is what the budget is FOR. A fix that swallowed this one would be
    cheaper and wrong.
    """
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    engine = await boot(tmp_path, agent_id="agent-act")
    try:
        broker.queue(
            MODEL_ID,
            "sse",
            cut_off_after_a_tool_call_sse("recall_session_log", {"limit": 3}),
        )
        broker.queue(MODEL_ID, "sse", text_turn_sse("Right - back to work."))

        await engine.send_tick("look around", call_budget=2)

        streamed = [b for b in broker.request_bodies if b.get("stream")]
        assert len(streamed) == 2, (
            "an action interrupted by the output cap lost its continuation; "
            "the resident can no longer finish a call it had already begun"
        )
    finally:
        await engine.shutdown()


class _ScriptedAdapter:
    """Shaped like a herald Provider: yields the events it was handed."""

    def __init__(self, events):
        self._events = events
        self.model = MODEL_ID
        self.calls = 0

    async def stream_with_tools(self, *args, **kwargs):
        self.calls += 1
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_the_wrapper_rewrites_only_a_toolless_max_tokens_stop():
    from src.herald.types import ToolCall

    prose = [
        StreamEvent(type="text", text="thinking..."),
        StreamEvent(type="stop", stop_reason=StopReason.MAX_TOKENS, message_id="m1"),
    ]
    wrapped = end_the_turn_on_a_cut_monologue(_ScriptedAdapter(prose))
    stops = [e.stop_reason async for e in wrapped.stream_with_tools([]) if e.type == "stop"]
    assert stops == [StopReason.END_TURN]
    assert wrapped.monologues_cut == 1

    acted = [
        StreamEvent(type="tool_call", tool_call=ToolCall(id="t1", name="world:mine", input={})),
        StreamEvent(type="stop", stop_reason=StopReason.MAX_TOKENS, message_id="m2"),
    ]
    wrapped = end_the_turn_on_a_cut_monologue(_ScriptedAdapter(acted))
    stops = [e.stop_reason async for e in wrapped.stream_with_tools([]) if e.type == "stop"]
    assert stops == [StopReason.MAX_TOKENS], "an interrupted action was silently ended"
    assert wrapped.monologues_cut == 0

    finished = [
        StreamEvent(type="text", text="done"),
        StreamEvent(type="stop", stop_reason=StopReason.END_TURN, message_id="m3"),
    ]
    wrapped = end_the_turn_on_a_cut_monologue(_ScriptedAdapter(finished))
    stops = [e.stop_reason async for e in wrapped.stream_with_tools([]) if e.type == "stop"]
    assert stops == [StopReason.END_TURN]
    assert wrapped.monologues_cut == 0


@pytest.mark.asyncio
async def test_the_wrapper_hides_nothing_it_wraps():
    """A proxy that hid the adapter's model id or its non-streaming methods
    would break the engine in ways nothing in this package would notice."""
    inner = _ScriptedAdapter([])
    wrapped = end_the_turn_on_a_cut_monologue(inner)
    assert wrapped.model == MODEL_ID
    assert wrapped._inner is inner


@pytest.mark.asyncio
async def test_the_booted_engine_is_actually_wrapped(tmp_path, monkeypatch, broker):
    """A cap nothing installs is not a cap. Walk the adapter chain the
    Coordinator was actually handed and find the wrapper on it."""
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    engine = await boot(tmp_path, agent_id="agent-wrap")
    try:
        chain = []
        node = engine.coordinator.adapter
        while node is not None and len(chain) < 8:
            chain.append(type(node).__name__)
            node = getattr(node, "_inner", None)
        assert "_NoContinuationAdapter" in chain, (
            "boot_sim_engine never wrapped the primary adapter, so every "
            f"truncated monologue still buys a continuation (chain: {chain})"
        )
    finally:
        await engine.shutdown()
