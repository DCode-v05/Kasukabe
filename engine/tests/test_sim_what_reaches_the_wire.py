"""ENG-26: assert the SHAPE of what reaches the broker, not just the model id.

`test_sim_fake_broker.py` proved exactly one model id ever leaves the guard.
It said nothing about what the request carried - and what it carried was the
problem: ~14K tokens of tool schemas (ENG-03), a day-0 perception pinned in
by emergency compaction, and assistant-genre text like "[Post-compact tool
reminder] These tools remain available" in a villager's prompt (ENG-02).

Three assertions, all on the real request body:

  * the tool schemas fit a budget;
  * no assistant-genre marker appears in a resident's prompt;
  * the whole request fits inside the window it is billed against.
"""
from __future__ import annotations

import json

import httpx
import pytest

from src.sim.config import SimProfile
from src.sim.pinned_boot import boot_sim_engine
from src.sim.world_tools import WorldClient

from tests.sim_fake_citadel import FakeBroker, install_fake_broker, text_turn_sse


# Text that belongs to a coding assistant, not to a person living in a town.
# Each of these was observed in a real run-23 prompt.
ASSISTANT_MARKERS = (
    "[Post-compact tool reminder]",
    "[Context restored after compaction]",
    "This session is being continued from a previous conversation",
    "Files Touched",
    "Tool Operations",
)


def _world_with(tools: list[dict], agent_id: str) -> WorldClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=tools)
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "1"})
        return httpx.Response(200, json={"result": "ok"})

    return WorldClient(
        "http://world.test",
        agent_id=agent_id,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    b = FakeBroker()
    install_fake_broker(monkeypatch, b)
    return b


@pytest.mark.asyncio
async def test_the_request_carries_no_assistant_genre_text(tmp_path, broker):
    """ENG-02's symptom, guarded at the wire.

    A resident diagnosed this out loud in run 23: "the compaction is
    occurring BETWEEN my thought and the action". The prompt it was reading
    said "[Post-compact tool reminder] These tools remain available".

    Honest about what this is: a REGRESSION GUARD, not a proof. A single
    short turn does not trip compaction even with auto-compaction on, so
    this test passes either way today - it catches the marker if it ever
    reappears, on any turn. The proof that ENG-02's window arithmetic was
    broken is `test_sim_bounded_history.py`, which was watched to fail.
    """
    from src.sim.model_profile import MODEL_ID

    profile = SimProfile(
        agent_id="agent-wire",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    broker.queue(MODEL_ID, "sse", text_turn_sse("I walk to the mill."))
    engine = await boot_sim_engine(
        profile, "http://world.test", world_client=_world_with([], profile.agent_id)
    )
    try:
        await engine.send_tick("Dawn. You are home.")
    finally:
        await engine.shutdown()

    assert broker.request_bodies, "nothing reached the broker at all"
    body = json.dumps(broker.request_bodies[-1])
    found = [m for m in ASSISTANT_MARKERS if m in body]
    assert not found, (
        f"a villager's prompt carries assistant-genre text: {found}. That is "
        "the engine rewriting the prefix underneath the seam (ENG-02)."
    )


@pytest.mark.asyncio
async def test_the_tools_on_the_wire_fit_a_budget(tmp_path, broker):
    """ENG-03. The schemas were two thirds of every turn's bill."""
    from src.sim.model_profile import MODEL_ID

    profile = SimProfile(
        agent_id="agent-tools",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    # A registry the size the town actually serves, half of it indexed.
    tools = [
        {
            "name": f"verb_{i}",
            "description": "Does a thing." if i % 2 else "Only at X; `move` takes you there.",
            "input_schema": {"type": "object", "properties": {}},
        }
        for i in range(80)
    ]
    broker.queue(MODEL_ID, "sse", text_turn_sse("ok"))
    engine = await boot_sim_engine(
        profile, "http://world.test", world_client=_world_with(tools, profile.agent_id)
    )
    try:
        await engine.send_tick("Dawn.")
    finally:
        await engine.shutdown()

    sent = broker.request_bodies[-1].get("tools") or []
    chars = len(json.dumps(sent))
    assert chars < 60_000, (
        f"{chars:,} chars of tool schemas on one call. Run 23's whole turn "
        "input was 15-20K tokens and this was most of it (ENG-03)."
    )


@pytest.mark.asyncio
async def test_the_whole_request_fits_the_window_it_is_billed_against(tmp_path, broker):
    """A turn that does not fit is a turn the engine will compact, which is
    the loop ENG-02 was stuck in."""
    from src.sim.model_profile import MODEL_ID, SIM_CONTEXT_WINDOW

    profile = SimProfile(
        agent_id="agent-fit",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    broker.queue(MODEL_ID, "sse", text_turn_sse("ok"))
    engine = await boot_sim_engine(
        profile, "http://world.test", world_client=_world_with([], profile.agent_id)
    )
    try:
        await engine.send_tick("Dawn.")
    finally:
        await engine.shutdown()

    chars = len(json.dumps(broker.request_bodies[-1]))
    est_tokens = chars // 4
    assert est_tokens < SIM_CONTEXT_WINDOW, (
        f"~{est_tokens:,} estimated tokens against a {SIM_CONTEXT_WINDOW:,} "
        "window: the very first turn does not fit, so every call compacts"
    )
