"""Part E.3/E.4: the workshop and the council.

Proven against a real wired WorldServer rather than hand-built dicts, so
"what has this town invented" is answered from the same state the engine
writes to - the point of the views is that they need no bookkeeping of their
own, and a test that fabricated its own state would not prove that.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from world.charter.models import AmendmentProposal, AmendmentStatus
from world.tools.models import (
    CustomToolDefinition,
    SkillDefinition,
    SkillProgress,
    SkillStep,
    ToolInvocationRecord,
)
from world.wiring import WorldConfig, build_world_server
from world.workshop_api import WorkshopDeps, build_workshop_router
from world.tests.map_assets import needs_the_map

# TOOL-27. This module needs the Ville map assets, which live in the
# sibling `smallville/` checkout, are gitignored, and are absent on any
# host that did not clone them. Without them these FAILED rather than
# skipped, and red for environment hides real regressions inside the
# noise - which is how TOOL-09 stayed invisible for a whole cycle.
pytestmark = needs_the_map


def make_client(server) -> TestClient:
    app = FastAPI()
    app.include_router(
        build_workshop_router(lambda: WorkshopDeps(world=server.world, genesis=server.genesis))
    )
    return TestClient(app)


def a_workflow(name: str, author: str, *, governmental: bool = False, tick: int = 100,
               amendment_id: str | None = None) -> SkillDefinition:
    return SkillDefinition(
        name=name,
        author_agent_id=author,
        steps=[
            SkillStep(order=1, kind="check", note="is the saw sharp?"),
            SkillStep(order=2, kind="tool", ref="look_around"),
            SkillStep(order=3, kind="tool", ref="fell_timber"),
        ],
        discounts_tools=["look_around", "fell_timber"],
        governmental=governmental,
        created_tick=tick,
        status="PENDING_COUNCIL" if governmental else "ACTIVE",
        amendment_id=amendment_id,
    )


# -- the workshop (E.3) ---------------------------------------------------


@pytest.mark.asyncio
async def test_an_invented_tool_reports_who_filed_it_and_when():
    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.custom_tools["rope_making"] = CustomToolDefinition(
        tool_name="rope_making",
        description="twist fibre into rope",
        creator_agent_id=who,
        steps=[],
        approved=True,
        created_tick=412,
    )
    tools = make_client(server).get("/workshop").json()["tools"]
    assert [t["name"] for t in tools] == ["rope_making"]
    assert tools[0]["created_tick"] == 412
    assert tools[0]["creator_agent_id"] == who
    # Resolved through genesis, because WorldState carries no display name.
    assert tools[0]["creator_name"]


@pytest.mark.asyncio
async def test_a_tool_only_its_inventor_ever_called_is_not_adopted():
    """The spec's warning mark. Not a bug to fix - the town telling you
    which ideas did not take, which is as interesting as the ones that did.
    """
    server = await build_world_server(WorldConfig())
    ids = list(server.world.agents)
    inventor, other = ids[0], ids[1]
    server.world.custom_tools["dry_curing"] = CustomToolDefinition(
        tool_name="dry_curing", description="", creator_agent_id=inventor,
        steps=[], approved=True, created_tick=883,
    )
    server.world.custom_tools["rope_making"] = CustomToolDefinition(
        tool_name="rope_making", description="", creator_agent_id=inventor,
        steps=[], approved=True, created_tick=412,
    )
    for i in range(3):
        server.world.tool_invocations.append(ToolInvocationRecord(
            invocation_id=f"i{i}", tool_name="dry_curing", namespace="public",
            actor_id=inventor, tick=900 + i,
        ))
    server.world.tool_invocations.append(ToolInvocationRecord(
        invocation_id="x", tool_name="rope_making", namespace="public",
        actor_id=other, tick=500,
    ))

    by_name = {t["name"]: t for t in make_client(server).get("/workshop").json()["tools"]}
    # Used three times, by one agent, who invented it: nobody took it up.
    assert by_name["dry_curing"]["uses"] == 3
    assert by_name["dry_curing"]["distinct_users"] == 1
    assert by_name["dry_curing"]["adopted"] is False
    # Somebody other than the inventor reached for it: that is adoption.
    assert by_name["rope_making"]["adopted"] is True


@pytest.mark.asyncio
async def test_completions_come_from_reps_not_from_proficiency():
    """`skills` is the level a count was converted into; `skill_reps` is the
    count. For an authored workflow only an end-to-end walk earns a rep, so
    reporting proficiency as "completions" would inflate every row."""
    server = await build_world_server(WorldConfig())
    ids = list(server.world.agents)
    author, student = ids[0], ids[1]
    server.world.skill_definitions["clearing_a_stand"] = a_workflow("clearing_a_stand", author)
    server.world.skills[author] = {"clearing_a_stand": 61}
    server.world.skills[student] = {"clearing_a_stand": 21}
    server.world.skill_reps[author] = {"clearing_a_stand": 34}
    server.world.skill_reps[student] = {"clearing_a_stand": 5}

    skill = make_client(server).get("/workshop").json()["skills"][0]
    assert skill["holders"] == 2
    assert skill["average_proficiency"] == 41.0
    assert skill["completions"] == 39      # 34 + 5, not 61 + 21
    assert [s["kind"] for s in skill["steps"]] == ["check", "tool", "tool"]


@pytest.mark.asyncio
async def test_a_check_step_survives_the_wire_without_a_ref():
    """A `check` step has no ref at all. If the view dropped it, the step
    numbering in the resident file would silently disagree with the
    workflow the agent is actually walking."""
    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.skill_definitions["limbing"] = a_workflow("limbing", who)
    steps = make_client(server).get("/workshop").json()["skills"][0]["steps"]
    assert steps[0]["ref"] is None and steps[0]["note"] == "is the saw sharp?"


# -- the council (E.4) ----------------------------------------------------


@pytest.mark.asyncio
async def test_an_ordinary_trade_workflow_never_reaches_the_council():
    """The tab should be mostly empty, and that is only legible if trade
    procedures cannot appear in it."""
    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.skill_definitions["clearing_a_stand"] = a_workflow("clearing_a_stand", who)
    council = make_client(server).get("/council").json()
    assert council["before_council"] == []
    assert council["in_force"] == []
    assert council["refused"] == []


@pytest.mark.asyncio
async def test_a_standing_order_awaiting_votes_shows_the_seat_arithmetic():
    server = await build_world_server(WorldConfig())
    ids = list(server.world.agents)
    mover = ids[0]
    proposal = AmendmentProposal(
        field_name="curfew_hour", new_value=21, title="night curfew",
        rationale="too many burglaries", proposed_by_agent_id=mover, proposed_tick=1204,
    )
    proposal.votes = {ids[1]: True, ids[2]: True, ids[3]: False}
    server.world.charter.amendments.proposals[proposal.proposal_id] = proposal
    server.world.skill_definitions["night_curfew"] = a_workflow(
        "night_curfew", mover, governmental=True, tick=1204,
        amendment_id=proposal.proposal_id,
    )

    council = make_client(server).get("/council").json()
    assert len(council["before_council"]) == 1
    item = council["before_council"][0]
    assert item["skill"] == "night_curfew"
    assert item["title"] == "night curfew"
    assert item["yes_votes"] == 2 and item["votes_cast"] == 3
    assert item["votes_required"] == council["votes_required"] > 0
    assert item["moved_by_name"]


@pytest.mark.asyncio
async def test_enacted_and_refused_orders_land_in_their_own_groups():
    server = await build_world_server(WorldConfig())
    ids = list(server.world.agents)
    enacted = AmendmentProposal(
        field_name="a", new_value=1, title="summons before sentencing", rationale="",
        proposed_by_agent_id=ids[0], proposed_tick=800,
        status=AmendmentStatus.ENACTED, enacted_tick=903,
    )
    vetoed = AmendmentProposal(
        field_name="b", new_value=2, title="market inspection", rationale="",
        proposed_by_agent_id=ids[1], proposed_tick=1000,
        status=AmendmentStatus.VETOED, vetoed_tick=1102, veto_note="overreach",
    )
    for p in (enacted, vetoed):
        server.world.charter.amendments.proposals[p.proposal_id] = p
    server.world.skill_definitions["summons"] = a_workflow(
        "summons", ids[0], governmental=True, amendment_id=enacted.proposal_id)
    server.world.skill_definitions["inspection"] = a_workflow(
        "inspection", ids[1], governmental=True, amendment_id=vetoed.proposal_id)

    council = make_client(server).get("/council").json()
    assert [i["skill"] for i in council["in_force"]] == ["summons"]
    assert council["in_force"][0]["enacted_tick"] == 903
    assert [i["skill"] for i in council["refused"]] == ["inspection"]
    assert council["refused"][0]["veto_note"] == "overreach"


@pytest.mark.asyncio
async def test_a_governmental_workflow_with_no_amendment_is_shown_not_dropped():
    """Followable now, so it belongs with the things in force. Silently
    dropping it would under-report what the town is actually operating."""
    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.skill_definitions["old_rite"] = a_workflow(
        "old_rite", who, governmental=True, amendment_id=None)
    council = make_client(server).get("/council").json()
    assert [i["skill"] for i in council["in_force"]] == ["old_rite"]
    assert council["in_force"][0]["status"] is None


# -- the agent row carries it (E.1/E.2) -----------------------------------


@pytest.mark.asyncio
async def test_the_agent_row_carries_the_document_and_the_step():
    """One poll, one instant: the step an agent is on must arrive on the
    same row as where it is standing, not from a second call."""
    from world.agents_api import AgentsDeps, build_agents_router

    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.skill_definitions["clearing_a_stand"] = a_workflow("clearing_a_stand", who)
    server.world.skills[who] = {"clearing_a_stand": 61}
    server.world.skill_reps[who] = {"clearing_a_stand": 34}
    server.world.skill_progress[f"{who}::clearing_a_stand"] = SkillProgress(
        agent_id=who, skill_name="clearing_a_stand", next_step=2,
        started_tick=10, last_advance_tick=20,
    )

    app = FastAPI()
    app.include_router(build_agents_router(
        lambda: AgentsDeps(world=server.world, genesis=server.genesis)))
    rows = TestClient(app).get("/agents").json()
    row = next(r for r in rows if r["agent_id"] == who)

    assert row["following"] == {"skill": "clearing_a_stand", "step": 3, "total": 3}
    held = row["skills"][0]
    assert held["proficiency"] == 61 and held["completions"] == 34
    assert held["authored"] is True and held["next_step"] == 2
    assert len(held["steps"]) == 3


@pytest.mark.asyncio
async def test_a_built_in_trade_has_a_level_but_no_document():
    """farming/forestry/... are proficiencies the world always knew. They
    have no author and no steps, and the row must say so rather than
    inventing a workflow nobody wrote."""
    from world.agents_api import AgentsDeps, build_agents_router

    server = await build_world_server(WorldConfig())
    who = next(iter(server.world.agents))
    server.world.skills[who] = {"forestry": 44}

    app = FastAPI()
    app.include_router(build_agents_router(
        lambda: AgentsDeps(world=server.world, genesis=server.genesis)))
    row = next(r for r in TestClient(app).get("/agents").json() if r["agent_id"] == who)
    held = next(s for s in row["skills"] if s["name"] == "forestry")
    assert held["authored"] is False and held["steps"] == []
    assert row["following"] is None
