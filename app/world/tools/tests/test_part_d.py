"""Part D: the loop closes, procedures can be revised, secrets stay secret.

The gap-feedback tests carry the most weight. That module is the ONE place the
instrument writes into the experiment, so its constraints - names the gap not
the answer, rate-limited, logged - are not politeness. A run where agents were
nudged into inventing things, without a record of the nudging, produces
findings that cannot be told apart from compliance.
"""

import pytest

from world.gap_feedback import (
    MIN_AGENTS_FOR_A_GAP,
    NOTICE_COOLDOWN_TICKS,
    GapFeedback,
)
from world.tools.bootstrap import build_world
from world.tools.errors import ActorRequirementError, ToolValidationError
from world.tools.locations import farmland_location
from world.tools.models import Business
from world.tools.tests.conftest import spawn_agent

FIELD = farmland_location("wheat")


async def _world(seed=300):
    world, registry = await build_world(seed=seed)
    await spawn_agent(world, "ada", FIELD, starting_sim=100_000)
    return world, registry


async def _want(registry, world, who, tool="mend_nets"):
    try:
        await registry.execute(world, who, tool, {})
    except Exception:
        pass


# ── D.1 the loop closes ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_agents_frustration_is_not_a_town_gap() -> None:
    """A single agent's refusal is usually that agent being in the wrong
    place; two independent agents wanting the same impossible thing is the
    town wanting it."""
    world, registry = await _world()
    await _want(registry, world, "ada")

    assert world.gap_feedback.notice_for(world, "ada") is None


@pytest.mark.asyncio
async def test_a_shared_gap_reaches_the_agents_who_hit_it() -> None:
    world, registry = await _world()
    await spawn_agent(world, "bram", FIELD, starting_sim=100_000)
    for who in ("ada", "bram"):
        await _want(registry, world, who)

    notice = world.gap_feedback.notice_for(world, "ada")
    assert notice is not None
    assert "mend_nets" in notice
    assert MIN_AGENTS_FOR_A_GAP == 2


@pytest.mark.asyncio
async def test_an_agent_who_never_wanted_it_is_not_told() -> None:
    """A town-wide broadcast would make the first inventor whoever the
    scheduler happened to wake, rather than whoever wanted the thing."""
    world, registry = await _world()
    await spawn_agent(world, "bram", FIELD, starting_sim=100_000)
    await spawn_agent(world, "cyn", FIELD, starting_sim=100_000)
    for who in ("ada", "bram"):
        await _want(registry, world, who)

    assert world.gap_feedback.notice_for(world, "cyn") is None


@pytest.mark.asyncio
async def test_the_notice_names_the_gap_and_never_the_answer() -> None:
    """"Nobody knows how to mend_nets" is an observation the agents generated.
    "Use toolify to build mend_nets" would be us inventing it and crediting
    them."""
    world, registry = await _world()
    await spawn_agent(world, "bram", FIELD, starting_sim=100_000)
    for who in ("ada", "bram"):
        await _want(registry, world, who)

    notice = world.gap_feedback.notice_for(world, "ada")
    lowered = notice.lower()
    for verb in ("toolify", "skillify", "invent", "you should", "write a", "build a"):
        assert verb not in lowered, f"the notice told the agent what to do: {verb!r}"


@pytest.mark.asyncio
async def test_an_agent_is_not_told_the_same_thing_twice_in_a_day() -> None:
    """An agent told every tick is being nagged into compliance, and its
    eventual invention measures our persistence rather than its judgement."""
    world, registry = await _world()
    await spawn_agent(world, "bram", FIELD, starting_sim=100_000)
    for who in ("ada", "bram"):
        await _want(registry, world, who)

    assert world.gap_feedback.notice_for(world, "ada") is not None
    assert world.gap_feedback.notice_for(world, "ada") is None, "told twice in one tick"

    world.current_tick += NOTICE_COOLDOWN_TICKS
    assert world.gap_feedback.notice_for(world, "ada") is not None, "still silent a day later"


@pytest.mark.asyncio
async def test_every_notice_is_logged_so_it_can_be_subtracted() -> None:
    """Without this a researcher cannot tell a town that invented something
    from a town that was asked to."""
    world, registry = await _world()
    await spawn_agent(world, "bram", FIELD, starting_sim=100_000)
    for who in ("ada", "bram"):
        await _want(registry, world, who)

    world.gap_feedback.notice_for(world, "ada")

    assert len(world.gap_feedback.notices) == 1
    logged = world.gap_feedback.notices[0]
    assert logged.agent_id == "ada"
    assert logged.unmet_requirement == "no-such-tool:mend_nets"
    assert logged.tick == world.tick()


