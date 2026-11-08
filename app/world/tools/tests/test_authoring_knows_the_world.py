"""Authoring a skill or a tool must not be done blind.

THE OPERATOR'S ASK, in their words: "when skillify and toolify tool is
used, it should have complete knowledge about the environment and about
itself", and "the skill and tool can be built, and later can be improvised
by themselves if they're intelligent enough to get the work done in
efficient way."

WHAT AUTHORING LOOKED LIKE BEFORE. A resident filing a workflow had to
name tools from memory. `describe_primitives` exists and gives the
vocabulary with prices, but it is a SEPARATE call: an agent had to know it
existed, spend a turn on it, and still be holding the answer a turn later
when it wrote the skill. Measured across three runs, `describe_primitives`
was called 12 times and `skillify` 3.

When a filing was refused, the refusal named the problem and up to twelve
tool names, and nothing about the author - not what it already knows how
to do, not what it is carrying, not what it has already written down. So
the second attempt was as blind as the first, and it cost another turn.

WHAT THIS ADDS. Every skillify and toolify result, whether it is accepted
or refused, carries the whole picture the author needed: the vocabulary
priced for THIS resident, what it already knows, what it has already
authored, where it is, what it holds, and what it can afford. Authoring is
rare - three calls in three runs - so the tokens are cheap and the turn
they save is not.

AND IT SAYS THAT REVISION EXISTS. `revise_skill` and `revise_tool` have
been registered and reachable the whole time and were never called once. A
resident that does not know it can change its mind writes nothing, because
writing something wrong would be permanent. The result says so, in the one
place an author is certain to read.

WHAT IT DELIBERATELY DOES NOT DO. It suggests no skill, no name, and no
steps. What a town chooses to write down is the finding; supplying it
would measure our vocabulary instead of theirs.
"""

from __future__ import annotations

import pytest

from world.tools.errors import ToolValidationError

from .conftest import spawn_agent

FARM = "Kasukabe Wheat Field"


async def resident(world, agent_id: str = "n01", place: str = FARM):
    await spawn_agent(world, agent_id, place)
    world.agent_names[agent_id] = "Marta Thorne"
    return agent_id


# -- The context itself ----------------------------------------------


@pytest.mark.asyncio
async def test_the_context_knows_what_the_resident_can_do(world, registry) -> None:
    agent_id = await resident(world)
    context = await world.authoring_context(agent_id)
    names = {row["name"] for row in context["tools_you_can_use"]}
    assert "move" in names and "say" in names
    assert len(names) > 20, f"only {len(names)} tools offered to an author"


@pytest.mark.asyncio
async def test_every_tool_carries_what_it_would_cost_this_resident(world, registry) -> None:
    """The skill discount is the whole reason to write a procedure down.
    An author choosing between two ways to do one job has to see which is
    cheaper for IT, not a list price it then adjusts in its head."""
    agent_id = await resident(world)
    for row in (await world.authoring_context(agent_id))["tools_you_can_use"]:
        assert "sim_cost_minimum" in row
        assert "params" in row


@pytest.mark.asyncio
async def test_the_context_knows_who_the_resident_is(world, registry) -> None:
    agent_id = await resident(world)
    you = (await world.authoring_context(agent_id))["you"]
    assert you["agent_id"] == agent_id
    assert you["name"] == "Marta Thorne"
    assert you["location_id"]
    assert "sim" in you and "skills" in you and "holding" in you


@pytest.mark.asyncio
async def test_the_context_lists_what_is_already_written_down(world, registry) -> None:
    """So an author does not file a second copy of a procedure it already
    has, and can see what it would be revising."""
    agent_id = await resident(world)
    context = await world.authoring_context(agent_id)
    assert "already_written" in context
    assert isinstance(context["already_written"]["skills"], list)
    assert isinstance(context["already_written"]["tools"], list)


@pytest.mark.asyncio
async def test_an_unknown_resident_does_not_crash_the_context(world, registry) -> None:
    """Bookkeeping must never be able to fail a turn."""
    context = await world.authoring_context("nobody")
    assert context["you"]["agent_id"] == "nobody"


