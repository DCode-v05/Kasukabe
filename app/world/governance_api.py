"""Read-only Governance/World-View + Voting viewer API (DESIGN.md section
8's Viewers list: "World view / governance" and "Voting viewer").

Everything this module serves - the charter, its amendment history, the
sitting government, day-to-day policy (tax/margin/pension), jobs,
licenses, tenders, the immutable complaint ledger, and every election -
lives ONLY in `world.charter`'s in-process `CharterStore` (world/charter/
store.py). Nothing in this codebase persists that state to the SQLite
trace store: `world/wiring.py`'s `TraceStore` docstring says so plainly
("organizations, petitions, cases, buildings, skills, tools_registry,
relationships ... are columns in the same schema that nothing in this
wiring pass populates yet"), and a direct check confirms it - grepping
the whole `app/` tree for a write into the trace store's `organizations`
or `petitions` tables turns up only `world/charter/store.py`'s own
in-memory dict and `research/parquet_export.py`'s read-side query, never
an INSERT. So this module has to be mounted in-process with a live
`WorldState` (see `GovernanceDeps` below) - an out-of-process reader
pointed only at the trace database would see none of this.

This is a NEW peer to `world/admin_api.py`, not a replacement for it:
admin_api.py's `/admin/president/*` and `/admin/god/*` routes are the
WRITE surface (operator-token gated - land petitions, signing/vetoing
amendments, prayers, disasters). Every route here is a plain GET with no
token, because DESIGN.md section 8 draws that line itself: "All [viewers]
read from the trace store and the world's live state - they are windows,
never authorities. Nothing an operator views may mutate the world except
through the President/God console." A window does not need a key.

Mounted the same way admin_api.py's router is (world/main.py's own
docstring already documents this composition style): construct a
`GovernanceDeps` from whatever process holds the live `WorldServer`
(`world.charter`, `world.genesis` - see wiring.py's `WorldServer`) and
pass a zero-arg getter to `build_governance_router`. See this repo's
worker report for the exact one-line `app.include_router(...)` world/
main.py needs - this file deliberately does not edit main.py itself
(track boundary: "Do NOT edit world/main.py").

Cross-origin: the static viewer pages this API feeds (game/static/
views.html, game/static/agents.html) are served by game/server.py's own
process (a different origin/port - DESIGN.md section 9's architecture
diagram has world and game as separate deployables, and
infra/Caddyfile's production routing only exposes game/'s port on the
public domain, not world's). world/main.py has no CORSMiddleware and this
file cannot add app-level middleware from an APIRouter without editing
main.py, so every route below is registered with `add_cors_headers` as a
router-wide dependency instead - FastAPI shares one `Response` object
between a request's dependencies and its endpoint, so a dependency that
only takes `response: Response` and sets a header on it is the documented
way to attach a header to every route in a router without touching the
app object at all. Every route here is a GET with no custom request
headers and no credentials, so this never needs to satisfy a preflight
OPTIONS request either - a bare `Access-Control-Allow-Origin` on the
actual response is the whole story for the pages this feeds.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from world.charter.models import (
    AMENDABLE_CHARTER_FIELDS,
    AmendmentProposal,
    Charter,
    ComplaintEntry,
    Election,
    GovernmentPolicy,
    GovernmentState,
    Job,
    License,
    Tender,
)
from world.charter.elections import compute_tally
from world.life.models import Genesis
from world.tools.state import WorldState

# ── deps ──────────────────────────────────────────────────────────────


@dataclass
class GovernanceDeps:
    """Everything one request handler below needs. `genesis` is read only
    for its `agents` list - GenesisAgent.name is the one place a human
    display name lives (WorldState.agents is life/'s AgentLifeRecord,
    which deliberately carries no name, see life/models.py's own
    docstring), so every agent_id this module surfaces is resolved
    through it for a readable label.
    """

    world: WorldState
    genesis: Genesis


def name_map(genesis: Genesis) -> dict[str, str]:
    return {agent.agent_id: agent.name for agent in genesis.agents}


class AgentRef(BaseModel):
    """An agent_id plus its genesis display name, everywhere this module
    names an agent (proposer, voter, candidate, officer, ...) - a viewer
    should never have to cross-reference a second endpoint just to know
    who "a4f2c91b" is."""

    agent_id: str
    name: str | None = None


def agent_ref(names: dict[str, str], agent_id: str | None) -> AgentRef | None:
    if agent_id is None:
        return None
    return AgentRef(agent_id=agent_id, name=names.get(agent_id))


# ── charter + policy views ──────────────────────────────────────────────


class CharterView(BaseModel):
    version: int
    genesis_tick: int
    election_interval_days: float
    pm_seats: int
    minister_seats: int
    candidacy_window_days: float
    campaign_period_days: float
    voting_window_days: float
    amendment_threshold_numerator: int
    amendment_threshold_denominator: int
    amendment_votes_required: int
    total_seats: int
    suffrage_min_age: int
    suffrage_requires_residency: bool
    term_limit_terms: int | None
    patent_filing_fee_sim: int
    permit_fee_sim: int
    builder_license_fee_sim: int


def charter_view(charter: Charter) -> CharterView:
    return CharterView(
        **charter.model_dump(mode="json"),
        amendment_votes_required=charter.amendment_votes_required,
        total_seats=charter.total_seats,
    )


class PolicyView(BaseModel):
    version: int
    tax_rate_bps: int
    credit_store_margin_bps: int
    pension_private_pct: int
    pension_government_pct: int
    pension_eligibility_age: int


def policy_view(policy: GovernmentPolicy) -> PolicyView:
    return PolicyView(**policy.model_dump(mode="json"))


class JobView(BaseModel):
    job_id: str
    title: str
    job_type: str
    salary_sim: int
    status: str
    assigned_agent: AgentRef | None = None
    created_by: AgentRef
    created_tick: int
    cut_by: AgentRef | None = None
    cut_tick: int | None = None


class LicenseView(BaseModel):
    license_id: str
    license_type: str
    is_permit: bool
    holder: AgentRef
    fee_paid_sim: int
    issued_by: AgentRef
    issued_tick: int
    status: str
    revoked_by: AgentRef | None = None
    revoked_tick: int | None = None
    revoke_reason: str | None = None


class TenderView(BaseModel):
    tender_id: str
    title: str
    description: str
    budget_sim: int
    posted_by: AgentRef
    posted_tick: int
    status: str
    awarded_to: AgentRef | None = None
    awarded_tick: int | None = None
    cancel_reason: str | None = None


class ActivePoliciesView(BaseModel):
    """DESIGN.md section 8's exact list: "active policies (tax rates,
    credit-store margin, jobs created or cut, pensions, permits, police
    force size)" - one endpoint, one response shape, because that is how
    the design document itself frames "the state of the polity at a
    glance."""

    policy: PolicyView
    jobs_open: int
    jobs_filled: int
    jobs_cut: int
    jobs: list[JobView]
    police_force_size: int
    police_officers: list[JobView]
    permits_and_licenses: list[LicenseView]
    tenders: list[TenderView]


def job_view(names: dict[str, str], job: Job) -> JobView:
    return JobView(
        job_id=job.job_id,
        title=job.title,
        job_type=job.job_type,
        salary_sim=job.salary_sim,
        status=job.status.value,
        assigned_agent=agent_ref(names, job.assigned_agent_id),
        created_by=agent_ref(names, job.created_by_agent_id),  # type: ignore[arg-type]
        created_tick=job.created_tick,
        cut_by=agent_ref(names, job.cut_by_agent_id),
        cut_tick=job.cut_tick,
    )


def license_view(names: dict[str, str], license_: License) -> LicenseView:
    return LicenseView(
        license_id=license_.license_id,
        license_type=license_.license_type,
        is_permit=license_.license_type.startswith("permit:"),
        holder=agent_ref(names, license_.holder_agent_id),  # type: ignore[arg-type]
        fee_paid_sim=license_.fee_paid_sim,
        issued_by=agent_ref(names, license_.issued_by_agent_id),  # type: ignore[arg-type]
        issued_tick=license_.issued_tick,
        status=license_.status.value,
        revoked_by=agent_ref(names, license_.revoked_by_agent_id),
        revoked_tick=license_.revoked_tick,
        revoke_reason=license_.revoke_reason,
    )


def tender_view(names: dict[str, str], tender: Tender) -> TenderView:
    return TenderView(
        tender_id=tender.tender_id,
        title=tender.title,
        description=tender.description,
        budget_sim=tender.budget_sim,
        posted_by=agent_ref(names, tender.posted_by_agent_id),  # type: ignore[arg-type]
        posted_tick=tender.posted_tick,
        status=tender.status.value,
        awarded_to=agent_ref(names, tender.awarded_to_agent_id),
        awarded_tick=tender.awarded_tick,
        cancel_reason=tender.cancel_reason,
    )


class GovernmentStateView(BaseModel):
    prime_minister: AgentRef | None
    ministers: list[AgentRef]
    terms_served: dict[str, int]


def government_state_view(names: dict[str, str], state: GovernmentState) -> GovernmentStateView:
    return GovernmentStateView(
        prime_minister=agent_ref(names, state.pm_agent_id),
        ministers=[agent_ref(names, a) for a in state.minister_agent_ids],  # type: ignore[misc]
        terms_served=state.terms_served,
    )


class ComplaintView(BaseModel):
    ledger_index: int
    complainant: AgentRef
    against_id: str
    against_kind: str
    description: str
    filed_tick: int


# ── amendment log + charter diff ────────────────────────────────────────


class ChangedField(BaseModel):
    field_name: str
    before: Any
    after: Any


class CharterDiff(BaseModel):
    before_version: int
    after_version: int
    changed_fields: list[ChangedField]
    unified_text_diff: str


def diff_charters(before: Charter, after: Charter) -> CharterDiff:
    """A real text diff (not just "field X changed"), because DESIGN.md
    section 8 asks for exactly that: "a TEXT DIFF of the charter before
    vs after." `difflib.unified_diff` over the two charters' own
    pretty-printed JSON is the text diff; `changed_fields` is a cheaper
    structured summary of the same comparison for a UI that wants to
    render a compact table instead of (or above) the raw diff text.
    """
    before_json = json.dumps(before.model_dump(mode="json"), indent=2, sort_keys=True)
    after_json = json.dumps(after.model_dump(mode="json"), indent=2, sort_keys=True)
    diff_lines = difflib.unified_diff(
        before_json.splitlines(keepends=True),
        after_json.splitlines(keepends=True),
        fromfile=f"charter v{before.version}",
        tofile=f"charter v{after.version}",
    )
    changed = [
        ChangedField(field_name=name, before=getattr(before, name), after=getattr(after, name))
        for name in sorted(AMENDABLE_CHARTER_FIELDS)
        if getattr(before, name) != getattr(after, name)
    ]
    return CharterDiff(
        before_version=before.version,
        after_version=after.version,
        changed_fields=changed,
        unified_text_diff="".join(diff_lines),
    )


class AmendmentVoteView(BaseModel):
    voter: AgentRef
    support: bool


class AmendmentLogEntryView(BaseModel):
    """One amendment, start to finish: DESIGN.md section 8 - "each
    amendment shows the enacting tool call, proposer, per-agent votes,
    and a TEXT DIFF." `enacting_action` is the real mechanism (see its
    own note below) rather than a fabricated ToolInvocationRecord - see
    this module's own report for the honest gap this documents."""

    proposal_id: str
    field_name: str
    new_value: Any
    title: str
    rationale: str
    proposed_by: AgentRef
    proposed_tick: int
    status: str
    votes: list[AmendmentVoteView]
    yes_votes: int
    no_votes: int
    votes_required: int
    resulting_charter_version: int | None
    enacted_tick: int | None
    vetoed_tick: int | None
    veto_note: str | None
    enacting_action: str
    diff: CharterDiff | None