@pytest.mark.asyncio
async def test_the_feedback_can_be_switched_off_entirely() -> None:
    """A control run needs the instrument silent, not merely quiet."""
    world, registry = await _world()
    await spawn_agent(world, "bram", FIELD, starting_sim=100_000)
    for who in ("ada", "bram"):
        await _want(registry, world, who)

    world.gap_feedback = GapFeedback(enabled=False)
    assert world.gap_feedback.notice_for(world, "ada") is None


# ── D.2 revision ──────────────────────────────────────────────────────


async def _with_skill(seed=310):
    world, registry = await _world(seed)
    await registry.execute(world, "ada", "skillify", {
        "name": "clearing a stand", "preconditions": "a ripe field",
        "steps": [{"order": 1, "kind": "tool", "ref": "harvest", "note": "take it"}]})
    return world, registry


@pytest.mark.asyncio
async def test_a_revision_keeps_what_the_agent_used_to_think() -> None:
    world, registry = await _with_skill()

    await registry.execute(world, "ada", "revise_skill", {
        "name": "clearing a stand", "because": "the saw binds if you skip limbing",
        "steps": [
            {"order": 1, "kind": "check", "ref": None, "note": "is the saw sharp?"},
            {"order": 2, "kind": "tool", "ref": "harvest", "note": "take it"},
        ]})

    definition = world.skill_definitions["clearing a stand"]
    assert definition.version == 2
    assert len(definition.steps) == 2
    assert len(definition.superseded) == 1
    assert len(definition.superseded[0].steps) == 1, "version 1 is still readable"


@pytest.mark.asyncio
async def test_improving_a_procedure_does_not_make_you_a_beginner() -> None:
    """A reset would make improving something you are good at strictly worse
    than leaving it wrong."""
    world, registry = await _with_skill()
    for _ in range(20):
        await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})
    earned = world.skills["ada"]["clearing a stand"]
    assert earned > 1

    await registry.execute(world, "ada", "revise_skill", {
        "name": "clearing a stand",
        "steps": [{"order": 1, "kind": "tool", "ref": "harvest", "note": "better"}]})

    assert world.skills["ada"]["clearing a stand"] == earned


@pytest.mark.asyncio
async def test_only_the_author_may_revise() -> None:
    world, registry = await _with_skill()
    await spawn_agent(world, "bram", FIELD, starting_sim=100_000)

    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "bram", "revise_skill", {
            "name": "clearing a stand",
            "steps": [{"order": 1, "kind": "tool", "ref": "harvest", "note": "mine now"}]})


@pytest.mark.asyncio
async def test_a_revision_cannot_smuggle_in_state_power() -> None:
    """Otherwise revising a trade skill would be a way into the book of rules
    without the council ever seeing it."""
    world, registry = await _with_skill()
    seat_tool = next(
        (n for n, s in registry.tools.items() if s.actor_requirements.require_government_seat),
        None,
    )
    assert seat_tool is not None, "no seat-gated tool to test with"

    with pytest.raises(ToolValidationError):
        await registry.execute(world, "ada", "revise_skill", {
            "name": "clearing a stand",
            "steps": [{"order": 1, "kind": "tool", "ref": seat_tool, "note": "sneak"}]})


@pytest.mark.asyncio
async def test_progress_through_the_old_steps_is_cleared() -> None:
    """Step 4 of version 1 is not step 4 of version 2, and guessing which one
    it became would be inventing a fact."""
    world, registry = await _with_skill()
    await registry.execute(world, "ada", "skillify", {
        "name": "long job",
        "steps": [
            {"order": 1, "kind": "tool", "ref": "harvest", "note": "a"},
            {"order": 2, "kind": "tool", "ref": "look_around", "note": "b"},
        ]})
    await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})
    assert any(k.endswith("::long job") for k in world.skill_progress)

    await registry.execute(world, "ada", "revise_skill", {
        "name": "long job",
        "steps": [{"order": 1, "kind": "tool", "ref": "look_around", "note": "new"}]})

    assert not any(k.endswith("::long job") for k in world.skill_progress)


@pytest.mark.asyncio
async def test_an_invented_tool_can_be_revised_and_engines_notice() -> None:
    world, registry = await _world(320)
    await registry.execute(world, "ada", "toolify", {
        "name": "work_stand", "description": "harvest", "param_schema": {},
        "steps": [{"tool_name": "harvest", "params": {"crop": "wheat", "quantity": 1}}]})
    version_before = registry.version

    result = await registry.execute(world, "ada", "revise_tool", {
        "name": "work_stand", "because": "two swings, not one",
        "steps": [
            {"tool_name": "harvest", "params": {"crop": "wheat", "quantity": 1}},
            {"tool_name": "harvest", "params": {"crop": "wheat", "quantity": 1}},
        ]})

    assert result.result["version"] == 2
    assert registry.version > version_before, "engines must refetch"
    assert len(world.custom_tools["work_stand"].superseded_steps) == 1
    # And the live tool really does the new thing.
    before = world.inventory.quantity("ada", "wheat")
    await registry.execute(world, "ada", "work_stand", {})
    assert world.inventory.quantity("ada", "wheat") == before + 2