# -- skillify carries it ---------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_skill_comes_back_with_the_vocabulary(world, registry) -> None:
    """The turn this saves. Marta filed six steps as world_move,
    world_harvest and the rest, and lost all six to "no tool named
    world_move" - for a workflow whose every step she had used that
    morning."""
    agent_id = await resident(world)
    with pytest.raises(ToolValidationError) as caught:
        await registry.dispatch(world, agent_id, "skillify", {
            "name": "morning round",
            "steps": [{"order": 1, "kind": "tool", "ref": "definitely_not_a_tool"}],
        })
    # Carried on the error rather than crammed into its message, so it
    # reaches the model as data. Both wire renderers - admin_api's
    # exception handler and run_genesis's /agents/{id}/tools route - pass
    # it through; see ToolError.context.
    context = caught.value.context
    assert context is not None, "the refusal taught the author nothing"
    assert context["tools_you_can_use"]
    assert context["you"]["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_an_accepted_skill_comes_back_with_it_too(world, registry) -> None:
    agent_id = await resident(world)
    outcome = await registry.dispatch(world, agent_id, "skillify", {
        "name": "morning round",
        "steps": [{"order": 1, "kind": "tool", "ref": "look_around"}],
    })
    assert outcome.result["filed"] is True
    assert outcome.result["you"]["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_the_result_says_the_filing_can_be_changed_later(world, registry) -> None:
    """revise_skill was registered and reachable across nineteen runs and
    called zero times. A resident that believes a filing is permanent
    files nothing."""
    agent_id = await resident(world)
    outcome = await registry.dispatch(world, agent_id, "skillify", {
        "name": "morning round",
        "steps": [{"order": 1, "kind": "tool", "ref": "look_around"}],
    })
    assert "revise_skill" in str(outcome.result.get("note", ""))


# -- toolify carries it ----------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_tool_comes_back_with_the_vocabulary(world, registry) -> None:
    agent_id = await resident(world)
    with pytest.raises(ToolValidationError) as caught:
        await registry.dispatch(world, agent_id, "toolify", {
            "name": "gather",
            "description": "a made up composition",
            "steps": [{"tool_name": "definitely_not_a_tool", "params": {}}],
        })
    assert caught.value.tool_name == "toolify", (
        "toolify refusals used to be signed 'define_tool', so an inventor "
        "was told it had been refused by a tool it had not called"
    )
    assert caught.value.context is not None
    assert caught.value.context["tools_you_can_use"]


@pytest.mark.asyncio
async def test_the_tool_result_says_revise_tool_exists(world, registry) -> None:
    agent_id = await resident(world)
    outcome = await registry.dispatch(world, agent_id, "toolify", {
        "name": "gather",
        "description": "look, then look again",
        "steps": [{"tool_name": "look_around", "params": {}}],
    })
    assert "revise_tool" in str(outcome.result.get("note", ""))


# -- The hint points at work, not at walking -------------------------


def test_the_hint_ignores_walking_and_looking() -> None:
    """Measured in data/loop5.db: the eight agent-and-tool pairs that
    reached the old threshold of eight were move and look_around, every
    one of them. A hint that fires on "you have walked eight times, write
    a procedure down" is noise, and a resident is right to ignore it. A
    skill is a WORK routine."""
    from world.ledger.action_costs import ActionCostCategory
    from world.tools.state import AUTHORING_HINT_CATEGORIES

    assert ActionCostCategory.LABOUR in AUTHORING_HINT_CATEGORIES
    assert ActionCostCategory.TRADE in AUTHORING_HINT_CATEGORIES
    assert ActionCostCategory.TRAVEL not in AUTHORING_HINT_CATEGORIES


def test_the_threshold_is_reachable_in_a_run_this_long() -> None:
    """Run 19 reached tick 59 and reported "nothing was authored ... an
    agent is prompted only after repeating one job several times, and
    nobody crossed that line". The most any resident completed of one
    skill was two. Eight was not a high bar, it was an unreachable one."""
    from world.tools.state import AUTHORING_HINT_REPEATS

    assert AUTHORING_HINT_REPEATS <= 3
