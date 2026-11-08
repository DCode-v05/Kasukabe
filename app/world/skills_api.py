"""Read-only Skills viewer API (The 250 section 22).

"What's missing today is making this **visible as a feature**, not just a
number in the database."

Three views, one per bullet of section 22:

  22.a `/skills/agents/{agent_id}`  a character sheet: which skills, what
        level, how they were learned, and what each one is currently worth
  22.b `/skills/town`               the town-wide spread - who is the best
        baker, the best builder, right now
  22.c `/skills/families`           which trades run in which families,
        since teaching is cheapest between relatives (section 16)

Every route is a plain GET with no token, for the reason DESIGN.md section 8
gives and governance_api.py states: viewers "are windows, never
authorities."

**What a skill is worth is not invented here.** Section 22 asks for it
"measured as: how much cheaper/faster this agent's work in that trade is,
next to a novice" - and that number already exists, as
`world.ledger.action_costs.apply_skill_discount`, the same function the
ledger charges through. This module calls it rather than describing it, so
the sheet can never claim a discount the agent would not actually get.

**Provenance comes from world state, not from a guess.** `world.skills`
records levels only; `world.skill_sources` (added with this view) records
who taught what, written by `teach_skill_handler` at the moment a lesson
actually raised the student's proficiency.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from world.ledger.action_costs import (
    SKILL_DISCOUNT_PROFICIENCY_CAP,
    apply_skill_discount,
)
from world.life.models import Genesis
from world.tools.state import WorldState

NOVICE_REFERENCE_COST_SIM = 100


@dataclass
class SkillsDeps:
    world: WorldState
    genesis: Genesis


def name_map(genesis: Genesis) -> dict[str, str]:
    return {agent.agent_id: agent.name for agent in genesis.agents}


class SkillEntry(BaseModel):
    """One line of a character sheet."""

    skill: str
    proficiency: int
    learned: str
    taught_by_agent_id: str | None
    taught_by_name: str | None
    novice_cost_sim: int
    their_cost_sim: int
    discount_pct: float
    mastery_pct: float


class AgentSkillSheet(BaseModel):
    agent_id: str
    name: str
    skills: list[SkillEntry]
    best_skill: str | None
    total_taught: int
    total_self_taught: int


class SkillHolder(BaseModel):
    agent_id: str
    name: str
    proficiency: int
    learned: str


class SkillSpread(BaseModel):
    """One trade, and who in town is best at it."""

    skill: str
    practitioners: int
    best: SkillHolder | None
    average_proficiency: float
    holders: list[SkillHolder]


class FamilySkillBranch(BaseModel):
    """One trade running through one family."""

    skill: str
    members: list[SkillHolder]
    generations: int
    taught_within_family: int


class FamilySkillTree(BaseModel):
    household_id: str
    member_ids: list[str]
    branches: list[FamilySkillBranch]
    trades_that_run_in_the_family: list[str]


def skill_entry(
    world: WorldState, names: dict[str, str], agent_id: str, skill: str, proficiency: int
) -> SkillEntry:
    """One skill, with what it is actually worth at the ledger's own rate."""
    taught_by = world.skill_sources.get(agent_id, {}).get(skill)
    their_cost = apply_skill_discount(NOVICE_REFERENCE_COST_SIM, proficiency)
    saved = NOVICE_REFERENCE_COST_SIM - their_cost
    return SkillEntry(
        skill=skill,
        proficiency=proficiency,
        learned="taught" if taught_by else "self-taught",
        taught_by_agent_id=taught_by,
        taught_by_name=names.get(taught_by) if taught_by else None,
        novice_cost_sim=NOVICE_REFERENCE_COST_SIM,
        their_cost_sim=their_cost,
        discount_pct=round(100 * saved / NOVICE_REFERENCE_COST_SIM, 1),
        mastery_pct=round(
            100 * min(proficiency, SKILL_DISCOUNT_PROFICIENCY_CAP) / SKILL_DISCOUNT_PROFICIENCY_CAP,
            1,
        ),
    )


