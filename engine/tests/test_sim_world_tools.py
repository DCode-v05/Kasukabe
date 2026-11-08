"""World tools: the HTTP translation layer, and the ToolDefs built from it.

The last test in this file (test_a_world_tool_call_from_the_model_reaches_the_
world_server) drives a REAL ExecutionThread's agent loop with a scripted LLM
adapter (the "fake broker client" stub form CONVENTIONS.md names alongside
an httpx mock transport) to prove task 5's translation end-to-end: a
tool_use block the model emits results in an HTTP POST to the (mocked)
world server, and the world's answer comes back as the tool_result the next
model turn sees.
"""

from __future__ import annotations

from typing import AsyncIterator

import httpx
import pytest

from src.engine_core.events import EventEmitter
from src.engine_core.thread import ExecutionThread
from src.herald.types import Message, StopReason, StreamEvent, ToolCall, ToolDef as LLMToolDef, Usage
from src.memory.memory_factory import create_working_memory
from src.sandbox.permissions import PermissionBehavior
from src.sim.world_tools import (
    WORLD_TOOL_ALLOW_RULE,
    WORLD_TOOL_NAME_PREFIX,
    WorldClient,
    WorldToolSpec,
    build_tool_def,
    build_world_tool_registry,
    install_permissions,
    refresh_world_tool_registry,
)
from src.utility_directory.tool_runtime import ToolDef, ToolRegistry


async def _unused_handler(_: dict) -> str:
    raise AssertionError("not called in these tests")


def mock_world_client(handler, agent_id: str = "agent-1") -> WorldClient:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WorldClient("http://world.test", agent_id=agent_id, client=client)


# ── WorldClient: registry fetch ──────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_tool_registry_parses_world_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tools/registry"
        return httpx.Response(
            200,
            json=[
                {"name": "move", "description": "Walk somewhere.", "input_schema": {"type": "object"}},
                {"name": "harvest", "description": "Pick a crop."},
            ],
        )

    world = mock_world_client(handler)
    specs = await world.fetch_tool_registry()

    assert [s.name for s in specs] == ["move", "harvest"]
    assert specs[0].description == "Walk somewhere."
    # A missing input_schema defaults to an empty object schema, not KeyError.
    assert specs[1].input_schema == {"type": "object", "properties": {}}


# ── WorldClient: tool_calls ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_tool_posts_the_documented_contract_and_returns_result():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"result": "you moved north"})

    world = mock_world_client(handler, agent_id="agent-42")
    result = await world.call_tool("move", {"direction": "north"})

    assert captured["path"] == "/agents/agent-42/tool_calls"
    assert captured["body"] == {"tool": "move", "input": {"direction": "north"}}
    assert result == "you moved north"


@pytest.mark.asyncio
async def test_call_tool_error_response_becomes_error_text_not_an_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "not at the mill"})

    world = mock_world_client(handler)
    result = await world.call_tool("mill", {})

    assert result == "Error: not at the mill"


