"""Run 19, issue 5: the nudge to write things down could not fire.

The report says it plainly (L76-78): "Nothing was authored... An agent is
prompted to write a procedure down only after repeating one job several
times, and nobody crossed that line."

`authoring_hint` counts how many times an agent repeated ONE work tool and
fires at three. The repetition table (L84-96) shows the whole town's
maximum for the run: two. So in a 59-tick run the line is not merely
uncrossed, it is unreachable, and "nothing was authored" is a statement
about the length of the run rather than about the residents.

Lowering the number to make authoring happen would be gaming the eval. The
real defect is what is being counted: a procedure is a SEQUENCE, and the
hint only ever looked for one tool used again and again. Corwin Thorne
walked to the forest, felled timber and walked back, three times over -
three repetitions of a three-step job, and not one tool used three times.
That is exactly the thing `skillify` exists to write down, and it was
invisible to the only code that would have mentioned it.

So: the single-tool rule stays as it is, at three, and a second rule
notices a repeated PAIR of consecutive work actions. Two rules, both
evidence-driven, neither one lowered to manufacture a result.
"""

from __future__ import annotations

from world.call_budget import MAX_CALLS_PER_TICK

import pytest

from world.tools.state import WorldState

from .conftest import spawn_agent

FARM = "Kasukabe Farmland"


async def _worker(world: WorldState) -> None:
    await spawn_agent(world, "n09", FARM)
    world.agent_names["n09"] = "Corwin Thorne"


def _did(world: WorldState, tool: str, agent_id: str = "n09") -> None:
    from world.tools.models import ToolInvocationRecord

    world.tool_invocations.append(
        ToolInvocationRecord(
            invocation_id=f"i{len(world.tool_invocations)}",
            tool_name=tool,
            namespace="public",
            actor_id=agent_id,
            tick=world.tick(),
        )
    )


@pytest.mark.asyncio
async def test_a_repeated_two_step_job_is_noticed(world: WorldState, registry) -> None:
    """Two different work actions, done in order, twice.

    `move` is deliberately not counted - it is TRAVEL, and every pair that
    crossed the old threshold in two consecutive runs was `move` or
    `look_around`. So "walk, cut, walk, cut" is one work tool twice, and
    the single-tool rule owns it. The sequence this rule is for is two
    pieces of real work: harvest then cook, fell then craft, mine then
    smelt - the procedures a town actually writes down.
    """
    await _worker(world)
    for _ in range(2):
        _did(world, "harvest")
        _did(world, "cook")

    hint = world.authoring_hint("n09")
    assert hint, "a two-step job done twice produces no nudge at all"
    assert "harvest" in hint and "cook" in hint
    assert "skillify" in hint, "the nudge does not name the verb that would write it down"


@pytest.mark.asyncio
async def test_one_pass_through_a_job_is_not_a_habit(world: WorldState, registry) -> None:
    """Doing something once is not a procedure, and a hint that fires on
    the first pass is noise in every prompt."""
    await _worker(world)
    _did(world, "harvest")
    _did(world, "cook")
    assert world.authoring_hint("n09") is None


@pytest.mark.asyncio
async def test_walking_about_is_still_not_a_job(world: WorldState, registry) -> None:
    """`move` and `look_around` were excluded for a reason - every pair
    that crossed the old threshold in two consecutive runs was one of
    them. A pair of them together must not sneak past."""
    await _worker(world)
    for _ in range(4):
        _did(world, "move")
        _did(world, "look_around")
    assert world.authoring_hint("n09") is None


@pytest.mark.asyncio
async def test_the_single_tool_rule_still_fires_at_three(
    world: WorldState, registry
) -> None:
    """The rule that already existed is not weakened by the new one."""
    await _worker(world)
    for _ in range(3):
        _did(world, "harvest")
    hint = world.authoring_hint("n09")
    assert hint and "harvest" in hint


@pytest.mark.asyncio
async def test_a_job_already_written_down_is_not_suggested_again(
    world: WorldState, registry
) -> None:
    """The existing exclusion, which the pair rule must also honour."""
    await _worker(world)
    await registry.execute(world, "n09", "skillify", {
        "name": "Morning Cut",
        "steps": [
            {"order": 1, "kind": "tool", "ref": "harvest"},
            {"order": 2, "kind": "tool", "ref": "cook"},
        ],
    })
    for _ in range(3):
        _did(world, "harvest")
        _did(world, "cook")
    assert world.authoring_hint("n09") is None


