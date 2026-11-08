"""Read-only Scenario/Special-event + Conversations viewer API (DESIGN.md
section 8: "Scenario / special-event viewer" and "Conversations").

Both views read the SQLite trace store's durable log tables
(`app/supabase/sqlite_schema.sql`'s `events` and `utterances`), not
`world.event_bus.recent()` - that in-process buffer is capped at 2000
entries (`world/core/events.py`: `EventBus(buffer_size=2000)`), which is
a live-interrupt-detection ring buffer, not the "curated feed... browsable
past" history DESIGN.md section 8 asks for. `events` is: it is the one
durable, unbounded record every world/ mutation lands in
(`world/wiring.py`'s `TraceStore.record_event`, subscribed to `"*"` before
genesis population even runs).

`utterances` is the honest exception: verified against the real running
code (grep for `insert(.."utterances"..)` across the whole `app/` tree)
- nothing in this codebase writes a row to it yet. Conversation/dialogue
persistence is not wired by any track as of this module's writing. This
file still queries the real schema correctly (conversation_id, location,
tick, speaker/listener, is_public - see CONVERSATIONS_SCHEMA note below)
so the moment something starts writing utterances, this viewer renders
them with no further change; until then it correctly, honestly returns
an empty feed instead of fabricating conversations. See this track's
worker report for the exact empty-table evidence.

`SocialDeps.world` is kept even though no route currently reads it, for
the same reason `GovernanceDeps`/`AgentViewerDeps` both take one: every
sibling module in this file's family is mounted the same way (one
`WorldServer`-backed process, one shared `WorldState`), so the day a
scenario category needs a piece of LIVE in-process detail the durable
event payload alone doesn't carry - `world.crime_cases`,
`world.charter.organizations` - the seam is already there instead of
being a second signature change to main.py's mount point.

Cross-origin: see world/governance_api.py's module docstring - the same
`add_cors_headers` router-wide dependency pattern is used here, for the
identical reason (this API and the pages that call it are served by
different processes/origins, and this file cannot add app-level
middleware without editing world/main.py).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response

from pydantic import BaseModel

from supabase.client import SupabaseClient, eq, in_
from world.life.models import Genesis
from world.tools.state import WorldState

# ── deps ──────────────────────────────────────────────────────────────


@dataclass
class SocialDeps:
    world: WorldState
    genesis: Genesis
    trace: SupabaseClient


def name_map(genesis: Genesis) -> dict[str, str]:
    return {agent.agent_id: agent.name for agent in genesis.agents}


# ── scenarios: curated event-type -> category map ───────────────────────


class ScenarioCategory(str, Enum):
    CRIME = "crime"
    TRIAL = "trial"
    BIRTH = "birth"
    DEATH = "death"
    MARRIAGE = "marriage"
    DISASTER = "disaster"
    MIRACLE = "miracle"
    CULT = "cult"
    BUSINESS = "business"
    AMENDMENT = "amendment"
    REGIME = "regime"
    ELECTION = "election"

# DESIGN.md section 8: "crimes and trials, births and deaths, marriages,
# disasters, miracles, cult formation, business launches and failures,
# amendments tabled, regime transitions." Every key here is a REAL event
# type this codebase actually emits (verified: `grep -rhoE
# '"[a-z_]+\.[a-z_]+\.[a-z_]+"' world/ broker/` over every module that
# publishes to the shared EventBus - see this track's report for the full
# list). Two DESIGN.md-named categories have NO real emitter yet, and are
# left out of this map rather than silently mapped to nothing:
# "business launches and failures" (no `business.registered` /
# `business.closed` event exists - genesis businesses are seeded directly
# into the trace store's `businesses` table, never through an event, and
# there is no runtime "open a business" tool yet) and "cult formation"
# specifically (there is no `religion.cult.formed`-shaped event; the
# closest real signal is `politics.organization.registered` with
# `kind="cult"` in its payload, which IS mapped below under CULT).
EVENT_TYPE_TO_CATEGORY: dict[str, ScenarioCategory] = {
    "crime.theft.attempted": ScenarioCategory.CRIME,
    "crime.theft.witnessed": ScenarioCategory.CRIME,
    "crime.complaint.filed": ScenarioCategory.CRIME,
    "crime.case.opened": ScenarioCategory.CRIME,
    "crime.case.dismissed": ScenarioCategory.CRIME,
    "crime.fight.occurred": ScenarioCategory.CRIME,
    "crime.interview.logged": ScenarioCategory.TRIAL,
    "crime.warrant.requested": ScenarioCategory.TRIAL,
    "crime.warrant.issued": ScenarioCategory.TRIAL,
    "crime.warrant.denied": ScenarioCategory.TRIAL,
    "crime.arrest.made": ScenarioCategory.TRIAL,
    "crime.fine.imposed": ScenarioCategory.TRIAL,
    "crime.jail.imposed": ScenarioCategory.TRIAL,
    "crime.confinement.released": ScenarioCategory.TRIAL,
    "crime.subpoena.filed": ScenarioCategory.TRIAL,
    "crime.subpoena.granted": ScenarioCategory.TRIAL,
    "crime.subpoena.denied": ScenarioCategory.TRIAL,
    "agent.birth.requested": ScenarioCategory.BIRTH,
    "agent.birth.granted": ScenarioCategory.BIRTH,
    "agent.birth.denied": ScenarioCategory.BIRTH,
    "health.agent.died": ScenarioCategory.DEATH,
    "agent.marriage.recorded": ScenarioCategory.MARRIAGE,
    "world.disaster.storm": ScenarioCategory.DISASTER,
    "world.disaster.quake": ScenarioCategory.DISASTER,
    "world.disaster.crop_blight": ScenarioCategory.DISASTER,
    "health.disaster.plague": ScenarioCategory.DISASTER,
    "world.disaster.recorded": ScenarioCategory.DISASTER,
    "religion.miracle.cast": ScenarioCategory.MIRACLE,
    "religion.misfortune.cast": ScenarioCategory.MIRACLE,
    "religion.doctrine.emerged": ScenarioCategory.CULT,
    "politics.organization.registered": ScenarioCategory.CULT,
    "politics.charter.amendment_proposed": ScenarioCategory.AMENDMENT,
    "politics.charter.amendment_passed": ScenarioCategory.AMENDMENT,
    "politics.charter.amendment_rejected": ScenarioCategory.AMENDMENT,
    "politics.charter.amended": ScenarioCategory.AMENDMENT,
    "politics.charter.vetoed": ScenarioCategory.AMENDMENT,
    "politics.regime.changed": ScenarioCategory.REGIME,
    "politics.election.scheduled": ScenarioCategory.ELECTION,
    "politics.election.tallied": ScenarioCategory.ELECTION,
}

# Payload keys, checked in this order, that correlate one scenario event to
# the rest of its causal chain - e.g. every crime.* event on the same
# case_id, or every politics.charter.* event on the same proposal_id. Not
# every event type carries every key; build_trace() below tries each in
# turn and uses whichever the anchor event actually has.
CORRELATION_KEYS: tuple[str, ...] = (
    "case_id",
    "proposal_id",
    "election_id",
    "request_id",
    "org_id",
    "petition_id",
    "warrant_id",
)


class ScenarioEntry(BaseModel):
    event_id: str
    type: str
    category: str | None
    tick: int | None
    game_day: int | None
    agent_id: str | None
    agent_name: str | None
    headline: str
    payload: dict[str, Any]


def headline_for(event_type: str, payload: dict[str, Any], names: dict[str, str]) -> str:
    """A one-line, human-readable summary so a feed row doesn't force the
    reader to parse a raw payload dict just to know what happened.
    Deliberately simple string templates, not an LLM summarizer - this is
    a viewer over ground truth, not a second inference call."""

    def label(agent_id: str | None) -> str:
        if agent_id is None:
            return "someone"
        return names.get(agent_id, agent_id)

    if event_type == "health.agent.died":
        return f"{label(payload.get('agent_id'))} died ({payload.get('cause', payload.get('death_cause', 'unknown cause'))})"
    if event_type == "agent.marriage.recorded":
        return f"{label(payload.get('agent_id'))} married {label(payload.get('spouse_id'))}"
    if event_type == "agent.birth.granted":
        return f"a child was born into household {payload.get('household_id', '?')}"
    if event_type == "crime.case.opened":
        return f"case opened: {payload.get('kind', 'crime')} against {label(payload.get('accused_agent_id'))}"
    if event_type == "crime.theft.attempted":
        return f"{label(payload.get('perpetrator_agent_id'))} attempted theft"
    if event_type in ("crime.fine.imposed", "crime.jail.imposed"):
        verb = "fined" if event_type.endswith("fine.imposed") else "jailed"
        return f"{label(payload.get('convicted_agent_id'))} {verb}"
    if event_type.startswith("world.disaster.") or event_type == "health.disaster.plague":
        return f"disaster: {event_type.rsplit('.', 1)[-1]}"
    if event_type == "religion.miracle.cast":
        return f"miracle granted to {label(payload.get('agent_id'))}: {payload.get('description', '')}"
    if event_type == "politics.organization.registered":
        return f"{payload.get('kind', 'organization')} founded: {payload.get('name', payload.get('org_id', ''))}"
    if event_type == "politics.charter.amendment_proposed":
        return f"amendment tabled: {payload.get('title', payload.get('field_name', ''))}"
    if event_type == "politics.charter.amended":
        return f"charter amended: {payload.get('field_name', '')} -> {payload.get('new_value', '')}"
    if event_type == "politics.charter.vetoed":
        return f"amendment vetoed: {payload.get('field_name', '')}"
    if event_type == "politics.regime.changed":
        return f"regime change: {label(payload.get('previous_pm_agent_id'))} -> {label(payload.get('new_pm_agent_id'))}"
    if event_type == "politics.election.tallied":
        return f"election tallied: PM {label(payload.get('pm_agent_id'))}"
    return event_type.replace(".", " ").replace("_", " ")


def event_row_to_scenario(row: dict[str, Any], names: dict[str, str]) -> ScenarioEntry:
    payload = row.get("payload") or {}
    category = EVENT_TYPE_TO_CATEGORY.get(row["type"])
    return ScenarioEntry(
        event_id=str(row["id"]),
        type=row["type"],
        category=category.value if category is not None else None,
        tick=row.get("tick"),
        game_day=row.get("game_day"),
        agent_id=row.get("agent_id"),
        agent_name=names.get(row["agent_id"]) if row.get("agent_id") else None,
        headline=headline_for(row["type"], payload, names),
        payload=payload,
    )


# ── conversations ────────────────────────────────────────────────────────


class UtteranceView(BaseModel):
    id: int
    speaker: str
    speaker_name: str | None
    listener: str | None
    listener_name: str | None
    location: str | None
    content: str
    is_public: bool
    tick: int


class ConversationSummary(BaseModel):
    conversation_cluster_id: str
    kind: str  # "individual" | "group"
    location: str | None
    participant_ids: list[str]
    participant_names: list[str]
    tick_start: int
    tick_end: int
    utterance_count: int
    preview: str


class ConversationDetail(ConversationSummary):
    utterances: list[UtteranceView]


def utterance_view(row: dict[str, Any], names: dict[str, str]) -> UtteranceView:
    speaker = row["speaker_agent_id"]
    listener = row.get("listener_agent_id")
    return UtteranceView(
        id=row["id"],
        speaker=speaker,
        speaker_name=names.get(speaker),
        listener=listener,
        listener_name=names.get(listener) if listener else None,
        location=row.get("location"),
        content=row["content"],
        is_public=bool(row.get("is_public", True)),
        tick=row["tick"],
    )


def cluster_conversations(
    rows: list[dict[str, Any]], overlap_ticks: int
) -> list[list[dict[str, Any]]]:
    """DESIGN.md section 8: "Infer a group conversation from co-located
    participants exchanging utterances in an overlapping tick window - not
    one row per utterance." `utterances.conversation_id` (the schema
    column every row already carries) is the first-pass grouping key -
    whatever wrote the row already tagged which exchange it belongs to.
    This function does the SECOND pass DESIGN.md actually asks for:
    merging separate conversation_id threads that are really one scene -
    e.g. two 1:1 side-exchanges logged under different conversation_ids
    but at the same location, overlapping in time, sharing a participant
    - into one cluster, so a 5-person town-hall debate renders as one
    group thread instead of ten disconnected pairs.

    Union-find over threads (not individual utterances): two threads merge
    when they share a location, their [min_tick, max_tick] windows
    overlap within `overlap_ticks`, and they share at least one
    participant. O(n^2) over thread count, which is fine at this scale
    (a game-day's worth of conversation threads, not 4400 raw rows).
    """
    threads: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        threads[row["conversation_id"]].append(row)

    thread_ids = list(threads.keys())

    def participants_of(thread_rows: list[dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for r in thread_rows:
            ids.add(r["speaker_agent_id"])
            if r.get("listener_agent_id"):
                ids.add(r["listener_agent_id"])
        return ids

    def window_of(thread_rows: list[dict[str, Any]]) -> tuple[int, int]:
        ticks = [r["tick"] for r in thread_rows]
        return min(ticks), max(ticks)

    def location_of(thread_rows: list[dict[str, Any]]) -> str | None:
        for r in thread_rows:
            if r.get("location"):
                return r["location"]
        return None

    parent = {tid: tid for tid in thread_ids}

    def find(tid: str) -> str:
        while parent[tid] != tid:
            parent[tid] = parent[parent[tid]]
            tid = parent[tid]
        return tid

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    meta = {
        tid: (location_of(threads[tid]), window_of(threads[tid]), participants_of(threads[tid]))
        for tid in thread_ids
    }
    for i, tid_a in enumerate(thread_ids):
        loc_a, (start_a, end_a), participants_a = meta[tid_a]
        if loc_a is None:
            continue
        for tid_b in thread_ids[i + 1 :]:
            loc_b, (start_b, end_b), participants_b = meta[tid_b]
            if loc_b != loc_a:
                continue
            windows_overlap = start_a <= end_b + overlap_ticks and start_b <= end_a + overlap_ticks
            if not windows_overlap:
                continue
            if participants_a & participants_b:
                union(tid_a, tid_b)

    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for tid in thread_ids:
        clusters[find(tid)].extend(threads[tid])
    return list(clusters.values())


def conversation_summary(cluster_id: str, rows: list[dict[str, Any]], names: dict[str, str]) -> ConversationSummary:
    rows_sorted = sorted(rows, key=lambda r: r["tick"])
    participants: set[str] = set()
    for r in rows_sorted:
        participants.add(r["speaker_agent_id"])
        if r.get("listener_agent_id"):
            participants.add(r["listener_agent_id"])
    location = next((r.get("location") for r in rows_sorted if r.get("location")), None)
    preview_row = rows_sorted[0]
    preview = preview_row["content"][:140]
    return ConversationSummary(
        conversation_cluster_id=cluster_id,
        # DESIGN.md section 8: "make group visually distinct from
        # one-to-one" - 3+ distinct participants is a group scene; 2 (or a
        # 1-participant broadcast/monologue utterance) is individual.
        kind="group" if len(participants) > 2 else "individual",
        location=location,
        participant_ids=sorted(participants),
        participant_names=[names.get(a, a) for a in sorted(participants)],
        tick_start=rows_sorted[0]["tick"],
        tick_end=rows_sorted[-1]["tick"],
        utterance_count=len(rows_sorted),
        preview=preview,
    )


# ── the router ────────────────────────────────────────────────────────

DEFAULT_SCENARIO_LIMIT = 200
DEFAULT_CONVERSATION_ROW_LIMIT = 5000


async def add_cors_headers(response: Response) -> None:
    response.headers["Access-Control-Allow-Origin"] = "*"


def build_social_router(get_deps: Callable[[], SocialDeps]) -> APIRouter:
    router = APIRouter(tags=["social"], dependencies=[Depends(add_cors_headers)])

    # -- scenarios ----------------------------------------------------------

    @router.get("/scenarios", response_model=list[ScenarioEntry])
    async def scenarios(
        category: str | None = None,
        agent_id: str | None = None,
        since_tick: int | None = None,
        until_tick: int | None = None,
        limit: int = DEFAULT_SCENARIO_LIMIT,
    ) -> list[ScenarioEntry]:
        deps = get_deps()
        names = name_map(deps.genesis)
        types = sorted(EVENT_TYPE_TO_CATEGORY.keys())
        if category is not None:
            try:
                wanted = ScenarioCategory(category)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"unknown category {category!r}")
            types = [t for t, c in EVENT_TYPE_TO_CATEGORY.items() if c is wanted]
        filters: dict[str, str] = {"type": in_(types)}
        if agent_id is not None:
            filters["agent_id"] = eq(agent_id)
        # The generic select() interface takes one filter value per
        # column (see supabase/client.py), so a tick range needs two
        # passes rather than one query with both gte and lte on `tick`.
        rows = await deps.trace.select("events", filters=filters, order="tick.desc", limit=max(limit * 4, 500))
        if since_tick is not None:
            rows = [r for r in rows if r.get("tick") is not None and r["tick"] >= since_tick]
        if until_tick is not None:
            rows = [r for r in rows if r.get("tick") is not None and r["tick"] <= until_tick]
        rows.sort(key=lambda r: (r.get("tick") if r.get("tick") is not None else -1, r["id"]), reverse=True)
        return [event_row_to_scenario(r, names) for r in rows[:limit]]

    @router.get("/scenarios/{event_id}/trace", response_model=list[ScenarioEntry])
    async def scenario_trace(event_id: str) -> list[ScenarioEntry]:
        """DESIGN.md section 8: "each entry expands into its full causal
        trace." Loads the anchor event, then every other event sharing one
        of CORRELATION_KEYS' payload values (a case_id, a proposal_id, an
        election_id, ...) - the ordered chain of everything that happened
        to that same case/proposal/election/request, not just the one row
        that made the feed."""
        deps = get_deps()
        names = name_map(deps.genesis)
        anchor_rows = await deps.trace.select("events", filters={"id": eq(event_id)}, limit=1)
        if not anchor_rows:
            raise HTTPException(status_code=404, detail=f"no event {event_id!r}")
        anchor = anchor_rows[0]
        payload = anchor.get("payload") or {}

        correlated: dict[str, dict[str, Any]] = {str(anchor["id"]): anchor}
        for key in CORRELATION_KEYS:
            value = payload.get(key)
            if value is None:
                continue
            # `domain` (everything before the first dot in `type`) is a
            # real generated column on this table (sqlite_schema.sql), not
            # a Python-side reconstruction - a crime.case.opened anchor
            # pulls every OTHER crime.* event sharing the same case_id,
            # including ones no scenario category maps to (e.g. a
            # crime.interview.logged entry still belongs in the causal
            # trace even though it isn't curated-feed-worthy on its own).
            domain = anchor["type"].split(".", 1)[0]
            rows = await deps.trace.select(
                "events", filters={"domain": eq(domain)}, order="tick.asc", limit=2000
            )
            for row in rows:
                row_payload = row.get("payload") or {}
                if row_payload.get(key) == value:
                    correlated[str(row["id"])] = row
            break  # use the first correlation key the anchor actually carries

        ordered = sorted(correlated.values(), key=lambda r: (r.get("tick") if r.get("tick") is not None else -1, r["id"]))
        return [event_row_to_scenario(r, names) for r in ordered]

    # -- conversations --------------------------------------------------------

    @router.get("/conversations", response_model=list[ConversationSummary])
    async def conversations(
        place: str | None = None,
        participant: str | None = None,
        since_tick: int | None = None,
        until_tick: int | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[ConversationSummary]:
        deps = get_deps()
        names = name_map(deps.genesis)
        filters: dict[str, str] = {}
        if place is not None:
            filters["location"] = eq(place)
        rows = await deps.trace.select(
            "utterances", filters=filters, order="tick.asc", limit=DEFAULT_CONVERSATION_ROW_LIMIT
        )
        if since_tick is not None:
            rows = [r for r in rows if r["tick"] >= since_tick]
        if until_tick is not None:
            rows = [r for r in rows if r["tick"] <= until_tick]
        clusters = cluster_conversations(rows, overlap_ticks=3)
        summaries = [conversation_summary(f"cc-{i}", rows_, names) for i, rows_ in enumerate(clusters)]
        if participant is not None:
            summaries = [s for s in summaries if participant in s.participant_ids]
        if kind is not None:
            summaries = [s for s in summaries if s.kind == kind]
        summaries.sort(key=lambda s: s.tick_start, reverse=True)
        return summaries[:limit]

    @router.get("/conversations/{conversation_cluster_id}", response_model=ConversationDetail)
    async def conversation_detail(conversation_cluster_id: str) -> ConversationDetail:
        deps = get_deps()
        names = name_map(deps.genesis)
        rows = await deps.trace.select(
            "utterances", order="tick.asc", limit=DEFAULT_CONVERSATION_ROW_LIMIT
        )
        clusters = cluster_conversations(rows, overlap_ticks=3)
        for i, cluster_rows in enumerate(clusters):
            if f"cc-{i}" == conversation_cluster_id:
                summary = conversation_summary(conversation_cluster_id, cluster_rows, names)
                ordered = sorted(cluster_rows, key=lambda r: r["tick"])
                return ConversationDetail(
                    **summary.model_dump(),
                    utterances=[utterance_view(r, names) for r in ordered],
                )
        raise HTTPException(status_code=404, detail=f"no conversation cluster {conversation_cluster_id!r}")

    return router