def amendment_log_entry(
    names: dict[str, str], proposal: AmendmentProposal, charter_history: list[Charter]
) -> AmendmentLogEntryView:
    yes_votes = sum(1 for v in proposal.votes.values() if v)
    no_votes = sum(1 for v in proposal.votes.values() if not v)
    # charter_history is a version-indexed list (index i has .version == i
    # - see world/charter/amendments.py's sign_amendment: every push is
    # current.version + 1, and the only way a Charter is ever appended is
    # through that one call site), so "the charter right before this
    # amendment" is simply charter_history[v - 1].
    diff: CharterDiff | None = None
    if proposal.resulting_charter_version is not None:
        after_v = proposal.resulting_charter_version
        if 0 < after_v < len(charter_history):
            diff = diff_charters(charter_history[after_v - 1], charter_history[after_v])
    # There is no dedicated ToolInvocationRecord for propose/vote/sign/
    # veto today (verified: grepping world/tools/primitives for
    # "amendment" finds nothing - only `vote` on an ELECTION ballot is a
    # public tool; amendment propose/cast_amendment_vote have no tool
    # wrapper at all, and sign_amendment/veto_amendment are called
    # directly by world/admin_api.py's President routes, bypassing
    # ToolRegistry.execute() entirely, so they never reach
    # world.tool_invocations either). The real, honest "what act enacted
    # this" is the charter/ method name itself.
    action = {
        "proposed": "charter.amendments.propose_amendment",
        "passed_awaiting_president": "charter.amendments.cast_amendment_vote (threshold reached)",
        "enacted": "charter.amendments.sign_amendment (President console)",
        "vetoed": "charter.amendments.veto_amendment (President console)",
        "rejected": "charter.amendments.cast_amendment_vote (threshold unreachable)",
    }[proposal.status.value]
    # cast_amendment_vote() recomputes the required yes-count against
    # whatever charter is CURRENT at each vote (amendments.py never pins
    # a proposal to the charter version in force when it was proposed).
    # For an ENACTED proposal, "the charter in force right before this
    # amendment" (charter_history[resulting_charter_version - 1]) is
    # exactly that charter and gives an exact answer; for anything still
    # open or rejected, the latest charter is the best available
    # approximation (see this module's own report for the honest
    # limitation - no per-vote snapshot of the threshold is persisted).
    if proposal.resulting_charter_version is not None and 0 < proposal.resulting_charter_version <= len(charter_history):
        votes_required = charter_history[proposal.resulting_charter_version - 1].amendment_votes_required
    else:
        votes_required = charter_history[-1].amendment_votes_required if charter_history else 0
    return AmendmentLogEntryView(
        proposal_id=proposal.proposal_id,
        field_name=proposal.field_name,
        new_value=proposal.new_value,
        title=proposal.title,
        rationale=proposal.rationale,
        proposed_by=agent_ref(names, proposal.proposed_by_agent_id),  # type: ignore[arg-type]
        proposed_tick=proposal.proposed_tick,
        status=proposal.status.value,
        votes=[
            AmendmentVoteView(voter=agent_ref(names, voter_id), support=support)  # type: ignore[arg-type]
            for voter_id, support in proposal.votes.items()
        ],
        yes_votes=yes_votes,
        no_votes=no_votes,
        votes_required=votes_required,
        resulting_charter_version=proposal.resulting_charter_version,
        enacted_tick=proposal.enacted_tick,
        vetoed_tick=proposal.vetoed_tick,
        veto_note=proposal.veto_note,
        enacting_action=action,
        diff=diff,
    )


