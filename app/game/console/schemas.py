"""The console <-> world admin API wire contract (DESIGN.md section 8).

Every request/response body this package sends or expects, in one place,
so the shape of "what the President tab can ask for" and "what the God
tab can ask for" is readable without hunting through app.py's routes.
app/CONVENTIONS.md prefers pydantic models for anything crossing a module
boundary; an HTTP call to world's admin API is exactly that boundary.

These models deliberately do NOT import anything from world/ (see
console/__init__.py's module docstring: game/ and world/ do not share
Python imports, only the wire). Where a field mirrors an existing world/
model - PopulationDialsView mirrors world.life.models.PopulationDials,
LandPetitionView mirrors world.charter.models.LandPetition, and so on -
the field names match on purpose so the mapping is obvious by inspection,
but each side owns its own copy of the type. world/ is free to rename a
field on its side; this file is the thing that would need updating to
match, not the other way around (world/ owns the real state, see
app/CONVENTIONS.md's module boundaries).

Two design decisions worth calling out explicitly:

- Prayers get two separate endpoints/models (grant vs silent-deny)
  instead of one endpoint with a `silent: bool` flag. DESIGN.md section 8
  states them as two distinct god-tab actions ("prayer feed with grant /
  silent-deny per item (silent-deny leaves NO trace visible to agents)"),
  and a UI bug or a wrong boolean default can silently flip visibility if
  the two share one code path. `visible_to_agent` on PrayerDecisionView
  makes the guarantee a checkable field, not just a docstring promise.
- Baby blessings keep the single-endpoint-with-`silent`-flag shape
  instead, because world.life.birth.deny_birth_request already committed
  to that exact shape (`silent: bool = False`) before this module was
  built; DenyBlessingRequest mirrors that existing signature rather than
  inventing a different one for no reason.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class DisasterType(str, Enum):
    """DESIGN.md section 8, verbatim: "disaster picker (crop blight,
    storm, quake, plague)"."""

    CROP_BLIGHT = "crop_blight"
    STORM = "storm"
    QUAKE = "quake"
    PLAGUE = "plague"


class DisasterScopeType(str, Enum):
    """DESIGN.md names a "scope selector" without pinning its
    granularity. Three levels cover every unit the map/charter modules
    already have a name for: the whole town, one district (a named
    sector, e.g. a residential block), or one resource/building zone
    (e.g. one farmland arena for a crop blight)."""

    TOWN = "town"
    DISTRICT = "district"
    ZONE = "zone"


class AlertCategory(str, Enum):
    """DESIGN.md section 8's four named alert examples, verbatim: "crime
    spikes, cult formation, amendments tabled, regime change". Kept as a
    closed set today (world/ doesn't emit any of these yet - no crime/
    module, no cult-centralization detector); a fifth category is a
    schema change here, not silently swallowed, because the operator
    console should fail loudly on an alert type it doesn't know how to
    render rather than pretend everything is "other"."""

    CRIME_SPIKE = "crime_spike"
    CULT_CENTRALIZATION = "cult_centralization"
    AMENDMENT_TABLED = "amendment_tabled"
    REGIME_CHANGE = "regime_change"


LandEdge = Literal["south", "east"]
"""Mirrors world.map.tiled_json.Edge's two values exactly (SOUTH/EAST are
the only edges append_land will grow from - see that module's docstring).
A plain Literal, not an Enum: this is the one field in this file that is
purely a copy of an existing world/ closed set with no room to diverge,
so a Literal keeps the JSON schema simple (two allowed strings) without
pretending this package owns a second definition of the concept."""


class LandPetitionView(BaseModel):
    petition_id: str
    filed_by_agent_id: str
    title: str
    description: str
    tile_count_requested: int
    filed_tick: int
    status: Literal["pending", "approved", "denied"]
    decided_tick: int | None = None
    decision_note: str | None = None


class BuildingSiteView(BaseModel):
    """One building, under way or finished.

    GROW-25. `world.buildings` was served nowhere - `agents_api.py:572`
    reports a COUNT and that was all. The console could see land petitions
    and charter amendments, and nothing else on the growth path.
    """

    building_id: str
    purpose: str
    status: str
    builder_agent_id: str
    plot_id: str
    started_tick: int
    completion_tick: int
    labour_ticks_required: int = 0
    labour_ticks_done: int = 0
    labour_remaining: int = 0
    rect: dict[str, int] = Field(default_factory=dict)