# ── Run 29: the half of the nudge that had no number in it ───────────────
#
# Naming the verbs moved `skillify` from 0% to 4 of 5 and moved `toolify`
# and `do_in_order` not at all - with the hint and without it, measured
# three runs running. The asymmetry is not the naming, it is the payoff:
# `skillify` pays in proficiency, which is already in the perception every
# turn, and the other two pay in calls not spent, which nothing had ever
# shown a resident. "Better is cheaper" was a claim about an invisible
# number.


@pytest.mark.asyncio
async def test_the_nudge_says_what_the_repetition_actually_cost(
    world: WorldState, registry
) -> None:
    await _worker(world)
    for _ in range(3):
        _did(world, "harvest")

    hint = world.authoring_hint("n09")
    assert hint
    assert "'harvest' 3 times" in hint and "3 separate calls" in hint, (
        "the resident is told it repeated a job and not what the repetition "
        f"took: {hint!r}"
    )
    # Read from the constant, not typed here: the ceiling came down from 12
    # to 6 on 2026-09-18 and a literal in this assertion would have made
    # the nudge's own arithmetic look like a regression.
    assert f"{MAX_CALLS_PER_TICK} a turn can buy" in hint, (
        "the count has nothing to be measured against"
    )


@pytest.mark.asyncio
async def test_a_repeated_pair_is_priced_across_both_halves(
    world: WorldState, registry
) -> None:
    """When no single tool reaches three the PAIR rule owns the nudge, and
    the cost has to cover the whole job rather than half of it."""
    await _worker(world)
    for _ in range(2):
        _did(world, "harvest")
        _did(world, "cook")

    hint = world.authoring_hint("n09")
    assert hint
    assert "'harvest' then 'cook' 2 times" in hint
    assert "4 separate calls" in hint, f"only half the job was priced: {hint!r}"


@pytest.mark.asyncio
async def test_work_bought_by_a_program_is_not_charged_as_a_decision(
    world: WorldState, registry
) -> None:
    """The number has to be able to go DOWN, or it argues for nothing.

    A step inside a program was not its own decision - one model call
    bought the lot (`ToolInvocationRecord.program_id`). Counting those
    would make `do_in_order` look exactly as expensive as doing the same
    work by hand, which is the one claim this figure exists to settle.
    """
    from world.tools.models import ToolInvocationRecord

    await _worker(world)
    for _ in range(3):
        _did(world, "harvest")
        _did(world, "cook")
    for step, tool in enumerate(("harvest", "cook", "harvest", "cook"), start=1):
        world.tool_invocations.append(
            ToolInvocationRecord(
                invocation_id=f"p{step}", tool_name=tool, namespace="public",
                actor_id="n09", tick=world.tick(),
                program_id="prog-1", program_step=step,
            )
        )

    hint = world.authoring_hint("n09")
    assert hint
    assert "'harvest' 5 times" in hint and "3 separate calls" in hint, (
        "five acts that cost three decisions have to READ as five acts and "
        "three decisions; if the program's steps are charged as decisions "
        f"the figure can only ever go up and argues for nothing: {hint!r}"
    )


# ── Run 29: two residents wrote the same procedure an hour apart ─────────


async def _somebody_else_wrote_it(world: WorldState, *, proficiency: int = 20) -> None:
    """n12 files the job n09 keeps doing by hand, and has walked it."""
    await spawn_agent(world, "n12", FARM)
    world.agent_names["n12"] = "Silas Vance"
    from world.tools.models import SkillDefinition, SkillStep

    world.skill_definitions["Morning Cut"] = SkillDefinition(
        name="Morning Cut", namespace="public", owner_id=None,
        author_agent_id="n12",
        steps=[
            SkillStep(order=1, kind="tool", ref="harvest"),
            SkillStep(order=2, kind="tool", ref="cook"),
        ],
        discounts_tools=["harvest", "cook"], status="ACTIVE", created_tick=0,
    )
    world.skills.setdefault("n12", {})["Morning Cut"] = proficiency


@pytest.mark.asyncio
async def test_a_job_somebody_else_already_wrote_is_named_before_you_write_it_again(
    world: WorldState, registry
) -> None:
    """Corwin Thorne filed `Timber Felling` at tick 72 and Tobias Holloway
    filed `timber felling` at tick 76 - the same job, the same hour, two
    authors, and nothing in the town able to notice. This is the one line
    a resident reads at the moment it is about to write the duplicate."""
    await _worker(world)
    await _somebody_else_wrote_it(world)
    for _ in range(3):
        _did(world, "harvest")
        _did(world, "cook")

    hint = world.authoring_hint("n09")
    assert hint
    assert "Silas Vance" in hint and "Morning Cut" in hint, (
        f"the duplicate is about to be written with no word said: {hint!r}"
    )
    assert "teach_skill" in hint, (
        "`teach_skill` has worked since it was written and has never been "
        "called in any run; a resident that now knows who holds the "
        "procedure is still not told how it could reach it"
    )