# ── elections / voting ───────────────────────────────────────────────────


class CandidacyView(BaseModel):
    candidate: AgentRef
    declared_tick: int
    votes_so_far: int = 0


class BallotView(BaseModel):
    voter: AgentRef
    candidate: AgentRef


class ElectionResultView(BaseModel):
    prime_minister: AgentRef | None
    ministers: list[AgentRef]
    vote_counts: dict[str, int]
    tallied_tick: int


class ElectionSummaryView(BaseModel):
    election_id: str
    phase: str
    charter_version_at_scheduling: int
    seats_at_scheduling: int
    scheduled_tick: int
    candidacy_opens_tick: int
    candidacy_closes_tick: int
    voting_opens_tick: int
    voting_closes_tick: int
    candidate_count: int
    ballots_cast: int
    result: ElectionResultView | None
    regime_changed: bool | None


class ElectionDetailView(ElectionSummaryView):
    candidates: list[CandidacyView]
    ballots: list[BallotView]
    running_tally: dict[str, int]


def build_result_view(names: dict[str, str], election: Election) -> ElectionResultView | None:
    if election.result is None:
        return None
    return ElectionResultView(
        prime_minister=agent_ref(names, election.result.pm_agent_id),
        ministers=[agent_ref(names, a) for a in election.result.minister_agent_ids],  # type: ignore[misc]
        vote_counts=election.result.vote_counts,
        tallied_tick=election.result.tallied_tick,
    )