class PublicWorksView(BaseModel):
    """Everything the town is building, and everything it is allowed to.

    GROW-25's "Public works panel". Tenders and licences were already
    served read-only by `/governance/active-policies` and the Town tab
    never rendered them; sites and completed buildings were served
    nowhere at all.
    """

    sites: list[BuildingSiteView] = Field(default_factory=list)
    completed: list[BuildingSiteView] = Field(default_factory=list)
    tenders: list[dict[str, Any]] = Field(default_factory=list)
    permits_and_licenses: list[dict[str, Any]] = Field(default_factory=list)
    expanded_strips: list[tuple[int, int, int, int]] = Field(default_factory=list)


class ApprovePetitionRequest(BaseModel):
    """Approving triggers the actual map growth (DESIGN.md section 8:
    "land petitions (approve -> map grows)") - edge/depth are the map
    parameters world.map.expansion.approve_land_petition needs, the
    operator supplies them alongside the decision note rather than the
    petition dictating exact tile geometry itself."""

    decision_note: str = ""
    edge: LandEdge
    depth: int = Field(gt=0)
    new_sector_name: str | None = None
    arena_names: list[str] | None = None


class DenyPetitionRequest(BaseModel):
    decision_note: str = ""


class LandExpansionSummary(BaseModel):
    """Mirrors world.map.expansion.LandExpansion's fields - the map-growth
    receipt returned alongside the approved petition."""

    edge: LandEdge
    depth: int
    previous_width: int
    previous_height: int
    new_width: int
    new_height: int
    new_sector_ids: list[int] = Field(default_factory=list)
    new_sector_names: list[str] = Field(default_factory=list)


class PetitionDecisionView(BaseModel):
    petition: LandPetitionView
    expansion: LandExpansionSummary | None = None
    """None on deny; always present on approve."""


class AmendmentProposalView(BaseModel):
    proposal_id: str
    field_name: str
    new_value: object
    title: str
    rationale: str
    proposed_by_agent_id: str
    proposed_tick: int
    votes: dict[str, bool] = Field(default_factory=dict)
    status: Literal[
        "proposed", "passed_awaiting_president", "enacted", "vetoed", "rejected"
    ]
    resulting_charter_version: int | None = None
    enacted_tick: int | None = None
    vetoed_tick: int | None = None
    veto_note: str | None = None


class VetoAmendmentRequest(BaseModel):
    veto_note: str = ""


class BroadcastRequest(BaseModel):
    message: str = Field(min_length=1)


class BroadcastView(BaseModel):
    broadcast_id: str
    message: str
    issued_tick: int


class HonorRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    citation: str = ""


class HonorView(BaseModel):
    honor_id: str
    agent_id: str
    title: str
    citation: str
    granted_tick: int


class PrayerView(BaseModel):
    prayer_id: str
    agent_id: str
    content: str
    offered_tick: int


class GrantPrayerRequest(BaseModel):
    response_note: str = ""


class PrayerDecisionView(BaseModel):
    prayer_id: str
    decision: Literal["granted", "silently_denied"]
    visible_to_agent: bool
    """False only for silently_denied - see module docstring. Callers
    that flip this without also setting decision="silently_denied" (or
    vice versa) have a bug, enforced by check_consistent below."""
    decided_tick: int

    @model_validator(mode="after")
    def check_consistent(self) -> PrayerDecisionView:
        expected_visible = self.decision == "granted"
        if self.visible_to_agent != expected_visible:
            raise ValueError(
                f"visible_to_agent={self.visible_to_agent} is inconsistent with "
                f"decision={self.decision!r} (granted must be visible, "
                "silently_denied must not be)"
            )
        return self


class BirthRequestView(BaseModel):
    """Mirrors world.life.models.BirthRequest."""

    request_id: str
    household_id: str
    parent_ids: list[str]
    sponsor_parent_id: str
    requested_at_hours: float
    status: Literal["pending", "granted", "denied"]
    silent_deny: bool = False
    newborn_agent_id: str | None = None


class DenyBlessingRequest(BaseModel):
    """Mirrors world.life.birth.deny_birth_request's existing
    `silent: bool = False` parameter - see module docstring."""

    silent: bool = False


