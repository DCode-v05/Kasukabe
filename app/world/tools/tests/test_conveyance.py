"""C1: how a tool reaches a second and a third pair of hands.

`toolify`, `skillify` and the `revise_*` pair all existed. Nothing
licensed, sold, granted, taught or inherited what they produced, so a
tool invented once could never reach a third resident and **diffusion was
zero by construction** - not a finding about the residents, a property of
the code. C4 ("a tool held by three residents") could not be non-zero.

The three verbs are deliberately distinct, and the difference is the
interesting part: a licence is a rent, a sale is a sale, and teaching
hands over a copy nobody can take back.
"""

from __future__ import annotations

import pytest

from world.charter.models import GovernmentState
from world.core.clock import TICKS_PER_DAY
from world.tools.errors import (
    ActorRequirementError,
    AlreadyExistsError,
    NotFoundError,
    ToolNotFoundError,
)
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX, LOCATION_PATENT_OFFICE
from world.tools.state import WorldState

from .conftest import move_agent, spawn_agent

OFFICE = LOCATION_PATENT_OFFICE


async def _invented(
    world: WorldState, registry, *, inventor: str = "ada", name: str = "rope_making"
) -> str:
    """One approved, callable invented tool, owned by `inventor`."""
    await spawn_agent(world, inventor, OFFICE, age_years=30.0)
    world.agent_names[inventor] = "Ada Gray"
    filed = await registry.execute(world, inventor, "define_tool", {
        "name": name,
        "description": "walk somewhere",
        "param_schema": {"dest": "str"},
        "steps": [{"tool_name": "move", "params": {"destination_location_id": "$dest"}}],
    })
    await spawn_agent(world, "minister", LOCATION_GOVERNMENT_COMPLEX)
    await world.charter.store.set_government_state(
        GovernmentState(pm_agent_id="minister", minister_agent_ids=[])
    )
    await registry.execute(world, "minister", "approve_patent", {
        "application_id": filed.result["application_id"],
    })
    return name


# ── holding is real now ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_invented_tool_is_not_the_whole_towns(world: WorldState, registry) -> None:
    """The premise of every other test here.

    An approved invention was registered PUBLIC and callable by anyone, so
    a licence would have granted a right everybody already had.
    """
    name = await _invented(world, registry)
    await spawn_agent(world, "stranger", OFFICE)

    assert world.holders_of(name, world.tick()) == {"ada"}
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "stranger", name, {"dest": "temple"})


# ── licensing ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_licensed_tool_stops_working_when_the_term_ends(
    world: WorldState, registry
) -> None:
    """The spec's first named test."""
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)

    await registry.execute(world, "ada", "license_tool", {
        "tool_name": name, "licensee_id": "bram", "fee_sim": 200, "term_days": 1,
    })
    await registry.execute(world, "bram", name, {"dest": "temple"})
    assert world.location_of("bram") == "temple"

    world.current_tick = TICKS_PER_DAY + 1
    await move_agent(world, "bram", OFFICE)

    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "bram", name, {"dest": "park"})


@pytest.mark.asyncio
async def test_a_licensee_calls_it_under_their_own_name(
    world: WorldState, registry
) -> None:
    """The addendum's own words, and the reason the old behaviour could not
    stand: a licensee's call used to move the INVENTOR and spend the
    inventor's money."""
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    await registry.execute(world, "ada", "license_tool", {
        "tool_name": name, "licensee_id": "bram", "term_days": 3,
    })
    ada_sim_before = world.ledger.sim_balance("ada")

    await registry.execute(world, "bram", name, {"dest": "temple"})

    assert world.location_of("bram") == "temple"
    assert world.location_of("ada") == OFFICE
    assert world.ledger.sim_balance("ada") == ada_sim_before, (
        "the licensee spent the inventor's money"
    )


@pytest.mark.asyncio
async def test_the_fee_moves_when_the_licence_is_granted(
    world: WorldState, registry
) -> None:
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    ada_before = world.ledger.sim_balance("ada")
    bram_before = world.ledger.sim_balance("bram")

    await registry.execute(world, "ada", "license_tool", {
        "tool_name": name, "licensee_id": "bram", "fee_sim": 200, "term_days": 1,
    })

    assert world.ledger.sim_balance("ada") == ada_before + 200
    assert world.ledger.sim_balance("bram") == bram_before - 200