def election_summary(
    names: dict[str, str], election: Election, previous_pm_agent_id: str | None
) -> ElectionSummaryView:
    regime_changed = None
    if election.result is not None:
        regime_changed = election.result.pm_agent_id != previous_pm_agent_id
    return ElectionSummaryView(
        election_id=election.election_id,
        phase=election.phase.value,
        charter_version_at_scheduling=election.charter_version_at_scheduling,
        seats_at_scheduling=election.seats_at_scheduling,
        scheduled_tick=election.scheduled_tick,
        candidacy_opens_tick=election.candidacy_opens_tick,
        candidacy_closes_tick=election.candidacy_closes_tick,
        voting_opens_tick=election.voting_opens_tick,
        voting_closes_tick=election.voting_closes_tick,
        candidate_count=len(election.candidates),
        ballots_cast=len(election.ballots),
        result=build_result_view(names, election),
        regime_changed=regime_changed,
    )


def election_detail(names: dict[str, str], election: Election, previous_pm_agent_id: str | None) -> ElectionDetailView:
    running_tally, _ranked = compute_tally(election)
    summary = election_summary(names, election, previous_pm_agent_id)
    return ElectionDetailView(
        **summary.model_dump(),
        candidates=[
            CandidacyView(
                candidate=agent_ref(names, c.agent_id),  # type: ignore[arg-type]
                declared_tick=c.declared_tick,
                votes_so_far=running_tally.get(c.agent_id, 0),
            )
            for c in election.candidates.values()
        ],
        # DESIGN.md section 7: "one agent, one vote, cast in person" reads
        # as a secret ballot in the live event feed (elections.py's
        # cast_vote deliberately omits the candidate from the broadcast
        # event - see its own comment) but the full ballot is still real,
        # queryable state in the store, and DESIGN.md section 8 explicitly
        # asks this viewer for "votes as they are cast" - a research
        # instrument, not the town's own public record.
        ballots=[
            BallotView(voter=agent_ref(names, voter_id), candidate=agent_ref(names, candidate_id))  # type: ignore[arg-type]
            for voter_id, candidate_id in election.ballots.items()
        ],
        running_tally=running_tally,
    )


