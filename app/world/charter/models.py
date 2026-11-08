"""Data models for politics-as-data (DESIGN.md section 7).

Everything a government can decide is a typed field on a small set of
pydantic models: `Charter` (the constitutional rules, amendable only by a
3/5 vote of the sitting government) and `GovernmentPolicy` (the day-to-day
levers any sitting seat holder can pull directly: tax rate, credit-store
margin, pension config). Both are immutable snapshots (`frozen=True`) with
a monotonic `version` counter: an amendment or a policy change never
mutates a field in place, it produces a new snapshot and the store keeps
the whole history. That history is not incidental bookkeeping - DESIGN.md
section 10 flags "regime-transition triggers" and "authoritarianism index"
as flagship political-science metrics, and those need to be computed from
a sequence of charters over time, not just the current one.

Nothing here enforces "collapse to one-person rule" as a special case.
DESIGN.md section 7 is explicit that authoritarianism "must be reachable,
never scripted": every field below is amendable by the same generic path,
so a government that sets `minister_seats=0`,
`amendment_threshold_denominator=1`, and `pm_seats=1` has legally voted
itself into one-person rule using the ordinary amendment tool, not a
special code path.

Money fields are always an integer count of sim-currency units (never a
float - app/CONVENTIONS.md is explicit that floating-point currency is a
bug). Rates (tax, credit-store margin) are integer basis points for the
same reason: a float percentage is one rounding error away from breaking
reconciliation.

This module intentionally does not import anything from `world/`'s
agent registry, ledger, or tool registry. GROW-30: that used to say those
were not built yet. They are - `world/tools/registry.py`, `world/ledger/`,
`world/agents_api.py` - and the boundary is now a deliberate one rather
than a wait. charter/ is imported BY the tool layer, so importing back
would be a cycle. Eligibility facts
(age, residency) and location strings are passed in by the caller at call
time (see `elections.py`, `government.py`) instead of being looked up
here, so charter/ stays importable from the layer that calls it.
"""

from __future__ import annotations

import math
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TICKS_PER_GAME_DAY = 144

LOCATION_TOWN_HALL = "town_hall"
LOCATION_GOVERNMENT_COMPLEX = "government_complex"
LOCATION_PATENT_OFFICE = "patent_office"


def days_to_ticks(days: float) -> int:
    """Convert a game-day duration to an integer tick count.

    Rounds rather than truncates so a 0.5-day window (72 ticks exactly)
    and similar fractional-but-clean values round-trip precisely.
    """
    return round(days * TICKS_PER_GAME_DAY)


