"""Pydantic records for the state world/tools/ owns because no sibling
module (ledger, charter, life, map) claims it: physical businesses,
resource zones, storage containers, building plots and the buildings
raised on them, prayer requests, crime cases, and invented-tool
definitions. CONVENTIONS.md: "Prefer pydantic models for anything that
crosses a module boundary" - every one of these is read by
WorldState.snapshot()/restore() (registry.py's atomicity seam) and will
eventually be a Supabase row, so pydantic throughout, matching
ledger/models.py's Transaction and life/models.py's AgentLifeRecord.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Business(BaseModel):
    """A registered business's non-financial identity. The financial half
    (its sim-only account, opened by `world.ledger.Ledger.register_business`)
    already lives in ledger/; this is the "name, type, who works here"
    half that no other module models yet (life/models.py's SmallBusiness
    etc. are genesis-snapshot shapes, not a live mutable registry - see
    that module's docstring).
    """

    business_id: str
    name: str
    business_type: str
    owner_agent_id: str
    employee_agent_ids: set[str] = Field(default_factory=set)
    location_id: str
    active: bool = True
    provides_workshops: set[str] = Field(default_factory=set)


class PricePost(BaseModel):
    """A standing offer to sell one item, and optionally to buy it.

    Tool spec A3. Before this, `buy` took the BUYER's own
    `unit_price_sim` and checked it against nothing: a bakery could not
    have a price, only the baker's speech, and a customer could pay one
    sim a loaf. Price discovery had nothing to converge to, which is why
    evaluation E2 (does the spread across shops narrow) was unaskable.

    The holder is a business id or an agent id - deliberately the same
    key space, because `Inventory` already makes no distinction and a
    farmer standing in the market with three cabbages is doing the same
    thing a shop does, at a smaller scale. A business's post hangs at its
    premises whether the owner is there or not; a person's travels with
    them.

    `ask_sim` is what the holder sells one unit for, `bid_sim` what they
    pay for one. At least one is always set (`post_price` refuses both
    null and names `withdraw_price`), so a post always means something.
    """

    item: str
    ask_sim: int | None = None
    bid_sim: int | None = None
    posted_tick: int = 0
    poster_id: str = ""


class WagePromise(BaseModel):
    """What an employer said it would pay, and what it has actually paid.

    Tool spec A5. `hire_employee` grants tool access and pays nobody, so
    before this a job was a label: a student was told "your work is at Oak
    Hill College" and nothing there earned.

    The world never pays on its own - it counts. `accrued_sim` grows at
    each day boundary the period comes round; `paid_sim` only ever moves
    because somebody called `pay_wage`. The gap between them is the whole
    measurement, and it is what both sides read in perception each day.
    """

    business_id: str
    employee_id: str
    wage_sim: int
    every_days: int = 1
    promised_tick: int = 0
    last_paid_tick: int | None = None
    accrued_sim: int = 0
    paid_sim: int = 0
    accrued_through_day: int | None = None

    @property
    def owed_sim(self) -> int:
        return max(0, self.accrued_sim - self.paid_sim)

    def days_overdue(self, now_tick: int) -> int:
        """Game-days since the last payment, or since the promise if none."""
        from world.core.clock import TICKS_PER_DAY

        since = self.last_paid_tick if self.last_paid_tick is not None else self.promised_tick
        return max(0, (now_tick - since) // TICKS_PER_DAY)


class LoanRecord(BaseModel):
    """One `transfer` the lender tagged as a loan, and what has come back.

    C2. The 400-sim story - a debt carried unspoken for five weeks, then
    named, acknowledged and repaid - had to be reconstructed by a human
    reading transcripts, because nothing in the world recorded that the
    transfer was a loan rather than a gift. Next time it is a ledger fact.
    """

    lender_id: str
    borrower_id: str
    principal_sim: int
    repaid_sim: int = 0
    since_tick: int = 0

    @property
    def outstanding_sim(self) -> int:
        return max(0, self.principal_sim - self.repaid_sim)


class NeedKind(str, Enum):
    """The seven pressures a resident can be under (ENG-24, §5.3)."""

    FOOD = "food"
    ENERGY = "energy"
    MONEY = "money"
    REST = "rest"
    SHELTER = "shelter"
    SOCIAL = "social"
    SAFETY = "safety"


class Need(BaseModel):
    """How badly one pressure is pressing, 0 to 1.

    Computed by the world from facts it already holds - hunger, wallet
    runway, sleep debt, unanswered questions - and never set by a resident.
    A need is not a goal: it is the reason a goal might exist, and the
    resident decides what to do about it in its own words.
    """

    kind: NeedKind
    level: float = Field(ge=0.0, le=1.0)
    since_tick: int = 0
    source: str = "world"


class GoalHorizon(str, Enum):
    NOW = "now"
    TODAY = "today"
    WEEK = "week"
    LIFE = "life"


class GoalStatus(str, Enum):
    ACTIVE = "active"
    DONE = "done"
    ABANDONED = "abandoned"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"


class GoalOrigin(str, Enum):
    NEED = "need"
    PERSONA = "persona"
    SOCIAL = "social"
    REFUSAL = "refusal"
    REFLECTION = "reflection"
    ORDER = "order"


class Goal(BaseModel):
    """Something a resident is trying to do, in its own words.

    ENG-24. The review's line was that "agents that develop their own
    goals" had **no substrate** - there was `Intention`, which is one
    sentence with no horizon, no priority, no progress and no way to be
    superseded, and nothing else. A town cannot be measured on goals it
    has no way to hold.

    `text` is the resident's own wording, capped rather than structured,
    because the point of the measurement is what a resident chooses to
    pursue - a menu of enum goals would answer the question for it.
    """

    goal_id: str
    agent_id: str
    text: str = Field(max_length=160)
    horizon: GoalHorizon = GoalHorizon.TODAY
    priority: int = Field(default=3, ge=1, le=5)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    status: GoalStatus = GoalStatus.ACTIVE
    origin: GoalOrigin = GoalOrigin.PERSONA
    created_tick: int = 0
    deadline_tick: int | None = None
    parent_id: str | None = None
    done_when: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    set_in_stage: str | None = None


class Belief(BaseModel):
    """Something a resident holds to be true, and how sure it is.

    ENG-24. Beliefs are what make a refusal worth something: "cannot craft
    at the farmland" is a fact the resident learned by being refused, and
    without somewhere to put it the same wall is walked into every tick.
    """

    belief_id: str
    agent_id: str
    subject: str
    statement: str = Field(max_length=160)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    last_updated_tick: int = 0
    contradicted_by: str | None = None


class Intention(BaseModel):
    """What a resident said it set out to do, in its own words.

    Tool spec B2. Nothing enforces it - the ratio of intentions set to
    intentions fulfilled is the measurement, and a tool that forced
    follow-through would answer the question it exists to ask.
    """

    text: str
    set_tick: int = 0
    done_when_tool: str | None = None
    set_clock: str = ""


class ToolLicence(BaseModel):
    """The right to call somebody else's invented tool, for a while.

    Tool spec C1. A licence is a rent: the owner keeps the tool, the
    licensee calls it under their own name and at their own cost, and it
    ends on its term or - if the owner said so when granting - on a
    revocation.
    """

    tool_name: str
    licensee_id: str
    granted_by: str
    granted_tick: int = 0
    until_tick: int = 0
    revocable: bool = True
    fee_sim: int = 0

    def is_current(self, now_tick: int) -> bool:
        return now_tick < self.until_tick


class ResourceZoneKind(str, Enum):
    FARMLAND = "farmland"
    ORE_OUTCROP = "ore_outcrop"
    FOREST = "forest"


class ResourceZone(BaseModel):
    """A map-edge resource zone (DESIGN.md section 1: "not buildings").
    `remaining_yield=None` means effectively unlimited - DESIGN.md tags
    forest/timber specifically as "common", the one resource genesis does
    not want to gate on scarcity.
    """

    location_id: str
    kind: ResourceZoneKind
    resource_item: str
    remaining_yield: int | None = None
    regen_per_tick: int = 0


class Storage(BaseModel):
    """A named container an owner (agent or business) can stash items in
    away from their personal inventory - what `store_item`/`withdraw_item`
    move things into and out of. DESIGN.md's "steal" primitive targets
    "unattended cash/inventory"; a Storage left at a location its owner
    is not currently standing in is exactly what "unattended" means here
    (see primitives/crime.py).
    """

    storage_id: str
    owner_id: str
    location_id: str


class Plot(BaseModel):
    """Development rights over a tile footprint (DESIGN.md section 1:
    freeform building "Requires plot rights + government permit").
    `footprint` is the FULL area the owner may build within; one
    `build_structure` call may claim any subset of it (see
    primitives/construction.py) - a plot is the government's grant, not
    a single building's shape.
    """

    plot_id: str
    owner_agent_id: str
    location_id: str
    footprint: frozenset[tuple[int, int]]
    permitted: bool = False


class OwnerClass(str, Enum):
    """The 250 section 31.2's two broad classes.

    GOVERNMENT covers unclaimed land, the civic buildings, and every tile
    of freshly expanded land until it is assigned elsewhere. PRIVATE
    covers everything else, and "everything else" is exhaustive: section
    31.2 is explicit that a private tile is never ownerless, which is why
    there is no third UNOWNED class here - unowned land IS government
    land, and saying so with one class rather than two removes the state
    where a tile belongs to nobody at all.
    """

    GOVERNMENT = "government"
    PRIVATE = "private"


class TileOwnership(BaseModel):
    """One 1x1 unit of land and who holds it (The 250 section 31.1).

    "The town's land divides into 1x1 units, each addressable by its own
    simulation coordinate... Every unit has exactly one owner record at
    any given time. This is the smallest thing this plan proposes
    tracking; a building or a farm is just a contiguous set of owned
    units, not a new kind of object" - so this is deliberately not a
    rectangle, a parcel, or an estate. Those are all just sets of these.

    `owner_id` is an agent, household, or business id: section 31.2 lets
    any of the three hold land, and the ledger already keys agent and
    business accounts in one namespace, so no discriminator is needed to
    tell them apart at the point of sale.
    """

    tile_x: int = Field(ge=0)
    tile_y: int = Field(ge=0)
    owner_id: str
    owner_class: OwnerClass = OwnerClass.GOVERNMENT
    acquired_at_tick: int = 0
    from_expansion: bool = False


class Bequest(BaseModel):
    """One line of a will: who gets what.

    `sim_share` is a weight, not a percentage - the estate is split in
    proportion to the weights across every bequest, so a will never has
    to add up to exactly 100 and can be edited one line at a time without
    rebalancing the rest.
    """

    heir_id: str
    sim_share: int = Field(default=0, ge=0)
    tiles: list[tuple[int, int]] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class Will(BaseModel):
    """An owner's own wishes, overriding the automatic default (section 31.3).

    "That default still holds when nothing else is specified - but it is
    not the only path." A Will is exactly the "specified" case: absent
    one, `world.ledger.Ledger.settle_inheritance` and the nearest-living-
    relative rule apply unchanged.
    """

    agent_id: str
    bequests: list[Bequest] = Field(default_factory=list)
    updated_at_tick: int = 0


class BuildingStatus(str, Enum):
    UNDER_CONSTRUCTION = "under_construction"
    COMPLETE = "complete"


class Building(BaseModel):
    """One structure raised on a Plot. `footprint` here is the specific
    tiles this building actually occupies (a subset of its Plot's
    granted footprint); BoM and construction_ticks are derived from its
    geometry at creation time (see primitives/construction.py) and kept
    here for audit rather than recomputed.
    """

    building_id: str
    plot_id: str
    builder_agent_id: str
    purpose: str
    footprint: frozenset[tuple[int, int]]
    timber_used: int
    metal_used: int
    started_tick: int
    completion_tick: int
    status: BuildingStatus = BuildingStatus.UNDER_CONSTRUCTION
    labour_ticks_required: int = 0
    labour_ticks_done: int = 0

    @property
    def labour_remaining(self) -> int:
        return max(0, self.labour_ticks_required - self.labour_ticks_done)


class PrayerRequest(BaseModel):
    """DESIGN.md section 7 ("Temple: ... prayers, donations") and section 8
    ("prayer feed with grant / silent-deny"). This module owns filing the
    request (the `pray` tool call) and the queue a future god-console read
    endpoint would drain; the grant/silent-deny decision itself is the
    operator's, out of this module's scope - see primitives/social.py.
    """

    request_id: str
    agent_id: str
    tick: int
    message: str
    donation_sim: int = 0


class CrimeCaseKind(str, Enum):
    THEFT = "theft"
    COMPLAINT = "complaint"


class CrimeCase(BaseModel):
    """DESIGN.md section 6's crime-reporting flow: theft attempts and
    filed complaints both become case rows here. This is explicitly
    *not* charter/'s ComplaintEntry (that is the immutable constitutional
    ledger against government power - see charter/models.py's
    ComplaintEntry docstring, which calls out this exact distinction and
    assigns the crime-reporting desk to "a future crime/ module under
    world/"). Until that module exists, `steal` and `file_complaint`
    record here; a real investigation/warrant pipeline is out of this
    module's scope (see primitives/crime.py and primitives/civic.py).
    """

    case_id: str
    kind: CrimeCaseKind
    tick: int
    filer_or_actor_agent_id: str
    against_agent_id: str = ""
    description: str
    outcome: str | None = None
    verdict: str | None = None

    victim_agent_id: str | None = None
    ground_truth_perpetrator_id: str | None = None


class ParamType(str, Enum):
    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"


PARAM_TYPE_PYTHON: dict[ParamType, type] = {
    ParamType.STR: str,
    ParamType.INT: int,
    ParamType.FLOAT: float,
    ParamType.BOOL: bool,
}


CONDITION_OPS = frozenset({
    "==", "!=", "<", "<=", ">", ">=", "in", "not_in", "truthy", "falsy",
    "contains", "not_contains",
})


class StepCondition(BaseModel):
    """`when` on a program step: run this step, or skip it.

    `left` and `right` are REFERENCES or literals, never expressions:

      * `"$name"`  - the composite tool's own call-time parameter `name`
      * `"@name"`  - the result a previous step bound under `name`
      * `"@name.field"` - one field of that result, one level deep
      * anything else - a literal

    **A condition can only see what a step already paid to find out.**
    There is deliberately no way to read the world from inside a
    condition. A resident that wants to branch on whether the shelf is
    empty puts a real `look_around` in the program, binds it, and reads
    the binding - which is a gated, priced, traced call like any other.
    Giving conditions their own private window onto world state would be
    a second way to read the town that nothing charges for, and the whole
    reason composites are safe is that they add no path the registry does
    not already own.
    """

    left: Any
    op: str
    right: Any = None

    @model_validator(mode="after")
    def _op_is_one_we_have(self) -> "StepCondition":
        if self.op not in CONDITION_OPS:
            raise ValueError(
                f"`when.op` must be one of {', '.join(sorted(CONDITION_OPS))}; "
                f"got {self.op!r}. Conditions are a fixed set of comparisons, "
                "not an expression language - see StepCondition."
            )
        return self


class ToolStep(BaseModel):
    """One call in an invented tool's composition. `params` values that
    are strings starting with "$" are placeholders resolved against the
    composite tool's own call-time params at execution (see meta.py's
    `world/tools/program.py:resolve_params`); any other value is a
    literal passed through
    unchanged.

    PROG-02 added three optional fields and changed nothing about a step
    that does not use them - a tool filed before this existed replays
    exactly as it did, because `when` unset means "always", `repeat`
    unset means "once", and `bind` unset means "nobody reads this".

      * `when`   - a `StepCondition`; the step is SKIPPED if it is false.
      * `repeat` - run this step N times, N a literal bounded by
                   `PROGRAM_MAX_REPEAT`, or the string `"$name"` resolving
                   to a call parameter which is bounded the same way at
                   run time. A cap that could be argued past is not a cap.
      * `bind`   - a name a later step's `when` or `params` may read this
                   step's result under, as `@name` / `@name.field`.

    This is the whole of the "bounded expression layer" the audit asks
    for, and the bound is the point: an unbounded loop is a resident that
    spends its entire wallet inside one turn, so the ceiling is a world
    rule enforced by `world/tools/program.py`, never a convention the
    author is trusted to keep.
    """

    tool_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    when: StepCondition | None = None
    repeat: int | str | None = None
    bind: str | None = None


class CustomToolDefinition(BaseModel):
    """A composite tool invented via the meta-tool (DESIGN.md section 2).
    Two provenances share this one shape:

    1. A `define_tool` filing bound for the PUBLIC namespace: real physical
       property, disclosed in exchange for the patent office's protection,
       exactly like a real-world patent. `patent_application_id` is set,
       `approved` starts False, and registry.py's ToolSpec is only wired in
       once `approve_patent` flips it True (charter/patent_office.py's
       PENDING/APPROVED split).
    2. A `define_business_tool`/`define_organization_tool` filing bound for
       the BUSINESS/ORGANIZATION namespace: a trade secret or a society's
       private rite. DESIGN.md section 2 is explicit these need "no patent
       office" - no public disclosure, no filing fee, no review queue - so
       `patent_application_id` is None and `approved` is True from the
       moment of creation; the ToolSpec is wired into the registry in the
       same handler call that builds this record (see meta.py).
    """

    tool_name: str
    description: str
    creator_agent_id: str
    param_schema: dict[str, ParamType] = Field(default_factory=dict)
    steps: list[ToolStep]
    returns: dict[str, Any] = Field(default_factory=dict)
    patent_application_id: str | None = None
    approved: bool = False
    created_tick: int = 0
    version: int = 1
    superseded_steps: list[list[dict[str, Any]]] = Field(default_factory=list)
    revised_tick: int | None = None


class SubpoenaStatus(str, Enum):
    """DESIGN.md section 2: "Court subpoena via a valid warrant reveals
    invocation records." Deliberately its own small state machine here,
    not a reuse of `crime.warrants.Warrant` (world/crime/'s own, richer
    minister-signed warrant used for arrests/fines/jail, DESIGN.md section
    6) - crime/ has no seam into WorldState yet (its ports.py is built to
    stay independently testable, with no adapter wired to charter/ledger/
    tools/ by anyone yet), and world/tools/ is not the track that owns
    building that adapter. This is the narrower "does a sitting government
    member's sign-off unlock the confidential invocation log for one
    target agent" question that namespaces + ACLs (this module's own job)
    actually needs answered; a future integration pass can point this at
    crime/'s real Warrant once that seam exists, without changing anything
    namespace/ACL callers see here.
    """

    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"


class Subpoena(BaseModel):
    """One request to pierce namespace secrecy for a single target agent's
    tool-call history, tied to a `CrimeCase` this package already tracks
    (`file_complaint`/`steal`, above). Filing and deciding are both
    GOVERNMENT-namespace tools (primitives/justice.py); deciding requires a
    *different* sitting government member than the one who filed, so no
    single official can both request and grant their own subpoena - the
    same separation of powers DESIGN.md section 6 gives police-request /
    minister-sign in the real crime/warrants.py.
    """

    subpoena_id: str
    case_id: str
    target_agent_id: str
    requested_by_agent_id: str
    justification: str
    status: SubpoenaStatus = SubpoenaStatus.PENDING
    requested_tick: int
    decided_by_agent_id: str | None = None
    decided_tick: int | None = None
    denial_reason: str | None = None


class ToolInvocationRecord(BaseModel):
    """DESIGN.md section 2: "Everything is still logged for researchers
    regardless of in-world secrecy" - the ground-truth record that makes a
    court subpoena (above) mean something. `ToolRegistry.dispatch()`
    (registry.py) appends one of these for every call that actually
    reaches and completes a handler, namespace notwithstanding: a
    GOVERNMENT/BUSINESS/ORGANIZATION tool invisible to everyone else is
    still recorded here, exactly as visibly to research as a PUBLIC one -
    including each inner primitive step of a composite (invented) tool,
    logged under its creator's agent id.

    Included in `OWNED_STATE_FIELDS` (state.py) for the same reason every
    other owned list is: `ToolRegistry.execute()` snapshots tools-owned
    state before dispatching and restores it on any exception, so a call
    that fails partway through never leaves a phantom record behind - the
    log only ever contains calls that genuinely happened and committed,
    exactly what "a complete causal record" (DESIGN.md section 2) means.
    """

    invocation_id: str
    tool_name: str
    namespace: str
    actor_id: str
    tick: int
    params: dict[str, Any] = Field(default_factory=dict)
    owner_id: str | None = None
    program_id: str | None = None
    program_step: int | None = None


class SkillStep(BaseModel):
    """One step of an authored workflow.

    `check` steps have no `ref` - they are the agent telling its future self
    what to look at before continuing ("is the saw sharp?"). They cost nothing
    and dispatch nothing, and a workflow made only of checks discounts nothing,
    which is the correct price for a procedure that never touches the world.
    """

    order: int
    kind: str
    ref: str | None = None
    note: str = ""


class SkillDefinition(BaseModel):
    """A workflow one agent authored, and that any agent may be taught.

    `discounts_tools` is DERIVED from the steps, never declared - the guard
    that stops an agent claiming mastery over 42 tools without writing 42
    steps. `governmental` is likewise read off the steps rather than asserted
    by the author (see `skillify`), so a procedure of state cannot be filed as
    an ordinary trade to dodge the council.
    """

    name: str
    author_agent_id: str
    preconditions: str = ""
    steps: list[SkillStep] = Field(default_factory=list)
    discounts_tools: list[str] = Field(default_factory=list)
    governmental: bool = False
    status: str = "ACTIVE"
    amendment_id: str | None = None
    created_tick: int = 0
    version: int = 1
    superseded: list[SkillDefinition] = Field(default_factory=list)
    revised_tick: int | None = None
    namespace: str = "public"
    owner_id: str | None = None

    def tool_steps(self) -> list[SkillStep]:
        return [s for s in self.steps if s.kind == "tool" and s.ref]


class SkillProgress(BaseModel):
    """Where an agent has got to in a workflow it is following.

    The one genuinely new piece of state in Part B. Without it a skill is a
    document nobody is measured against: proficiency could only ever count
    individual tool calls, and "did this agent actually follow its own
    procedure end to end" would be unanswerable.
    """

    agent_id: str
    skill_name: str
    next_step: int
    started_tick: int
    last_advance_tick: int


class ToolRefusalRecord(BaseModel):
    """One time an agent tried to do something and the world said no.

    The other half of `ToolInvocationRecord`. That one answers "what did the
    town do"; this answers **"what did the town try to do and could not"** -
    which is the more interesting question, because it is the only place a
    capability gap has a name and a headcount before anyone has invented
    anything to fill it.

    `unmet_requirement` is a short stable key, not a message: `skill:medicine`,
    `location:hospital`, `seat:government`, `funds`, `no-such-tool:repair`.
    Stable so it groups - "14 agents tried to treat illness and lacked the
    skill" needs the same string every time, not fourteen phrasings of it.
    """

    refusal_id: str
    agent_id: str
    tool_name: str
    tick: int
    unmet_requirement: str
    detail: str = ""

