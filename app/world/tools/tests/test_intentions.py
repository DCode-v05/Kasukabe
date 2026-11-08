"""B2: what a resident set out to do, and whether it did it.

Perception carries what others said to you and what you owe an answer to,
and carried nothing about what you set out to do. Aldous Vance decided to
go to Oak Hill College on four consecutive ticks and never went - not
because he changed his mind, but because each tick he arrived at the
decision fresh, with no record that he had already made it.

Nothing enforces an intention. That is the design: the ratio of
intentions set to intentions fulfilled is the business-readiness figure
(T4, R4), and a tool that forced follow-through would answer the question
it exists to ask.
"""

from __future__ import annotations

import pytest

from world.retrieval_depth import format_intention
from world.tools.errors import ToolValidationError
from world.tools.state import WorldState

from .conftest import spawn_agent

HOME = "Kasukabe Town Square"


@pytest.mark.asyncio
async def test_an_intention_is_rendered_until_its_tool_succeeds(
    world: WorldState, registry
) -> None:
    """The spec's first named test."""
    await spawn_agent(world, "n06", HOME)
    world.inventory.credit("n06", "cabbage", 3)

    await registry.execute(world, "n06", "set_intention", {
        "text": "sell the cabbages at the market", "done_when_tool": "post_price",
    })
    assert world.intention_of("n06").text == "sell the cabbages at the market"

    await registry.execute(world, "n06", "look_around", {})
    assert world.intention_of("n06") is not None

    outcome = await registry.execute(world, "n06", "post_price", {
        "item": "cabbage", "ask_sim": 12,
    })

    assert world.intention_of("n06") is None
    dropped = [e for e in outcome.events if e.type == "agent.intention.dropped"]
    assert dropped and dropped[0].payload["reason"] == "fulfilled"
    assert dropped[0].payload["by_tool"] == "post_price"


@pytest.mark.asyncio
async def test_an_unfulfilled_intention_expires_at_day_end_and_says_so(
    world: WorldState, registry
) -> None:
    """The spec's second named test. An intention that outlives its day
    stops being a purpose and becomes furniture in the prompt."""
    await spawn_agent(world, "n06", HOME)
    await registry.execute(world, "n06", "set_intention", {
        "text": "go to Oak Hill College",
    })

    lapsed = world.expire_intentions()

    assert [agent_id for agent_id, _ in lapsed] == ["n06"]
    assert world.intention_of("n06") is None
    intention, reason = world.last_intention_outcome["n06"]
    assert intention.text == "go to Oak Hill College"
    assert reason == "the day ended"


@pytest.mark.asyncio
async def test_an_intention_naming_no_tool_is_still_valid(
    world: WorldState, registry
) -> None:
    """`done_when_tool` is optional: most of what a person sets out to do
    has no single act that finishes it."""
    await spawn_agent(world, "n06", HOME)

    await registry.execute(world, "n06", "set_intention", {"text": "be kinder to Marta"})

    assert world.intention_of("n06").done_when_tool is None


@pytest.mark.asyncio
async def test_naming_a_tool_that_does_not_exist_is_refused(
    world: WorldState, registry
) -> None:
    """Otherwise the resident carries an intention nothing could ever
    clear, forever, in every prompt."""
    await spawn_agent(world, "n06", HOME)

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n06", "set_intention", {
            "text": "go to college", "done_when_tool": "enrol",
        })

    assert "enrol" in str(exc.value)
    assert world.intention_of("n06") is None


@pytest.mark.asyncio
async def test_an_intention_longer_than_the_cap_is_refused_with_its_length(
    world: WorldState, registry
) -> None:
    await spawn_agent(world, "n06", HOME)

    with pytest.raises(ToolValidationError) as exc:
        await registry.execute(world, "n06", "set_intention", {"text": "x" * 201})

    assert "200" in str(exc.value) and "201" in str(exc.value)


@pytest.mark.asyncio
async def test_setting_a_second_intention_replaces_the_first(
    world: WorldState, registry
) -> None:
    """One at a time, deliberately. A list of intentions is a plan, and a
    resident reading a plan every turn reads a document rather than a
    purpose."""
    await spawn_agent(world, "n06", HOME)
    await registry.execute(world, "n06", "set_intention", {"text": "harvest the strip"})

    outcome = await registry.execute(world, "n06", "set_intention", {
        "text": "find a buyer for the wheat",
    })

    assert outcome.result["replaced"] == "harvest the strip"
    assert world.intention_of("n06").text == "find a buyer for the wheat"


@pytest.mark.asyncio
async def test_dropping_an_intention_you_never_set(world: WorldState, registry) -> None:
    await spawn_agent(world, "n06", HOME)

    with pytest.raises(ToolValidationError):
        await registry.execute(world, "n06", "drop_intention", {})


@pytest.mark.asyncio
async def test_setting_an_intention_is_free(world: WorldState, registry) -> None:
    """A resident charged to write down what it meant to do would stop
    writing it down, and the fulfilled ratio is the whole measurement."""
    await spawn_agent(world, "n06", HOME, starting_sim=0)

    await registry.execute(world, "n06", "set_intention", {"text": "eat something"})

    assert world.ledger.sim_balance("n06") == 0


@pytest.mark.asyncio
async def test_intentions_survive_a_checkpoint(world: WorldState, registry) -> None:
    await spawn_agent(world, "n06", HOME)
    await registry.execute(world, "n06", "set_intention", {"text": "harvest the strip"})

    saved = world.snapshot()
    world.intentions.clear()
    world.restore(saved)

    assert world.intention_of("n06").text == "harvest the strip"


def test_the_line_says_when_and_what_and_nothing_else() -> None:
    line = format_intention({"text": "sell the wheat", "set_clock": "08:30"})

    assert line == "You set out at 08:30 to: sell the wheat"
    assert "should" not in line and "consider" not in line


def test_a_lapsed_intention_is_named_at_the_day_boundary() -> None:
    line = format_intention(
        None,
        {"text": "go to Oak Hill College", "reason": "the day ended", "set_clock": "08:30"},
    )

    assert line == (
        "The day ended and you had not done what you set out to at 08:30: "
        "go to Oak Hill College"
    )


def test_an_outcome_replaces_the_standing_line_rather_than_joining_it() -> None:
    """A resident just told its intention lapsed does not also need to be
    told it is still standing."""
    line = format_intention(
        {"text": "the new one", "set_clock": "09:00"},
        {"text": "the old one", "reason": "fulfilled", "set_clock": "08:30"},
    )

    assert "the old one" in line
    assert "the new one" not in line


def test_no_intention_renders_nothing_at_all() -> None:
    """Silence, not "you have not set out to do anything" - a line every
    resident reads and learns to skip costs tokens and teaches nothing."""
    assert format_intention(None) is None
    assert format_intention({}) is None
