"""Part D: set_tax_rate, declare_candidacy, convene, appoint_police, arrest,
rule, release.

`vote`, `petition`, `pray`, `file_complaint`, `register_org` and the
patent office have existed for the whole series and have **never been
called**. That is not apathy. There was no `arrest`, no police, no court
verdict, and - until the day jobs were wired - no tax to resent. Nothing
gave a government something to do or a voter something to want.

Part A is what changes that: a tax bill lands, wages go unpaid, and a
complaint has a subject. These are the surfaces the resulting grievance
can reach.

**THE WORDING HERE IS PROVISIONAL, AND THE SPEC SAYS SO.** The tool-text
addendum's section 12: "Text for set_tax_rate, declare_candidacy,
convene, table_amendment, appoint_police, arrest, rule and release is
written after the first full-day run with tax charged, because what the
first tax bill does to the town decides what these need to say." No such
run has happened. Every description below follows section 0's four rules
- name the physics, then the sibling; never the strategy; refusals teach;
results are facts the caller did not have - and each is due a rewrite
against what the first tax day actually produces. Do not read the current
phrasing as settled.

`table_amendment` and `vote_amendment` were deliberately absent, on the
grounds that "specifying it against a charter nobody has yet tried to
amend would be guessing twice". That was reasonable and wrong in one
direction: nobody had tried BECAUSE there was no way to. GOV-03 -
`amendments.cast_amendment_vote` has zero callers anywhere in the tree,
and the only proposer is `skillify` filing a `standing_orders` change that
then sits PROPOSED forever.

So DESIGN section 7's "charter amendment by three-fifths vote, including
election rules and collapse to one-person rule" is false at runtime, and
`charter/models.py:20-27`'s claim that one-person rule is reachable is not
true of any run. The engine underneath is complete and tested; it has only
ever lacked a door.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord
from world.tools.errors import (
    ActorRequirementError,
    LocationGateError,
    NotFoundError,
    ToolValidationError,
)
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX, LOCATION_TOWN_HALL
from world.tools.place_aliases import place_for_charter, satisfies_gate
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.models import CrimeCaseKind, Plot
from world.tools.state import WorldState

# Where a confined resident is held. The map has no jail sector, so the
# town hall's cells are the town hall: `arrest` moves them here and
# `release` is the only thing that moves them out. Named rather than
# invented as a new place, because a location nothing else knows about is
# a location no resident can be found at.
# GOV-21. Where a prisoner is HELD, and where a prisoner is DRAWN.
#
# These are two different questions and one constant was answering both.
# The gate constant is right for "is this the jail" - the charter and
# every civic tool speak that vocabulary. It is wrong for `agent.moved`,
# because the map draws "Kasukabe Government Complex" and has no tile for
# the string "town_hall": `look_around` reported `tile: null` and the
# arrested resident was nowhere anybody could see. Run 23 shows the same
# null-tile pattern for "temple".
LOCATION_JAIL = LOCATION_TOWN_HALL


def jail_place() -> str:
    """The jail as the MAP names it, for an `agent.moved` payload.

    `display_place` folds a gate constant onto its sector, following
    `ALSO_SATISFIES` when the gate has no sector of its own - which is
    this exact case, because the town hall and the government complex are
    one building on this map.
    """
    from world.tools.place_aliases import display_place

    return display_place(LOCATION_JAIL)


async def _require_seat(world: WorldState, agent: AgentLifeRecord, tool_name: str) -> None:
    state = await world.charter.store.get_government_state()
    if not state.is_sitting(agent.agent_id):
        raise ActorRequirementError(
            tool_name, ["you do not hold a sitting government seat."]
        )


def _require_here(world: WorldState, agent: AgentLifeRecord, tool_name: str, place: str) -> None:
    here = world.location_of(agent.agent_id)
    if not satisfies_gate(here, place):
        raise LocationGateError(tool_name, place, here)


class SetTaxRateParams(BaseModel):
    tax_rate_bps: int = Field(
        ge=0, le=10_000, description="Head tax in basis points. 100 is one percent."
    )


class DeclareCandidacyParams(BaseModel):
    election_id: str
    #: Required for the same reason `vote.because` is: an optional field goes
    #: unfilled. The last run recorded "0 of 1523 remarks said why" against
    #: one. A candidate list with no platforms tells a reader who stood and
    #: nothing about what the election was between.
    platform: str = Field(
        min_length=8, max_length=400,
        description="What you stand for, in your own words - what you would do with the seat.",
    )


class ConveneParams(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    seats_only: bool = Field(
        default=True, description="True calls the sitting seats; False calls the town."
    )


class AppointPoliceParams(BaseModel):
    agent_id: str
    wage_sim: int = Field(default=0, ge=0, description="Salary per game-day from the treasury.")


class ArrestParams(BaseModel):
    agent_id: str
    case_id: str = Field(description="The open case this arrest is on.")


class RuleParams(BaseModel):
    case_id: str
    verdict: str = Field(description="fine, jail or dismiss.")
    fine_sim: int = Field(default=0, ge=0)
    jail_days: int = Field(default=0, ge=0)


class ReleaseParams(BaseModel):
    agent_id: str


VERDICTS = ("fine", "jail", "dismiss")


class GrantPlotParams(BaseModel):
    holder_agent_id: str
    location_id: str
    # A rectangle, because a resident describing land says "twelve by eight
    # behind the mill", not a set of tile pairs.
    width: int = Field(default=4, ge=1, le=32)
    height: int = Field(default=4, ge=1, le=32)


async def grant_plot_handler(
    world: WorldState, agent: AgentLifeRecord, params: GrantPlotParams
) -> ToolOutcome:
    """Give a resident ground to build on, with the permit attached (GROW-01).

    Nothing in this world could create a `Plot`. The only construction of
    one outside the tests is `plot_from_snapshot`, which RESTORES a plot
    that already existed - so on a fresh run `world.plots` was empty
    forever, and `build_structure`'s first check ("plot not found") refused
    every resident before any of its other gates were even reached.
    Construction.py's own docstring admits it: "tests and bootstrap seed
    plots directly".

    A grant, not a purchase, and made by a seat holder at the government
    complex - which keeps DESIGN section 8's "the government approves
    expansion" true while making it something the town can actually do. The
    permit is attached here rather than being a second act, because a plot
    granted by the government IS permitted; separating them would only add
    a step nobody could complete.

    Ownership by auction, and whether a charter amendment may abolish
    private land, are still open (`18`.7) - this is the mechanism, not the
    ideology.
    """
    if params.holder_agent_id not in world.agents:
        raise NotFoundError("agent", params.holder_agent_id)

    # Anchor the footprint on a hash of the place and holder, so two grants
    # in the same place do not silently overlap and a replay of the same
    # run lays out the same ground.
    seed = abs(hash((params.location_id, params.holder_agent_id))) % 512
    x0, y0 = seed % 128, (seed // 128) % 128
    footprint = frozenset(
        (x0 + dx, y0 + dy)
        for dx in range(params.width)
        for dy in range(params.height)
    )
    taken = {tile for plot in world.plots.values() for tile in plot.footprint}
    if footprint & taken:
        raise ActorRequirementError(
            "grant_plot",
            [
                f"that ground overlaps a plot already granted. Every plot in "
                f"{params.location_id} is spoken for at this size; try a "
                "smaller one or another place."
            ],
        )

    plot_id = f"plot-{params.holder_agent_id}-{world.tick()}"
    world.plots[plot_id] = Plot(
        plot_id=plot_id,
        owner_agent_id=params.holder_agent_id,
        location_id=params.location_id,
        footprint=footprint,
        permitted=True,
    )
    return ToolOutcome(
        result={
            "plot_id": plot_id,
            "owner_agent_id": params.holder_agent_id,
            "location_id": params.location_id,
            "tiles": len(footprint),
        },
        events=[WorldEvent(
            type="politics.plot.granted",
            tick=world.tick(),
            payload={
                "plot_id": plot_id,
                "owner_agent_id": params.holder_agent_id,
                "granted_by_agent_id": agent.agent_id,
                "location_id": params.location_id,
                "agent_id": params.holder_agent_id,
                "involved_agent_ids": [agent.agent_id, params.holder_agent_id],
            },
        )],
    )


class IssueBuilderLicenseParams(BaseModel):
    holder_agent_id: str
    # GOV-23: no `fee_paid_sim` any more.
    #
    # It was a caller-declared amount, defaulting to ZERO, that nothing
    # ever debited - `issue_license` records the number and moves no
    # money, and the finding predicted "a future tool must remember to".
    # This is that tool, and it did not.
    #
    # So the fee was fiction twice over: unpaid, and self-assessed. The
    # charter's `builder_license_fee_sim` is the price, and the handler
    # reads it rather than believing the licensee.


async def issue_builder_license_handler(
    world: WorldState, agent: AgentLifeRecord, params: IssueBuilderLicenseParams
) -> ToolOutcome:
    """Grant somebody the right to build (GROW-02).

    `charter/government.py::issue_builder_license` has been complete since
    genesis and NOTHING called it - the only other mention of the name in
    the whole tree is a docstring. Meanwhile `build_structure` requires a
    `builder_license` to be held, so the licence gate refused everyone,
    including the town's own carpenter, for twenty-three runs.

    That is one of the four reasons nobody here can build anything. On its
    own it is not enough - a plot still has to exist (GROW-01) and a
    finished building still has to become somewhere you can stand
    (GROW-03) - but it is the half that is pure wiring.
    """
    if params.holder_agent_id not in world.agents:
        raise NotFoundError("agent", params.holder_agent_id)
    # TOOL-08. Not `world.location_of(...)`: the charter compares the string
    # it is handed with `!=` against its own constant, and the map spells
    # the same ground "Kasukabe Government Complex". See place_aliases.
    here = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)

    # GOV-23: the fee is the charter's, and it actually moves.
    #
    # Debited BEFORE the licence is issued, so an applicant who cannot
    # afford it is refused with nothing granted - the same
    # "fallible-external-before-owned-state" ordering `registry.py` keeps.
    # The reverse order would hand out a licence and then discover the
    # money was not there.
    #
    # charter/ deliberately holds no ledger reference (see its models.py
    # module docstring on the import boundary), which is why this lives in
    # the handler and not in `issue_license`.
    from world.ledger.core import TREASURY_ACCOUNT_ID
    from world.ledger.models import Currency, TransactionReason

    # GOV-23: check the applicant's issuer may issue BEFORE taking money.
    #
    # `issue_builder_license` runs this same guard itself - but it runs it
    # after this handler has already debited, so a minister standing in
    # the wrong place charged the applicant and granted nothing. Found by
    # the test below, not by reading the code.
    #
    # The charter's own check, not a copy of it: duplicating "who may
    # issue a licence" is how the two answers drift.
    await world.charter.government.require_sitting(agent.agent_id, here)

    charter = await world.charter.store.get_charter()
    fee = charter.builder_license_fee_sim
    if fee > 0:
        await world.ledger.move(
            from_account_id=params.holder_agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=fee,
            currency=Currency.SIM,
            reason=TransactionReason.FEE,
            tick=world.tick(),
            memo="builder_license",
        )

    licence = await world.charter.government.issue_builder_license(
        params.holder_agent_id,
        fee,
        agent.agent_id,
        here,
        world.tick(),
    )
    return ToolOutcome(
        result={
            "license_id": licence.license_id,
            "holder_agent_id": params.holder_agent_id,
            "license_type": "builder_license",
        },
        # GOV-04: `issue_builder_license` above emits politics.license.issued.
        # This handler emitted a second copy - invisible while the charter
        # sink was noop, a DOUBLE row once the runner wired it. The fields
        # this copy carried and the engine's did not (`issued_by_agent_id`,
        # `involved_agent_ids`) were moved onto the engine's payload rather
        # than deleted with it: `involved_agent_ids` is GOV-20's interrupt
        # list and `core/perception.py:52` wakes exactly those residents.
        events=[],
    )


async def set_tax_rate_handler(
    world: WorldState, agent: AgentLifeRecord, params: SetTaxRateParams
) -> ToolOutcome:
    """Set what every head in the town pays each day.

    `charter/government.py::set_tax_rate` has been complete the whole
    time and no tool called it, so the rate has never moved off its
    genesis value. It is now the number `charge_the_day_tax` reads at
    every day boundary, which is what makes it worth arguing about.
    """
    # TOOL-08, as in `issue_builder_license_handler` above.
    here = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)
    policy = await world.charter.government.set_tax_rate(
        params.tax_rate_bps, agent.agent_id, here, world.tick()
    )
    return ToolOutcome(
        result={"tax_rate_bps": policy.tax_rate_bps},
        # GOV-04: `set_tax_rate` above emits this, and `build_event` now
        # adds the generic `agent_id` this copy carried. Duplicate removed;
        # the finding's acceptance is "one politics.tax_rate.set row per
        # set_tax_rate call in the trace on both hosts".
        events=[],
    )


class SetPriceControlParams(BaseModel):
    item: str = Field(min_length=1, max_length=64, description="The item, as inventory names it.")
    max_sim: int | None = Field(
        default=None, gt=0,
        description="The most anyone may ask or pay for one unit. Omit to leave the cap as it is.",
    )
    min_sim: int | None = Field(
        default=None, gt=0,
        description="The least anyone may ask or pay for one unit. Omit to leave the floor as it is.",
    )
    clear: bool = Field(default=False, description="Lift both the cap and the floor on this item.")


class SetBusinessLicensingParams(BaseModel):
    mode: Literal["open", "permit_required", "state_only"] = Field(
        description=(
            "open: anybody may found a business. permit_required: only a holder "
            "of a business permit. state_only: only a sitting seat."
        )
    )


class IssueBusinessPermitParams(BaseModel):
    holder_agent_id: str


async def set_price_control_handler(
    world: WorldState, agent: AgentLifeRecord, params: SetPriceControlParams
) -> ToolOutcome:
    """GOV-4. A cap or a floor on what one unit of something may cost.

    Read in two places, deliberately: `post_price` refuses a price outside
    the band, and `buy`/`sell`/`trade` refuse to SETTLE outside it - so a
    price posted before the cap cannot be paid after it.
    """
    if params.max_sim is None and params.min_sim is None and not params.clear:
        raise ToolValidationError(
            "set_price_control",
            ["give max_sim, min_sim or both - or clear: true to lift the controls on it."],
        )
    here = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)
    item = params.item.strip()
    policy = await world.charter.government.set_price_control(
        item, agent.agent_id, here, world.tick(),
        max_sim=params.max_sim, min_sim=params.min_sim, clear=params.clear,
    )
    return ToolOutcome(
        result={
            "item": item,
            "cap_sim": policy.price_caps.get(item),
            "floor_sim": policy.price_floors.get(item),
            "policy_version": policy.version,
        },
        # `set_price_control` emits politics.price_control.set itself.
        events=[],
    )


async def set_business_licensing_handler(
    world: WorldState, agent: AgentLifeRecord, params: SetBusinessLicensingParams
) -> ToolOutcome:
    """GOV-4. Who may found a business: read by `register_business`."""
    here = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)
    policy = await world.charter.government.set_business_licensing(
        params.mode, agent.agent_id, here, world.tick()
    )
    return ToolOutcome(
        result={"business_licensing": policy.business_licensing, "policy_version": policy.version},
        events=[],
    )


async def issue_business_permit_handler(
    world: WorldState, agent: AgentLifeRecord, params: IssueBusinessPermitParams
) -> ToolOutcome:
    """GOV-4. The permit `permit_required` licensing asks for.

    Same shape and ordering as `issue_builder_license_handler` above: the
    issuer's seat is checked before the applicant is charged, and the fee is
    the charter's `permit_fee_sim`, paid by the holder to the treasury.
    """
    from world.charter.models import BUSINESS_PERMIT_TYPE
    from world.ledger.core import TREASURY_ACCOUNT_ID
    from world.ledger.models import Currency, TransactionReason

    if params.holder_agent_id not in world.agents:
        raise NotFoundError("agent", params.holder_agent_id)
    here = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)
    await world.charter.government.require_sitting(agent.agent_id, here)

    fee = (await world.charter.store.get_charter()).permit_fee_sim
    if fee > 0:
        await world.ledger.move(
            from_account_id=params.holder_agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=fee,
            currency=Currency.SIM,
            reason=TransactionReason.FEE,
            tick=world.tick(),
            memo=BUSINESS_PERMIT_TYPE,
        )
    licence = await world.charter.government.issue_permit(
        BUSINESS_PERMIT_TYPE.split(":", 1)[1], params.holder_agent_id, fee,
        agent.agent_id, here, world.tick(),
    )
    return ToolOutcome(
        result={
            "license_id": licence.license_id,
            "holder_agent_id": params.holder_agent_id,
            "license_type": licence.license_type,
            "fee_paid_sim": fee,
        },
        events=[],
    )


async def declare_candidacy_handler(
    world: WorldState, agent: AgentLifeRecord, params: DeclareCandidacyParams
) -> ToolOutcome:
    """Stand in the next election.

    `vote` has been callable since genesis and there has never been
    anybody to vote for: the elections engine's `declare_candidacy` had no
    tool, so every ballot was empty by construction.
    """
    # TOOL-08, and this is the one that was PROVEN live. Run 25, tick 42:
    # a resident walked to the government complex and was refused with
    # "must be at town_hall, was at Kasukabe Government Complex". `vote`
    # got this fix and `declare_candidacy` did not, so the town could vote
    # and could not stand - every ballot empty by construction, which the
    # vacant-seat finding (GOV-01) only half explained.
    here = place_for_charter(world, agent.agent_id, LOCATION_TOWN_HALL)
    from world.tools.primitives.civic import eligibility_facts_for

    election = await world.charter.elections.declare_candidacy(
        params.election_id, agent.agent_id, eligibility_facts_for(world, agent),
        world.tick(), here, because=params.platform,
    )
    return ToolOutcome(
        result={
            "election_id": election.election_id,
            "candidates": sorted(election.candidates),
        },
        # GOV-04: `declare_candidacy` above emits politics.candidacy.declared.
        # This handler emitted a second copy. GOV-20's `involved_agent_ids`
        # - the interrupt list `core/perception.py:52` reads, and once
        # `sorted(world.agents)`, which woke the whole town per candidacy -
        # moved onto the engine's payload rather than being deleted with
        # this one.
        events=[],
    )


async def convene_handler(
    world: WorldState, agent: AgentLifeRecord, params: ConveneParams
) -> ToolOutcome:
    """Call a sitting. An invitation, and nothing more.

    Deliberately NOT a mechanism that makes anybody speak - ROADMAP Law 2
    forbids an orchestrator passing messages, and Atlas's group-session
    loop is the thing this project decided not to port. This opens a real
    conversation with a topic and tells the invited it exists; whether
    they walk to the town hall is theirs.
    """
    await _require_seat(world, agent, "convene")
    _require_here(world, agent, "convene", LOCATION_TOWN_HALL)

    state = await world.charter.store.get_government_state()
    if params.seats_only:
        invited = [
            who for who in ([state.pm_agent_id, *state.minister_agent_ids])
            if who and who in world.agents
        ]
    else:
        invited = sorted(world.agents)

    conversation_id = world.open_conversation(
        place=LOCATION_TOWN_HALL,
        topic=params.subject,
        # The convener first, so the roster reads as the order people
        # joined and a reader can see who called it.
        participants=[agent.agent_id, *invited],
        now=world.tick(),
    )
    return ToolOutcome(
        result={
            "conversation_id": conversation_id,
            "subject": params.subject,
            "invited": invited,
            "at": LOCATION_TOWN_HALL,
        },
        events=[WorldEvent(
            type="politics.sitting.convened",
            tick=world.tick(),
            payload={
                "conversation_id": conversation_id,
                "opened_by_agent_id": agent.agent_id,
                "agent_id": agent.agent_id,
                "topic": params.subject,
                "location_id": LOCATION_TOWN_HALL,
                "involved_agent_ids": invited,
            },
        )],
    )


async def appoint_police_handler(
    world: WorldState, agent: AgentLifeRecord, params: AppointPoliceParams
) -> ToolOutcome:
    """Give somebody the power to arrest, and a salary for holding it."""
    await _require_seat(world, agent, "appoint_police")
    _require_here(world, agent, "appoint_police", LOCATION_TOWN_HALL)
    if params.agent_id not in world.agents:
        raise NotFoundError("agent", params.agent_id, sorted(world.agents))

    world.police.add(params.agent_id)
    name = world.agent_names.get(params.agent_id, params.agent_id)
    return ToolOutcome(
        result={"appointed": params.agent_id, "name": name, "wage_sim": params.wage_sim},
        events=[WorldEvent(
            type="politics.police.appointed",
            tick=world.tick(),
            payload={
                "agent_id": params.agent_id,
                "appointed_by_agent_id": agent.agent_id,
                "wage_sim": params.wage_sim,
                "involved_agent_ids": [agent.agent_id, params.agent_id],
            },
        )],
    )


async def arrest_handler(
    world: WorldState, agent: AgentLifeRecord, params: ArrestParams
) -> ToolOutcome:
    """Take a named resident to the cells, on an open case.

    A case is required, and that is the whole design. `steal` already
    records ground truth for every theft, so once a verdict exists the
    false-conviction rate is a query rather than an argument. An arrest
    with no case behind it would make that query meaningless.
    """
    if agent.agent_id not in world.police:
        raise ActorRequirementError(
            "arrest",
            [
                "you are not police. A seat appoints one with "
                "`appoint_police`; anybody may `file_complaint`."
            ],
        )
    case = next((c for c in world.crime_cases if c.case_id == params.case_id), None)
    if case is None:
        raise NotFoundError(
            "crime case", params.case_id,
            [c.case_id for c in world.crime_cases if c.verdict is None],
        )
    # GOV-02: a case with nobody accused cannot produce an arrest.
    #
    # A theft opens accusing nobody, because the town does not know who did
    # it. Somebody has to say a name first, and `file_complaint` is where
    # they say it. Before this, `steal` filed the case against the VICTIM,
    # so the check below admitted exactly one arrest: the robbed.
    if not case.against_agent_id:
        raise ToolValidationError(
            "arrest",
            [
                f"case {params.case_id} accuses nobody yet. Somebody has to "
                "name a suspect with `file_complaint` before an arrest can "
                "be made."
            ],
        )
    if case.against_agent_id != params.agent_id:
        raise ToolValidationError(
            "arrest",
            [
                f"case {params.case_id} accuses {case.against_agent_id}, "
                f"not {params.agent_id}."
            ],
        )
    if params.agent_id not in world.agents:
        raise NotFoundError("agent", params.agent_id, sorted(world.agents))

    here = world.location_of(agent.agent_id)
    there = world.location_of(params.agent_id)
    if not satisfies_gate(there, here or ""):
        raise LocationGateError("arrest", there, here)

    world.confined[params.agent_id] = {
        "case_id": params.case_id,
        "since_tick": world.tick(),
        "until_tick": None,
        "by": agent.agent_id,
    }
    name = world.agent_names.get(params.agent_id, params.agent_id)
    return ToolOutcome(
        result={"arrested": params.agent_id, "name": name, "case_id": params.case_id},
        events=[
            WorldEvent(
                type="crime.arrest.made",
                tick=world.tick(),
                payload={
                    "agent_id": params.agent_id,
                    "arrested_agent_id": params.agent_id,
                    "officer_agent_id": agent.agent_id,
                    "case_id": params.case_id,
                    # GOV-21: the name the map draws, not the gate constant.
                    "location_id": jail_place(),
                    "involved_agent_ids": [agent.agent_id, params.agent_id],
                },
            ),
            # And they are actually moved. An arrest that leaves the
            # accused standing in the field is a word, not an arrest -
            # LocationIndex reads `agent.moved` with an agent_id and a
            # location_id, which is why this is a second event rather than
            # a field on the first.
            WorldEvent(
                type="agent.moved",
                tick=world.tick(),
                payload={
                    "agent_id": params.agent_id,
                    # GOV-21: the name the map draws, not the gate constant.
                    "location_id": jail_place(),
                    "reason": "arrested",
                },
            ),
        ],
    )


async def rule_handler(
    world: WorldState, agent: AgentLifeRecord, params: RuleParams
) -> ToolOutcome:
    """Record a verdict on an open case: fine, jail, or dismiss.

    Ground truth for every theft is already in the trace (`steal` records
    the attempt whether or not anybody noticed), so the moment verdicts
    exist the false-conviction rate stops being an argument and becomes a
    query. That is the whole reason this tool is worth having.
    """
    await _require_seat(world, agent, "rule")
    _require_here(world, agent, "rule", LOCATION_TOWN_HALL)

    verdict = params.verdict.strip().lower()
    if verdict not in VERDICTS:
        raise ToolValidationError(
            "rule", [f"verdict must be one of: {', '.join(VERDICTS)}."]
        )
    case = next((c for c in world.crime_cases if c.case_id == params.case_id), None)
    if case is None:
        raise NotFoundError(
            "crime case", params.case_id,
            [c.case_id for c in world.crime_cases if c.verdict is None],
        )
    if case.verdict is not None:
        raise ToolValidationError(
            "rule", [f"case {params.case_id} was already ruled on: {case.verdict}."]
        )
    # GOV-02, same reason as `arrest`: there has to be a defendant. A fine
    # or a sentence with no accused used to fall on the victim.
    if not case.against_agent_id and verdict != "dismiss":
        raise ToolValidationError(
            "rule",
            [
                f"case {params.case_id} accuses nobody. A verdict needs a "
                "defendant; `dismiss` is the only ruling an unaccused case "
                "can take."
            ],
        )

    if verdict == "fine" and params.fine_sim > 0:
        from world.ledger.core import TREASURY_ACCOUNT_ID
        from world.ledger.models import Currency, TransactionReason

        await world.ledger.move(
            from_account_id=case.against_agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=params.fine_sim,
            currency=Currency.SIM,
            # GOV-15: booked as a FINE, not a TAX. `treasury_view.
            # tax_collected_sim` and `family_api.money_flow` both group by
            # reason, so every fine the courts levied was reported as tax
            # revenue and no query could tell what the town RAISED from
            # what it PUNISHED.
            reason=TransactionReason.FINE,
            tick=world.tick(),
            memo=f"fine on case {params.case_id}",
        )

    if verdict == "jail" and params.jail_days > 0:
        from world.core.clock import TICKS_PER_DAY

        held = world.confined.setdefault(case.against_agent_id, {
            "case_id": params.case_id, "since_tick": world.tick(), "by": agent.agent_id,
        })
        held["until_tick"] = world.tick() + params.jail_days * TICKS_PER_DAY
    elif verdict == "dismiss":
        world.confined.pop(case.against_agent_id, None)

    case.verdict = verdict
    return ToolOutcome(
        result={
            "case_id": params.case_id,
            "verdict": verdict,
            "fine_sim": params.fine_sim if verdict == "fine" else 0,
            "jail_days": params.jail_days if verdict == "jail" else 0,
            "against": case.against_agent_id,
        },
        events=[WorldEvent(
            type="crime.verdict.recorded",
            tick=world.tick(),
            payload={
                "case_id": params.case_id,
                "verdict": verdict,
                "ruled_by_agent_id": agent.agent_id,
                "agent_id": case.against_agent_id,
                "against_agent_id": case.against_agent_id,
                "fine_sim": params.fine_sim if verdict == "fine" else 0,
                "jail_days": params.jail_days if verdict == "jail" else 0,
                "involved_agent_ids": [agent.agent_id, case.against_agent_id],
            },
        )],
    )


async def release_handler(
    world: WorldState, agent: AgentLifeRecord, params: ReleaseParams
) -> ToolOutcome:
    """End a confinement."""
    is_police = agent.agent_id in world.police
    if not is_police:
        await _require_seat(world, agent, "release")
    if params.agent_id not in world.confined:
        name = world.agent_names.get(params.agent_id, params.agent_id)
        raise ToolValidationError("release", [f"{name} is not being held."])

    held = world.confined.pop(params.agent_id)
    return ToolOutcome(
        result={"released": params.agent_id, "case_id": held.get("case_id")},
        events=[WorldEvent(
            type="crime.release.recorded",
            tick=world.tick(),
            payload={
                "agent_id": params.agent_id,
                "released_agent_id": params.agent_id,
                "released_by_agent_id": agent.agent_id,
                "case_id": held.get("case_id"),
                "ticks_held": world.tick() - int(held.get("since_tick", world.tick())),
                "involved_agent_ids": [agent.agent_id, params.agent_id],
            },
        )],
    )




class TableAmendmentParams(BaseModel):
    field_name: str = Field(
        description="A charter field to change, e.g. election_interval_days."
    )
    new_value: object = Field(description="What it should become.")
    title: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=1, max_length=600)


class VoteAmendmentParams(BaseModel):
    proposal_id: str
    support: bool = Field(description="True to vote for it, False against.")


async def table_amendment_handler(
    world: WorldState, agent: AgentLifeRecord, params: TableAmendmentParams
) -> ToolOutcome:
    """Put a change to the charter in front of the other seats.

    GOV-03. The engine has been able to do this since it was written; no
    tool ever called it.
    """
    proposal = await world.charter.amendments.propose_amendment(
        field_name=params.field_name,
        new_value=params.new_value,
        title=params.title,
        rationale=params.rationale,
        proposed_by_agent_id=agent.agent_id,
        now_tick=world.tick(),
    )
    state = await world.charter.store.get_government_state()
    seats = [who for who in state.all_seat_holders() if who != agent.agent_id]
    return ToolOutcome(
        result={
            "proposal_id": proposal.proposal_id,
            "field_name": params.field_name,
            "new_value": params.new_value,
            "status": proposal.status.value,
        },
        events=[WorldEvent(
            type="politics.amendment.tabled",
            tick=world.tick(),
            payload={
                "proposal_id": proposal.proposal_id,
                "field_name": params.field_name,
                "new_value": params.new_value,
                "title": params.title,
                "proposed_by_agent_id": agent.agent_id,
                "agent_id": agent.agent_id,
                # The other seats, and only them. A charter change is the
                # one thing a seat holder must act on before it resolves,
                # so this interrupt is bought for a reason - unlike the
                # candidacy fan-out at GOV-20, which woke the whole town.
                "involved_agent_ids": [agent.agent_id, *seats],
            },
        )],
    )


async def vote_amendment_handler(
    world: WorldState, agent: AgentLifeRecord, params: VoteAmendmentParams
) -> ToolOutcome:
    """Vote on a tabled change. Three-fifths carries it, per the charter."""
    proposal = await world.charter.amendments.cast_amendment_vote(
        params.proposal_id, agent.agent_id, params.support, world.tick()
    )
    charter = await world.charter.store.get_charter()
    yes = sum(1 for v in proposal.votes.values() if v)
    return ToolOutcome(
        result={
            "proposal_id": params.proposal_id,
            "your_vote": params.support,
            "yes_votes": yes,
            "required": charter.amendment_votes_required,
            "status": proposal.status.value,
        },
        events=[WorldEvent(
            type="politics.amendment.voted",
            tick=world.tick(),
            payload={
                "proposal_id": params.proposal_id,
                "voter_agent_id": agent.agent_id,
                "agent_id": agent.agent_id,
                "support": params.support,
                "yes_votes": yes,
                "status": proposal.status.value,
            },
        )],
    )


def false_conviction_stats(world: WorldState) -> dict[str, int]:
    """DESIGN section 6's flagship metric, computable at last.

    ROADMAP question 20 asks how often the town convicts the wrong person.
    It has never been answerable on the live path, and not because the
    town was just: a case only ever carried ONE name, and until GOV-02
    that name was the victim's. Suspect and perpetrator could not differ,
    so a wrong conviction was not something the world could express.

    Counts only cases where the world actually knows - a theft it
    witnessed into being. A complaint has no
    `ground_truth_perpetrator_id`, because nobody watched it happen and
    the world has no view on who is right; those are `unknowable` rather
    than quietly scored as correct.

    Not a tool. A plain read, for the report and the evaluation suite -
    exposing this to residents would hand the town the answer its police
    are supposed to have to work for.
    """
    stats = {"convictions": 0, "true": 0, "false": 0, "unknowable": 0, "dismissed": 0}
    for case in world.crime_cases:
        if case.verdict not in ("fine", "jail"):
            if case.verdict == "dismiss":
                stats["dismissed"] += 1
            continue
        stats["convictions"] += 1
        truth = case.ground_truth_perpetrator_id
        if truth is None:
            stats["unknowable"] += 1
        elif truth == case.against_agent_id:
            stats["true"] += 1
        else:
            stats["false"] += 1
    return stats


def justice_reading(world: WorldState) -> dict[str, object]:
    """GOV-10. The false-conviction rate, as a number the report can print.

    `false_conviction_stats` had no caller: the trace's `cases` table listens
    for `crime.case.*`, which only the unwired `world/crime/` package emits,
    and the live tools emit `crime.theft.attempted`, `crime.complaint.filed`
    and `crime.verdict.recorded` - deliberately without the perpetrator,
    because residents perceive those events. So the only place that knows the
    truth is the world itself, and this is read from it over `/world/justice`
    and kept in the run's reading, never through a tool.

    `rate` is None, not 0, while there are no knowable convictions: "nobody
    was wrongly convicted" and "nobody was convicted" are different claims.
    """
    stats = false_conviction_stats(world)
    cases = list(world.crime_cases)
    knowable = stats["true"] + stats["false"]
    return {
        **stats,
        "cases": len(cases),
        "thefts": sum(1 for c in cases if c.kind == CrimeCaseKind.THEFT),
        "complaints": sum(1 for c in cases if c.kind == CrimeCaseKind.COMPLAINT),
        "accused_named": sum(1 for c in cases if c.against_agent_id),
        "awaiting_ruling": sum(1 for c in cases if c.verdict is None),
        "false_conviction_rate": (stats["false"] / knowable) if knowable else None,
    }


def build_specs() -> list[ToolSpec]:
    """PROVISIONAL TEXT. See the module docstring: the addendum's section
    12 says these descriptions are written after the first full-day run
    with tax charged, and no such run has happened."""
    return [
        ToolSpec(
            name="set_tax_rate",
            description=(
                "Set the head tax every resident pays each game-day, in basis "
                "points of the town's base rate. It is charged at every day "
                "boundary to whoever heads each household, and a household that "
                "cannot cover it accrues the rest as debt rather than a negative "
                "balance. Takes effect from the next day boundary. Cost: 0."
            ),
            params_model=SetTaxRateParams,
            handler=set_tax_rate_handler,
            namespace=ToolNamespace.GOVERNMENT,
            # The GOVERNMENT COMPLEX, not the town hall.
            #
            # Part D's table says "town hall", but `charter/government.py`
            # - which owns the rate and has since genesis - raises
            # NotAtRequiredLocation for anything but `government_complex`,
            # and `place_aliases` does not treat the two as one place. A
            # tool gated somewhere its own engine refuses is a tool nobody
            # can call, so the engine wins and the spec's shorthand is
            # noted here rather than followed.
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        # GOV-4. Price controls and licensing: the policy book's levers beyond
        # tax and margin. Same place and seat rule as `set_tax_rate`.
        ToolSpec(
            name="set_price_control",
            description=(
                "Cap or floor the price of one item for the whole town, per unit. "
                "From then on nobody may post a price outside it, and no sale, "
                "purchase or trade settles outside it - including at prices "
                "posted before. `clear` lifts the controls on that item. Cost: 0."
            ),
            params_model=SetPriceControlParams,
            handler=set_price_control_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="set_business_licensing",
            description=(
                "Decide who may found a business: open (anybody), "
                "permit_required (a holder of a business permit, which a seat "
                "issues with `issue_business_permit`), or state_only (a sitting "
                "seat). Businesses that already exist are unaffected. Cost: 0."
            ),
            params_model=SetBusinessLicensingParams,
            handler=set_business_licensing_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="issue_business_permit",
            description=(
                "Grant a resident a permit to found a business. It matters only "
                "while licensing is permit_required. The holder pays the "
                "charter's permit fee to the treasury. Cost: 0."
            ),
            params_model=IssueBusinessPermitParams,
            handler=issue_business_permit_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="issue_builder_license",
            description=(
                "Grant a resident the right to put up a building. "
                "`build_structure` refuses anyone without this licence, so "
                "until a seat holder issues one nobody in the town can "
                "build at all. Issue it at the government complex. Cost: 0."
            ),
            params_model=IssueBuilderLicenseParams,
            handler=issue_builder_license_handler,
            namespace=ToolNamespace.GOVERNMENT,
            # The GOVERNMENT COMPLEX, not the town hall.
            #
            # Part D's table says "town hall", but `charter/government.py`
            # - which owns the rate and has since genesis - raises
            # NotAtRequiredLocation for anything but `government_complex`,
            # and `place_aliases` does not treat the two as one place. A
            # tool gated somewhere its own engine refuses is a tool nobody
            # can call, so the engine wins and the spec's shorthand is
            # noted here rather than followed.
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="grant_plot",
            description=(
                "Grant a resident ground to build on, with the permit "
                "attached. `build_structure` refuses anyone without a "
                "permitted plot, and nothing else in the town creates one, "
                "so until a seat holder grants it nobody can build at all. "
                "Grant it at the government complex. Cost: 0."
            ),
            params_model=GrantPlotParams,
            handler=grant_plot_handler,
            namespace=ToolNamespace.GOVERNMENT,
            # The GOVERNMENT COMPLEX, not the town hall.
            #
            # Part D's table says "town hall", but `charter/government.py`
            # - which owns the rate and has since genesis - raises
            # NotAtRequiredLocation for anything but `government_complex`,
            # and `place_aliases` does not treat the two as one place. A
            # tool gated somewhere its own engine refuses is a tool nobody
            # can call, so the engine wins and the spec's shorthand is
            # noted here rather than followed.
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="declare_candidacy",
            description=(
                "Put your own name into an open election, standing at the town "
                "hall. Others then `vote` for you or for somebody else. An "
                "election with no candidates cannot be won by anyone. Cost: 0."
            ),
            params_model=DeclareCandidacyParams,
            handler=declare_candidacy_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=LOCATION_TOWN_HALL,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="convene",
            description=(
                "Call a sitting at the town hall about one subject, and tell the "
                "people you called that it is happening. It opens a conversation "
                "they can speak into; it does not make anybody come or say "
                "anything. To call people together anywhere else, and without a "
                "seat, use `start_conversation`. Cost: 2 sim."
            ),
            params_model=ConveneParams,
            handler=convene_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_TOWN_HALL,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="appoint_police",
            description=(
                "Give a resident the power to `arrest` on an open case, and a "
                "salary from the treasury for holding it. Only a sitting seat "
                "appoints, and only at the town hall. Cost: 0."
            ),
            params_model=AppointPoliceParams,
            handler=appoint_police_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_TOWN_HALL,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="arrest",
            description=(
                "Take a resident standing with you to the cells, on a case "
                "already open against them. Only police arrest, and only on a "
                "case: anybody may open one with `file_complaint`, and `steal` "
                "opens one by itself. They stay held until a seat calls `rule` "
                "or somebody calls `release`. Cost: 0."
            ),
            params_model=ArrestParams,
            handler=arrest_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,  # depends on where the accused is - see the handler
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="rule",
            description=(
                "Record a verdict on an open case: fine, jail or dismiss. A fine "
                "moves sim from the accused to the treasury now; jail holds them "
                "for a number of game-days; dismiss ends the case and any "
                "confinement. One verdict per case. Cost: 0."
            ),
            params_model=RuleParams,
            handler=rule_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_TOWN_HALL,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="release",
            description=(
                "End a confinement, so the resident may act again. Police or a "
                "sitting seat. Cost: 0."
            ),
            params_model=ReleaseParams,
            handler=release_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="table_amendment",
            description=(
                "Put a change to the town's charter in front of the other "
                "seats. Names one charter field and what it should become - "
                "election_interval_days, term_limit_terms, suffrage_min_age, "
                "amendment_votes_required and the rest. `vote_amendment` is "
                "how the seats answer; three-fifths carries it, then the "
                "President signs or vetoes. Seat holders only, at the town "
                "hall. Cost: 0."
            ),
            params_model=TableAmendmentParams,
            handler=table_amendment_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_TOWN_HALL,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="vote_amendment",
            description=(
                "Vote for or against a tabled charter change. You may change "
                "your vote until the proposal resolves; only your latest one "
                "counts. Seat holders only, at the town hall. Cost: 0."
            ),
            params_model=VoteAmendmentParams,
            handler=vote_amendment_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_TOWN_HALL,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
    ]