@pytest.mark.asyncio
async def test_an_author_who_never_walked_their_own_procedure_is_not_offered(
    world: WorldState, registry
) -> None:
    """`teach_skill_handler` refuses a teacher at proficiency 0. Naming one
    would point a resident at a lesson the world would then refuse."""
    await _worker(world)
    await _somebody_else_wrote_it(world, proficiency=0)
    for _ in range(3):
        _did(world, "harvest")
        _did(world, "cook")

    hint = world.authoring_hint("n09")
    assert hint and "Silas Vance" not in hint


@pytest.mark.asyncio
async def test_a_trade_secret_is_not_advertised_to_an_outsider(
    world: WorldState, registry
) -> None:
    """Part D: a business's procedure stays inside the business. The hint
    must not be the leak that `teach_skill` was careful not to be."""
    from world.tools.models import Business

    await _worker(world)
    await _somebody_else_wrote_it(world)
    world.businesses["b_mill"] = Business(
        business_id="b_mill", name="Kasukabe Mill", business_type="mill",
        owner_agent_id="n12", location_id=FARM,
    )
    secret = world.skill_definitions["Morning Cut"]
    world.skill_definitions["Morning Cut"] = secret.model_copy(
        update={"namespace": "business", "owner_id": "b_mill"}
    )
    for _ in range(3):
        _did(world, "harvest")
        _did(world, "cook")

    hint = world.authoring_hint("n09")
    assert hint and "Morning Cut" not in hint, (
        "the hint told a stranger what the mill knows"
    )


@pytest.mark.asyncio
async def test_the_same_name_in_two_cases_is_one_name(
    world: WorldState, registry
) -> None:
    """The collision check was an exact string match, so `Timber Felling`
    and `timber felling` were two skills in one town."""
    from world.tools.errors import ToolError

    await _worker(world)
    steps = [
        {"order": 1, "kind": "tool", "ref": "fell_timber"},
        {"order": 2, "kind": "tool", "ref": "look_around"},
    ]
    await registry.execute(world, "n09", "skillify", {
        "name": "Timber Felling", "steps": steps})

    await spawn_agent(world, "n03", FARM)
    world.agent_names["n03"] = "Tobias Holloway"
    with pytest.raises(ToolError) as refused:
        await registry.execute(world, "n03", "skillify", {
            "name": "timber felling", "steps": steps})
    assert "Timber Felling" in str(refused.value), (
        "the refusal has to name the filing that EXISTS, not the one just "
        f"typed, or the author cannot go and find it: {refused.value}"
    )
    assert "timber felling" not in world.skill_definitions


# ── The tripwire this string never had ───────────────────────────────────


def test_the_nudge_states_facts_and_never_a_plan() -> None:
    """`soul_seed` and `cost_disclosure` both carry this check over every
    string they can render. The hint did not, and it is read by every
    resident on every turn that crosses the threshold - the same standing
    rule, on a string that had nothing holding it.

    It matters more now than it did. The line went from naming verbs to
    naming a NEIGHBOUR who holds a procedure, which is one short step from
    telling a resident who to go and talk to. It names who holds what and
    what the verb does; which of those facts is worth anything is the
    resident's to work out.
    """
    from world.life.tests.test_soul_seed import STRATEGY_WORDS
    from world.tools.state import authoring_hint_text

    every_shape = [
        authoring_hint_text("'harvest'", 3),
        authoring_hint_text("'harvest' then 'cook'", 2, decisions=4, call_ceiling=12),
        authoring_hint_text(
            "'fell_timber'", 8, decisions=8, call_ceiling=12,
            already_written=("Timber Felling", "Corwin Thorne"),
        ),
    ]
    for text in every_shape:
        lowered = text.lower()
        leaked = [word for word in STRATEGY_WORDS if word in lowered]
        assert not leaked, f"the nudge tells the resident what to do: {leaked} in {text!r}"
        # The specific temptation this line invites, and the one the whole
        # no-coaching rule exists to refuse.
        for plan in ("ask them", "go and", "you could ask", "learn from", "instead of"):
            assert plan not in lowered, f"{plan!r} is a plan, not a fact: {text!r}"
