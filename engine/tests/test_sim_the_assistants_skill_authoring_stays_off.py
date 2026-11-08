"""The town has one way to write a procedure, and it is `skillify`.

ENG-06 reports that "no engine skill artifact has ever been produced or
could be loaded": `skill_catalog` is None, `create_skill` is unregistered
because `data_dir` is empty, and `load_skills_from_roots` has no sim
caller. All three clauses are true. The obvious reading - "so pass a
catalog and a data_dir" - is the one this file exists to stop.

WHAT PASSING `data_dir` WOULD ACTUALLY DO. `ExecutionThread.initiate`
calls `register_create_skill_tool` unconditionally (thread.py:2230); the
method returns early only because `self.data_dir` is "" (thread.py:1823).
Set it and a miner is served a tool whose own description reads:

    "Create a reusable skill. Use when the user asks to create, save, or
     build a skill/workflow. Gather requirements via ask_user first."

There is no user, and `ask_user` is the first name on
`NOT_A_RESIDENTS_TOOL` for exactly that reason. The handler writes a
folder under `<data_dir>/skills/` that no part of Kasukabe ever reads:
no proficiency, no discount, no teaching, no council review, no cost.
The town already has all of that behind `skillify`. A second authoring
verb that does none of it is not half a skill system, it is a decoy.

WHY THE GUARD IS SHAPED LIKE THIS. `create_skill` is not registered
today, so a test that merely booted and asserted its absence would pass
with `NOT_A_RESIDENTS_TOOL` emptied - it would be guarding nothing. So
this drives the real boot, then does the one thing an ENG-06 fix would
do (set `data_dir` and let the frozen engine's own registrar run), then
runs the real prune from `pinned_boot.py:674` and the real `send_tick`,
and asserts at the WIRE that the tool is not in the array the broker was
handed. Take "create_skill" out of the frozenset and this fails.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.herald import catalog_sync
from src.sim.config import SimProfile
from src.sim.model_profile import MODEL_ID
from src.sim.pinned_boot import boot_sim_engine
from src.sim.tool_surface import NOT_A_RESIDENTS_TOOL, prune_to_the_towns_tools
from src.sim.world_tools import WorldClient

from tests.sim_fake_citadel import FakeBroker, install_fake_broker, text_turn_sse

# A perception of the shape the world actually sends (retrieval_depth.
# render_perception_text): where you are, what you hold, who is here.
PERCEPTION = (
    "It is game-day 0, 06:10 (day).\n"
    "You are Marta Thorne, the town's baker.\n"
    "You are at home:h04. Your work is at the bakery.\n"
    "You have no food. You have 5000 sim in hand.\n"
    "With you, and nobody else: Wren Thorne (n11).\n"
)


def empty_world_client(agent_id: str) -> WorldClient:
    """A world that registers no tools of its own - this test is about the
    ENGINE's built-ins, and a town vocabulary would only add noise."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=[])
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


def served_tool_names(body: dict) -> set[str]:
    """The tool names in one /v1/messages request body."""
    return {
        str(tool.get("name", ""))
        for tool in body.get("tools", [])
        if isinstance(tool, dict)
    }


@pytest.mark.asyncio
async def test_create_skill_never_reaches_the_wire_even_once_data_dir_is_wired(
    tmp_path, monkeypatch, broker
) -> None:
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    catalog_sync.runtime_overlay.clear()

    profile = SimProfile(
        agent_id="agent-skills",
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    engine = await boot_sim_engine(
        profile, "http://world.test", world_client=empty_world_client(profile.agent_id)
    )

    try:
        # Where the boot stands today: nothing is wired, so the frozen
        # engine's registrar returned early and there is nothing to prune.
        assert engine.coordinator.data_dir == ""
        assert engine.coordinator.skill_catalog is None
        assert engine.thread.tools.get_tool("create_skill") is None

        # Now do the thing ENG-06 invites: hand the thread a data_dir and
        # let thread.py's OWN registrar run - the same method initiate()
        # calls at thread.py:2230, on the real thread this boot built.
        engine.thread.data_dir = str(tmp_path / "home")
        engine.thread.register_create_skill_tool()
        assert engine.thread.tools.get_tool("create_skill") is not None, (
            "thread.py no longer registers create_skill from a data_dir - "
            "re-measure what is served before trusting this guard"
        )

        # The real prune, the same call pinned_boot.py makes after the
        # thread is built.
        removed = prune_to_the_towns_tools(engine.thread)
        assert "create_skill" in removed

        # And the real production entry point: one tick, through
        # Coordinator.handle_input to the broker.
        broker.queue(MODEL_ID, "sse", text_turn_sse("I will light the oven."))
        reply = await engine.send_tick(PERCEPTION)
        assert reply == "I will light the oven."

        body = broker.request_bodies[-1]
        names = served_tool_names(body)
        assert "create_skill" not in names, (
            "a miner is being served the assistant's skill-authoring tool; "
            "the town writes procedures with skillify"
        )
        # Nowhere in the request at all - not in the tool array, not in a
        # system-reminder, not in a restored transcript.
        assert "create_skill" not in json.dumps(body)

        # The control: the other names on the list are gone from the same
        # wire too, so a pass here cannot mean "the array was empty".
        assert not (names & NOT_A_RESIDENTS_TOOL)
        assert names, "no tools reached the wire at all - this proves nothing"
    finally:
        await engine.db.close()