def new_id(prefix: str) -> str:
    """Short, prefixed, collision-safe id for records this module creates.

    Prefixed so a bare id string in a log line or Supabase row is
    self-describing (e.g. "election-...", "patent-...") without a join.
    """
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Charter(BaseModel):
    """The constitutional rules of Kasukabe. Amendable only via 3/5 vote.

    `frozen=True`: an amendment produces a brand new `Charter` instance
    (see `amendments.py`'s `sign_amendment`) rather than mutating a field
    in place. The store keeps every version, so "what did the charter say
    on day 12" is answerable, which matters for the regime-transition
    research track (DESIGN.md section 10).
    """

    model_config = ConfigDict(frozen=True)

    version: int = 0
    genesis_tick: int = 0

    election_interval_days: float = 3.0
    pm_seats: int = 1
    minister_seats: int = 4
    candidacy_window_days: float = 1.0
    campaign_period_days: float = 1.0
    voting_window_days: float = 1.0

    amendment_threshold_numerator: int = 3
    amendment_threshold_denominator: int = 5

    suffrage_min_age: int = 18
    compulsory_voting: bool = False
    suffrage_requires_residency: bool = True

    term_limit_terms: int | None = None

    patent_filing_fee_sim: int = 1_000

    permit_fee_sim: int = 200
    builder_license_fee_sim: int = 500

    standing_orders: tuple[str, ...] = ()

    @property
    def total_seats(self) -> int:
        return self.pm_seats + self.minister_seats

    @property
    def amendment_votes_required(self) -> int:
        """Yes-votes needed out of the current sitting body to pass.

        Ceiling division: a 3/5 threshold over 5 seats needs 3 yes votes,
        not 2.something rounded down. Recomputed from current seats every
        time, so a government that first amends `minister_seats` down and
        then votes on a second amendment is judged against the new seat
        count, not a snapshot taken at genesis.
        """
        return math.ceil(
            self.amendment_threshold_numerator
            * self.total_seats
            / self.amendment_threshold_denominator
        )

    @model_validator(mode="after")
    def check_invariants(self) -> Charter:
        errors: list[str] = []
        if self.pm_seats < 0:
            errors.append(f"pm_seats={self.pm_seats} must be >= 0")
        if self.minister_seats < 0:
            errors.append(f"minister_seats={self.minister_seats} must be >= 0")
        if self.pm_seats + self.minister_seats < 1:
            errors.append(
                "pm_seats + minister_seats must be >= 1 (a government of zero cannot sit)"
            )
        if self.amendment_threshold_numerator < 1:
            errors.append(
                f"amendment_threshold_numerator={self.amendment_threshold_numerator} must be >= 1"
            )
        if self.amendment_threshold_denominator < 1:
            errors.append(
                "amendment_threshold_denominator="
                f"{self.amendment_threshold_denominator} must be >= 1"
            )
        if (
            self.amendment_threshold_numerator > 0
            and self.amendment_threshold_denominator > 0
            and self.amendment_threshold_numerator > self.amendment_threshold_denominator
        ):
            errors.append(
                "amendment_threshold_numerator "
                f"{self.amendment_threshold_numerator} cannot exceed denominator "
                f"{self.amendment_threshold_denominator} (threshold must be <= 100%)"
            )
        if self.election_interval_days <= 0:
            errors.append(f"election_interval_days={self.election_interval_days} must be > 0")
        for name, value in (
            ("candidacy_window_days", self.candidacy_window_days),
            ("campaign_period_days", self.campaign_period_days),
            ("voting_window_days", self.voting_window_days),
        ):
            if value <= 0:
                errors.append(f"{name}={value} must be > 0")
        window_total = (
            self.candidacy_window_days + self.campaign_period_days + self.voting_window_days
        )
        if window_total > self.election_interval_days:
            errors.append(
                f"candidacy_window_days + campaign_period_days + voting_window_days "
                f"({window_total}) exceeds election_interval_days ({self.election_interval_days}); "
                "an election could not finish before the next one is due"
            )
        if self.suffrage_min_age < 0:
            errors.append(f"suffrage_min_age={self.suffrage_min_age} must be >= 0")
        if self.term_limit_terms is not None and self.term_limit_terms < 1:
            errors.append(
                f"term_limit_terms={self.term_limit_terms} must be >= 1 or None (unlimited)"
            )
        for name, value in (
            ("patent_filing_fee_sim", self.patent_filing_fee_sim),
            ("permit_fee_sim", self.permit_fee_sim),
            ("builder_license_fee_sim", self.builder_license_fee_sim),
        ):
            if value < 0:
                errors.append(f"{name}={value} must be >= 0")
        if errors:
            raise ValueError("; ".join(errors))
        return self


AMENDABLE_CHARTER_FIELDS = frozenset(Charter.model_fields.keys()) - {"version", "genesis_tick"}


BusinessLicensing = Literal["open", "permit_required", "state_only"]
BUSINESS_LICENSING_MODES: tuple[str, ...] = ("open", "permit_required", "state_only")
BUSINESS_PERMIT_TYPE = "permit:business"