# ── the router ────────────────────────────────────────────────────────


async def add_cors_headers(response: Response) -> None:
    """See module docstring's "Cross-origin" note - this is the whole
    CORS story for a read-only, credential-less GET API."""
    response.headers["Access-Control-Allow-Origin"] = "*"


# ---------------------------------------------------------------------------
# B5: the treasury, and which term a policy change happened under (section 23)
# ---------------------------------------------------------------------------
# "A real budget an elected government actually manages: tax income in,
# pensions/police/permits/tenders out, and a visible balance - so a policy
# choice has a traceable financial consequence, not just a rule change."
# "Election outcomes should visibly change what happens next: a new Prime
# Minister's actual policy choices should be attributable, in the trace, to
# that specific term in office."
#
# Both are read paths over state that already exists: the treasury balance
# lives in world.ledger, and every amendment already records its own
# enacted_tick. What was missing was showing the balance next to the policies
# that move it, and joining an amendment's tick back to whoever held the seat
# at that moment.


class TreasuryView(BaseModel):
    """The town's own balance, and what moved it."""

    balance_sim: int
    balance_usd_cents: int
    tax_collected_sim: int
    wages_paid_sim: int
    pensions_paid_sim: int
    fees_collected_sim: int
    other_out_sim: int


class PolicyWithTreasuryView(BaseModel):
    """Section 23: the policy AND the balance it acts on, in one view.

    Serving the tax rate without the treasury it feeds is exactly the gap
    this closes - a rate is a rule, and a rule with no visible consequence
    is what "exists but is not lived in" means.
    """

    policy: PolicyView
    treasury: TreasuryView


