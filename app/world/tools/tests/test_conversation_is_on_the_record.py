"""A conversation that opened and left no event never happened.

`world.conversations` is in-memory world state. The run report, the
observatory and every after-the-fact analysis read the event stream and
the trace, so a mechanic that touches only the registry is invisible to
all three. This project has already paid for that shape once: six real
`trade` calls returned `"traded": true` and the settlements gate read
zero, because `TransactionReason.TRADE` had no trace mapping. The tool
worked, the agents used it, and the record said it never happened - the
worst failure shape there is, because nothing goes red.
"""

from __future__ import annotations

import pytest

from .conftest import spawn_agent


@pytest.mark.asyncio
async def test_opening_a_conversation_emits_a_world_event(world, registry) -> None:
    """Six real trades once ran and the trace said they never happened,
    because nothing wrote them down. A conversation opening is exactly as
    easy to lose: the registry is in-memory world state, and the run
    report reads events."""
    await spawn_agent(world, "n01", "Kasukabe Market Square")
    await spawn_agent(world, "n02", "Kasukabe Market Square")
    world.agent_names.update({"n01": "Marta Thorne", "n02": "Bram Colley"})

    outcome = await registry.dispatch(
        world, "n01", "start_conversation",
        {"topic": "the boundary marker", "with_agent_ids": ["n02"]},
    )
    kinds = [event.type for event in outcome.events]
    assert "agent.conversation.opened" in kinds, (
        "a conversation that opened and left no event is a conversation the "
        f"report cannot count; got {kinds}"
    )