def agent_sheet(world: WorldState, names: dict[str, str], agent_id: str) -> AgentSkillSheet:
    held = world.skills.get(agent_id, {})
    entries = [
        skill_entry(world, names, agent_id, skill, level)
        for skill, level in sorted(held.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return AgentSkillSheet(
        agent_id=agent_id,
        name=names.get(agent_id, agent_id),
        skills=entries,
        best_skill=entries[0].skill if entries else None,
        total_taught=sum(1 for e in entries if e.learned == "taught"),
        total_self_taught=sum(1 for e in entries if e.learned == "self-taught"),
    )


def town_spread(world: WorldState, names: dict[str, str]) -> list[SkillSpread]:
    """Every trade practised in town, best-attested first."""
    by_skill: dict[str, list[SkillHolder]] = defaultdict(list)
    for agent_id, held in world.skills.items():
        for skill, level in held.items():
            if level <= 0:
                continue
            by_skill[skill].append(
                SkillHolder(
                    agent_id=agent_id,
                    name=names.get(agent_id, agent_id),
                    proficiency=level,
                    learned="taught" if world.skill_sources.get(agent_id, {}).get(skill) else "self-taught",
                )
            )
    spreads = []
    for skill, holders in by_skill.items():
        holders.sort(key=lambda h: (-h.proficiency, h.agent_id))
        spreads.append(
            SkillSpread(
                skill=skill,
                practitioners=len(holders),
                best=holders[0] if holders else None,
                average_proficiency=round(sum(h.proficiency for h in holders) / len(holders), 1),
                holders=holders,
            )
        )
    spreads.sort(key=lambda s: (-s.practitioners, s.skill))
    return spreads


def family_tree(world: WorldState, names: dict[str, str], household_id: str) -> FamilySkillTree:
    """Which trades run in this family, and how many were taught inside it.

    "Since teaching is cheapest between relatives, a natural view is: which
    trades run in which families, across generations." A trade "runs in the
    family" when more than one member holds it - one practitioner is a job,
    two is a trade being handed on.
    """
    household = world.households.get(household_id)
    member_ids = list(household.member_ids) if household is not None else []
    members = set(member_ids)

    by_skill: dict[str, list[SkillHolder]] = defaultdict(list)
    taught_inside: dict[str, int] = defaultdict(int)
    for agent_id in member_ids:
        for skill, level in world.skills.get(agent_id, {}).items():
            if level <= 0:
                continue
            teacher = world.skill_sources.get(agent_id, {}).get(skill)
            by_skill[skill].append(
                SkillHolder(
                    agent_id=agent_id,
                    name=names.get(agent_id, agent_id),
                    proficiency=level,
                    learned="taught" if teacher else "self-taught",
                )
            )
            if teacher in members:
                taught_inside[skill] += 1

    branches = []
    for skill, holders in by_skill.items():
        holders.sort(key=lambda h: -h.proficiency)
        ages = {
            _generation_of(world, h.agent_id)
            for h in holders
            if world.agents.get(h.agent_id) is not None
        }
        branches.append(
            FamilySkillBranch(
                skill=skill,
                members=holders,
                generations=len(ages),
                taught_within_family=taught_inside.get(skill, 0),
            )
        )
    branches.sort(key=lambda b: (-len(b.members), b.skill))
    return FamilySkillTree(
        household_id=household_id,
        member_ids=member_ids,
        branches=branches,
        trades_that_run_in_the_family=[b.skill for b in branches if len(b.members) > 1],
    )


def _generation_of(world: WorldState, agent_id: str) -> str:
    agent = world.agents.get(agent_id)
    if agent is None:
        return "unknown"
    if agent.age_years < 18:
        return "child"
    if agent.age_years < 45:
        return "adult"
    return "elder"


async def add_cors_headers(response: Response) -> None:
    response.headers["Access-Control-Allow-Origin"] = "*"


def build_skills_router(get_deps: Callable[[], SkillsDeps]) -> APIRouter:
    router = APIRouter(tags=["skills"], dependencies=[Depends(add_cors_headers)])

    @router.get("/skills/agents/{agent_id}", response_model=AgentSkillSheet)
    async def sheet(agent_id: str) -> AgentSkillSheet:
        deps = get_deps()
        if agent_id not in deps.world.agents:
            raise HTTPException(status_code=404, detail=f"no agent {agent_id!r}")
        return agent_sheet(deps.world, name_map(deps.genesis), agent_id)

    @router.get("/skills/town", response_model=list[SkillSpread])
    async def town() -> list[SkillSpread]:
        deps = get_deps()
        return town_spread(deps.world, name_map(deps.genesis))

    @router.get("/skills/families", response_model=list[FamilySkillTree])
    async def families() -> list[FamilySkillTree]:
        """Only households that actually hold a skill - a list of empty
        trees is noise, not a view."""
        deps = get_deps()
        names = name_map(deps.genesis)
        trees = [family_tree(deps.world, names, hid) for hid in deps.world.households]
        trees = [t for t in trees if t.branches]
        trees.sort(key=lambda t: (-len(t.trades_that_run_in_the_family), t.household_id))
        return trees

    @router.get("/skills/families/{household_id}", response_model=FamilySkillTree)
    async def one_family(household_id: str) -> FamilySkillTree:
        deps = get_deps()
        if household_id not in deps.world.households:
            raise HTTPException(status_code=404, detail=f"no household {household_id!r}")
        return family_tree(deps.world, name_map(deps.genesis), household_id)

    return router
