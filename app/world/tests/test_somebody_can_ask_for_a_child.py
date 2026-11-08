"""The blessing queue can be filled by the town it is for.

`UI-05`. The God console's baby-blessing queue, its panel, the grant and
deny routes, the population governor and the whole of
`world/life/birth.py` were built and tested - and on the host every run
actually uses there was NO WAY TO PUT ANYTHING IN IT.
`submit_birth_request` existed only as a method on the retired standalone
`WorldServer`: no HTTP route, no tool, no caller. The embedded runner
built `AdminExtras(operator_token=...)` with an empty `birth_requests`
dict that nothing ever wrote to.

So the queue was empty in every run this project has made, and an
operator reading it learned "nobody wants a child" from a mechanism that
could not have said anything else.

This project's most common defect, in its usual shape: a complete, tested
mechanism with no caller. What makes this one worth its own test is that
the queue is what the population governor and the whole birth path hang
off, so an empty queue means none of it ever ran.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from world.life.models import BirthRequestStatus
from world.run_genesis import (
    GenesisRunner,
    GenesisRunnerConfig,
    build_embedded_world_app,
)
from world.tools.errors import ActorRequirementError


@pytest_asyncio.fixture
async def runner(tmp_path):
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    return runner


def _housed(runner) -> str:
    """Somebody who actually lives in a household - a request names one."""
    for agent_id, agent in runner.world_state.agents.items():
        if agent.household_id and agent.household_id in runner.world_state.households:
            return agent_id
    pytest.skip("this genesis put nobody in a household")


async def _ask(runner, agent_id: str, **params):
    return await runner.registry.execute(
        runner.world_state, agent_id, "ask_for_a_child", params
    )


@pytest.mark.asyncio
async def test_the_tool_exists_at_all(runner) -> None:
    """The finding, at its simplest. Nothing residents could call put
    anything in the queue."""
    names = set(runner.registry.tools)
    assert "ask_for_a_child" in names, (
        "UI-05: no tool anywhere lets a resident ask for a child, so the "
        "operator's blessing queue can never contain anything"
    )


@pytest.mark.asyncio
async def test_asking_puts_a_request_in_the_queue(runner) -> None:
    who = _housed(runner)
    assert runner.world_state.birth_requests == {}

    await _ask(runner, who)

    assert len(runner.world_state.birth_requests) == 1
    request = next(iter(runner.world_state.birth_requests.values()))
    assert request.status is BirthRequestStatus.PENDING
    assert request.sponsor_parent_id == who


@pytest.mark.asyncio
async def test_the_console_reads_what_the_town_wrote(runner) -> None:
    """The other half of the finding: the queue the console shows was a
    DIFFERENT dict from any the world could write to."""
    who = _housed(runner)
    extras = runner.build_admin_extras()
    assert extras.birth_requests is runner.world_state.birth_requests, (
        "UI-05: the console is handed a different dict from the one the "
        "town writes to, so the queue can never show anything"
    )
    app = build_embedded_world_app(
        runner.world_state, runner.registry, runner.ledger, runner.metrics,
        tick_engine=runner.tick_engine, genesis=runner.genesis,
        map_service=runner.map_service, admin_extras=extras,
    )
    await _ask(runner, who)

    with TestClient(app) as client:
        rows = client.get(
            "/admin/god/blessings",
            headers={"X-Operator-Token": extras.operator_token},
        ).json()

    assert rows, (
        "UI-05: a resident asked for a child and the operator's console "
        "shows an empty queue - the two are looking at different objects"
    )
    assert rows[0]["sponsor_parent_id"] == who


@pytest.mark.asyncio
async def test_asking_twice_does_not_flood_the_queue(runner) -> None:
    """A resident that reaches for this every turn would make the queue
    unreadable, which is the same as having no queue."""
    who = _housed(runner)
    first = await _ask(runner, who)
    second = await _ask(runner, who)

    assert len(runner.world_state.birth_requests) == 1
    assert first.result["request_id"] == second.result["request_id"]
    assert "already asked" in second.result["note"]


@pytest.mark.asyncio
async def test_the_spouse_is_named_as_a_parent(runner) -> None:
    """A child is born to a household, and the record must say who asked
    for it - the grant path reads `parent_ids`."""
    who = _housed(runner)
    household_id = runner.world_state.agents[who].household_id
    other = next(
        (a for a, rec in runner.world_state.agents.items()
         if a != who and rec.household_id == household_id),
        None,
    )
    if other is None:
        pytest.skip("this household has one member")

    await _ask(runner, who, with_agent_id=other)
    request = next(iter(runner.world_state.birth_requests.values()))
    assert set(request.parent_ids) == {who, other}


@pytest.mark.asyncio
async def test_somebody_outside_the_household_cannot_be_named(runner) -> None:
    who = _housed(runner)
    household_id = runner.world_state.agents[who].household_id
    stranger = next(
        a for a, rec in runner.world_state.agents.items()
        if rec.household_id != household_id
    )
    with pytest.raises(ActorRequirementError):
        await _ask(runner, who, with_agent_id=stranger)
    assert runner.world_state.birth_requests == {}


@pytest.mark.asyncio
async def test_the_queue_is_owned_state(runner) -> None:
    """It survives a checkpoint. A request the town made and the operator
    has not yet answered must not vanish on a resume."""
    from world.tools.state import OWNED_STATE_FIELDS

    assert "birth_requests" in OWNED_STATE_FIELDS
