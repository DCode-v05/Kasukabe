"""file_subpoena / grant_subpoena / deny_subpoena: DESIGN.md section 2's
"Court subpoena via a valid warrant reveals invocation records," and the
`ToolInvocationRecord` log (registry.py's `dispatch()`) that makes it mean
something.
"""

from __future__ import annotations

import pytest

from world.charter.models import GovernmentState
from world.tools.errors import ActorRequirementError, ToolNotFoundError
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX
from world.tools.tests.conftest import spawn_agent


async def make_government(world, pm_id: str, minister_id: str) -> None:
    await spawn_agent(world, pm_id, LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, minister_id, LOCATION_GOVERNMENT_COMPLEX)
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id=pm_id, minister_agent_ids=[minister_id])
    )


async def file_a_complaint(world, registry, complainant_id: str, target_id: str) -> str:
    """`complainant_id` must already be a spawned agent (both `pm` and
    `minister` are spawned by `make_government` above); re-spawning here
    would collide with the ledger account `make_government` already
    opened for them.
    """
    result = await registry.execute(
        world,
        complainant_id,
        "file_complaint",
        {"against_agent_id": target_id, "description": "suspicious dealings"},
    )
    return result.result["case_id"]


@pytest.mark.asyncio
async def test_grant_subpoena_reveals_target_invocation_records(world, registry) -> None:
    await make_government(world, "pm", "minister")
    await spawn_agent(world, "target", LOCATION_GOVERNMENT_COMPLEX)
    case_id = await file_a_complaint(world, registry, "pm", "target")

    # target does something ordinary (PUBLIC namespace) - still logged.
    await registry.execute(world, "target", "move", {"destination_location_id": "park"})

    filing = await registry.execute(
        world,
        "pm",
        "file_subpoena",
        {"case_id": case_id, "target_agent_id": "target", "justification": "investigating a case"},
    )
    subpoena_id = filing.result["subpoena_id"]

    granted = await registry.execute(world, "minister", "grant_subpoena", {"subpoena_id": subpoena_id})

    records = granted.result["invocation_records"]
    assert any(r["tool_name"] == "move" and r["actor_id"] == "target" for r in records)
    # Nothing about another agent leaks through.
    assert all(r["actor_id"] == "target" for r in records)


@pytest.mark.asyncio
async def test_grant_subpoena_reveals_confidential_namespace_calls_too(world, registry) -> None:
    """DESIGN.md section 2: "Everything is still logged for researchers
    regardless of in-world secrecy." A GOVERNMENT-namespace call an
    ordinary agent could never even see exists is still in the log a
    valid warrant unlocks.
    """
    await make_government(world, "pm", "minister")
    case_id = await file_a_complaint(world, registry, "minister", "pm")

    # pm exercises a confidential GOVERNMENT-namespace tool.
    await registry.execute(
        world, "pm", "petition", {"title": "More farmland", "description": "need it", "tile_count_requested": 5}
    )

    filing = await registry.execute(
        world, "minister", "file_subpoena", {"case_id": case_id, "target_agent_id": "pm", "justification": "audit"}
    )
    granted = await registry.execute(world, "pm", "grant_subpoena", {"subpoena_id": filing.result["subpoena_id"]})

    records = granted.result["invocation_records"]
    assert any(r["tool_name"] == "petition" and r["namespace"] == "government" for r in records)


@pytest.mark.asyncio
async def test_subpoena_reveal_never_appears_on_the_public_event_bus(world, registry) -> None:
    """The reveal must reach only the calling court official (`result`),
    never every event-bus subscriber (`events`) - see justice.py's module
    docstring for why broadcasting a confidential trade secret's call
    history on `crime.subpoena.granted` would defeat namespace secrecy for
    anyone else listening to the crime domain.
    """
    await make_government(world, "pm", "minister")
    await spawn_agent(world, "target", LOCATION_GOVERNMENT_COMPLEX)
    case_id = await file_a_complaint(world, registry, "pm", "target")
    await registry.execute(world, "target", "move", {"destination_location_id": "park"})
    filing = await registry.execute(
        world, "pm", "file_subpoena", {"case_id": case_id, "target_agent_id": "target", "justification": "x"}
    )

    seen_events = []

    async def record_event(event) -> None:
        seen_events.append(event)

    world.event_bus.subscribe("crime", record_event)

    granted = await registry.execute(world, "minister", "grant_subpoena", {"subpoena_id": filing.result["subpoena_id"]})

    grant_events = [e for e in seen_events if e.type == "crime.subpoena.granted"]
    assert len(grant_events) == 1
    assert "invocation_records" not in grant_events[0].payload
    assert grant_events[0].payload["record_count"] == len(granted.result["invocation_records"])


@pytest.mark.asyncio
async def test_subpoena_requires_a_different_government_member_to_decide(world, registry) -> None:
    await make_government(world, "pm", "minister")
    await spawn_agent(world, "target", LOCATION_GOVERNMENT_COMPLEX)
    case_id = await file_a_complaint(world, registry, "pm", "target")
    filing = await registry.execute(
        world, "pm", "file_subpoena", {"case_id": case_id, "target_agent_id": "target", "justification": "x"}
    )

    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "pm", "grant_subpoena", {"subpoena_id": filing.result["subpoena_id"]})


@pytest.mark.asyncio
async def test_deny_subpoena_does_not_reveal_anything(world, registry) -> None:
    await make_government(world, "pm", "minister")
    await spawn_agent(world, "target", LOCATION_GOVERNMENT_COMPLEX)
    case_id = await file_a_complaint(world, registry, "pm", "target")
    filing = await registry.execute(
        world, "pm", "file_subpoena", {"case_id": case_id, "target_agent_id": "target", "justification": "x"}
    )

    result = await registry.execute(
        world, "minister", "deny_subpoena", {"subpoena_id": filing.result["subpoena_id"], "reason": "not warranted"}
    )
    assert "invocation_records" not in result.result
    assert world.subpoenas[filing.result["subpoena_id"]].status.value == "denied"


@pytest.mark.asyncio
async def test_subpoena_tools_are_government_namespace_existence_leak(world, registry) -> None:
    await spawn_agent(world, "citizen", LOCATION_GOVERNMENT_COMPLEX)
    with pytest.raises(ToolNotFoundError):
        await registry.execute(
            world, "citizen", "file_subpoena", {"case_id": "whatever", "target_agent_id": "whoever", "justification": "x"}
        )


@pytest.mark.asyncio
async def test_failed_call_is_not_added_to_the_invocation_log(world, registry) -> None:
    """Companion to registry.py's `dispatch()`/`execute()` atomicity
    guarantee: a call that raises must not leave a phantom
    ToolInvocationRecord behind, or a subpoena could reveal something that
    never actually happened.
    """
    await spawn_agent(world, "a1", "park")
    before = len(world.tool_invocations)

    with pytest.raises(Exception):
        await registry.execute(world, "a1", "move", {})  # missing required param

    assert len(world.tool_invocations) == before


@pytest.mark.asyncio
async def test_successful_call_is_added_to_the_invocation_log_with_correct_shape(world, registry) -> None:
    await spawn_agent(world, "a1", "park")

    await registry.execute(world, "a1", "move", {"destination_location_id": "cafe"})

    record = world.tool_invocations[-1]
    assert record.tool_name == "move"
    assert record.actor_id == "a1"
    assert record.namespace == "public"
    assert record.params == {"destination_location_id": "cafe"}