class TermView(BaseModel):
    """One Prime Minister's term in office, and what changed during it."""

    pm_agent_id: str | None
    pm_name: str | None
    minister_agent_ids: list[str]
    election_id: str | None
    took_office_tick: int
    left_office_tick: int | None
    is_current: bool
    amendments_enacted: list[str]
    amendment_titles: list[str]
    treasury_at_start_sim: int | None = None


def treasury_view(world: WorldState) -> TreasuryView:
    """The treasury balance plus a breakdown of the flows that built it."""
    from world.ledger.models import Currency
    from world.ledger.money import TREASURY_ACCOUNT_ID

    account = world.ledger.accounts.get(TREASURY_ACCOUNT_ID)
    tax = wages = pensions = fees = other_out = 0
    for t in world.ledger.transactions:
        if t.currency is not Currency.SIM:
            continue
        reason = getattr(t.reason, "value", str(t.reason))
        if t.to_account == TREASURY_ACCOUNT_ID:
            if reason == "tax":
                tax += t.amount
            elif reason == "business_registration_fee":
                fees += t.amount
        elif t.from_account == TREASURY_ACCOUNT_ID:
            if reason == "wage":
                wages += t.amount
            elif reason == "pension":
                pensions += t.amount
            else:
                other_out += t.amount
    return TreasuryView(
        balance_sim=account.sim_units if account is not None else 0,
        balance_usd_cents=account.usd_cents if account is not None else 0,
        tax_collected_sim=tax,
        wages_paid_sim=wages,
        pensions_paid_sim=pensions,
        fees_collected_sim=fees,
        other_out_sim=other_out,
    )