class GovernmentPolicy(BaseModel):
    """Day-to-day executive levers (DESIGN.md section 7: "Government
    powers: taxes, credit-store margin, jobs, permits, pensions, police,
    tenders").

    Unlike `Charter`, these don't need a 3/5 supermajority - DESIGN.md
    lists them as ordinary government powers exercised by "the sitting
    government" as a body, distinct from the explicit "charter amendment
    by 3/5 vote" clause that follows them in the same sentence. Any
    current seat holder (PM or minister) can set these directly; see
    `government.py`.

    Still versioned and snapshotted like `Charter`, same rationale: the
    tax-rate and pension-config history over time is itself research data
    (DESIGN.md section 10: "pension fiscal gap").
    """

    model_config = ConfigDict(frozen=True)

    version: int = 0
    tax_rate_bps: int = 0
    credit_store_margin_bps: int = 0
    pension_private_pct: int = 25
    pension_government_pct: int = 50
    pension_eligibility_age: int = 70
    price_caps: dict[str, int] = Field(default_factory=dict)
    price_floors: dict[str, int] = Field(default_factory=dict)
    business_licensing: BusinessLicensing = "open"

    @model_validator(mode="after")
    def check_invariants(self) -> GovernmentPolicy:
        errors: list[str] = []
        for item, cap in self.price_caps.items():
            if cap <= 0:
                errors.append(f"price cap for {item} must be above 0 sim, not {cap}")
        for item, floor in self.price_floors.items():
            if floor <= 0:
                errors.append(f"price floor for {item} must be above 0 sim, not {floor}")
            cap = self.price_caps.get(item)
            if cap is not None and floor > cap:
                errors.append(
                    f"the floor for {item} ({floor} sim) is above its cap ({cap} sim); "
                    "no price could be legal"
                )
        if not (0 <= self.tax_rate_bps <= 10_000):
            errors.append(f"tax_rate_bps={self.tax_rate_bps} must be within 0..10000 (0%..100%)")
        if not (0 <= self.credit_store_margin_bps <= 10_000):
            errors.append(
                f"credit_store_margin_bps={self.credit_store_margin_bps} must be within 0..10000"
            )
        if not (0 <= self.pension_private_pct <= 100):
            errors.append(f"pension_private_pct={self.pension_private_pct} must be within 0..100")
        if not (0 <= self.pension_government_pct <= 100):
            errors.append(
                f"pension_government_pct={self.pension_government_pct} must be within 0..100"
            )
        if self.pension_eligibility_age < 0:
            errors.append(f"pension_eligibility_age={self.pension_eligibility_age} must be >= 0")
        if errors:
            raise ValueError("; ".join(errors))
        return self


    def price_band_refusal(self, item: str, unit_price_sim: float) -> str | None:
        """Why this price for one unit of `item` is not allowed, or None.

        The one place the rule is written, so posting a price and paying one
        cannot disagree about it. A price of 0 is a gift (`trade` allows it)
        and no floor forbids giving something away.
        """
        cap = self.price_caps.get(item)
        if cap is not None and unit_price_sim > cap:
            return (
                f"the government caps {item} at {cap} sim a unit (policy v{self.version}); "
                f"{unit_price_sim:g} is above the cap"
            )
        floor = self.price_floors.get(item)
        if floor is not None and 0 < unit_price_sim < floor:
            return (
                f"the government sets a floor of {floor} sim a unit on {item} "
                f"(policy v{self.version}); {unit_price_sim:g} is below the floor"
            )
        return None


    def pension_pct_for(self, employment_type: Literal["private", "government"]) -> int:
        """Percentage of last salary owed to a retiree of this kind.

        DESIGN.md section 4 sets the genesis split (25% private, 50%
        government); both halves are policy a government may change.
        """
        if employment_type == "government":
            return self.pension_government_pct
        return self.pension_private_pct

    def is_pension_eligible(self, age_years: float) -> bool:
        """Has this resident reached the retirement age currently in force?"""
        return age_years >= self.pension_eligibility_age

    def pension_due_sim(
        self,
        last_salary_sim: int,
        employment_type: Literal["private", "government"],
        age_years: float,
    ) -> int:
        """Integer sim owed to this retiree for one pension period, 0 if
        they are not yet of age or never held a salary.

        Integer floor division, never a float: app/CONVENTIONS.md forbids
        floating-point currency, and a percentage of an integer salary is
        exactly where a float would creep in.
        """
        if last_salary_sim <= 0 or not self.is_pension_eligible(age_years):
            return 0
        return last_salary_sim * self.pension_pct_for(employment_type) // 100