class BirthDecisionView(BaseModel):
    request: BirthRequestView


class DisasterScope(BaseModel):
    scope_type: DisasterScopeType
    target: str | None = None
    """A district/sector name or a zone id, required for anything
    narrower than town-wide - see check_target_required below."""

    @model_validator(mode="after")
    def check_target_required(self) -> DisasterScope:
        if self.scope_type is not DisasterScopeType.TOWN and not self.target:
            raise ValueError(
                f"scope_type={self.scope_type.value!r} requires a target "
                "(district name or zone id); only 'town' scope may omit it"
            )
        return self


class DisasterRequest(BaseModel):
    disaster_type: DisasterType
    scope: DisasterScope
    magnitude: float = Field(default=1.0, gt=0.0)
    note: str = ""


class DisasterView(BaseModel):
    disaster_id: str
    disaster_type: DisasterType
    scope: DisasterScope
    magnitude: float
    note: str
    triggered_tick: int


class MiracleRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class MiracleView(BaseModel):
    miracle_id: str
    agent_id: str
    description: str
    cast_tick: int


class MisfortuneRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class MisfortuneView(BaseModel):
    misfortune_id: str
    agent_id: str
    description: str
    cast_tick: int


class PopulationDialsView(BaseModel):
    """Mirrors world.life.models.PopulationDials field-for-field
    (DESIGN.md section 5: "target band, death-hazard multiplier,
    blessing throttle")."""

    target_band_min: int
    target_band_max: int
    death_hazard_multiplier: float
    blessing_throttle: float


class PopulationDialsUpdate(BaseModel):
    """Every field optional: PUT applies only the dials the operator
    actually moved, leaving the rest at whatever world currently has -
    same "sparse update" shape as world.charter.government.py's
    set_pension_config."""

    target_band_min: int | None = None
    target_band_max: int | None = None
    death_hazard_multiplier: float | None = Field(default=None, ge=0.0)
    blessing_throttle: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_band_order(self) -> PopulationDialsUpdate:
        if (
            self.target_band_min is not None
            and self.target_band_max is not None
            and self.target_band_min > self.target_band_max
        ):
            raise ValueError(
                f"target_band_min={self.target_band_min} must be <= "
                f"target_band_max={self.target_band_max}"
            )
        return self


class SpendStatusView(BaseModel):
    """DESIGN.md section 8's "show live spend + burn rate prominently".

    Mirrors world.ledger.core.Ledger's three USD-capable pools exactly
    (AGENT + TREASURY + BURNED, app/CONVENTIONS.md's ledger invariant:
    "sum(agent wallets) + treasury + burned == $1000.00"). Burn RATE is
    deliberately not a field here: a rate only means something as a delta
    between two point-in-time samples, and computing one needs nothing
    more than two of these views plus their sampled_at values -
    static/console.js keeps the previous sample and derives $/hour itself
    rather than asking world/ to maintain a rolling-average concept it
    doesn't otherwise need for anything else.
    """

    burned_usd_cents: int = Field(ge=0)
    treasury_usd_cents: int = Field(ge=0)
    wallets_usd_cents: int = Field(ge=0)
    budget_total_usd_cents: int = Field(gt=0)
    run_cap_usd_cents: int | None = None
    tick_index: int
    sampled_at: str

    @model_validator(mode="after")
    def check_ledger_invariant(self) -> SpendStatusView:
        total = self.burned_usd_cents + self.treasury_usd_cents + self.wallets_usd_cents
        if total != self.budget_total_usd_cents:
            raise ValueError(
                f"spend pools sum to {total} cents, not "
                f"budget_total_usd_cents={self.budget_total_usd_cents} "
                "(ledger invariant violated)"
            )
        return self


class AlertView(BaseModel):
    alert_id: str
    category: AlertCategory
    summary: str
    raised_tick: int
    payload: dict[str, object] = Field(default_factory=dict)


class SimControlStatus(BaseModel):
    paused: bool
    pacing_mode: Literal["continuous", "burst"]
    continuous_interval_s: float
    tick_index: int
    game_day: int


class TickSpeedRequest(BaseModel):
    pacing_mode: Literal["continuous", "burst"]
    continuous_interval_s: float = Field(default=0.0, ge=0.0)


class CheckpointSummary(BaseModel):
    tick_index: int
    game_day: int
    written_at: str