def build_terms(
    names: dict[str, str],
    elections: list[Any],
    amendments: list[Any],
    current: Any,
) -> list[TermView]:
    """Join every enacted amendment to the term it was enacted under.

    A term runs from the tick its election was tallied to the tick the next
    one was - so an amendment's own `enacted_tick` places it in exactly one
    term without needing a new field on the amendment or a new table. An
    amendment enacted before any election ever ran belongs to no term and is
    deliberately dropped rather than attributed to the first PM, who was not
    in office when it happened.
    """
    tallied = sorted(
        (e for e in elections if e.result is not None),
        key=lambda e: e.result.tallied_tick,
    )
    terms: list[TermView] = []
    for i, election in enumerate(tallied):
        start = election.result.tallied_tick
        end = tallied[i + 1].result.tallied_tick if i + 1 < len(tallied) else None
        enacted = [
            a for a in amendments
            if a.enacted_tick is not None
            and a.enacted_tick >= start
            and (end is None or a.enacted_tick < end)
        ]
        pm = election.result.pm_agent_id
        terms.append(
            TermView(
                pm_agent_id=pm,
                pm_name=names.get(pm) if pm else None,
                minister_agent_ids=list(election.result.minister_agent_ids),
                election_id=election.election_id,
                took_office_tick=start,
                left_office_tick=end,
                is_current=end is None,
                amendments_enacted=[a.proposal_id for a in enacted],
                amendment_titles=[a.title for a in enacted],
            )
        )
    if not terms and current is not None and current.pm_agent_id:
        # A government seated without an election (genesis, or an operator
        # appointment) is still a term - it just has no election to point at.
        terms.append(
            TermView(
                pm_agent_id=current.pm_agent_id,
                pm_name=names.get(current.pm_agent_id),
                minister_agent_ids=list(current.minister_agent_ids),
                election_id=None,
                took_office_tick=0,
                left_office_tick=None,
                is_current=True,
                amendments_enacted=[a.proposal_id for a in amendments if a.enacted_tick is not None],
                amendment_titles=[a.title for a in amendments if a.enacted_tick is not None],
            )
        )
    return terms