class GovernmentState(BaseModel):
    """Who currently holds power. The atomic thing that changes on
    transfer of power (DESIGN.md section 7 election flow).

    `terms_served` counts PM and minister terms together per agent - the
    charter's `term_limit_terms` is a cap on total terms in *any* seat,
    not per-seat, so an agent who serves two terms as minister and then
    runs for PM is on their third term for eligibility purposes.
    """

    pm_agent_id: str | None = None
    minister_agent_ids: list[str] = Field(default_factory=list)
    terms_served: dict[str, int] = Field(default_factory=dict)
    caretaker: bool = False

    def is_sitting(self, agent_id: str) -> bool:
        return agent_id == self.pm_agent_id or agent_id in self.minister_agent_ids

    def is_caretaker_seat(self, agent_id: str) -> bool:
        """GROW-12: sitting, but by appointment rather than by election.

        Anything that measures political history should exclude these;
        anything that asks "may this resident act" should not care.
        """
        return self.caretaker and self.is_sitting(agent_id)

    def all_seat_holders(self) -> list[str]:
        holders = list(self.minister_agent_ids)
        if self.pm_agent_id is not None:
            holders = [self.pm_agent_id] + holders
        return holders


class EligibilityFacts(BaseModel):
    """Age and residency for one agent, supplied by the caller at call
    time.

    charter/ does not look this up itself, so suffrage checks take these
    as an explicit argument. GROW-30: the reason used to be that
    "world/agents doesn't exist yet". It does - `world/agents_api.py` -
    and the argument stays explicit to keep charter/ importable from the
    tool layer without a cycle. The tool dispatcher populates it from the
    agent record it already holds.
    """

    agent_id: str
    age_years: float
    is_resident: bool


class ElectionPhase(str, Enum):
    SCHEDULED = "scheduled"
    CANDIDACY_OPEN = "candidacy_open"
    CAMPAIGNING = "campaigning"
    VOTING_OPEN = "voting_open"
    TALLIED = "tallied"


class Candidacy(BaseModel):
    agent_id: str
    declared_tick: int


class ElectionResult(BaseModel):
    pm_agent_id: str | None
    minister_agent_ids: list[str]
    vote_counts: dict[str, int]
    tallied_tick: int


class Election(BaseModel):
    """One election cycle. Timestamps are ticks, derived from the charter
    that was in force when the election was scheduled (see
    `elections.py`'s `schedule_election`).
    """

    election_id: str = Field(default_factory=lambda: new_id("election"))
    charter_version_at_scheduling: int
    seats_at_scheduling: int
    scheduled_tick: int
    candidacy_opens_tick: int
    candidacy_closes_tick: int
    voting_opens_tick: int
    voting_closes_tick: int
    phase: ElectionPhase = ElectionPhase.SCHEDULED
    candidates: dict[str, Candidacy] = Field(default_factory=dict)
    ballots: dict[str, str] = Field(default_factory=dict)
    result: ElectionResult | None = None


class AmendmentStatus(str, Enum):
    PROPOSED = "proposed"
    PASSED_AWAITING_PRESIDENT = "passed_awaiting_president"
    ENACTED = "enacted"
    VETOED = "vetoed"
    REJECTED = "rejected"