@pytest.mark.asyncio
async def test_call_tool_transport_failure_becomes_error_text_not_an_exception():
    """The original property: a dead socket is data, not a crash.

    TRADE-05 changed the WORDING, on purpose. It used to say "world
    server unreachable", which a resident cannot tell from a refusal -
    both arrive as `Error: ...` - so it read "the world would not let
    me" and reasoned from that for the rest of a turn in which it had
    done nothing. 97 calls went that way in run `deni-a6f9eb4-5h`.
    The message now says the call never arrived and nothing was refused.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    world = mock_world_client(handler)
    result = await world.call_tool("move", {})

    assert result.startswith("Error:"), "a transport failure raised instead of answering"
    assert "never arrived" in result
    assert "nothing was refused" in result.lower()


# ── ToolDef construction ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_tool_def_prefixes_the_engine_name_and_calls_world():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"result": "ok"})

    world = mock_world_client(handler)
    spec = WorldToolSpec(name="move", description="Walk.", input_schema={"type": "object"})
    tool_def = build_tool_def(spec, world)

    assert tool_def.name == f"{WORLD_TOOL_NAME_PREFIX}move"
    result = await tool_def.handler({"direction": "east"})

    assert result == "ok"
    assert seen == [{"tool": "move", "input": {"direction": "east"}}]


@pytest.mark.asyncio
async def test_build_world_tool_registry_registers_every_spec():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry":
            return httpx.Response(
                200,
                json=[
                    {"name": "move", "description": "Walk."},
                    {"name": "harvest", "description": "Pick."},
                ],
            )
        return httpx.Response(200, json={"result": "ok"})

    world = mock_world_client(handler)
    registry = await build_world_tool_registry(world)

    assert registry.tool_count == 2
    assert registry.get_tool("world:move") is not None
    assert registry.get_tool("world:harvest") is not None
    assert registry.get_tool("move") is None  # unprefixed name is not registered engine-side


# ── Permissions ─────────────────────────────────────────────────────


class StubThread:
    def __init__(self):
        self.session_rules = []


def test_install_permissions_grants_the_allow_rule():
    thread = StubThread()
    install_permissions(thread)
    assert thread.session_rules == [WORLD_TOOL_ALLOW_RULE]
    assert WORLD_TOOL_ALLOW_RULE.behavior == PermissionBehavior.ALLOW
    assert WORLD_TOOL_ALLOW_RULE.tool == "world:*"


def test_install_permissions_is_idempotent():
    thread = StubThread()
    install_permissions(thread)
    install_permissions(thread)
    assert thread.session_rules == [WORLD_TOOL_ALLOW_RULE]


# ── End-to-end: a model's tool_use reaches the (mocked) world server ──


class ScriptedAdapter:
    """Yields one scripted StreamEvent batch per call - "fake broker client"
    stub form (CONVENTIONS.md), no network. Batch 1: a tool_use for a world
    tool. Batch 2: plain text, end_turn."""

    def __init__(self, batches: list[list[StreamEvent]]):
        self.batches = batches
        self.call_count = 0

    async def stream_with_tools(
        self,
        messages: list[Message],
        system=None,
        tools: list[LLMToolDef] | None = None,
        super_thinking: bool = False,
        max_output_tokens: int | None = None,
        skip_cache_write: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        batch = self.batches[self.call_count]
        self.call_count += 1
        for event in batch:
            yield event

    async def generate(self, messages, system=""):
        return "summary"


def tool_use_then_text_batches() -> list[list[StreamEvent]]:
    return [
        [
            StreamEvent(
                type="tool_call",
                tool_call=ToolCall(id="t1", name="world:move", input={"direction": "north"}),
            ),
            StreamEvent(type="stop", stop_reason=StopReason.TOOL_USE),
            StreamEvent(type="usage", usage=Usage(input_tokens=5, output_tokens=5)),
        ],
        [
            StreamEvent(type="text", text="You moved north into the field."),
            StreamEvent(type="stop", stop_reason=StopReason.END_TURN),
            StreamEvent(type="usage", usage=Usage(input_tokens=5, output_tokens=5)),
        ],
    ]


@pytest.mark.asyncio
async def test_a_world_tool_call_from_the_model_reaches_the_world_server(db):
    world_calls = []

    def world_handler(request: httpx.Request) -> httpx.Response:
        import json

        if request.url.path == "/tools/registry":
            return httpx.Response(
                200, json=[{"name": "move", "description": "Walk somewhere."}]
            )
        world_calls.append(json.loads(request.content))
        return httpx.Response(200, json={"result": "you are now standing in the field"})

    world = mock_world_client(world_handler, agent_id="agent-e2e")
    tools = await build_world_tool_registry(world)

    thread = ExecutionThread(
        adapter=ScriptedAdapter(tool_use_then_text_batches()),
        emitter=EventEmitter(),
        memory=create_working_memory("sess-world", db=db),
        tools=tools,
    )
    install_permissions(thread)

    result = await thread.execute("look around", thread_id="t-world-e2e")

    # The translation happened: the world server received exactly the tool
    # call the model made, unprefixed, with the model's own input.
    assert world_calls == [{"tool": "move", "input": {"direction": "north"}}]
    # And the model's final turn (informed by the tool result) is what the
    # caller gets back.
    assert result == "You moved north into the field."


# ── Part B: a tool invented mid-run becomes callable ──────────────────


@pytest.mark.asyncio
async def test_refresh_adds_a_tool_the_town_invented_after_boot():
    """THE blocker Part B removes. build_world_tool_registry runs once, at
    boot, so before this an agent could compose a tool at tick 500 and never
    be able to call it - not even its inventor - until the town restarted."""
    state = {"version": 1, "rows": [{"name": "move", "description": "Walk."}]}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry/version":
            return httpx.Response(200, json={"version": state["version"]})
        if request.url.path == "/tools/registry":
            return httpx.Response(200, json=state["rows"])
        raise AssertionError(f"unexpected: {request.url.path}")

    world = mock_world_client(handler)
    registry = await build_world_tool_registry(world)
    assert set(registry.tools) == {f"{WORLD_TOOL_NAME_PREFIX}move"}

    state["version"] = 2
    state["rows"] = state["rows"] + [{"name": "rope_making", "description": "Twist fibre."}]

    version = await refresh_world_tool_registry(world, registry, 1)
    assert version == 2
    assert f"{WORLD_TOOL_NAME_PREFIX}rope_making" in registry.tools, (
        "the invented tool is now callable"
    )
    assert len(registry.tools) == 2, "move was not rebuilt, only rope_making added"


@pytest.mark.asyncio
async def test_refresh_is_one_small_call_when_nothing_changed():
    """Almost every tick. Refetching the whole vocabulary just to learn that
    it is unchanged would multiply by 40 engines, every tick, forever."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/tools/registry/version":
            return httpx.Response(200, json={"version": 7})
        return httpx.Response(200, json=[])

    world = mock_world_client(handler)
    registry = ToolRegistry()

    version = await refresh_world_tool_registry(world, registry, 7)

    assert version == 7
    assert calls == ["/tools/registry/version"], "the full registry was never fetched"


@pytest.mark.asyncio
async def test_refresh_keeps_tools_the_engine_registered_itself():
    """The engine adds its own session-log and memory-stream tools onto the
    same registry after boot. Rebuilding a fresh one would drop them silently,
    so refresh ADDS rather than replaces."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry/version":
            return httpx.Response(200, json={"version": 9})
        return httpx.Response(200, json=[{"name": "move", "description": "Walk."}])

    world = mock_world_client(handler)
    registry = await build_world_tool_registry(world)
    registry.register(
        ToolDef(
            name="recall_session_log",
            description="local",
            input_schema={},
            handler=_unused_handler,
        )
    )

    await refresh_world_tool_registry(world, registry, 0)

    assert "recall_session_log" in registry.tools
    assert f"{WORLD_TOOL_NAME_PREFIX}move" in registry.tools


@pytest.mark.asyncio
async def test_a_world_without_the_version_route_is_not_an_error():
    """An older world server 404s. A staleness check is not worth failing a
    tick over - the engine keeps what it has and asks again next tick."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tools/registry/version":
            return httpx.Response(404)
        return httpx.Response(200, json=[])

    world = mock_world_client(handler)
    assert await world.fetch_tool_registry_version() == 0