@pytest.mark.asyncio
async def test_a_revocable_licence_can_be_ended_and_an_irrevocable_one_cannot(
    world: WorldState, registry
) -> None:
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    await spawn_agent(world, "nell", OFFICE)

    await registry.execute(world, "ada", "license_tool", {
        "tool_name": name, "licensee_id": "bram", "term_days": 9, "revocable": True,
    })
    await registry.execute(world, "ada", "revoke_license", {
        "tool_name": name, "licensee_id": "bram",
    })
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "bram", name, {"dest": "temple"})

    await registry.execute(world, "ada", "license_tool", {
        "tool_name": name, "licensee_id": "nell", "term_days": 9, "revocable": False,
    })
    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "ada", "revoke_license", {
            "tool_name": name, "licensee_id": "nell",
        })
    assert "without revocation" in str(exc.value)


@pytest.mark.asyncio
async def test_licensing_twice_names_the_verb_that_clears_the_first(
    world: WorldState, registry
) -> None:
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    await registry.execute(world, "ada", "license_tool", {
        "tool_name": name, "licensee_id": "bram", "term_days": 2,
    })

    with pytest.raises(AlreadyExistsError) as exc:
        await registry.execute(world, "ada", "license_tool", {
            "tool_name": name, "licensee_id": "bram", "term_days": 2,
        })

    assert "revoke_license" in str(exc.value)


@pytest.mark.asyncio
async def test_you_cannot_license_what_you_do_not_own(
    world: WorldState, registry
) -> None:
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    await spawn_agent(world, "nell", OFFICE)

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "bram", "license_tool", {
            "tool_name": name, "licensee_id": "nell", "term_days": 1,
        })

    assert "Ada Gray" in str(exc.value)


# ── selling ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_sold_tool_is_called_under_the_new_owner_and_the_record_follows(
    world: WorldState, registry
) -> None:
    """The spec's second named test."""
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)

    await registry.execute(world, "ada", "sell_tool", {
        "tool_name": name, "buyer_id": "bram", "price_sim": 500,
    })

    assert world.tool_owner(name) == "bram"
    await registry.execute(world, "bram", name, {"dest": "temple"})
    assert world.location_of("bram") == "temple"
    # And the seller is out. They sold it.
    with pytest.raises(ToolNotFoundError):
        await registry.execute(world, "ada", name, {"dest": "park"})


@pytest.mark.asyncio
async def test_a_licence_survives_the_sale_of_the_tool(
    world: WorldState, registry
) -> None:
    """The licensee did not sell anything, so a change of owner is no
    reason for the promise they were given to lapse."""
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    await spawn_agent(world, "nell", OFFICE)
    await registry.execute(world, "ada", "license_tool", {
        "tool_name": name, "licensee_id": "nell", "term_days": 5,
    })

    await registry.execute(world, "ada", "sell_tool", {
        "tool_name": name, "buyer_id": "bram", "price_sim": 500,
    })

    await registry.execute(world, "nell", name, {"dest": "temple"})
    assert world.location_of("nell") == "temple"


# ── teaching, and the third pair of hands ─────────────────────────────


@pytest.mark.asyncio
async def test_a_taught_tool_reaches_a_third_resident(
    world: WorldState, registry
) -> None:
    """The spec's third named test, and the whole of C4.

    Ada teaches Bram; Bram teaches Nell. If only the inventor could teach,
    the town would be capped at one hop from wherever an idea started.
    """
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    await spawn_agent(world, "nell", OFFICE)

    await registry.execute(world, "ada", "teach_tool", {
        "tool_name": name, "student_id": "bram", "student_consent": True,
    })
    await registry.execute(world, "bram", "teach_tool", {
        "tool_name": name, "student_id": "nell", "student_consent": True,
    })

    assert world.holders_of(name, world.tick()) == {"ada", "bram", "nell"}
    await registry.execute(world, "nell", name, {"dest": "temple"})
    assert world.location_of("nell") == "temple"


