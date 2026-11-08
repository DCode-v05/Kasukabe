"""B6: the skills views (The 250 section 22).

"What's missing today is making this visible as a feature, not just a
number in the database."

Proven against a real wired WorldServer, and - for provenance - against the
real `teach_skill` tool call rather than a hand-written dict, so "taught by
whom" is proven end to end.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from world.ledger.action_costs import apply_skill_discount
from world.skills_api import (
    NOVICE_REFERENCE_COST_SIM,
    SkillsDeps,
    build_skills_router,
    town_spread,
)
from world.life.models import Household
from world.tools.bootstrap import build_world
from world.tools.tests.conftest import spawn_agent
from world.wiring import WorldConfig, build_world_server
from world.tests.map_assets import needs_the_map

pytestmark = needs_the_map


def make_client(server) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_skills_router(lambda: SkillsDeps(world=server.world, genesis=server.genesis))
    )
    return TestClient(app)


@pytest.mark.asyncio
async def test_the_sheet_lists_skills_best_first():
    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.skills[who] = {"baking": 40, "haggling": 80}
    sheet = make_client(server).get(f"/skills/agents/{who}").json()
    assert [s["skill"] for s in sheet["skills"]] == ["haggling", "baking"]
    assert sheet["best_skill"] == "haggling"


@pytest.mark.asyncio
async def test_what_a_skill_is_worth_is_the_ledgers_own_number():
    """Section 22 asks for it "measured as: how much cheaper this agent's
    work is, next to a novice" - so the sheet must never claim a discount
    the agent would not actually be charged."""
    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.skills[who] = {"baking": 50}
    entry = make_client(server).get(f"/skills/agents/{who}").json()["skills"][0]
    assert entry["their_cost_sim"] == apply_skill_discount(NOVICE_REFERENCE_COST_SIM, 50)
    assert entry["novice_cost_sim"] == NOVICE_REFERENCE_COST_SIM
    assert 0 < entry["discount_pct"] < 100


@pytest.mark.asyncio
async def test_mastery_is_reported_against_the_real_cap():
    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.skills[who] = {"baking": 500}
    entry = make_client(server).get(f"/skills/agents/{who}").json()["skills"][0]
    assert entry["mastery_pct"] == 100.0, "a proficiency past the cap is still 100% mastered"


@pytest.mark.asyncio
async def test_a_skill_nobody_taught_reads_as_self_taught():
    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.skills[who] = {"baking": 10}
    sheet = make_client(server).get(f"/skills/agents/{who}").json()
    assert sheet["skills"][0]["learned"] == "self-taught"
    assert sheet["skills"][0]["taught_by_agent_id"] is None
    assert sheet["total_self_taught"] == 1


@pytest.mark.asyncio
async def test_an_unknown_agent_is_a_404():
    server = await build_world_server(WorldConfig())
    assert make_client(server).get("/skills/agents/nobody").status_code == 404


@pytest.mark.asyncio
async def test_a_real_lesson_is_attributed_to_the_teacher():
    """End to end: the tool call, not a hand-written dict."""
    world, registry = await build_world(seed=1)
    await spawn_agent(world, "lucia", "square")
    await spawn_agent(world, "sofia", "square")
    world.skills["lucia"] = {"construction": 60}

    await registry.execute(
        world, "lucia", "teach_skill",
        {"student_agent_id": "sofia", "skill": "construction", "student_consent": True},
    )
    assert world.skills["sofia"]["construction"] > 0
    assert world.skill_sources["sofia"]["construction"] == "lucia"


@pytest.mark.asyncio
async def test_being_taught_something_you_already_know_better_records_no_teacher():
    """It would put a false teacher on the sheet."""
    world, registry = await build_world(seed=1)
    await spawn_agent(world, "lucia", "square")
    await spawn_agent(world, "sofia", "square")
    world.skills["lucia"] = {"construction": 10}
    world.skills["sofia"] = {"construction": 90}

    await registry.execute(
        world, "lucia", "teach_skill",
        {"student_agent_id": "sofia", "skill": "construction", "student_consent": True},
    )
    assert world.skills["sofia"]["construction"] == 90
    assert "construction" not in world.skill_sources.get("sofia", {})


@pytest.mark.asyncio
async def test_the_town_view_names_the_best_at_each_trade():
    server = await build_world_server(WorldConfig())
    a, b, c = list(server.world.agents)[:3]
    server.world.skills[a] = {"baking": 30}
    server.world.skills[b] = {"baking": 90}
    server.world.skills[c] = {"building": 50}
    rows = {s["skill"]: s for s in make_client(server).get("/skills/town").json()}
    assert rows["baking"]["best"]["agent_id"] == b
    assert rows["baking"]["practitioners"] == 2
    assert rows["baking"]["average_proficiency"] == 60.0
    assert rows["building"]["best"]["agent_id"] == c


@pytest.mark.asyncio
async def test_a_zero_proficiency_is_not_a_practitioner():
    server = await build_world_server(WorldConfig())
    a = next(iter(server.world.agents))
    server.world.skills[a] = {"baking": 0}
    assert town_spread(server.world, {}) == []


@pytest.mark.asyncio
async def test_a_trade_running_in_a_family_is_visible():
    """"Which trades run in which families, across generations"."""
    server = await build_world_server(WorldConfig())
    parent, child = list(server.world.agents)[:2]
    server.world.agents[parent] = server.world.agents[parent].model_copy(update={"age_years": 50.0})
    server.world.agents[child] = server.world.agents[child].model_copy(update={"age_years": 12.0})
    server.world.households["hh-reyes"] = Household(
        household_id="hh-reyes", member_ids=[parent, child]
    )
    server.world.skills[parent] = {"construction": 80}
    server.world.skills[child] = {"construction": 40}
    server.world.skill_sources[child] = {"construction": parent}

    tree = make_client(server).get("/skills/families/hh-reyes").json()
    branch = tree["branches"][0]
    assert branch["skill"] == "construction"
    assert len(branch["members"]) == 2
    assert branch["generations"] == 2, "a child and an elder is two generations"
    assert branch["taught_within_family"] == 1
    assert tree["trades_that_run_in_the_family"] == ["construction"]


@pytest.mark.asyncio
async def test_one_practitioner_is_a_job_not_a_trade_that_runs_in_the_family():
    server = await build_world_server(WorldConfig())
    solo = next(iter(server.world.agents))
    server.world.households["hh-solo"] = Household(household_id="hh-solo", member_ids=[solo])
    server.world.skills[solo] = {"baking": 70}
    tree = make_client(server).get("/skills/families/hh-solo").json()
    assert tree["branches"][0]["skill"] == "baking"
    assert tree["trades_that_run_in_the_family"] == []


@pytest.mark.asyncio
async def test_a_teacher_outside_the_family_does_not_count_as_taught_within():
    server = await build_world_server(WorldConfig())
    member, outsider = list(server.world.agents)[:2]
    server.world.households["hh-x"] = Household(household_id="hh-x", member_ids=[member])
    server.world.skills[member] = {"baking": 30}
    server.world.skill_sources[member] = {"baking": outsider}
    tree = make_client(server).get("/skills/families/hh-x").json()
    assert tree["branches"][0]["taught_within_family"] == 0


@pytest.mark.asyncio
async def test_the_family_listing_skips_households_with_no_skills():
    """A list of empty trees is noise, not a view."""
    server = await build_world_server(WorldConfig())
    server.world.skills.clear()
    assert make_client(server).get("/skills/families").json() == []


@pytest.mark.asyncio
async def test_an_unknown_household_is_a_404():
    server = await build_world_server(WorldConfig())
    assert make_client(server).get("/skills/families/nope").status_code == 404


@pytest.mark.asyncio
async def test_every_skills_route_is_read_only():
    server = await build_world_server(WorldConfig())
    router = build_skills_router(lambda: SkillsDeps(world=server.world, genesis=server.genesis))
    assert {m for r in router.routes for m in r.methods} == {"GET"}
