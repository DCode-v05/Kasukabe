"""Read-only Agent Finder + Agent Activity + Agent Reasoning viewer API
(DESIGN.md section 8, the three most individual-agent-centric entries in
the Viewers list - "Agent finder", "Agent activity viewer", and "Agent
reasoning viewer", the latter explicitly called out as "the research
centrepiece").

Identity (name, sex, occupation, household/business links, ablation arm,
bio, personality) comes from `genesis.json` (`world.life.models.
GenesisAgent`), not the trace store's `agents` table - `AgentLifeRecord`
(what `WorldState.agents` actually holds) deliberately carries no name
(life/models.py's own docstring), and `ablation_arm` was never even
schema'd into the trace store's `agents` table in the first place (it
lives only on `GenesisAgent`, verified against `sqlite_schema.sql`'s real
column list). Everything that changes over time - wealth, health, alive/
dormant/dead, location, household (marriage moves people between
households) - comes from the LIVE, in-process `WorldState` and `Ledger`,
never from the trace store's `agents` row, which is a genesis-time seed
`world/wiring.py`'s `TraceStore.seed_agent` never updates again (verified:
that class has no method that re-writes `usd_wallet_cents`, `sim_balance`,
`health_status`, `is_alive`, or `is_dormant` after genesis - `set_employer`
is its only post-seed write). So this module, like governance_api.py and
for the same reason, has to be mounted in-process with a live `WorldState`
and `Genesis` - it is not reachable by pointing a process only at the
trace database.

Agent Reasoning is the one view that DOES lean on the trace store: every
`llm_calls` row (model, region, call_class, purpose, tokens, cost,
success, and - since the 2026-07-31 trace-store fix landed in
broker/trace.py and schema_gen.py - the full `request_body`/
`response_body` JSON of the actual Bedrock call). `request_body` carries
the prompt (including whatever memory context the agent's own herald
folded in before the call reached broker/); `response_body` carries the
model's text ("reasoning") and any `tool_use` blocks (the chosen tool
call). Both are returned verbatim alongside a best-effort structured
extraction, never only the extraction - a heuristic parse of an
Anthropic-Messages-shaped `content` array is a convenience, not a
guarantee this module can make about every model's exact wire shape, so
the ground truth is always one field away.

Cross-origin: see world/governance_api.py's module docstring - identical
`add_cors_headers` router-wide dependency, same reason (this API and the
static pages that call it are different processes/origins).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from supabase.client import SupabaseClient, eq
from world.core.engine import TickEngine
from world.life.models import Genesis
from world.tools.state import WorldState

# ── deps ──────────────────────────────────────────────────────────────


@dataclass
class AgentViewerDeps:
    world: WorldState
    genesis: Genesis
    tick_engine: TickEngine
    trace: SupabaseClient


# ── agent finder ─────────────────────────────────────────────────────────

AgentLifeStatus = Literal["alive", "dormant", "dead"]


class AgentFinderRow(BaseModel):
    agent_id: str
    name: str
    sex: str
    age_years: float
    is_minor: bool
    occupation: str
    employer_business_id: str | None
    owns_business_ids: list[str]
    household_id: str | None
    place: str | None
    usd_wallet_cents: int
    sim_balance: int
    status: AgentLifeStatus
    health_status: str
    organizations: list[str]
    ablation_arm: str


class AgentFinderPage(BaseModel):
    total: int
    agents: list[AgentFinderRow]


def health_status_of(agent: Any) -> str:
    """Mirrors sqlite_schema.sql's `agents.health_status` vocabulary
    ('healthy'/'sick_short'/'sick_medium'/'sick_long'/'terminal'/
    'deceased') so a researcher who has looked at the trace-store schema
    recognizes these values immediately, computed live from
    AgentLifeRecord.health instead of read from a stale seed row."""
    if not agent.alive:
        return "deceased"
    ailment = agent.health.current_ailment
    if ailment is None:
        return "healthy"
    return f"sick_{ailment.kind.value}" if ailment.kind.value != "terminal" else "terminal"


def life_status_of(agent: Any) -> AgentLifeStatus:
    if not agent.alive:
        return "dead"
    if agent.dormancy.is_dormant:
        return "dormant"
    return "alive"


def build_finder_rows(deps: AgentViewerDeps) -> list[AgentFinderRow]:
    org_membership: dict[str, list[str]] = {}
    for org in deps.world.charter.organizations.store.organizations.values():
        for member_id in org.members:
            org_membership.setdefault(member_id, []).append(org.name)

    owned_businesses: dict[str, list[str]] = {}
    for business in deps.world.businesses.values():
        owned_businesses.setdefault(business.owner_agent_id, []).append(business.business_id)

    rows: list[AgentFinderRow] = []
    for genesis_agent in deps.genesis.agents:
        agent = deps.world.agents.get(genesis_agent.agent_id)
        if agent is None:
            # Genesis listed them but the live world never registered them
            # (should not happen post-boot - apply_genesis_population
            # registers every genesis agent - but a defensive skip beats a
            # 500 for the whole finder over one bad row).
            continue
        rows.append(
            AgentFinderRow(
                agent_id=agent.agent_id,
                name=genesis_agent.name,
                sex=agent.sex.value,
                age_years=agent.age_years,
                is_minor=agent.is_minor,
                occupation=genesis_agent.occupation,
                employer_business_id=genesis_agent.business_id,
                owns_business_ids=sorted(owned_businesses.get(agent.agent_id, [])),
                household_id=agent.household_id,
                place=deps.world.location_of(agent.agent_id),
                usd_wallet_cents=deps.world.ledger.usd_balance(agent.agent_id),
                sim_balance=deps.world.ledger.sim_balance(agent.agent_id),
                status=life_status_of(agent),
                health_status=health_status_of(agent),
                organizations=sorted(org_membership.get(agent.agent_id, [])),
                ablation_arm=genesis_agent.ablation_arm.value,
            )
        )
    return rows


# ── agent activity ────────────────────────────────────────────────────


class CoLocatedAgent(BaseModel):
    agent_id: str
    name: str


class LastKnownActionView(BaseModel):
    """Best-effort "what are they doing and why", derived from the most
    recent trace-store event naming this agent - see module docstring.
    There is no first-class "current action" field anywhere in world/'s
    state (that lives in the engine's own per-agent runtime, outside
    app/), so this is honestly labeled as derived, not read from a field
    that claims to be authoritative."""

    event_type: str
    tick: int | None
    summary: dict[str, Any]


class ScheduleStatusView(BaseModel):
    """The scheduler's own bookkeeping (world/core/scheduler.py) - the
    closest real substitute this codebase has today for DESIGN.md's "day
    plan": not a plan of intended actions, only when this agent is next
    due for a turn and whether something already interrupted their
    schedule early."""

    cadence_ticks: int
    next_decision_tick: int
    interrupted: bool


class SkillView(BaseModel):
    task_type: str
    level: int


class TraceSkillRecordView(BaseModel):
    task_type: str
    proficiency_level: float
    repetitions_observed: int
    taught_by_agent_id: str | None
    tick: int


class RelationshipView(BaseModel):
    other_agent_id: str
    other_agent_name: str | None
    organization_id: str | None
    relationship_type: str
    sentiment: float | None
    trust: float | None
    interaction_count: int
    tick: int


class UtteranceBrief(BaseModel):
    id: int
    listener: str | None
    location: str | None
    content: str
    tick: int


class AgentActivityView(BaseModel):
    agent_id: str
    name: str
    alive: bool
    status: AgentLifeStatus
    age_years: float
    is_minor: bool
    place: str | None
    co_located_agents: list[CoLocatedAgent]
    last_known_action: LastKnownActionView | None
    schedule: ScheduleStatusView | None
    usd_wallet_cents: int
    sim_balance: int
    health: dict[str, Any]
    live_skills: list[SkillView]
    trace_skill_records: list[TraceSkillRecordView]
    relationships: list[RelationshipView]
    recent_utterances: list[UtteranceBrief]


def resolve_agent_or_404(deps: AgentViewerDeps, agent_id: str) -> Any:
    agent = deps.world.agents.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"no agent {agent_id!r}")
    return agent


def name_of(deps: AgentViewerDeps, agent_id: str) -> str:
    genesis_agent = next((a for a in deps.genesis.agents if a.agent_id == agent_id), None)
    return genesis_agent.name if genesis_agent is not None else agent_id


# ── agent reasoning ──────────────────────────────────────────────────────


class LlmCallSummary(BaseModel):
    id: str
    model: str
    requested_model: str
    region: str | None
    call_class: str
    purpose: str
    lane: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cents_debited: int
    latency_ms: int
    success: bool
    error_type: str | None
    error_message: str | None
    tick: int | None
    created_at: float


def llm_call_summary(row: dict[str, Any]) -> LlmCallSummary:
    region = row.get("region")
    return LlmCallSummary(
        id=str(row["id"]),
        model=row["model"],
        requested_model=row["requested_model"],
        region=region,
        call_class=row["call_class"],
        purpose=row["purpose"],
        # broker/lanes.py's own lane_id() convention: f"{model}@{region}".
        lane=f"{row['model']}@{region}" if region else row["model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cost_usd=row["cost_nano_dollars"] / 1_000_000_000,
        cents_debited=row["cents_debited"],
        latency_ms=row["latency_ms"],
        success=bool(row["success"]),
        error_type=row.get("error_type"),
        error_message=row.get("error_message"),
        tick=row.get("tick"),
        created_at=row["created_at"],
    )


class ExtractedToolCall(BaseModel):
    name: str | None
    input: dict[str, Any] | None


class ExtractedReasoning(BaseModel):
    """Best-effort parse of an Anthropic-Messages-shaped `content` array
    (`[{"type": "text", "text": ...}, {"type": "tool_use", ...}]`) - the
    shape broker/translate.py normalizes every lane's response into for
    the agent's own herald. Empty lists (not an error) when
    `response_body` is absent or shaped differently; the raw
    `response_body` on `LlmCallDetail` is always present regardless, so a
    mismatch here never hides data, only a convenience rendering of it."""

    text_blocks: list[str]
    tool_calls: list[ExtractedToolCall]


def extract_reasoning(response_body: dict[str, Any] | None) -> ExtractedReasoning:
    if not response_body:
        return ExtractedReasoning(text_blocks=[], tool_calls=[])
    content = response_body.get("content")
    if not isinstance(content, list):
        return ExtractedReasoning(text_blocks=[], tool_calls=[])
    text_blocks = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text" and "text" in b]
    tool_calls = [
        ExtractedToolCall(name=b.get("name"), input=b.get("input"))
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use"
    ]
    return ExtractedReasoning(text_blocks=text_blocks, tool_calls=tool_calls)


class ToolInvocationBrief(BaseModel):
    invocation_id: str
    tool_name: str
    namespace: str
    tick: int
    params: dict[str, Any]


class MemoryBrief(BaseModel):
    id: int
    memory_type: str
    content: str
    importance: int
    tick: int


class LlmCallDetail(LlmCallSummary):
    request_body: dict[str, Any] | None
    response_body: dict[str, Any] | None
    extracted: ExtractedReasoning
    matched_tool_calls: list[ToolInvocationBrief]
    matched_tool_calls_note: str
    nearby_memories: list[MemoryBrief]
    nearby_memories_note: str


# ── the router ────────────────────────────────────────────────────────

MATCHED_TOOL_CALLS_NOTE = (
    "Best-effort correlation by actor_id + exact tick match against "
    "world.tool_invocations (no direct llm_calls -> tool_invocations "
    "foreign key exists in the schema) - the actual world-state result "
    "of whatever tool call this decision chose, when one landed in the "
    "same tick."
)
NEARBY_MEMORIES_NOTE = (
    "Agent memories at or before this call's tick, most recent and most "
    "important first - chronological context, NOT a confirmed record of "
    "which memories the retrieval pipeline actually fed into this "
    "specific prompt (no such linkage is recorded in the schema)."
)


# List-valued fields (owns_business_ids, organizations) sort ill-defined
# by design - "sort by which org" has no single answer for an agent in
# three - so they are excluded from FINDER_SORT_FIELDS entirely rather
# than silently sorting by list length or first element.
FINDER_SORT_FIELDS: frozenset[str] = frozenset(AgentFinderRow.model_fields.keys()) - {
    "owns_business_ids", "organizations",
}


def finder_sort_key(row: AgentFinderRow, field: str) -> tuple[int, Any]:
    """A None-safe, type-safe sort key. `getattr(row, field) or ""` (the
    obvious one-liner) is a real bug, not just inelegant: age_years=0.0,
    usd_wallet_cents=0, and sim_balance=0 are all falsy, so a cognitively
    poor or newborn agent's `or ""` would silently coerce a real number
    into the string "" and Python 3 raises TypeError comparing str to
    float the moment the sort actually needs to order that row against a
    numeric one - not a hypothetical, every fresh 250-agent genesis
    population has agents at exactly $0 or sim 0. `(0, "")` for a genuine
    None sorts every unset value first, consistently, without that type
    clash; strings sort case-insensitively so "Ana" and "ana" interleave
    the way a human filtering the table would expect.
    """
    value = getattr(row, field)
    if value is None:
        return (0, "")
    if isinstance(value, str):
        return (1, value.lower())
    return (1, value)


async def add_cors_headers(response: Response) -> None:
    """See world/governance_api.py's module docstring "Cross-origin"
    note - identical reasoning and mechanism, repeated per module since
    each router-building function must be independently mountable."""
    response.headers["Access-Control-Allow-Origin"] = "*"


def build_agent_viewer_router(get_deps: Callable[[], AgentViewerDeps]) -> APIRouter:
    router = APIRouter(prefix="/agent-viewer", tags=["agent-viewer"], dependencies=[Depends(add_cors_headers)])

    # -- agent finder ---------------------------------------------------------

    @router.get("/agents", response_model=AgentFinderPage)
    async def find_agents(
        q: str | None = None,
        sex: str | None = None,
        status: AgentLifeStatus | None = None,
        occupation: str | None = None,
        business_id: str | None = None,
        household_id: str | None = None,
        place: str | None = None,
        organization: str | None = None,
        ablation_arm: str | None = None,
        sort_by: str = "name",
        sort_desc: bool = False,
        # A PAGE SIZE, not a town size. It was 250 - exactly the old cast -
        # so the finder appeared to list everyone right up until the town
        # grew past it, at which point it silently dropped the 64
        # institution staff off the end of the first page.
        limit: int = 1000,
        offset: int = 0,
    ) -> AgentFinderPage:
        deps = get_deps()
        rows = build_finder_rows(deps)

        if q:
            needle = q.lower()
            rows = [r for r in rows if needle in r.name.lower() or needle in r.agent_id.lower()]
        if sex:
            rows = [r for r in rows if r.sex == sex]
        if status:
            rows = [r for r in rows if r.status == status]
        if occupation:
            rows = [r for r in rows if r.occupation == occupation]
        if business_id:
            rows = [r for r in rows if r.employer_business_id == business_id or business_id in r.owns_business_ids]
        if household_id:
            rows = [r for r in rows if r.household_id == household_id]
        if place:
            rows = [r for r in rows if r.place == place]
        if organization:
            rows = [r for r in rows if organization in r.organizations]
        if ablation_arm:
            rows = [r for r in rows if r.ablation_arm == ablation_arm]

        if sort_by not in FINDER_SORT_FIELDS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown or unsortable sort_by {sort_by!r} (choose one of {sorted(FINDER_SORT_FIELDS)})",
            )
        rows.sort(key=lambda r: finder_sort_key(r, sort_by), reverse=sort_desc)

        total = len(rows)
        page = rows[offset : offset + limit]
        return AgentFinderPage(total=total, agents=page)

    # -- agent activity -------------------------------------------------------

    @router.get("/agents/{agent_id}", response_model=AgentActivityView)
    async def agent_activity(agent_id: str) -> AgentActivityView:
        deps = get_deps()
        agent = resolve_agent_or_404(deps, agent_id)
        place = deps.world.location_of(agent_id)

        co_located: list[CoLocatedAgent] = []
        if place is not None:
            for other_id in sorted(deps.world.locations.agents_at(place)):
                if other_id != agent_id:
                    co_located.append(CoLocatedAgent(agent_id=other_id, name=name_of(deps, other_id)))

        last_event_rows = await deps.trace.select(
            "events", filters={"agent_id": eq(agent_id)}, order="tick.desc", limit=1
        )
        last_action = None
        if last_event_rows:
            row = last_event_rows[0]
            last_action = LastKnownActionView(
                event_type=row["type"], tick=row.get("tick"), summary=row.get("payload") or {}
            )

        schedule_entry = deps.tick_engine.scheduler.entries.get(agent_id)
        schedule = (
            ScheduleStatusView(
                cadence_ticks=schedule_entry.cadence_ticks,
                next_decision_tick=schedule_entry.next_decision_tick,
                interrupted=schedule_entry.interrupted,
            )
            if schedule_entry is not None
            else None
        )

        live_skills = [
            SkillView(task_type=task_type, level=level)
            for task_type, level in sorted(deps.world.skills.get(agent_id, {}).items())
        ]
        trace_skill_rows = await deps.trace.select(
            "skills", filters={"agent_id": eq(agent_id)}, order="tick.desc", limit=50
        )
        trace_skills = [
            TraceSkillRecordView(
                task_type=r["task_type"],
                proficiency_level=r["proficiency_level"],
                repetitions_observed=r["repetitions_observed"],
                taught_by_agent_id=r.get("taught_by_agent_id"),
                tick=r["tick"],
            )
            for r in trace_skill_rows
        ]

        relationship_rows = await deps.trace.select(
            "relationships", filters={"agent_id": eq(agent_id)}, order="tick.desc", limit=100
        )
        relationships = [
            RelationshipView(
                other_agent_id=r.get("other_agent_id") or "",
                other_agent_name=(name_of(deps, r["other_agent_id"]) if r.get("other_agent_id") else None),
                organization_id=r.get("organization_id"),
                relationship_type=r["relationship_type"],
                sentiment=r.get("sentiment"),
                trust=r.get("trust"),
                interaction_count=r["interaction_count"],
                tick=r["tick"],
            )
            for r in relationship_rows
        ]

        speaker_rows = await deps.trace.select(
            "utterances", filters={"speaker_agent_id": eq(agent_id)}, order="tick.desc", limit=25
        )
        listener_rows = await deps.trace.select(
            "utterances", filters={"listener_agent_id": eq(agent_id)}, order="tick.desc", limit=25
        )
        by_id = {r["id"]: r for r in speaker_rows + listener_rows}
        recent_utterances = [
            UtteranceBrief(
                id=r["id"], listener=r.get("listener_agent_id"), location=r.get("location"),
                content=r["content"], tick=r["tick"],
            )
            for r in sorted(by_id.values(), key=lambda r: r["tick"], reverse=True)[:25]
        ]

        return AgentActivityView(
            agent_id=agent.agent_id,
            name=name_of(deps, agent_id),
            alive=agent.alive,
            status=life_status_of(agent),
            age_years=agent.age_years,
            is_minor=agent.is_minor,
            place=place,
            co_located_agents=co_located,
            last_known_action=last_action,
            schedule=schedule,
            usd_wallet_cents=deps.world.ledger.usd_balance(agent_id),
            sim_balance=deps.world.ledger.sim_balance(agent_id),
            health=agent.health.model_dump(mode="json"),
            live_skills=live_skills,
            trace_skill_records=trace_skills,
            relationships=relationships,
            recent_utterances=recent_utterances,
        )

    # -- agent reasoning --------------------------------------------------------

    @router.get("/agents/{agent_id}/reasoning", response_model=list[LlmCallSummary])
    async def agent_reasoning_list(agent_id: str, limit: int = 100) -> list[LlmCallSummary]:
        deps = get_deps()
        resolve_agent_or_404(deps, agent_id)
        rows = await deps.trace.select(
            "llm_calls", filters={"agent_id": eq(agent_id)}, order="created_at.desc", limit=limit
        )
        return [llm_call_summary(r) for r in rows]

    @router.get("/agents/{agent_id}/reasoning/{call_id}", response_model=LlmCallDetail)
    async def agent_reasoning_detail(agent_id: str, call_id: str) -> LlmCallDetail:
        deps = get_deps()
        resolve_agent_or_404(deps, agent_id)
        rows = await deps.trace.select("llm_calls", filters={"id": eq(call_id)}, limit=1)
        if not rows:
            raise HTTPException(status_code=404, detail=f"no llm_call {call_id!r}")
        row = rows[0]
        if row["agent_id"] != agent_id:
            raise HTTPException(
                status_code=404, detail=f"llm_call {call_id!r} belongs to agent {row['agent_id']!r}, not {agent_id!r}"
            )

        matched_tools: list[ToolInvocationBrief] = []
        if row.get("tick") is not None:
            for record in deps.world.tool_invocations:
                if record.actor_id == agent_id and record.tick == row["tick"]:
                    matched_tools.append(
                        ToolInvocationBrief(
                            invocation_id=record.invocation_id,
                            tool_name=record.tool_name,
                            namespace=record.namespace,
                            tick=record.tick,
                            params=record.params,
                        )
                    )

        nearby_memories: list[MemoryBrief] = []
        if row.get("tick") is not None:
            memory_rows = await deps.trace.select(
                "memories",
                filters={"agent_id": eq(agent_id)},
                order="tick.desc",
                limit=200,
            )
            filtered = [m for m in memory_rows if m["tick"] <= row["tick"]]
            filtered.sort(key=lambda m: (m["tick"], m["importance"]), reverse=True)
            nearby_memories = [
                MemoryBrief(id=m["id"], memory_type=m["memory_type"], content=m["content"], importance=m["importance"], tick=m["tick"])
                for m in filtered[:8]
            ]

        summary = llm_call_summary(row)
        return LlmCallDetail(
            **summary.model_dump(),
            request_body=row.get("request_body"),
            response_body=row.get("response_body"),
            extracted=extract_reasoning(row.get("response_body")),
            matched_tool_calls=matched_tools,
            matched_tool_calls_note=MATCHED_TOOL_CALLS_NOTE,
            nearby_memories=nearby_memories,
            nearby_memories_note=NEARBY_MEMORIES_NOTE,
        )

    return router
