"""The workshop and the council: what the town invented, and what it legislated.

Part E.3 and E.4. Both views answer a question that was already true in
`WorldState` and simply had no way out of the process:

  GET /workshop   every tool an agent composed and every workflow an agent
                  authored, each with the adoption figures that say whether
                  the town actually took the idea up.
  GET /council    the governmental workflows only, grouped by where they
                  have got to in `charter/amendments.py`'s own state machine.

**No new state.** Every number below is read from live world state at request
time - `custom_tools`, `tool_invocations`, `skill_definitions`, `skills`,
`skill_reps`, `skill_progress`, and `charter.amendments.proposals`. Nothing
here is cached, counted incrementally, or written back, so a workshop view
cannot drift from the world it describes and there is no invalidation to get
wrong.

**Why a separate router and not more of `skills_api`.** That module answers
"how good is this agent at things" - a character sheet, keyed by agent. This
one answers "what has this town produced" - keyed by the artefact, across
every agent. Same underlying dicts, opposite index, and joining them into one
module would mean every response carrying the half the caller did not ask for.

**On "repealed".** The spec's Council sketch shows a REPEALED group. There is
no repeal transition in `AmendmentStatus` - it runs PROPOSED ->
PASSED_AWAITING_PRESIDENT -> ENACTED | VETOED | REJECTED, and nothing moves a
proposal out of ENACTED afterwards. So this module reports `refused` (vetoed
or rejected) and says so plainly rather than labelling a refusal a repeal,
which would be a fabricated distinction the data cannot support. If a repeal
transition is added later this is the module that grows a fourth group.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from world.charter.models import AmendmentStatus
from world.life.models import Genesis
from world.tools.state import WorldState

TICKS_PER_DAY = 144


@dataclass
class WorkshopDeps:
    world: WorldState
    genesis: Genesis


def name_map(genesis: Genesis) -> dict[str, str]:
    return {agent.agent_id: agent.name for agent in genesis.agents}


async def add_cors_headers(response: Response) -> None:
    response.headers["Access-Control-Allow-Origin"] = "*"


class StepView(BaseModel):
    """One step of an authored workflow, as the author wrote it.

    `ref` is None for a `check` step - the agent reminding its future self
    what to look at - which is why the viewer must render kind before ref
    rather than assuming every step names something callable.
    """

    order: int
    kind: str
    ref: str | None = None
    note: str = ""


class InventedToolView(BaseModel):
    name: str
    description: str
    creator_agent_id: str
    creator_name: str | None
    created_tick: int
    version: int
    revised_tick: int | None
    approved: bool
    pending_patent: bool
    uses: int
    distinct_users: int
    adopted: bool


class AuthoredSkillView(BaseModel):
    name: str
    author_agent_id: str
    author_name: str | None
    created_tick: int
    version: int
    revised_tick: int | None
    revisions: int
    governmental: bool
    namespace: str
    owner_id: str | None
    status: str
    preconditions: str
    steps: list[StepView]
    discounts_tools: list[str]
    holders: int
    average_proficiency: float
    completions: int
    followers_now: int
    spread_per_day: float


class WorkshopView(BaseModel):
    tick: int
    tools: list[InventedToolView]
    skills: list[AuthoredSkillView]
    tools_invented: int
    skills_authored: int


class CouncilItemView(BaseModel):
    skill: str
    author_agent_id: str
    author_name: str | None
    created_tick: int
    steps: list[StepView]
    proposal_id: str | None = None
    title: str | None = None
    rationale: str | None = None
    moved_by_agent_id: str | None = None
    moved_by_name: str | None = None
    moved_tick: int | None = None
    status: str | None = None
    yes_votes: int = 0
    votes_cast: int = 0
    seats: int = 0
    votes_required: int = 0
    enacted_tick: int | None = None
    vetoed_tick: int | None = None
    veto_note: str | None = None


class CouncilView(BaseModel):
    tick: int
    seats: int
    votes_required: int
    before_council: list[CouncilItemView]
    in_force: list[CouncilItemView]
    refused: list[CouncilItemView]


def _steps(definition: Any) -> list[StepView]:
    return [
        StepView(order=s.order, kind=s.kind, ref=s.ref, note=s.note)
        for s in sorted(definition.steps, key=lambda s: s.order)
    ]


def invented_tools(world: WorldState, names: dict[str, str]) -> list[InventedToolView]:
    """Every composed tool, with how much use it actually got.

    Usage is counted over `tool_invocations`, which records one row per call
    that reached and completed a handler - so a tool that was filed and only
    ever refused reports zero uses, which is the honest number.
    """
    uses: Counter[str] = Counter()
    users: dict[str, set[str]] = defaultdict(set)
    for record in world.tool_invocations:
        uses[record.tool_name] += 1
        users[record.tool_name].add(record.actor_id)

    rows: list[InventedToolView] = []
    for name, tool in world.custom_tools.items():
        distinct = users.get(name, set())
        rows.append(
            InventedToolView(
                name=name,
                description=tool.description,
                creator_agent_id=tool.creator_agent_id,
                creator_name=names.get(tool.creator_agent_id),
                created_tick=tool.created_tick,
                version=tool.version,
                revised_tick=tool.revised_tick,
                approved=tool.approved,
                pending_patent=tool.patent_application_id is not None and not tool.approved,
                uses=uses.get(name, 0),
                distinct_users=len(distinct),
                adopted=len(distinct - {tool.creator_agent_id}) > 0,
            )
        )
    rows.sort(key=lambda r: (-r.created_tick, r.name))
    return rows


def authored_skills(world: WorldState, names: dict[str, str]) -> list[AuthoredSkillView]:
    """Every workflow the town wrote down, with how far it has spread.

    `completions` is `skill_reps`, not `skills`: for an authored workflow a
    repetition is only earned by walking the thing end to end (see
    `WorldState.advance_skill_workflows`), which makes it the count of times
    the procedure was actually performed. `skills` is the proficiency that
    count is converted into, and the two are shown side by side deliberately.
    """
    following: Counter[str] = Counter()
    for progress in world.skill_progress.values():
        following[progress.skill_name] += 1

    rows: list[AuthoredSkillView] = []
    for name, definition in world.skill_definitions.items():
        levels = [
            held[name] for held in world.skills.values() if name in held
        ]
        completions = sum(
            reps.get(name, 0) for reps in world.skill_reps.values()
        )
        age_days = max(1.0, (world.tick() - definition.created_tick) / TICKS_PER_DAY)
        rows.append(
            AuthoredSkillView(
                name=name,
                author_agent_id=definition.author_agent_id,
                author_name=names.get(definition.author_agent_id),
                created_tick=definition.created_tick,
                version=definition.version,
                revised_tick=definition.revised_tick,
                revisions=len(definition.superseded),
                governmental=definition.governmental,
                namespace=definition.namespace,
                owner_id=definition.owner_id,
                status=definition.status,
                preconditions=definition.preconditions,
                steps=_steps(definition),
                discounts_tools=list(definition.discounts_tools),
                holders=len(levels),
                average_proficiency=round(sum(levels) / len(levels), 1) if levels else 0.0,
                completions=completions,
                followers_now=following.get(name, 0),
                spread_per_day=round(len(levels) / age_days, 2),
            )
        )
    rows.sort(key=lambda r: (-r.created_tick, r.name))
    return rows


def _council_item(
    definition: Any,
    names: dict[str, str],
    proposal: Any | None,
    seats: int,
    required: int,
) -> CouncilItemView:
    item = CouncilItemView(
        skill=definition.name,
        author_agent_id=definition.author_agent_id,
        author_name=names.get(definition.author_agent_id),
        created_tick=definition.created_tick,
        steps=_steps(definition),
        seats=seats,
        votes_required=required,
    )
    if proposal is None:
        return item
    item.proposal_id = proposal.proposal_id
    item.title = proposal.title
    item.rationale = proposal.rationale
    item.moved_by_agent_id = proposal.proposed_by_agent_id
    item.moved_by_name = names.get(proposal.proposed_by_agent_id)
    item.moved_tick = proposal.proposed_tick
    item.status = proposal.status.value
    item.yes_votes = sum(1 for v in proposal.votes.values() if v)
    item.votes_cast = len(proposal.votes)
    item.enacted_tick = proposal.enacted_tick
    item.vetoed_tick = proposal.vetoed_tick
    item.veto_note = proposal.veto_note
    return item


def build_workshop_router(get_deps: Callable[[], WorkshopDeps]) -> APIRouter:
    router = APIRouter(tags=["workshop"], dependencies=[Depends(add_cors_headers)])

    @router.get("/workshop", response_model=WorkshopView)
    async def workshop() -> WorkshopView:
        deps = get_deps()
        names = name_map(deps.genesis)
        tools = invented_tools(deps.world, names)
        skills = authored_skills(deps.world, names)
        return WorkshopView(
            tick=deps.world.tick(),
            tools=tools,
            skills=skills,
            tools_invented=len(tools),
            skills_authored=len(skills),
        )

    @router.get("/council", response_model=CouncilView)
    async def council() -> CouncilView:
        """Governmental workflows only.

        The tab should be mostly empty. A town that legislates constantly is
        a more interesting finding than one that does not, and that is only
        legible if ordinary trade workflows never appear here.
        """
        deps = get_deps()
        names = name_map(deps.genesis)
        world = deps.world

        charter = await world.charter.store.get_charter()
        government = await world.charter.store.get_government_state()
        seats = len(government.all_seat_holders())
        required = charter.amendment_votes_required
        proposals = world.charter.amendments.proposals

        before: list[CouncilItemView] = []
        in_force: list[CouncilItemView] = []
        refused: list[CouncilItemView] = []

        for definition in world.skill_definitions.values():
            if not definition.governmental:
                continue
            proposal = (
                proposals.get(definition.amendment_id) if definition.amendment_id else None
            )
            item = _council_item(definition, names, proposal, seats, required)
            if proposal is None:
                in_force.append(item)
            elif proposal.status in (
                AmendmentStatus.PROPOSED,
                AmendmentStatus.PASSED_AWAITING_PRESIDENT,
            ):
                before.append(item)
            elif proposal.status is AmendmentStatus.ENACTED:
                in_force.append(item)
            else:
                refused.append(item)

        before.sort(key=lambda i: -(i.moved_tick or 0))
        in_force.sort(key=lambda i: -(i.enacted_tick or i.created_tick))
        refused.sort(key=lambda i: -(i.vetoed_tick or i.moved_tick or 0))

        return CouncilView(
            tick=world.tick(),
            seats=seats,
            votes_required=required,
            before_council=before,
            in_force=in_force,
            refused=refused,
        )

    return router