class AmendmentProposal(BaseModel):
    """One proposed change to one `Charter` field.

    `new_value` is untyped at this layer on purpose: the amendment engine
    validates it by attempting to build the resulting `Charter` via
    `model_copy`, which is pydantic's own field validation, rather than
    duplicating a type table here that could drift from `Charter`'s real
    field types.
    """

    proposal_id: str = Field(default_factory=lambda: new_id("amend"))
    field_name: str
    new_value: Any
    title: str
    rationale: str
    proposed_by_agent_id: str
    proposed_tick: int
    votes: dict[str, bool] = Field(default_factory=dict)
    status: AmendmentStatus = AmendmentStatus.PROPOSED
    resulting_charter_version: int | None = None
    passed_tick: int | None = None
    enacted_tick: int | None = None
    vetoed_tick: int | None = None
    veto_note: str | None = None


class JobStatus(str, Enum):
    OPEN = "open"
    FILLED = "filled"
    CUT = "cut"


class Job(BaseModel):
    """A government job (DESIGN.md section 3: "government and businesses
    can create/cut jobs") or a police hire (section 6: "police (engines
    recruited on treasury salary)"). `hire_police` in government.py is
    sugar over this with `job_type="police"` and `assigned_agent_id` set
    immediately, since hiring implies filling in the same act.

    ECON-10: `salary_sim` used to be a number this record simply held.
    A government could hire the whole town and pay nobody, because nothing
    ever asked which jobs were owed money, for how many days, or whether a
    day had already been settled. `last_paid_tick` and `total_paid_sim`
    are what make the record answerable: the salary is per game-day, the
    period is closed only when a payer says it has been settled, and
    `payroll_periods_due` counts whole unpaid game-days from the last
    settled tick (or from the hire, for a job that has never been paid).
    Charter/ still moves no money - see the module docstring's import
    boundary - it says what is owed and remembers what was answered.
    """

    job_id: str = Field(default_factory=lambda: new_id("job"))
    title: str
    job_type: str
    salary_sim: int
    assigned_agent_id: str | None = None
    status: JobStatus = JobStatus.OPEN
    created_by_agent_id: str
    created_tick: int
    cut_by_agent_id: str | None = None
    cut_tick: int | None = None
    last_paid_tick: int | None = None
    total_paid_sim: int = 0

    @model_validator(mode="after")
    def check_invariants(self) -> Job:
        errors: list[str] = []
        if self.salary_sim < 0:
            errors.append(f"salary_sim={self.salary_sim} must be >= 0")
        if self.total_paid_sim < 0:
            errors.append(f"total_paid_sim={self.total_paid_sim} must be >= 0")
        if self.last_paid_tick is not None and self.last_paid_tick < self.created_tick:
            errors.append(
                f"last_paid_tick={self.last_paid_tick} precedes created_tick={self.created_tick}"
            )
        if errors:
            raise ValueError("; ".join(errors))
        return self


    def is_on_the_payroll(self) -> bool:
        """Does this job owe anyone anything? A vacant post and an unpaid
        (0 sim) post both answer no, and neither is an error: DESIGN.md
        section 3 lets a government post a job that sits open.
        """
        return self.assigned_agent_id is not None and self.salary_sim > 0

    def settled_through_tick(self) -> int:
        """Tick this job is paid up to: the last settled period, or the
        moment of hire for a job that has never been paid.
        """
        return self.created_tick if self.last_paid_tick is None else self.last_paid_tick

    def accrues_through_tick(self, now_tick: int) -> int:
        """Last tick that can be claimed for. A cut job stops accruing the
        moment it was cut, but the days it worked before that are still
        owed - otherwise cutting a post would be a way to erase the wages
        it had already earned.
        """
        if self.cut_tick is not None:
            return min(now_tick, self.cut_tick)
        return now_tick

    def payroll_periods_due(self, now_tick: int) -> int:
        """Whole unpaid game-days owed to the holder as of `now_tick`."""
        if not self.is_on_the_payroll():
            return 0
        elapsed = self.accrues_through_tick(now_tick) - self.settled_through_tick()
        return max(0, elapsed) // TICKS_PER_GAME_DAY

    def payroll_due_sim(self, now_tick: int) -> int:
        """Integer sim owed to the holder as of `now_tick`."""
        return self.payroll_periods_due(now_tick) * self.salary_sim


