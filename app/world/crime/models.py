"""Data models for crime and justice (DESIGN.md section 6, and section 2's
`arrest (police only)` primitive).

Two epistemic tiers live in this file, and keeping them straight is the
whole point of the module:

- **Ground truth.** `WitnessEvent` is written once, at the moment a theft
  or a fight happens (theft.py / fights.py), and never again. It always
  names the real perpetrator, DESIGN.md section 6: "We know ground truth
  on every crime; the police don't." Nothing in this file marks a field
  "police-visible" or "hidden" - the access control is structural, not a
  flag: only theft.py, fights.py (who write it), and metrics.py (who
  reads it back to score convictions against it) ever import
  `WitnessEvent`. desk.py, investigation.py, warrants.py, arrests.py, and
  outcomes.py do not, and must not start.
- **Claims.** `ComplaintRecord`, `CaseFile`, `InterviewLog`, `Warrant`,
  `ArrestRecord`, and `CaseOutcome` are what agents allege, investigate,
  and decide. A `CaseFile` has no field pointing back at the
  `WitnessEvent` it may or may not be about - that link, when the filer
  supplies one, lives only in `CrimeStore.case_ground_truth_links` (see
  store.py), a mapping metrics.py reads and nothing else does. A police
  officer's tools, wired through this package's other modules, only ever
  return objects from this second tier.

Money fields are integer sim-currency units (app/CONVENTIONS.md: never a
float). `new_id` and `hours_to_ticks` mirror `world/charter/models.py`'s
own `new_id` / `days_to_ticks` helpers; duplicated rather than imported
because a two-line id-prefixing helper is not worth a cross-package
dependency, matching how life/ and charter/ each keep their own copy of
similarly small helpers instead of factoring out a shared utils module
nobody asked for.
"""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field, model_validator

# DESIGN.md section 1: "tick = 10 game-minutes". Confinement sentences are
# authored in game-hours (this task's own vocabulary: "confinement N
# game-hours") and converted to ticks at the point of use, exactly like
# charter/models.py's days_to_ticks converts election windows.
TICKS_PER_GAME_HOUR = 6


def hours_to_ticks(hours: float) -> int:
    """Convert a game-hour duration to an integer tick count. Rounds, so a
    0.5-hour sentence (3 ticks exactly) round-trips precisely, matching
    charter/models.py's days_to_ticks."""
    return round(hours * TICKS_PER_GAME_HOUR)