def build_governance_router(get_deps: Callable[[], GovernanceDeps]) -> APIRouter:
    router = APIRouter(tags=["governance"], dependencies=[Depends(add_cors_headers)])

    # -- charter -----------------------------------------------------------

    @router.get("/governance/charter", response_model=CharterView)
    async def current_charter() -> CharterView:
        deps = get_deps()
        return charter_view(await deps.world.charter.store.get_charter())

    @router.get("/governance/charter/history", response_model=list[CharterView])
    async def charter_history() -> list[CharterView]:
        deps = get_deps()
        async with deps.world.charter.store.lock:
            history = list(deps.world.charter.store.charter_history)
        return [charter_view(c) for c in history]

    # -- policy / "active policies" -----------------------------------------

    @router.get("/governance/policy", response_model=PolicyView)
    async def current_policy() -> PolicyView:
        deps = get_deps()
        return policy_view(await deps.world.charter.store.get_policy())

    @router.get("/governance/policy/history", response_model=list[PolicyView])
    async def policy_history() -> list[PolicyView]:
        deps = get_deps()
        async with deps.world.charter.store.lock:
            history = list(deps.world.charter.store.policy_history)
        return [policy_view(p) for p in history]

    @router.get("/governance/active-policies", response_model=ActivePoliciesView)
    async def active_policies() -> ActivePoliciesView:
        deps = get_deps()
        names = name_map(deps.genesis)
        store = deps.world.charter.store
        policy = await store.get_policy()
        jobs = await store.list_jobs()
        licenses = await store.list_licenses()
        tenders = await store.list_tenders()
        police_jobs = [j for j in jobs if j.job_type == "police" and j.status.value == "filled"]
        return ActivePoliciesView(
            policy=policy_view(policy),
            jobs_open=sum(1 for j in jobs if j.status.value == "open"),
            jobs_filled=sum(1 for j in jobs if j.status.value == "filled"),
            jobs_cut=sum(1 for j in jobs if j.status.value == "cut"),
            jobs=[job_view(names, j) for j in jobs],
            police_force_size=len(police_jobs),
            police_officers=[job_view(names, j) for j in police_jobs],
            permits_and_licenses=[license_view(names, lic) for lic in licenses],
            tenders=[tender_view(names, t) for t in tenders],
        )

    # -- government / who sits ----------------------------------------------

    @router.get("/governance/government", response_model=GovernmentStateView)
    async def government() -> GovernmentStateView:
        deps = get_deps()
        names = name_map(deps.genesis)
        state = await deps.world.charter.store.get_government_state()
        return government_state_view(names, state)

    # -- amendment log --------------------------------------------------------

    # -- B5: the budget, and who is answerable for it (section 23) --------

    @router.get("/governance/treasury", response_model=TreasuryView)
    async def treasury() -> TreasuryView:
        return treasury_view(get_deps().world)

    @router.get("/governance/policy-and-treasury", response_model=PolicyWithTreasuryView)
    async def policy_and_treasury() -> PolicyWithTreasuryView:
        """The rate and the balance it feeds, together - a rule with no
        visible consequence is what "exists but is not lived in" means."""
        deps = get_deps()
        policy = await deps.world.charter.store.get_policy()
        return PolicyWithTreasuryView(
            policy=policy_view(policy), treasury=treasury_view(deps.world)
        )

    @router.get("/governance/terms", response_model=list[TermView])
    async def terms() -> list[TermView]:
        """Every term in office, newest first, with the policy changes
        enacted under it - "attributable, in the trace, to that specific
        term in office"."""
        deps = get_deps()
        names = name_map(deps.genesis)
        rows = build_terms(
            names,
            await deps.world.charter.store.list_elections(),
            list(deps.world.charter.amendments.proposals.values()),
            await deps.world.charter.store.get_government_state(),
        )
        rows.sort(key=lambda t: t.took_office_tick, reverse=True)
        return rows

    @router.get("/governance/amendments", response_model=list[AmendmentLogEntryView])
    async def amendments() -> list[AmendmentLogEntryView]:
        deps = get_deps()
        names = name_map(deps.genesis)
        async with deps.world.charter.store.lock:
            history = list(deps.world.charter.store.charter_history)
        proposals = sorted(
            deps.world.charter.amendments.proposals.values(),
            key=lambda p: p.proposed_tick,
            reverse=True,
        )
        return [amendment_log_entry(names, p, history) for p in proposals]

    # -- immutable complaint ledger (DESIGN.md section 7's constitutional
    # check, distinct from crime/'s complaint desk - see charter/models.py's
    # ComplaintEntry docstring) --------------------------------------------

    @router.get("/governance/complaints", response_model=list[ComplaintView])
    async def complaints() -> list[ComplaintView]:
        deps = get_deps()
        names = name_map(deps.genesis)
        entries: list[ComplaintEntry] = await deps.world.charter.store.list_complaints()
        return [
            ComplaintView(
                ledger_index=e.ledger_index,
                complainant=agent_ref(names, e.complainant_agent_id),  # type: ignore[arg-type]
                against_id=e.against_id,
                against_kind=e.against_kind,
                description=e.description,
                filed_tick=e.filed_tick,
            )
            for e in reversed(entries)
        ]

    # -- voting: elections ----------------------------------------------------

    @router.get("/voting/elections", response_model=list[ElectionSummaryView])
    async def elections() -> list[ElectionSummaryView]:
        deps = get_deps()
        names = name_map(deps.genesis)
        all_elections = sorted(
            await deps.world.charter.store.list_elections(), key=lambda e: e.scheduled_tick
        )
        summaries: list[ElectionSummaryView] = []
        previous_pm: str | None = None
        for election in all_elections:
            summaries.append(election_summary(names, election, previous_pm))
            if election.result is not None:
                previous_pm = election.result.pm_agent_id
        summaries.reverse()  # newest first, matching every other feed here
        return summaries

    @router.get("/voting/elections/{election_id}", response_model=ElectionDetailView)
    async def election_detail_route(election_id: str) -> ElectionDetailView:
        deps = get_deps()
        names = name_map(deps.genesis)
        all_elections = sorted(
            await deps.world.charter.store.list_elections(), key=lambda e: e.scheduled_tick
        )
        previous_pm: str | None = None
        target: Election | None = None
        for election in all_elections:
            if election.election_id == election_id:
                target = election
                break
            if election.result is not None:
                previous_pm = election.result.pm_agent_id
        if target is None:
            raise HTTPException(status_code=404, detail=f"no election {election_id!r}")
        return election_detail(names, target, previous_pm)

    return router