@pytest.mark.asyncio
async def test_a_taught_copy_cannot_be_taken_back(world: WorldState, registry) -> None:
    """The difference between teaching and licensing, in one assertion."""
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    await registry.execute(world, "ada", "teach_tool", {
        "tool_name": name, "student_id": "bram", "student_consent": True,
    })

    with pytest.raises(NotFoundError):
        await registry.execute(world, "ada", "revoke_license", {
            "tool_name": name, "licensee_id": "bram",
        })

    await registry.execute(world, "bram", name, {"dest": "temple"})
    assert world.location_of("bram") == "temple"


@pytest.mark.asyncio
async def test_teaching_without_consent_is_refused(world: WorldState, registry) -> None:
    """DESIGN.md section 4: teaching is "consensual, refusable".

    `student_consent` is passed explicitly as False rather than omitted.
    It used to default, and a defaulted field is not in pydantic's
    `required`, so an index row - which carries only the required names
    (TRADE-02) - never showed it. A flag that gates the handler and does
    not appear in the schema makes the tool uncallable, which is how
    `hire_employee` refused run 25's baker six times over an agreement her
    employee had already given out loud.

    Saying False explicitly is the case that matters anyway: this test is
    about a teacher who asked and was told no, not about one who forgot to
    fill in a form.
    """
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "ada", "teach_tool", {
            "tool_name": name, "student_id": "bram", "student_consent": False,
        })

    assert "consent" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_a_licensee_may_call_it_but_not_pass_it_on(
    world: WorldState, registry
) -> None:
    """A rent is not a right to sublet. Otherwise a one-day licence is a
    permanent grant to the whole town."""
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    await spawn_agent(world, "nell", OFFICE)
    await registry.execute(world, "ada", "license_tool", {
        "tool_name": name, "licensee_id": "bram", "term_days": 5,
    })

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "bram", "teach_tool", {
            "tool_name": name, "student_id": "nell", "student_consent": True,
        })

    assert "licence lets you call it" in str(exc.value)


# ── inheritance ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_will_can_name_a_tool_and_only_your_own(
    world: WorldState, registry
) -> None:
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)

    await registry.execute(world, "ada", "set_will", {
        "bequests": [{"heir_id": "bram", "sim_share": 1, "tools": [name]}],
    })
    assert world.wills["ada"].bequests[0].tools == [name]

    with pytest.raises(ActorRequirementError) as exc:
        await registry.execute(world, "bram", "set_will", {
            "bequests": [{"heir_id": "ada", "sim_share": 1, "tools": [name]}],
        })
    assert "not yours to leave" in str(exc.value)


@pytest.mark.asyncio
async def test_a_dead_inventors_tools_reach_the_heir(tmp_path) -> None:
    """The spec's fourth named test.

    A tool that dies with the person who thought of it is the one way
    diffusion can run backwards, and until now it was the only thing that
    ever happened to one.
    """
    from world.tests.test_run_genesis import make_runner

    runner = make_runner(tmp_path)
    await runner.boot(resume=False)
    world = runner.world_state

    inventor, heir = list(world.agents)[:2]
    world.custom_tools["rope_making"] = _definition(inventor)
    world.agents[inventor] = world.agents[inventor].model_copy(
        update={"spouse_id": heir}
    )

    await runner.apply_death(world.agents[inventor], "elderly_hazard")

    assert world.tool_owner("rope_making") == heir


def _definition(creator: str):
    from world.tools.models import CustomToolDefinition, ToolStep

    return CustomToolDefinition(
        tool_name="rope_making",
        description="twist fibre into rope",
        creator_agent_id=creator,
        param_schema={},
        steps=[ToolStep(tool_name="rest", params={})],
        approved=True,
    )


# ── the record ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_holding_survives_a_checkpoint(world: WorldState, registry) -> None:
    """C4 is measured off exactly these, so a resume that lost them would
    put every taught copy back in the inventor's hands."""
    name = await _invented(world, registry)
    await spawn_agent(world, "bram", OFFICE)
    await spawn_agent(world, "nell", OFFICE)
    await registry.execute(world, "ada", "teach_tool", {
        "tool_name": name, "student_id": "bram", "student_consent": True,
    })
    await registry.execute(world, "ada", "license_tool", {
        "tool_name": name, "licensee_id": "nell", "term_days": 5,
    })

    saved = world.snapshot()
    world.tool_copies.clear()
    world.tool_licences.clear()
    world.restore(saved)

    assert world.holders_of(name, world.tick()) == {"ada", "bram", "nell"}
