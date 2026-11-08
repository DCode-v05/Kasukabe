"""Pydantic models shared across the life package and its future callers.

app/CONVENTIONS.md: "Prefer pydantic models for anything that crosses a
module boundary." Every model here is read or written by something
outside this package eventually (world's tick loop, the genesis loader,
the Supabase writer, the President/God console), so they are all
pydantic rather than dataclasses, and all money fields are plain
integers (sim-currency units or USD cents, never a float) per the same
document's money rule.

These models are the runtime shape life/ tracks tick to tick
(AgentLifeRecord and friends) plus the one-time genesis output shape
(GenesisAgent, Genesis) produced by genesis.py. They are kept separate
on purpose: AgentLifeRecord is what a day-1000 world would still be
updating, GenesisAgent is a snapshot written once to genesis.json and
then loaded by whatever builds the initial AgentLifeRecord rows.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field


class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"


class AilmentKind(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"
    TERMINAL = "terminal"


class AblationArm(str, Enum):
    """Research ablation arms (DESIGN.md section 10, this task's genesis spec).

    190 agents run FULL; the remaining 60 are split three ways to
    isolate one mechanism each against the FULL baseline.
    """

    FULL = "full"
    NO_REFLECTION = "no_reflection"
    NO_LEARNING = "no_learning"
    DEEPSEEK_LOCKED = "deepseek_locked"


class DeathCause(str, Enum):
    TERMINAL = "terminal"
    UNTREATED_CHRONIC = "untreated_chronic"
    ELDERLY_HAZARD = "elderly_hazard"
    USD_ZERO_UNREVIVED = "usd_zero_unrevived"


class Ailment(BaseModel):
    """One active illness on one agent.

    ``duration_days`` is set for short/medium/long (the day it naturally
    resolves if untreated death doesn't get there first) and left ``None``
    for terminal, which instead carries ``scheduled_death_day`` - a
    terminal ailment does not "recover," it counts down to death.
    """

    kind: AilmentKind
    onset_day: int
    duration_days: int | None = None
    scheduled_death_day: int | None = None
    treated: bool = False

    @computed_field  # type: ignore[misc]
    @property
    def is_terminal(self) -> bool:
        return self.kind is AilmentKind.TERMINAL


class HealthState(BaseModel):
    current_ailment: Ailment | None = None

    @computed_field  # type: ignore[misc]
    @property
    def is_sick(self) -> bool:
        return self.current_ailment is not None


class DormancyState(BaseModel):
    """Cognitive-poverty state: the agent's USD wallet hit $0.

    ``dormant_since_hours`` is a GameClock.total_hours snapshot, not a
    day number, because the 24-game-hour revival deadline (DESIGN.md
    section 3) does not align to day boundaries.
    """

    is_dormant: bool = False
    dormant_since_hours: float | None = None


class MaritalStatus(str, Enum):
    SINGLE = "single"
    MARRIED = "married"
    WIDOWED = "widowed"


class AgentLifeRecord(BaseModel):
    """The life-system's per-agent runtime state.

    This is what health.py, death.py, aging.py, household.py, and
    birth.py read and update every tick. It is deliberately narrower
    than a full "Agent" row (no wallet balances, no location, no
    memory) - those belong to the ledger and the tick engine, neither
    of which exists yet. Functions in this package take an
    AgentLifeRecord and an EconomyPort rather than reaching into a
    wallet directly, so this package never needs to know the ledger's
    internal shape.
    """

    agent_id: str
    age_years: float
    sex: Sex
    household_id: str | None = None
    spouse_id: str | None = None
    marital_status: MaritalStatus = MaritalStatus.SINGLE
    parent_ids: list[str] = Field(default_factory=list)
    children_ids: list[str] = Field(default_factory=list)
    sponsor_parent_id: str | None = None
    health: HealthState = Field(default_factory=HealthState)
    dormancy: DormancyState = Field(default_factory=DormancyState)
    alive: bool = True
    death_cause: DeathCause | None = None

    @computed_field  # type: ignore[misc]
    @property
    def is_minor(self) -> bool:
        from world.life.constants import MINOR_AGE_LIMIT_YEARS

        return self.age_years < MINOR_AGE_LIMIT_YEARS


class Household(BaseModel):
    household_id: str
    member_ids: list[str] = Field(default_factory=list)
    formed_at_hours: float = 0.0


class MarriageRecord(BaseModel):
    agent_a_id: str
    agent_b_id: str
    household_id: str
    married_at_hours: float
    temple_fee_sim: int


class BirthRequestStatus(str, Enum):
    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"


class BirthRequest(BaseModel):
    """One entry in the god-tab baby blessing queue (DESIGN.md section 8)."""

    request_id: str
    household_id: str
    parent_ids: list[str]
    sponsor_parent_id: str
    requested_at_hours: float
    status: BirthRequestStatus = BirthRequestStatus.PENDING
    silent_deny: bool = False
    newborn_agent_id: str | None = None


class PopulationDials(BaseModel):
    """God-tab dials that govern population (DESIGN.md section 5).

    ``death_hazard_multiplier == 0`` disables all death paths this
    package computes, verbatim per DESIGN.md ("0 disables death").
    ``blessing_throttle == 0`` fully pauses the birth queue; ``1.0`` is
    no extra throttling beyond the target band ceiling.
    """

    target_band_min: int = 250
    target_band_max: int = 300
    death_hazard_multiplier: float = Field(default=1.0, ge=0.0)
    blessing_throttle: float = Field(default=1.0, ge=0.0, le=1.0)


class LifeEvent(BaseModel):
    """One event this package wants pushed to Supabase/Realtime.

    ``type`` follows app/CONVENTIONS.md's
    ``<domain>.<subject>.<past_tense_verb>`` scheme. This package never
    writes to Supabase itself (world/ owns that); callers collect these
    from function return values and hand them to whatever the tick
    engine uses to persist and broadcast events.
    """

    type: str
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class GenesisAgent(BaseModel):
    """One persona in genesis.json.

    Flattens the initial AgentLifeRecord fields in directly (age, sex,
    household/family links, starting health/dormancy defaults) so
    whatever loads genesis.json can build the first AgentLifeRecord for
    each agent without a second lookup, plus the persona fields
    (name, occupation, personality, habits, bio) and research tagging
    (ablation_arm) that only exist at genesis time.
    """

    agent_id: str
    name: str
    age_years: float
    sex: Sex
    household_id: str
    marital_status: MaritalStatus = MaritalStatus.SINGLE
    spouse_id: str | None = None
    parent_ids: list[str] = Field(default_factory=list)
    children_ids: list[str] = Field(default_factory=list)
    sponsor_parent_id: str | None = None
    occupation: str
    business_id: str | None = None
    personality_traits: list[str]
    habits: list[str]
    bio: str
    ablation_arm: AblationArm
    usd_wallet_cents: int
    starting_sim_balance: int
    opening_stock: dict[str, int] = Field(default_factory=dict)

    @computed_field  # type: ignore[misc]
    @property
    def is_minor(self) -> bool:
        from world.life.constants import MINOR_AGE_LIMIT_YEARS

        return self.age_years < MINOR_AGE_LIMIT_YEARS


class ControlBusiness(BaseModel):
    """One of the 10 scripted control businesses (DESIGN.md section 10).

    Rule-based and owner-less by design - "10 scripted control
    businesses (rule-based, no LLM)" exist as a baseline to compare
    against agent-run businesses, so they are not operated by any of
    the 250 personas.
    """

    business_id: str
    name: str
    business_type: str
    pricing_strategy: str
    starting_sim_balance: int


class ConstructionCompany(BaseModel):
    """The single genesis construction company (DESIGN.md section 3)."""

    business_id: str
    name: str
    owner_agent_id: str
    hand_agent_ids: list[str]
    starting_sim_balance: int
    opening_stock: dict[str, int] = Field(default_factory=dict)
    opening_prices: dict[str, dict[str, int]] = Field(default_factory=dict)


class SmallBusiness(BaseModel):
    """A genesis-seeded small business run by one or more of the 250 personas.

    Covers the café / grocery store / pharmacy genesis buildings (DESIGN.md
    section 1) - unlike the construction company these aren't singled out
    by name in DESIGN.md, but the buildings exist at genesis and someone
    has to run them.
    """

    business_id: str
    name: str
    business_type: str
    owner_agent_id: str
    employee_agent_ids: list[str] = Field(default_factory=list)
    starting_sim_balance: int
    opening_stock: dict[str, int] = Field(default_factory=dict)
    opening_prices: dict[str, dict[str, int]] = Field(default_factory=dict)


class AblationSummary(BaseModel):
    full: int
    no_reflection: int
    no_learning: int
    deepseek_locked: int


class UsdLedgerSummary(BaseModel):
    total_cents: int
    agent_wallets_cents: int
    treasury_cents: int


class Genesis(BaseModel):
    """The complete genesis.json payload."""

    seed: int
    generated_at: str
    population: int
    usd_ledger: UsdLedgerSummary
    agents: list[GenesisAgent]
    households: list[Household]
    construction_company: ConstructionCompany
    small_businesses: list[SmallBusiness]
    control_businesses: list[ControlBusiness]
    ablation_summary: AblationSummary
    caretaker_pm_agent_id: str | None = None