class PayrollLine(BaseModel):
    """One line of a government payroll run: who is owed, how much, and
    for which closed game-days (ECON-10).

    Frozen, and computed rather than stored: it is the answer to a
    question asked at a tick, not a record the store keeps. The payer
    moves `amount_sim` to `agent_id` and then calls back with `job_id` and
    `periods` so the same days are never paid twice.
    """

    model_config = ConfigDict(frozen=True)

    job_id: str
    agent_id: str
    title: str
    job_type: str
    salary_sim: int
    periods: int
    amount_sim: int
    settled_through_tick: int
    now_tick: int

    @model_validator(mode="after")
    def check_invariants(self) -> PayrollLine:
        errors: list[str] = []
        if self.periods < 1:
            errors.append(
                f"periods={self.periods} must be >= 1 (a line owing nothing is not a line)"
            )
        if self.amount_sim != self.periods * self.salary_sim:
            errors.append(
                f"amount_sim={self.amount_sim} is not periods={self.periods} "
                f"x salary_sim={self.salary_sim}"
            )
        if errors:
            raise ValueError("; ".join(errors))
        return self


class LicenseStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class License(BaseModel):
    """A permit or a builder license (DESIGN.md section 1: "issue permits
    and builder licenses"). The two are structurally identical - a
    revocable grant to one holder issued by government - so they share
    one model distinguished by `license_type`; `issue_permit` and
    `issue_builder_license` in government.py are named convenience
    wrappers over the same `issue_license`.
    """

    license_id: str = Field(default_factory=lambda: new_id("license"))
    license_type: str
    holder_agent_id: str
    fee_paid_sim: int
    issued_by_agent_id: str
    issued_tick: int
    status: LicenseStatus = LicenseStatus.ACTIVE
    revoked_by_agent_id: str | None = None
    revoked_tick: int | None = None
    revoke_reason: str | None = None


class TenderStatus(str, Enum):
    OPEN = "open"
    AWARDED = "awarded"
    CANCELLED = "cancelled"


class Tender(BaseModel):
    """A government tender (DESIGN.md section 3: businesses win "contracts
    from individuals/businesses and government tenders"). Charter/ owns
    posting and awarding, the government side of the transaction; bidding
    itself belongs to the business layer. GROW-30: that used to name a
    business module and call it unbuilt. There is still no such module -
    `Business` lives in `world/tools/models.py` and `civic.py` registers
    them - but what is actually missing is BIDDING, which is GROW-11 and
    a missing mechanism rather than a missing file.
    """

    tender_id: str = Field(default_factory=lambda: new_id("tender"))
    title: str
    description: str
    budget_sim: int
    posted_by_agent_id: str
    posted_tick: int
    status: TenderStatus = TenderStatus.OPEN
    awarded_to_agent_id: str | None = None
    awarded_tick: int | None = None
    cancel_reason: str | None = None

    @model_validator(mode="after")
    def check_invariants(self) -> Tender:
        if self.budget_sim < 0:
            raise ValueError(f"budget_sim={self.budget_sim} must be >= 0")
        return self


class PatentStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PatentApplication(BaseModel):
    """A filing at the patent office (DESIGN.md section 2: agent-invented
    tools "must be registered at the government patent office (fee;
    reviewable; revocable)"). `composed_of` is informational metadata
    about which existing primitives the invented tool composes - charter/
    has no tool registry to validate that list against yet, so it is
    recorded, not checked.
    """

    application_id: str = Field(default_factory=lambda: new_id("patent-app"))
    inventor_agent_id: str
    tool_name: str
    description: str
    composed_of: list[str] = Field(default_factory=list)
    fee_paid_sim: int
    filed_tick: int
    status: PatentStatus = PatentStatus.PENDING
    reviewed_by_agent_id: str | None = None
    reviewed_tick: int | None = None
    reject_reason: str | None = None


