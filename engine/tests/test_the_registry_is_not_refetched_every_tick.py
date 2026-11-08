"""ENG-19: the tool registry is fetched once, not once per tick per engine.

`pinned_boot.SimEngineState.send_tick` refreshes the world tool registry at
the top of EVERY tick, with `force=LOCAL_TOOL_SCHEMAS` - and that flag is on
by default, because when schemas are served by location the world's version
counter cannot see a resident walk. `force` skipped the version check
entirely, so every tick of every engine pulled the whole ~53 KB payload and
rebuilt all 76 ToolDefs from it. Fine at 20 agents; at the roadmap's 250 it
is 13 MB a tick to be told nothing has changed.

Two savings, and they are separable, which is why there are two tests:

  * the TRANSFER, which needs the world to answer `If-None-Match` with a
    304 (world/main.py::tools_registry);
  * the REBUILD, which is this side's and works against a world that has
    never heard of an ETag - and that matters today, because the host every
    real run serves (`world/run_genesis.py`'s embedded app) has not been
    given the ETag yet.

`test_a_tool_the_town_invented_still_arrives` is the one that stops this
whole optimisation from quietly undoing the thing
`refresh_world_tool_registry` was written for: a tool invented at tick 500
must still reach its inventor without restarting the town.
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from src.herald import catalog_sync
from src.sim.config import SimProfile
from src.sim.model_profile import MODEL_ID
from src.sim.pinned_boot import boot_sim_engine
from src.sim.world_tools import WORLD_TOOL_NAME_PREFIX, WorldClient

from tests.sim_fake_citadel import FakeBroker, install_fake_broker, text_turn_sse

MINE = {"name": "mine", "description": "Mine ore here.", "input_schema": {"type": "object"}}
BAKE = {"name": "bake", "description": "Bake bread.", "input_schema": {"type": "object"}}


class FakeWorld:
    """A world that serves /tools/registry, and counts how it answered.

    ``serves_etag=False`` is `world/run_genesis.py`'s embedded app as it
    stands today: a correct world that simply does not know the question.
    """

    def __init__(self, agent_id: str, *, serves_etag: bool = True) -> None:
        self.agent_id = agent_id
        self.serves_etag = serves_etag
        self.rows = [dict(MINE)]
        self.version = 1
        self.full_payloads_served = 0
        self.not_modified_served = 0

    def _body_and_etag(self) -> tuple[bytes, str]:
        body = json.dumps(self.rows, sort_keys=True, separators=(",", ":")).encode()
        return body, '"' + hashlib.sha256(body).hexdigest()[:32] + '"'

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry/version":
            return httpx.Response(200, json={"version": self.version})
        if request.url.path == "/tools/registry":
            body, etag = self._body_and_etag()
            if self.serves_etag and request.headers.get("if-none-match") == etag:
                self.not_modified_served += 1
                return httpx.Response(304, headers={"ETag": etag})
            self.full_payloads_served += 1
            headers = {"content-type": "application/json"}
            if self.serves_etag:
                headers["ETag"] = etag
            return httpx.Response(200, content=body, headers=headers)
        return httpx.Response(200, json={"result": "ok"})

    def client(self) -> WorldClient:
        return WorldClient(
            "http://world.test",
            agent_id=self.agent_id,
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    b = FakeBroker()
    install_fake_broker(monkeypatch, b)
    return b


async def boot(tmp_path, world: FakeWorld):
    catalog_sync.runtime_overlay.clear()
    profile = SimProfile(
        agent_id=world.agent_id,
        broker_base_url="http://broker.test",
        home_root=str(tmp_path),
    )
    return await boot_sim_engine(profile, "http://world.test", world_client=world.client())


async def tick(engine, broker: FakeBroker, text: str = "I look around.") -> None:
    broker.queue(MODEL_ID, "sse", text_turn_sse(text))
    await engine.send_tick("look")


@pytest.mark.asyncio
async def test_a_world_that_answers_if_none_match_sends_the_payload_once(
    tmp_path, monkeypatch, broker
):
    """Boot pulls it. Two ticks after that pull nothing at all."""
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    world = FakeWorld("agent-etag")
    engine = await boot(tmp_path, world)
    try:
        assert world.full_payloads_served == 1, "boot must fetch the registry"
        before = engine.tools.tools[f"{WORLD_TOOL_NAME_PREFIX}mine"]

        await tick(engine, broker)
        await tick(engine, broker)

        assert world.full_payloads_served == 1, (
            "every tick pulled the whole registry again: at 250 engines that "
            f"is the 53 KB x 250 x per-tick refetch of ENG-19 "
            f"({world.full_payloads_served} full payloads for one boot and "
            "two ticks)"
        )
        assert world.not_modified_served == 2, (
            "the seam never sent If-None-Match, so the world had no way to "
            "answer 304 even though it knows how"
        )
        assert engine.tools.tools[f"{WORLD_TOOL_NAME_PREFIX}mine"] is before, (
            "the ToolDef was rebuilt from an identical payload"
        )
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_a_world_with_no_etag_still_stops_the_rebuild(tmp_path, monkeypatch, broker):
    """The host every real run serves today has no ETag, and must still win.

    A fix that only worked against an upgraded world would be worth nothing
    until `run_genesis.py`'s embedded app was changed too - and that file is
    out of this change's reach. The transfer still happens here; the 76
    ToolDef closures are not rebuilt.
    """
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    world = FakeWorld("agent-noetag", serves_etag=False)
    engine = await boot(tmp_path, world)
    try:
        before = engine.tools.tools[f"{WORLD_TOOL_NAME_PREFIX}mine"]

        await tick(engine, broker)
        await tick(engine, broker)

        assert world.not_modified_served == 0, "this world cannot 304; that is the point"
        assert engine.tools.tools[f"{WORLD_TOOL_NAME_PREFIX}mine"] is before, (
            "the ToolDef was rebuilt on every tick from a byte-identical "
            "payload - 76 closures per tick per engine for nothing"
        )
        assert engine.world.registry_unchanged == 2
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_a_tool_the_town_invented_still_arrives(tmp_path, monkeypatch, broker):
    """The reason refresh_world_tool_registry exists at all.

    A tool an agent composes at tick 500 must reach every engine without
    restarting the town. If a conditional fetch ever swallowed that, this
    change would have traded the whole invention machinery for bandwidth.
    """
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    world = FakeWorld("agent-invent")
    engine = await boot(tmp_path, world)
    try:
        await tick(engine, broker)
        assert f"{WORLD_TOOL_NAME_PREFIX}bake" not in engine.tools.tools

        world.rows.append(dict(BAKE))
        world.version = 2
        await tick(engine, broker)

        assert f"{WORLD_TOOL_NAME_PREFIX}bake" in engine.tools.tools, (
            "the town invented a tool and the conditional fetch hid it: the "
            "inventor cannot call its own invention until the run restarts"
        )
        assert engine.tool_registry_version == 2
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_a_resident_that_walks_gets_the_redescribed_schema(tmp_path, monkeypatch, broker):
    """B3/ENG-03's re-description, which the version counter cannot see.

    A tool's row is a full schema at the outcrop and a one-line index from
    the bakery. The world's registry did not change - the resident walked -
    so the version number is identical and only the BODY differs. This is
    the case `force=True` exists for, and the case a version-keyed ETag
    would have got wrong.
    """
    monkeypatch.setenv("FORK_SUBAGENT", "false")
    world = FakeWorld("agent-walk")
    engine = await boot(tmp_path, world)
    try:
        await tick(engine, broker)

        world.rows[0]["description"] = "Mine ore. Requires: ore_grade, depth_m."
        await tick(engine, broker)

        assert engine.tool_registry_version == 1, "the world's counter did not move"
        assert engine.tools.tools[f"{WORLD_TOOL_NAME_PREFIX}mine"].description == (
            "Mine ore. Requires: ore_grade, depth_m."
        ), (
            "the resident walked and kept the schema for where it used to be "
            "standing (ENG-03/ENG-16)"
        )
    finally:
        await engine.shutdown()
