"""A refusal a resident can act on came back as "Internal Server Error".

The 50-resident run: Camille Quarshie put `search_memory` - one of the
engine's own tools, not a world tool - inside a `do_in_order` program. The
world refused it correctly ("'search_memory' is neither a primitive this town
has nor a tool you can call ... You may also build on tools you can already
call: ...") and the resident was shown `Error: Internal Server Error`.

The refusal carried, as help, the list of tools the resident could build on
(`WorldState.authoring_context`), and each entry named where the tool can be
used - `spec.location_gate`, raw. For a tool whose place depends on what you
name ("wherever the thing you name is") that gate is a FUNCTION, and the
route could not turn a function into JSON. So every refusal from
`do_in_order`, `skillify` and `toolify` that listed such a tool was a 500,
with the one message that would have helped thrown away.

`describe_primitives` carried the same function in its answer.

Fixed at the source - a gate is written as words, as the index row already
does ("elsewhere") - and the route now cannot 500 on a refusal whatever it
carries, because a refusal the resident cannot read is worse than no help.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _has_a_callable(value) -> bool:
    if callable(value) and not isinstance(value, type):
        return True
    if isinstance(value, dict):
        return any(_has_a_callable(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_a_callable(v) for v in value)
    return False


@pytest_asyncio.fixture
async def town(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True, checkpoint_dir=tmp_path / "cp", home_root=str(tmp_path / "homes"),
        world_mode="embedded", world_port=0, start_at_dawn=True,
    ))
    await runner.boot(resume=False)
    runner.build_world_app()
    return runner


@pytest.mark.asyncio
async def test_a_program_naming_an_engine_tool_is_refused_not_crashed(town) -> None:
    """The exact call from the run, through the route an engine uses."""
    agent = sorted(town.world_state.agents)[0]
    client = TestClient(town.world_app, raise_server_exceptions=False)

    resp = client.post(f"/agents/{agent}/tool_calls", json={"tool": "do_in_order", "input": {
        "steps": [{"tool_name": "search_memory", "params": {"query": "candidates for election"}}],
    }})

    assert resp.status_code == 400, f"HTTP {resp.status_code}: {resp.text[:200]}"
    assert "search_memory" in resp.text, "the reason the resident needed is gone"


@pytest.mark.asyncio
async def test_the_help_a_refusal_carries_is_plain_data(town) -> None:
    agent = sorted(town.world_state.agents)[0]

    context = await town.world_state.authoring_context(agent)

    assert not _has_a_callable(context), "a function in the help is a 500 on the wire"
    json.dumps(context)


@pytest.mark.asyncio
async def test_a_place_that_depends_on_what_you_name_is_said_in_words(town) -> None:
    agent = sorted(town.world_state.agents)[0]
    context = await town.world_state.authoring_context(agent)

    dynamic = [row for row in context["tools_you_can_use"]
               if row["at_location"] not in (None, "") and ":" not in str(row["at_location"])
               and "_" not in str(row["at_location"])]
    assert dynamic, "no tool with a place that depends on its target - the fixture changed"
    assert all(isinstance(row["at_location"], str) for row in dynamic)


@pytest.mark.asyncio
async def test_describe_primitives_answers_in_plain_data(town) -> None:
    agent = sorted(town.world_state.agents)[0]

    result = await town.registry.execute(town.world_state, agent, "describe_primitives", {})

    assert not _has_a_callable(result.result)
    json.dumps(result.result)


@pytest.mark.asyncio
async def test_the_route_cannot_500_on_a_refusal_whatever_it_carries(town, monkeypatch) -> None:
    """Defence in depth: the next thing somebody puts in a refusal's help
    must not take the refusal down with it."""
    from world.tools.errors import ToolValidationError

    async def refuses(*a, **k):
        raise ToolValidationError("look_around", ["no"], context={"oops": lambda: None})

    monkeypatch.setattr(town.registry, "execute", refuses)
    agent = sorted(town.world_state.agents)[0]
    client = TestClient(town.world_app, raise_server_exceptions=False)

    resp = client.post(f"/agents/{agent}/tool_calls", json={"tool": "look_around", "input": {}})

    assert resp.status_code == 400, f"HTTP {resp.status_code}"