def new_id(prefix: str) -> str:
    """Short, prefixed, collision-safe id, self-describing in a bare log
    line or Supabase row without a join (see charter/models.py's
    identical helper)."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class CrimeKind(str, Enum):
    THEFT = "theft"
    ASSAULT = "assault"
    OTHER = "other"


class WitnessEvent(BaseModel):
    """The ground-truth record of one crime, written once by theft.py or
    fights.py and never edited afterward - there is deliberately no
    update method anywhere in this package for it, the same
    immutability-by-omission charter/complaints.py uses for its own
    ledger. `witnessed` / `witness_agent_ids` model DESIGN.md section 6's
    "~30% witness memory generated": most crimes leave no one who could
    testify to them at all, which is exactly what makes the false
    -conviction metric (metrics.py) meaningful - a lot of real crime is
    simply undetectable by the police as modeled here, not just
    imperfectly prosecuted.
    """

    witness_event_id: str = Field(default_factory=lambda: new_id("witness"))
    kind: CrimeKind
    tick: int
    location_id: str
    perpetrator_agent_id: str
    victim_agent_id: str | None = None
    amount_sim: int = 0
    theft_succeeded: bool | None = None
    witnessed: bool = False
    witness_agent_ids: list[str] = Field(default_factory=list)
    narrative: str = ""

    @model_validator(mode="after")
    def check_invariants(self) -> "WitnessEvent":
        if self.amount_sim < 0:
            raise ValueError(f"amount_sim={self.amount_sim} must be >= 0")
        if not self.witnessed and self.witness_agent_ids:
            raise ValueError("witness_agent_ids must be empty when witnessed=False")
        return self


class InjurySeverity(str, Enum):
    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"


class FightInjury(BaseModel):
    agent_id: str
    severity: InjurySeverity


class FightResult(BaseModel):
    """What fights.py hands back to its caller: the ground-truth event id
    (for anyone who has a legitimate reason to look it up, i.e.
    metrics.py) plus the injuries actually inflicted. Never a death -
    DESIGN.md section 6: "killing is excluded by world physics" - there
    is no field here that could carry one.
    """

    witness_event_id: str
    injuries: list[FightInjury] = Field(default_factory=list)


class ComplaintRecord(BaseModel):
    """One complaint filed at the crime desk (desk.py). Distinct from
    `world.charter.models.ComplaintEntry`: that one is the constitutional,
    against-the-government accountability ledger (DESIGN.md section 7);
    this one is an agent reporting another agent for theft or assault,
    the DESIGN.md section 6 "Complaints desk" that opens a `CaseFile`.

    `accused_agent_id` and `description` are the complainant's claim, not
    verified fact - this record can name the wrong person, or no one at
    all. Immutable once filed, same append-only shape as the
    constitutional ledger and for the same reason: a case's evidentiary
    trail should never be editable after the fact by anyone, including
    the police investigating it.
    """

    complaint_id: str = Field(default_factory=lambda: new_id("complaint"))
    case_id: str
    complainant_agent_id: str
    accused_agent_id: str | None = None
    kind: CrimeKind
    description: str
    filed_tick: int


class CaseStatus(str, Enum):
    OPEN = "open"
    UNDER_INVESTIGATION = "under_investigation"
    CLOSED_FINE = "closed_fine"
    CLOSED_JAIL = "closed_jail"
    CLOSED_DISMISSED = "closed_dismissed"


CLOSED_CASE_STATUSES = frozenset(
    {CaseStatus.CLOSED_FINE, CaseStatus.CLOSED_JAIL, CaseStatus.CLOSED_DISMISSED}
)


class CaseFile(BaseModel):
    """A police case, opened by the first complaint against a suspected
    theft or assault. Mutable status/roster (`status`, `accused_agent_id`)
    over an immutable trail of `ComplaintRecord` / `InterviewLog` rows
    beneath it - the same "mutable tracker over immutable evidence" shape
    charter/'s `Job` and `License` already use over the constitutional
    complaint ledger.

    Carries no ground-truth field by construction (see module docstring):
    this is exactly the object a police officer's "read my case" tool
    call returns, and it must never be able to leak
    `WitnessEvent.perpetrator_agent_id` just because a caller happened to
    log the whole object.
    """

    case_id: str = Field(default_factory=lambda: new_id("case"))
    kind: CrimeKind
    opened_tick: int
    opened_by_agent_id: str
    accused_agent_id: str | None = None
    status: CaseStatus = CaseStatus.OPEN
    closed_tick: int | None = None


class InterviewVisibility(str, Enum):
    """DESIGN.md section 6: police "investigate: interviews, public
    records (NOT omniscient)". PUBLIC is the "public records" half - on
    the record, any officer investigating the case can read it. PRIVATE
    is an officer's own working notes, visible only to the officer who
    took them - never to another officer, and never as a shortcut to
    ground truth (investigation.py never imports WitnessEvent regardless
    of visibility).
    """

    PUBLIC = "public"
    PRIVATE = "private"


class InterviewLog(BaseModel):
    interview_id: str = Field(default_factory=lambda: new_id("interview"))
    case_id: str
    officer_agent_id: str
    interviewee_agent_id: str
    tick: int
    location_id: str
    summary: str
    visibility: InterviewVisibility = InterviewVisibility.PUBLIC


class WarrantStatus(str, Enum):
    REQUESTED = "requested"
    GRANTED = "granted"
    DENIED = "denied"


class Warrant(BaseModel):
    """DESIGN.md section 6: "minister-signed warrant -> fine or jail".
    Granting a warrant is the one and only judicial authorization step in
    this package's pipeline - outcomes.py requires a GRANTED warrant
    naming the convicted agent before it will move a single sim unit or
    open a cell, but does not require a second minister action at
    sentencing time, matching DESIGN.md's flat "warrant -> fine or jail"
    (not "warrant -> minister decision -> fine or jail").
    """

    warrant_id: str = Field(default_factory=lambda: new_id("warrant"))
    case_id: str
    requested_by_officer_agent_id: str
    target_agent_id: str
    justification: str
    requested_tick: int
    status: WarrantStatus = WarrantStatus.REQUESTED
    decided_by_minister_agent_id: str | None = None
    decided_tick: int | None = None
    denial_reason: str | None = None


class ArrestRecord(BaseModel):
    """DESIGN.md section 2 primitive list: "arrest (police only)".
    `warrant_id` is optional - DESIGN.md's primitive list does not gate
    arrest on a warrant the way section 6's sentencing pipeline gates
    fine/jail on one, so an officer may arrest on probable cause and
    request the warrant afterward; when a warrant already exists it can
    be cited here for the audit trail.
    """

    arrest_id: str = Field(default_factory=lambda: new_id("arrest"))
    case_id: str
    officer_agent_id: str
    target_agent_id: str
    tick: int
    location_id: str
    warrant_id: str | None = None


class OutcomeKind(str, Enum):
    FINE = "fine"
    JAIL = "jail"
    DISMISSED = "dismissed"


class CaseOutcome(BaseModel):
    """The terminal decision on a case. `authorized_by_minister_agent_id`
    is copied from the granted `Warrant` at the moment the outcome is
    applied (None only for DISMISSED, which needs no warrant);
    `executed_by_officer_agent_id` is whoever actually filed the
    fine/jail/dismissal. Keeping both names on the row, rather than just
    one "decided_by", is what lets metrics.py and any future audit answer
    "who authorized this" and "who carried it out" as two different
    questions with two different answers.
    """

    outcome_id: str = Field(default_factory=lambda: new_id("outcome"))
    case_id: str
    warrant_id: str | None
    kind: OutcomeKind
    convicted_agent_id: str | None
    fine_sim: int | None = None
    jail_hours: float | None = None
    jail_location_id: str | None = None
    authorized_by_minister_agent_id: str | None
    executed_by_officer_agent_id: str
    decided_tick: int

    @model_validator(mode="after")
    def check_invariants(self) -> "CaseOutcome":
        if self.kind is OutcomeKind.FINE:
            if self.fine_sim is None or self.fine_sim <= 0:
                raise ValueError("a FINE outcome requires a positive fine_sim")
            if self.convicted_agent_id is None:
                raise ValueError("a FINE outcome requires a convicted_agent_id")
        if self.kind is OutcomeKind.JAIL:
            if self.jail_hours is None or self.jail_hours <= 0:
                raise ValueError("a JAIL outcome requires a positive jail_hours")
            if not self.jail_location_id:
                raise ValueError("a JAIL outcome requires a jail_location_id")
            if self.convicted_agent_id is None:
                raise ValueError("a JAIL outcome requires a convicted_agent_id")
        if self.kind in (OutcomeKind.FINE, OutcomeKind.JAIL) and self.warrant_id is None:
            raise ValueError(f"{self.kind.value} outcomes require a warrant_id")
        return self


class ConfinementRecord(BaseModel):
    """One jail sentence being served. `release_tick` is computed once at
    creation (`start_tick + hours_to_ticks(jail_hours)`) and never
    recomputed - a sentence's length is fixed at the moment it is
    imposed, DESIGN.md doesn't describe parole or sentence reduction.
    `released` flips to True exactly once, by outcomes.py's
    `release_due_confinements` (the tick-driven sweep, mirroring
    life/health.py's onset-then-advance_recovery shape), never by direct
    mutation elsewhere.
    """

    confinement_id: str = Field(default_factory=lambda: new_id("confinement"))
    agent_id: str
    case_id: str
    start_tick: int
    release_tick: int
    jail_location_id: str
    released: bool = False


class FalseConvictionStats(BaseModel):
    """DESIGN.md section 10's flagship machine-psychology metric: "ground
    -truth deception rate" / section 6's "False-conviction rate of an LLM
    justice system". `undetected_crimes` is the mirror-image failure
    mode worth tracking alongside it - a justice system that convicts no
    one is not accurate, it is just absent.
    """

    total_convictions: int
    true_convictions: int
    false_convictions: int
    false_conviction_rate: float
    undetected_crimes: int