class Patent(BaseModel):
    """A granted, revocable patent. Invented tools are property (DESIGN.md
    section 2: "sellable, licensable, teachable") - `owner_agent_id` is
    mutable through a future sale/inheritance flow that is out of this
    module's scope; charter/ only tracks the grant and revocation.
    """

    patent_id: str = Field(default_factory=lambda: new_id("patent"))
    application_id: str
    tool_name: str
    owner_agent_id: str
    granted_by_agent_id: str
    granted_tick: int
    revoked: bool = False
    revoked_by_agent_id: str | None = None
    revoked_tick: int | None = None
    revoke_reason: str | None = None


class PetitionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class LandPetition(BaseModel):
    """A government petition to the President for land expansion
    (DESIGN.md section 1: "government petitions the President
    (the operator). On approval the app appends tiles to the Tiled JSON").

    charter/ owns filing and the approve/deny decision record only. It
    never touches the tilemap.

    GROW-30. This used to describe the map side as a separate land/map
    module that did not exist yet, expected to consume the
    `politics.petition.approved` event. It is built - `world/map/
    service.py`, `MapService.expand_land` - and it is not event-driven:
    `admin_api.py:684` calls it directly on the operator's approval. The
    event does have one consumer, `wiring.py`'s `record_petition_if_new`,
    which writes the `petitions` trace row; no consumer grows the map.

    The event name still stands for the reason it always did: this
    module records a DECISION, and `world.land.expanded` is the map
    module's own event for having actually grown (see app/CONVENTIONS.md's
    event domain table). What changed is that the second event is emitted
    by a direct call rather than by anything listening to the first.
    """

    petition_id: str = Field(default_factory=lambda: new_id("petition"))
    filed_by_agent_id: str
    title: str
    description: str
    tile_count_requested: int
    filed_tick: int
    status: PetitionStatus = PetitionStatus.PENDING
    decided_tick: int | None = None
    decision_note: str | None = None

    @model_validator(mode="after")
    def check_invariants(self) -> LandPetition:
        if self.tile_count_requested < 1:
            raise ValueError(f"tile_count_requested={self.tile_count_requested} must be >= 1")
        return self


class OrganizationKind(str, Enum):
    """DESIGN.md section 7: "Organizations first-class: parties, cults,
    societies, religions". Underground (unregistered) coordination is
    deliberately not a kind here - it is the *absence* of an
    `Organization` row, detectable elsewhere in the relationship graph
    (DESIGN.md: "unregistered coordination is detectable in the graph and
    is itself data"), not something this module models directly.
    """

    PARTY = "party"
    CULT = "cult"
    RELIGION = "religion"
    SOCIETY = "society"


OrganizationRole = Literal["founder", "member"]


class Organization(BaseModel):
    org_id: str = Field(default_factory=lambda: new_id("org"))
    kind: OrganizationKind
    name: str
    founder_agent_id: str
    charter_text: str
    founded_tick: int
    members: dict[str, OrganizationRole] = Field(default_factory=dict)
    active: bool = True


class ComplaintEntry(BaseModel):
    """One row of the immutable complaint ledger (DESIGN.md section 7:
    "Genesis checks are deliberately minimal: immutable complaint ledger +
    Presidential veto").

    This is the constitutional accountability instrument - a record the
    sitting government cannot edit or delete, filed against government
    power itself. It is a different thing from the crime-reporting
    "Complaints desk" in DESIGN.md section 6 (theft/assault reports that
    route to police investigation, case files, and warrants); that flow
    belongs to a future crime/ module under world/, not here.
    `ledger_index` is the append-only sequence position and is the
    immutability proof: entries are never renumbered or removed.
    """

    ledger_index: int
    complainant_agent_id: str
    against_id: str
    against_kind: str
    description: str
    filed_tick: int
