"""GOVERNMENT-namespace access transfer on election handover: DESIGN.md
section 2's "access transfers ATOMICALLY on election handover with no gap
or overlap."

charter/elections.py's `tally()` performs the actual transfer of power as
one `CharterStore.set_government_state()` call, replacing the whole
`GovernmentState` value in a single write (see `CustomToolDefinition`/
`GovernmentState`'s own docstrings) - that atomicity is charter/'s to
guarantee and is exercised by charter/'s own tests. What THIS module
(world/tools/) is responsible for, and what these tests exercise, is that
registry.py's `is_visible` never caches a government-seat check: it reads
`CharterStore.get_government_state()` fresh on every single call, so the
instant that one write lands, every subsequent call sees the new holder
and none sees the old one - no separate revocation step, no window where
a stale grant lingers, and (since a `GovernmentState` is always a single
complete value, never a partial one) no window where neither the old nor
the new officeholder is recognized as sitting.
"""

from __future__ import annotations

import asyncio

import pytest

from world.charter.models import GovernmentState
from world.tools.errors import ToolNotFoundError
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX
from world.tools.tests.conftest import spawn_agent

PETITION_PARAMS = {"title": "More farmland", "description": "we need it", "tile_count_requested": 3}


async def swear_in(world, pm_id: str) -> None:
    await world.charter.store.set_government_state(GovernmentState(pm_agent_id=pm_id, minister_agent_ids=[]))


@pytest.mark.asyncio
async def test_old_pm_loses_access_the_instant_the_swap_lands(world, registry) -> None:
    await spawn_agent(world, "old_pm", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "new_pm", LOCATION_GOVERNMENT_COMPLEX)
    await swear_in(world, "old_pm")

    # Old PM can exercise a GOVERNMENT-namespace tool before the handover.
    result = await registry.execute(world, "old_pm", "petition", PETITION_PARAMS)
    assert result.result["petition_id"]

    # The handover: one atomic GovernmentState replacement, exactly what
    # charter/elections.py's tally() does.
    await swear_in(world, "new_pm")

    # No overlap: the old PM's access is gone, not merely "also" the new
    # PM's - reads as the identical error an outsider gets (existence
    # leak), never a distinguishable "you used to be allowed."
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "old_pm", "petition", PETITION_PARAMS)

    # No gap: the new PM's access is live in the same instant, not delayed
    # a tick or requiring any separate grant step.
    result = await registry.execute(world, "new_pm", "petition", PETITION_PARAMS)
    assert result.result["petition_id"]


@pytest.mark.asyncio
async def test_no_third_party_ever_sees_the_tool_exists_across_a_handover(world, registry) -> None:
    await spawn_agent(world, "old_pm", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "new_pm", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "citizen", LOCATION_GOVERNMENT_COMPLEX)
    await swear_in(world, "old_pm")

    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "citizen", "petition", PETITION_PARAMS)

    await swear_in(world, "new_pm")

    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "citizen", "petition", PETITION_PARAMS)


@pytest.mark.asyncio
async def test_handover_access_transitions_monotonically_under_concurrent_calls(world, registry) -> None:
    """A stronger, concurrency-flavored version of the two tests above:
    fire many old-PM and new-PM calls concurrently, interleaved with the
    handover itself, and prove access only ever moves old -> new, never
    flips back and never grants both sides at once. Because
    `ToolRegistry.execute()` holds `self.lock` for a call's entire
    dispatch (registry.py), calls are fully serialized end to end - what
    this test actually probes is that `is_visible`'s fresh read (no
    caching layer anywhere in tools/) makes that serialized order agree
    with whichever `GovernmentState` was live at each call's turn, with no
    call ever seeing a torn/partial value.
    """
    await spawn_agent(world, "old_pm", LOCATION_GOVERNMENT_COMPLEX)
    await spawn_agent(world, "new_pm", LOCATION_GOVERNMENT_COMPLEX)
    await swear_in(world, "old_pm")

    completion_order: list[tuple[str, str]] = []

    async def call_as(agent_id: str, hops: int) -> None:
        for _ in range(hops):
            await asyncio.sleep(0)
        try:
            await registry.execute(world, agent_id, "petition", PETITION_PARAMS)
            completion_order.append((agent_id, "granted"))
        except ToolNotFoundError:
            completion_order.append((agent_id, "denied"))

    async def do_handover(hops: int) -> None:
        for _ in range(hops):
            await asyncio.sleep(0)
        await swear_in(world, "new_pm")
        completion_order.append(("handover", "done"))

    tasks = [call_as("old_pm", 0), call_as("new_pm", 0), call_as("old_pm", 1), call_as("new_pm", 1)]
    tasks += [do_handover(2)]
    tasks += [call_as("old_pm", 3), call_as("new_pm", 3)]
    tasks += [call_as("old_pm", 4), call_as("new_pm", 4)]
    await asyncio.gather(*tasks)

    handover_index = next(i for i, (who, _) in enumerate(completion_order) if who == "handover")
    old_pm_after_handover = [
        outcome for who, outcome in completion_order[handover_index + 1 :] if who == "old_pm"
    ]
    old_pm_before_handover = [
        outcome for who, outcome in completion_order[:handover_index] if who == "old_pm"
    ]
    new_pm_before_handover = [
        outcome for who, outcome in completion_order[:handover_index] if who == "new_pm"
    ]

    # No overlap: once the handover has completed, old_pm is never granted
    # again.
    assert all(outcome == "denied" for outcome in old_pm_after_handover)
    # No gap: before the handover, old_pm was always granted (it is still
    # legitimately sitting) and new_pm was never granted (not yet elected).
    assert all(outcome == "granted" for outcome in old_pm_before_handover)
    assert all(outcome == "denied" for outcome in new_pm_before_handover)
    # And at least one call landed on each side of the handover, or the
    # test would not actually be exercising the race.
    assert old_pm_before_handover and old_pm_after_handover