@pytest.mark.asyncio
async def test_a_patented_tools_disclosed_steps_cannot_be_swapped() -> None:
    """The patent office approved a specific disclosure; letting the holder
    change it would make the approval refer to something that no longer
    exists."""
    world, registry = await _world(321)
    world.custom_tools["patented"] = world.custom_tools.get("patented") or None
    from world.tools.models import CustomToolDefinition, ToolStep

    world.custom_tools["patented"] = CustomToolDefinition(
        tool_name="patented", description="d", creator_agent_id="ada",
        steps=[ToolStep(tool_name="harvest", params={"crop": "wheat", "quantity": 1})],
        patent_application_id="app-1", approved=True,
    )
    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "ada", "revise_tool", {
            "name": "patented",
            "steps": [{"tool_name": "look_around", "params": {}}]})


# ── D.4 trade secrets ─────────────────────────────────────────────────


async def _with_business(seed=330):
    world, registry = await _world(seed)
    await spawn_agent(world, "hired", FIELD, starting_sim=100_000)
    await spawn_agent(world, "rival", FIELD, starting_sim=100_000)
    world.businesses["mill-1"] = Business(
        business_id="mill-1", name="Ada's Mill", business_type="mill",
        owner_agent_id="ada", location_id=FIELD, employee_agent_ids={"hired"},
    )
    return world, registry


@pytest.mark.asyncio
async def test_a_skill_can_belong_to_a_business() -> None:
    world, registry = await _with_business()
    result = await registry.execute(world, "ada", "skillify", {
        "name": "the mill's way", "business_id": "mill-1",
        "steps": [{"order": 1, "kind": "tool", "ref": "harvest", "note": "ours"}]})

    assert result.result["namespace"] == "business"
    assert world.skill_definitions["the mill's way"].owner_id == "mill-1"


@pytest.mark.asyncio
async def test_a_stranger_cannot_attach_a_secret_to_someone_elses_shop() -> None:
    world, registry = await _with_business()
    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "rival", "skillify", {
            "name": "not mine", "business_id": "mill-1",
            "steps": [{"order": 1, "kind": "tool", "ref": "harvest", "note": "x"}]})


@pytest.mark.asyncio
async def test_a_trade_secret_cannot_be_taught_outside_the_business() -> None:
    """Teaching is the only way a skill leaves the person holding it, so it is
    the one door that needs the lock."""
    world, registry = await _with_business()
    await registry.execute(world, "ada", "skillify", {
        "name": "the mill's way", "business_id": "mill-1",
        "steps": [{"order": 1, "kind": "tool", "ref": "harvest", "note": "ours"}]})

    with pytest.raises(ActorRequirementError):
        await registry.execute(world, "ada", "teach_skill", {
            "student_agent_id": "rival", "skill": "the mill's way",
            "student_consent": True, "price_sim": 0})


@pytest.mark.asyncio
async def test_an_employee_may_be_taught_it() -> None:
    world, registry = await _with_business()
    await registry.execute(world, "ada", "skillify", {
        "name": "the mill's way", "business_id": "mill-1",
        "steps": [{"order": 1, "kind": "tool", "ref": "harvest", "note": "ours"}]})
    for _ in range(6):
        await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})

    await registry.execute(world, "ada", "teach_skill", {
        "student_agent_id": "hired", "skill": "the mill's way",
        "student_consent": True, "price_sim": 0})

    assert world.skills["hired"]["the mill's way"] > 0


@pytest.mark.asyncio
async def test_a_public_skill_is_still_teachable_to_anyone() -> None:
    world, registry = await _with_business()
    await registry.execute(world, "ada", "skillify", {
        "name": "open knowledge",
        "steps": [{"order": 1, "kind": "tool", "ref": "harvest", "note": "free"}]})
    for _ in range(6):
        await registry.execute(world, "ada", "harvest", {"crop": "wheat", "quantity": 1})

    await registry.execute(world, "ada", "teach_skill", {
        "student_agent_id": "rival", "skill": "open knowledge",
        "student_consent": True, "price_sim": 0})
    assert world.skills["rival"]["open knowledge"] > 0
