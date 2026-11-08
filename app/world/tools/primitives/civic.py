"""vote, petition, register_business, register_org, file_complaint:
DESIGN.md section 2's civic primitives.

Four of these five are thin gates in front of a charter/ sub-engine that
already implements the real bureaucratic logic (charter/elections.py,
charter/land_petitions.py, charter/organizations.py) - this module's job
is exactly the one charter/'s own docstrings say is missing: resolve the
caller's real location and age/residency facts and hand them to charter/
as the plain arguments its functions already expect (see e.g.
charter/models.py's EligibilityFacts docstring: "The future tool
dispatcher is expected to populate this"). `register_business` is the
one primitive with no charter/ counterpart (charter/'s docstrings never
claim businesses); it drives `world.ledger.Ledger.register_business`
directly and records the non-financial half of a business in this
package's own `Business` model (see models.py's docstring for why).
`file_complaint` is DESIGN.md section 6's crime-reporting complaints
desk, explicitly not charter/'s constitutional ComplaintEntry - see
models.py's CrimeCase docstring.

`hire_employee`/`fire_employee`/`transfer_business` are this module's
namespaces-and-ACLs work (DESIGN.md section 2: business namespace is
"owner + explicitly granted employees ... transfers with sale/
inheritance"). Before these existed, `Business.employee_agent_ids` could
only ever be empty and `owner_agent_id` could only ever be the founding
registrant - registry.py's BUSINESS-namespace visibility check
(`is_visible`) was correct code with no path that could ever exercise it.
These three give a business owner the only two things the namespace rule
actually needs: a live-checked employee roster, and a way to move
`owner_agent_id` itself (a sale or an inheritance handoff - the trigger is
whoever calls this, human-in-the-loop for a sale, a future death/estate
handler for inheritance; this primitive only does the transfer itself,
not deciding when one is warranted). All three are PUBLIC-namespace: the
concept "this business has a hire/fire/transfer tool" is not a secret
(unlike a BUSINESS-namespace trade secret define_business_tool composes -
see meta.py), only the owner-only *authorization* to use it on a
particular business_id is, checked in the handler the same way
commerce.py's buy/sell check `require_business` rather than gating
visibility on it.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from world.charter.models import (
    BUSINESS_PERMIT_TYPE,
    EligibilityFacts,
    LicenseStatus,
    OrganizationKind,
)
from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord
from world.ledger.money import (
    ENERGY_UNITS_PER_USD_CENT,
    SIM_PER_ENERGY_UNIT,
    SIM_PER_USD,
    SIM_PER_USD_CENT,
    energy_units,
)
from world.tools.errors import (
    ActorRequirementError,
    AlreadyExistsError,
    NotFoundError,
    ToolValidationError,
)
from world.tools.locations import (
    LOCATION_GOVERNMENT_COMPLEX,
    LOCATION_TOWN_HALL,
    workshops_for_business_type,
)
from world.tools.models import Business, CrimeCase, CrimeCaseKind
from world.tools.place_aliases import place_for_charter, resolve_business, satisfies_gate
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState

# One `buy_energy` may spend at most this share of the sim its buyer holds.
#
# Run 26: Giselle Ingram converted all 4,900 of her sim into energy in one
# call. She ended the run holding more energy than anyone in town and no sim
# at all - refused `move`, `rest`, `say`, `find_people`, `describe_person`
# and `draw_from_till` for the next 130 ticks - and her household defaulted
# on its upkeep at the day boundary. Programs are held to 50% of the caller's
# wallet because "a program that can empty a wallet inside one turn takes the
# run's behaviour with it"; this was the one act left that could take every
# sim a resident had in a single call, and energy cannot be turned back.
#
# Read with the program ceilings' own reader, so it behaves like them: at
# import, failing at boot on a value it cannot parse (its message names the
# variable), 0 allowed as the strictest setting. Buying again is still
# allowed - each purchase is its own decision - but no single call empties a
# resident's sim.
from world.tools.program import _cap as _operator_ceiling  # noqa: E402

ENERGY_PURCHASE_MAX_SHARE_PERCENT = _operator_ceiling(
    "KASUKABE_ENERGY_PURCHASE_MAX_SHARE_PERCENT", 50
)

# ASSUMPTION: DESIGN.md section 3 says businesses "register with government
# (fee + taxes)" but never prices the fee. charter/models.py's Charter has
# no business_registration_fee_sim field either (only patent/permit/builder
# fees) - flagged here for whoever eventually owns that field.
BUSINESS_REGISTRATION_FEE_SIM = 5_000

MIN_CIVIC_AGE_YEARS = 18.0


def eligibility_facts_for(world: WorldState, agent: AgentLifeRecord) -> EligibilityFacts:
    """Who this resident is, for the suffrage rules.

    GOV-25. `is_resident` used to be `household_id is not None`, on the
    reasoning that DESIGN section 4 gives every genesis agent a household
    and nothing else distinguishes a resident from a visitor.

    The first half is true and the conclusion does not follow. A resident
    can LOSE a household - a newborn does not have one yet, and a
    household dissolves when its last other member dies - and each of
    them silently stopped being a citizen. Silently is the word that
    matters: `check_suffrage` refuses with "not a resident", so an
    orphan who tried to vote was told it did not live here.

    Being on the town's roster is the honest proxy, and it is the same
    reasoning the old one used: nothing here distinguishes residents from
    visitors, and every agent in `world.agents` was seeded as a resident
    or born to one. If Kasukabe ever admits visitors, this is the line
    that has to learn the difference - not the household table.
    """
    return EligibilityFacts(
        agent_id=agent.agent_id,
        age_years=agent.age_years,
        is_resident=agent.agent_id in world.agents,
    )


class VoteParams(BaseModel):
    election_id: str
    candidate_agent_id: str
    #: Required, because an optional one is not filled in. The last 50-resident
    #: run recorded "0 of 1523 remarks said why" against an optional intent
    #: field - a town given the option to explain itself never took it. A
    #: ballot with no reason makes the one question an election run is for -
    #: why did they vote as they did - answerable only by reading the
    #: conversation around it and guessing.
    because: str = Field(
        min_length=8, max_length=300,
        description="Why this candidate and not another. One sentence, in your own words.",
    )


async def vote_handler(world: WorldState, agent: AgentLifeRecord, params: VoteParams) -> ToolOutcome:
    location = place_for_charter(world, agent.agent_id, LOCATION_TOWN_HALL)
    await world.charter.elections.cast_vote(
        params.election_id,
        agent.agent_id,
        params.candidate_agent_id,
        eligibility_facts_for(world, agent),
        world.tick(),
        location,
        because=params.because,
    )
    # GOV-04: `charter.elections.cast_vote` above emits politics.vote.cast.
    # This handler emitted a second copy, which was invisible while the
    # charter sink was `noop_sink` and became a DOUBLE row the moment the
    # runner wired the sink to the bus.
    return ToolOutcome(result={"election_id": params.election_id}, events=[])


class PetitionParams(BaseModel):
    title: str
    description: str
    tile_count_requested: int = Field(gt=0)


async def petition_handler(
    world: WorldState, agent: AgentLifeRecord, params: PetitionParams
) -> ToolOutcome:
    location = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)
    petition = await world.charter.land_petitions.file_petition(
        agent.agent_id, params.title, params.description, params.tile_count_requested, location, world.tick()
    )
    # GOV-04: `file_petition` above emits politics.petition.filed, with a
    # `title` this copy did not even carry. Duplicate removed.
    return ToolOutcome(result={"petition_id": petition.petition_id}, events=[])


class RegisterBusinessParams(BaseModel):
    business_id: str
    name: str
    business_type: str
    premises: str | None = Field(
        default=None,
        description=(
            "Where the business will trade. Leave unset and it takes the "
            "place you are standing in - which, since registering happens at "
            "the government complex, means the town hall."
        ),
    )


async def _require_licensed_to_found(world: WorldState, agent_id: str) -> None:
    """GOV-4. `business_licensing`, read at the moment a business is founded.

    Checked before the registration fee is taken, so a founder who may not
    found is refused with nothing charged. Businesses that already exist are
    untouched by a change of licensing - it governs founding, not trading.
    """
    policy = await world.charter.store.get_policy()
    mode = policy.business_licensing
    if mode == "open":
        return
    if mode == "state_only":
        state = await world.charter.store.get_government_state()
        if not state.is_sitting(agent_id):
            raise ActorRequirementError("register_business", [
                f"the government has made founding a business a state matter "
                f"(business licensing: state_only, policy v{policy.version}). Only a "
                "sitting seat may register one; the policy is set at the government "
                "complex with `set_business_licensing`."
            ])
        return
    licences = await world.charter.store.list_licenses()
    if not any(
        lic.holder_agent_id == agent_id
        and lic.license_type == BUSINESS_PERMIT_TYPE
        and lic.status is LicenseStatus.ACTIVE
        for lic in licences
    ):
        raise ActorRequirementError("register_business", [
            f"founding a business needs a business permit (business licensing: "
            f"permit_required, policy v{policy.version}). A sitting seat issues one "
            "at the government complex with `issue_business_permit`, for the "
            "charter's permit fee."
        ])


async def register_business_handler(
    world: WorldState, agent: AgentLifeRecord, params: RegisterBusinessParams
) -> ToolOutcome:
    if params.business_id in world.businesses:
        raise AlreadyExistsError("business", params.business_id)
    await _require_licensed_to_found(world, agent.agent_id)

    # TOOL-11: registering is gated at the government complex, and the
    # premises used to be `world.location_of(agent)` - so EVERY business
    # registered in a run had the town hall as its shop, and `buy`, `sell`,
    # `post_price` and `pay_wage` for it all gated on the complex from then
    # on. A resident who founded a bakery could only sell bread in the
    # government building.
    #
    # `premises` is optional so the old call still works, but it is folded
    # through the same destination logic `move` uses and checked against
    # `is_real_destination` - the function that has been sitting in
    # place_aliases.py with no caller since it was written (TOOL-25).
    # Inventing a shop nobody can walk to is the one outcome worse than the
    # town hall.
    location = world.location_of(agent.agent_id) or ""
    if params.premises is not None:
        from world.tools.place_aliases import is_real_destination
        from world.tools.primitives.movement import canonical_destination
        from world.tools.primitives.places import known_places

        wanted = canonical_destination(world, params.premises)
        if not is_real_destination(world, wanted):
            raise NotFoundError(
                "place",
                params.premises,
                sorted({p["place_id"] for p in known_places(world)})[:12],
                hint=(
                    "A business has to stand somewhere the town can walk to. "
                    "`list_places` names them."
                ),
            )
        location = wanted

    await world.ledger.register_business(
        params.business_id, agent.agent_id, BUSINESS_REGISTRATION_FEE_SIM, world.tick()
    )

    world.businesses[params.business_id] = Business(
        business_id=params.business_id,
        name=params.name,
        business_type=params.business_type,
        owner_agent_id=agent.agent_id,
        location_id=location,
        provides_workshops=workshops_for_business_type(params.business_type),
    )
    event = WorldEvent(
        type="business.registered",
        tick=world.tick(),
        payload={
            "business_id": params.business_id,
            "owner_agent_id": agent.agent_id,
            "business_type": params.business_type,
            "fee_paid_sim": BUSINESS_REGISTRATION_FEE_SIM,
        },
    )
    return ToolOutcome(result={"business_id": params.business_id}, events=[event])


class RegisterOrgParams(BaseModel):
    kind: OrganizationKind
    name: str
    charter_text: str


async def register_org_handler(
    world: WorldState, agent: AgentLifeRecord, params: RegisterOrgParams
) -> ToolOutcome:
    location = place_for_charter(world, agent.agent_id, LOCATION_GOVERNMENT_COMPLEX)
    organization = await world.charter.organizations.register_organization(
        params.kind, params.name, agent.agent_id, params.charter_text, location, world.tick()
    )
    # GOV-04: `register_organization` above emits this same event with the
    # same payload. Duplicate removed.
    return ToolOutcome(result={"org_id": organization.org_id}, events=[])


class AdmitToOrgParams(BaseModel):
    org_id: str
    agent_id: str = Field(description="Who you are admitting. They must be here with you.")


class LeaveOrgParams(BaseModel):
    org_id: str


async def admit_to_org_handler(
    world: WorldState, agent: AgentLifeRecord, params: AdmitToOrgParams
) -> ToolOutcome:
    """Let somebody into an organisation you founded.

    GOV-10 / TOOL-30. `ComplaintLedger`-style: `add_member` and
    `remove_member` have existed since genesis, complete and with their own
    events, and NO TOOL reached either. So `registry.py`'s ORGANIZATION
    namespace check - `actor_id in organization.members` - could only ever
    be true for the founder, and every guild, party, cult and society in
    the town was permanently a society of one. DESIGN section 7's
    "organisations first-class" and ROADMAP T6 had no runtime surface at
    all, not by choice but by a missing verb.

    Founder-only, because admitting somebody is the founder's call and
    there is no officer role yet (that is the rest of GOV-10, which wants
    roles, dues and a treasury). A member who wants out uses `leave_org`
    and needs nobody's permission.

    Co-located, the same rule `teach_skill` and `trade` follow: an act
    between two residents happens where they both are.
    """
    from world.tools.errors import (
        ActorRequirementError,
        LocationGateError,
        NotFoundError,
    )

    organization = await world.charter.store.get_organization(params.org_id)
    if organization is None:
        raise NotFoundError("organization", params.org_id)
    if organization.founder_agent_id != agent.agent_id:
        raise ActorRequirementError(
            "admit_to_org",
            [
                f"only {organization.founder_agent_id} can admit members to "
                f"{organization.name}"
            ],
        )
    world.require_agent(params.agent_id)
    here = world.location_of(agent.agent_id)
    if not satisfies_gate(world.location_of(params.agent_id), here or ""):
        raise LocationGateError(
            "admit_to_org", here, world.location_of(params.agent_id)
        )
    if params.agent_id in organization.members:
        raise ActorRequirementError(
            "admit_to_org",
            [f"{params.agent_id} is already in {organization.name}"],
        )

    await world.charter.organizations.add_member(
        params.org_id, params.agent_id, world.tick()
    )
    # GOV-04: the charter engine emits politics.organization.member_added.
    return ToolOutcome(
        result={"org_id": params.org_id, "agent_id": params.agent_id},
        events=[],
    )


async def leave_org_handler(
    world: WorldState, agent: AgentLifeRecord, params: LeaveOrgParams
) -> ToolOutcome:
    """Leave an organisation.

    GOV-10. Needs nobody's permission: a society you cannot leave is not
    an association, and `remove_member` was as unreachable as `add_member`.

    The founder may not leave their own organisation - it would leave a
    body whose ORGANIZATION-namespace tools nobody can call and nobody can
    grant, which is a worse state than the founder-only one this fixes.
    """
    from world.tools.errors import ActorRequirementError, NotFoundError

    organization = await world.charter.store.get_organization(params.org_id)
    if organization is None:
        raise NotFoundError("organization", params.org_id)
    if organization.founder_agent_id == agent.agent_id:
        raise ActorRequirementError(
            "leave_org",
            [
                f"you founded {organization.name}; a founder cannot leave it, "
                "because nobody could then admit anyone to it"
            ],
        )
    if agent.agent_id not in organization.members:
        raise ActorRequirementError(
            "leave_org", [f"you are not a member of {organization.name}"]
        )

    await world.charter.organizations.remove_member(
        params.org_id, agent.agent_id, world.tick()
    )
    return ToolOutcome(result={"org_id": params.org_id, "left": True}, events=[])


class FileComplaintParams(BaseModel):
    against_agent_id: str = Field(
        description="Who you are accusing. Being wrong is allowed; the town decides."
    )
    description: str = Field(min_length=1)
    case_id: str | None = Field(
        default=None,
        description=(
            "An open case to name somebody on - the case_id a theft "
            "against you reported. Leave unset to open a new one."
        ),
    )
    # GOV-22. Who you are complaining ABOUT, as a kind of thing.
    #
    # DESIGN section 7 makes the immutable complaint ledger one of only
    # TWO genesis checks on government power, and
    # `ComplaintLedger.file_complaint` had no caller anywhere - so
    # `GET /governance/complaints` served a permanently empty list and "no
    # grievances" was indistinguishable from "no writer".
    #
    # Routed through this verb rather than a new one: residents already
    # know `file_complaint`, and a separate `complain_about_government`
    # would be a second word for the same act that a resident has to
    # discover before it can hold anybody to account.
    #
    # "resident" opens an ordinary crime case as before. "official" and
    # "government" write the constitutional ledger instead, which is
    # append-only and never returns a verdict - a complaint about power is
    # a record, not a prosecution.
    against_kind: str = Field(
        default="resident",
        description=(
            "resident (default), official, or government. An official or "
            "government complaint goes in the town's permanent complaint "
            "ledger rather than opening a criminal case."
        ),
    )


async def file_complaint_handler(
    world: WorldState, agent: AgentLifeRecord, params: FileComplaintParams
) -> ToolOutcome:
    from world.tools.errors import NotFoundError, ToolValidationError

    kind = (params.against_kind or "resident").strip().lower()
    if kind not in {"resident", "official", "government"}:
        raise ToolValidationError(
            "file_complaint",
            [f"against_kind must be resident, official or government, not {kind!r}"],
        )

    # GOV-22: a complaint about power goes in the constitutional ledger.
    #
    # Append-only, and it returns no verdict: DESIGN section 7 makes this
    # a RECORD that a government cannot erase, which is a different thing
    # from a prosecution. `ComplaintLedger.file_complaint` has existed
    # since genesis with no caller, so the ledger the design leans on was
    # empty by construction rather than by contentment.
    if kind in {"official", "government"}:
        if kind == "official" and params.against_agent_id not in world.agents:
            raise NotFoundError("agent", params.against_agent_id)
        entry = await world.charter.complaints.file_complaint(
            complainant_agent_id=agent.agent_id,
            against_id=params.against_agent_id,
            against_kind=kind,
            description=params.description,
            now_tick=world.tick(),
        )
        return ToolOutcome(
            result={
                "ledger_index": entry.ledger_index,
                "against_kind": kind,
                "recorded": True,
            },
            # The charter's own sink emits politics.complaint.filed
            # (GOV-04: the handler does not duplicate it).
            events=[],
        )

    if params.against_agent_id not in world.agents:
        raise NotFoundError("agent", params.against_agent_id)

    # GOV-02: naming somebody on an existing theft.
    #
    # A theft opens a case that accuses nobody, because the town does not
    # know who did it. This is how a name gets onto that case - and it is
    # deliberately possible to name the WRONG person, because a justice
    # system where the accusation is always correct is not one. The case
    # keeps its ground truth underneath, unread by any tool, so the
    # false-conviction rate is a query rather than an assumption.
    if params.case_id is not None:
        existing = next(
            (c for c in world.crime_cases if c.case_id == params.case_id), None
        )
        if existing is None:
            raise NotFoundError(
                "case",
                params.case_id,
                [c.case_id for c in world.crime_cases[-8:]],
            )
        if existing.against_agent_id:
            raise ToolValidationError(
                "file_complaint",
                [
                    f"case {params.case_id} already accuses "
                    f"{existing.against_agent_id}."
                ],
            )
        existing.against_agent_id = params.against_agent_id
        event = WorldEvent(
            type="crime.complaint.filed",
            tick=world.tick(),
            payload={
                "case_id": existing.case_id,
                "filed_by_agent_id": agent.agent_id,
                "against_agent_id": params.against_agent_id,
                "attached_to_existing_case": True,
                "involved_agent_ids": [agent.agent_id, params.against_agent_id],
            },
        )
        return ToolOutcome(result={"case_id": existing.case_id}, events=[event])

    case = CrimeCase(
        case_id=f"case-{uuid.uuid4().hex[:12]}",
        kind=CrimeCaseKind.COMPLAINT,
        tick=world.tick(),
        filer_or_actor_agent_id=agent.agent_id,
        against_agent_id=params.against_agent_id,
        # The complainant is the wronged party in a complaint they filed
        # themselves. Ground truth stays None: unlike a theft, the world
        # did not witness this one and has no view on who is right.
        victim_agent_id=agent.agent_id,
        description=params.description,
    )
    world.crime_cases.append(case)
    event = WorldEvent(
        type="crime.complaint.filed",
        tick=world.tick(),
        payload={
            "case_id": case.case_id,
            "filed_by_agent_id": agent.agent_id,
            "against_agent_id": params.against_agent_id,
            "involved_agent_ids": [agent.agent_id, params.against_agent_id],
        },
    )
    return ToolOutcome(result={"case_id": case.case_id}, events=[event])


def require_owned_business(world: WorldState, agent: AgentLifeRecord, business_id: str, tool_name: str) -> Business:
    business = resolve_business(world, business_id)
    if business is None:
        raise NotFoundError("business", business_id, sorted(world.businesses))
    if business.owner_agent_id != agent.agent_id:
        raise ActorRequirementError(tool_name, [f"{agent.agent_id} does not own {business_id}"])
    return business


class HireEmployeeParams(BaseModel):
    business_id: str
    employee_agent_id: str
    # TOOL-11.7. Teaching asks; hiring did not, so an owner could name
    # anyone in the town as their employee without that person knowing,
    # and the first they would learn of it is a wage they did not agree
    # to. This is the same consent path `teach_skill` uses - a declared
    # flag the caller must set after actually asking, which the transcript
    # then records as a claim the owner made.
    # REQUIRED, not defaulted - and that is the whole of run 25's bakery.
    #
    # A field with a default is not in pydantic's `required`, and an index
    # row carries only the required names (TRADE-02). So this flag - the
    # one thing deciding whether the call succeeds - was absent from the
    # schema Marta Thorne was sent: no property, no mention in `Needs:`.
    # She and Tova Okonjo negotiated a full contract out loud ("I agree.
    # Fifteen a day, six days, paid weekly."), she called `hire_employee`
    # six times, and every one was refused for a parameter she had never
    # been shown. She ended up paying Tova by hand and saying so in the
    # town: "the system's being stubborn about your agreement".
    #
    # `teach_skill.student_consent` is required and has always worked.
    # The comment above claiming this "is the same consent path" was true
    # of the intent and false of the code.
    employee_consent: bool = Field(
        description="True once they have agreed, in conversation, to work for you.",
    )


async def hire_employee_handler(
    world: WorldState, agent: AgentLifeRecord, params: HireEmployeeParams
) -> ToolOutcome:
    business = require_owned_business(world, agent, params.business_id, "hire_employee")
    if params.employee_agent_id not in world.agents:
        raise NotFoundError("agent", params.employee_agent_id)
    # TOOL-15. An owner hiring themselves and then paying themselves a
    # wage was, until `draw_from_till` existed, the ONLY way to get money
    # out of a business account - an accident of two missing checks that
    # residents would eventually have found. It also corrupts every wage
    # measurement: S6 asks whether a promise made is a promise kept, and a
    # promise to yourself answers nothing.
    if not params.employee_consent:
        raise ActorRequirementError(
            "hire_employee",
            [
                f"{params.employee_agent_id} has not agreed to work for you. "
                "Ask them first - `say` to them, and hire them once they "
                "have said yes. Naming somebody as your employee without "
                "asking is not hiring, it is a claim about them."
            ],
        )
    if params.employee_agent_id == agent.agent_id:
        raise ActorRequirementError(
            "hire_employee",
            [
                f"you already own {business.name}; you cannot also be its "
                "employee. `draw_from_till` is how an owner takes the takings."
            ],
        )
    business.employee_agent_ids.add(params.employee_agent_id)
    event = WorldEvent(
        type="business.employee.hired",
        tick=world.tick(),
        payload={
            "business_id": business.business_id,
            "employee_agent_id": params.employee_agent_id,
            "hired_by_agent_id": agent.agent_id,
            "involved_agent_ids": [agent.agent_id, params.employee_agent_id],
        },
    )
    return ToolOutcome(result={"business_id": business.business_id}, events=[event])


class FireEmployeeParams(BaseModel):
    business_id: str
    employee_agent_id: str


async def fire_employee_handler(
    world: WorldState, agent: AgentLifeRecord, params: FireEmployeeParams
) -> ToolOutcome:
    business = require_owned_business(world, agent, params.business_id, "fire_employee")
    if params.employee_agent_id not in business.employee_agent_ids:
        raise NotFoundError("employee", f"{business.business_id}:{params.employee_agent_id}")
    business.employee_agent_ids.discard(params.employee_agent_id)
    event = WorldEvent(
        type="business.employee.fired",
        tick=world.tick(),
        payload={
            "business_id": business.business_id,
            "employee_agent_id": params.employee_agent_id,
            "fired_by_agent_id": agent.agent_id,
            "involved_agent_ids": [agent.agent_id, params.employee_agent_id],
        },
    )
    return ToolOutcome(result={"business_id": business.business_id}, events=[event])


class TransferBusinessParams(BaseModel):
    business_id: str
    new_owner_agent_id: str


async def transfer_business_handler(
    world: WorldState, agent: AgentLifeRecord, params: TransferBusinessParams
) -> ToolOutcome:
    """A sale or an inheritance handoff (DESIGN.md section 2: business
    access "transfers with sale/inheritance"). registry.py's `is_visible`
    reads `Business.owner_agent_id` fresh on every BUSINESS-namespace call,
    with nothing in tools/ ever caching it - so the instant this handler's
    single-field assignment lands, the old owner's access to every
    BUSINESS-namespace tool naming this business_id is gone and the new
    owner's is live, with no tick where both or neither hold it, the same
    atomicity argument as an election handover (see registry.py's
    `is_visible` docstring and charter/models.py's `GovernmentState`).
    """
    business = require_owned_business(world, agent, params.business_id, "transfer_business")
    if params.new_owner_agent_id not in world.agents:
        raise NotFoundError("agent", params.new_owner_agent_id)
    if params.new_owner_agent_id == business.owner_agent_id:
        raise ActorRequirementError(
            "transfer_business", [f"{params.new_owner_agent_id} already owns {business.business_id}"]
        )

    previous_owner_agent_id = business.owner_agent_id
    business.owner_agent_id = params.new_owner_agent_id
    # The new owner inherits the going concern as-is, staff included - see
    # this module's docstring; they may `fire_employee` afterward if not.
    event = WorldEvent(
        type="business.ownership.transferred",
        tick=world.tick(),
        payload={
            "business_id": business.business_id,
            "previous_owner_agent_id": previous_owner_agent_id,
            "new_owner_agent_id": params.new_owner_agent_id,
            "involved_agent_ids": [previous_owner_agent_id, params.new_owner_agent_id],
        },
    )
    return ToolOutcome(result={"business_id": business.business_id}, events=[event])


class BuyEnergyParams(BaseModel):
    sim_amount: int = Field(gt=0, description="How much sim-currency to convert into thinking.")


async def buy_energy_handler(
    world: WorldState, agent: AgentLifeRecord, params: BuyEnergyParams
) -> ToolOutcome:
    """Turn sim-currency into the dollars that pay for your own thinking.

    THE LINK THAT WAS MISSING. DESIGN.md rests the town on one loop -
    energy lets you work, work earns money, money buys energy - and the
    last leg has never existed as a verb. `Ledger.exchange_sim_for_usd`
    has been written, complete and correct the whole time, and its own
    docstring says what was absent: "bought at the government store (a
    tool call, in person, location-gating is the tool registry's job, not
    this method's)". Nobody wrote the tool call.

    Until now a dollar wallet only ever went down, so nothing a resident
    did changed how long it would live. That is not a missing measurement,
    it is a missing mechanism: cognitive poverty, altruism under scarcity
    and any reasoning about the price of one's own thought were
    unanswerable by construction.

    ECON-27: the rate is `world.ledger.money.SIM_PER_USD`, and it HAS
    moved - this docstring said 10,000 to the dollar and 100 to the cent
    long after ECON-05 re-rated it to 5,000 and 50. Read the constant; do
    not quote a number here. The exchange is validated in full before
    either leg mutates, so a resident short of sim, or a treasury too poor
    to pay out, fails cleanly rather than losing the sim.
    """
    if params.sim_amount < SIM_PER_USD_CENT:
        raise ToolValidationError(
            "buy_energy",
            [
                f"{params.sim_amount} sim buys less than "
                f"{ENERGY_UNITS_PER_USD_CENT} energy, the smallest amount the "
                f"store sells. Energy is sold {ENERGY_UNITS_PER_USD_CENT} units "
                f"at a time for {SIM_PER_USD_CENT} sim, so buy at least that "
                f"much or the exchange takes your sim for nothing."
            ],
        )

    held = world.ledger.sim_balance(agent.agent_id)
    if held < params.sim_amount:
        raise ToolValidationError(
            "buy_energy",
            [
                f"you hold {held} sim and tried to spend {params.sim_amount}. "
                f"Buy {held} or less, or earn the difference first."
            ],
        )

    # See ENERGY_PURCHASE_MAX_SHARE_PERCENT. Checked before the margin is
    # taken, so nothing has moved when this refuses.
    ceiling = held * ENERGY_PURCHASE_MAX_SHARE_PERCENT // 100
    if params.sim_amount > ceiling:
        raise ToolValidationError(
            "buy_energy",
            [
                f"one purchase may spend at most {ENERGY_PURCHASE_MAX_SHARE_PERCENT}% "
                f"of the sim you hold - {ceiling} of your {held}. Sim is what every "
                "action, bill and trade is paid in, and energy cannot be turned "
                "back into it."
            ],
        )

    # ECON-30: the government's margin, at last read by something.
    #
    # `credit_store_margin_bps` is a power DESIGN section 7 gives the
    # government, with a setter (`government.set_credit_store_margin`) and
    # a validator and an API field - and no reader anywhere. A government
    # could set it, the treasury view would echo it back, and every
    # `buy_energy` still exchanged at the fixed rate. A lever with no
    # linkage is worse than no lever: it reads as a policy the town HAS.
    #
    # The margin is kept by the treasury, per the finding: the resident
    # spends the full amount, and only the remainder buys thinking. So a
    # government that raises the margin makes cognition dearer for
    # everybody and funds itself doing it, which is the trade-off the
    # power is for.
    policy = await world.charter.store.get_policy()
    margin_bps = max(0, policy.credit_store_margin_bps)
    margin = params.sim_amount * margin_bps // 10_000
    exchanged = params.sim_amount - margin
    if exchanged < SIM_PER_USD_CENT:
        raise ToolValidationError(
            "buy_energy",
            [
                f"after the government's {margin_bps / 100:.2f}% credit-store "
                f"margin, {params.sim_amount} sim leaves {exchanged} to "
                f"exchange, which is under the {SIM_PER_USD_CENT} sim the smallest purchase costs. "
                "Buy more, or petition for a lower margin."
            ],
        )
    if margin > 0:
        from world.ledger.core import TREASURY_ACCOUNT_ID
        from world.ledger.models import Currency, TransactionReason

        await world.ledger.move(
            from_account_id=agent.agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=margin,
            currency=Currency.SIM,
            reason=TransactionReason.FEE,
            tick=world.tick(),
            memo="credit_store_margin",
        )

    balance_before = world.ledger.usd_balance(agent.agent_id)
    await world.ledger.exchange_sim_for_usd(
        agent.agent_id, exchanged, world.tick()
    )
    balance_after = world.ledger.usd_balance(agent.agent_id)

    # What the ledger actually credited, not what the full sim amount would
    # have bought. This was `sim_to_usd_cents(params.sim_amount)` - the
    # amount BEFORE the government's margin - so with a 10% margin a
    # resident was told it bought 400 energy and received 360, and the run
    # report, reading the same number off the event below, overstated every
    # purchase. Read off the wallet, so it cannot disagree with it.
    bought = balance_after - balance_before
    # Tool spec A1's event row, exactly: the run report and the stakes
    # measures (S2, "residents who reach buy_energy by day 3") read this,
    # and a purchase with no event is a purchase no measurement can see.
    event = WorldEvent(
        type="economy.energy.bought",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "sim_amount": params.sim_amount,
            "usd_cents": bought,
            "usd_balance_after": balance_after,
        },
    )
    return ToolOutcome(
        result={
            "sim_spent": params.sim_amount,
            # Energy, not dollars. The event above keeps `usd_cents`: the
            # trace and the stakes measures read it, and the ledger is in
            # cents. What the RESIDENT is handed back is in the unit it
            # is shown everywhere else.
            "energy_bought": energy_units(bought),
            "energy_now": energy_units(world.ledger.usd_balance(agent.agent_id)),
            "sim_now": world.ledger.sim_balance(agent.agent_id),
            "rate": f"{SIM_PER_ENERGY_UNIT} sim to 1 energy",
        },
        events=[event],
    )


class PayTaxParams(BaseModel):
    amount_sim: int = Field(gt=0, description="How much of your head-tax debt to pay off.")


async def pay_tax_handler(
    world: WorldState, agent: AgentLifeRecord, params: PayTaxParams
) -> ToolOutcome:
    """Pay down what you owe the treasury in head tax.

    ECON-14. The debt was real, compounding, visible to the resident - and
    unpayable. `repay_head_tax_debt` was written, tested and called by
    nothing; `transfer` refuses a treasury that is not an agent
    (`commerce.py:311`); and `check_debts` told the resident to settle it
    with `transfer` or `pay_wage`, both of which would refuse. A town that
    bills you daily, tells you the total, and provides no way to pay is
    not enforcing an obligation, it is only counting.

    No location gate. Tax is owed to the treasury from wherever you are
    standing, and gating repayment at the government complex would mean a
    resident too poor to be anywhere else could never clear it.
    """
    from world.ledger.head_tax import repay_head_tax_debt
    from world.tools.errors import ActorRequirementError

    debts = world.head_tax_debts
    if debts is None:
        # Not reachable from either composition root, both of which
        # publish the store. Refuse in the resident's own vocabulary
        # rather than raising AttributeError into a tick.
        raise ActorRequirementError("pay_tax", ["there is no tax office to pay"])

    outstanding = await debts.outstanding(agent.agent_id)
    if outstanding <= 0:
        # The 50-resident run (2026-09-16): 18 refusals, met by residents who
        # had just read "Owed today: ... head tax 30" - Hendrik Whitlock:
        # "Someone told me I owe no head tax, yet my notice says thirty sim
        # due today." Both were true. Today's tax is TAKEN at the day
        # boundary; `pay_tax` clears arrears. The refusal now says so, since
        # a refusal met five times without a change is a text defect (X10).
        raise ActorRequirementError("pay_tax", [
            "you owe no head tax in arrears. The head tax shown as owed today is "
            "taken automatically at the day boundary, from whoever in your "
            "household holds the most sim - there is nothing to pay by hand. "
            "`pay_tax` pays off only what is left unpaid after a day boundary "
            "a household could not cover; `check_debts` shows any"
        ])

    # Overpaying is a refusal in `repay_head_tax_debt`. Clamping instead:
    # "pay everything I owe" is the commonest thing a resident means, and
    # making it an error teaches it not to try.
    amount = min(int(params.amount_sim), outstanding)

    event = await repay_head_tax_debt(
        world.ledger,
        debts,
        debtor_agent_id=agent.agent_id,
        amount_sim=amount,
        tick=world.tick(),
        # The ledger's own sink, which is what every other head-tax
        # caller passes (`day_jobs.py:326`, `run_genesis.py:7132`). The
        # EventBus itself is not callable and passing it raises inside the
        # repayment, after the debt check and before the money moves.
        event_sink=world.ledger.event_sink,
        memo="head tax repaid by the resident",
    )
    remaining = await debts.outstanding(agent.agent_id)
    return ToolOutcome(
        result={
            "paid_sim": amount,
            "remaining_debt_sim": remaining,
            "cleared": remaining == 0,
        },
        events=[],
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="buy_energy",
            description=(
                # ECON-27. Three things in this sentence were false.
                #
                # "Cost: 2 sim-currency" against a schedule of base 0 - the
                # finding names that one. Worse, and it does not: "a fixed
                # rate of 10,000 sim to the dollar" when SIM_PER_USD is
                # 5,000. ECON-05 halved the rate and this text was never
                # touched, so a resident budgeting from the description of
                # the one act that keeps it alive bought half the thinking
                # it expected. And "fixed" stopped being true again the
                # moment ECON-30 made the government's margin real.
                #
                # Derived from the constants rather than quoted, so the
                # next re-rating cannot leave a lie here.
                "Buy energy with sim-currency, in person at the government store. "
                "Energy is what your thinking runs on; it is not money, and nobody "
                "can pay, trade, lend or give it. "
                f"{SIM_PER_ENERGY_UNIT} sim buys 1 energy ({SIM_PER_USD:,} buys "
                f"{SIM_PER_USD // SIM_PER_ENERGY_UNIT:,}), sold "
                f"{ENERGY_UNITS_PER_USD_CENT} units at a time, and one purchase may "
                f"spend at most {ENERGY_PURCHASE_MAX_SHARE_PERCENT}% of the sim you "
                "hold. The government may "
                "take a margin on top. This is the only way your energy ever goes "
                "up: without it your thinking runs down and stops. The exchange "
                "is the price - there is no separate fee."
            ),
            params_model=BuyEnergyParams,
            handler=buy_energy_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            # A1: alive, not dormant. A dormant resident cannot act at all,
            # which is the point of dormancy - it may not buy its way out
            # of having stopped thinking.
            actor_requirements=ActorRequirements(require_alive=True, require_not_dormant=True),
        ),
        ToolSpec(
            name="vote",
            description=(
                # "town hall" is not what this town calls the building. The
                # world's name for it is Kasukabe Government Complex, which is
                # what `move` accepts, what the civic notice prints and what a
                # resident standing in it is told it is standing in. A tool
                # that names a third thing invites the reader to conclude it is
                # somewhere else - and two elections were tallied with zero
                # ballots by residents who were told "You are at Kasukabe
                # Government Complex: `vote` now" and did not.
                "Cast a ballot in an open election. You must be at the "
                "government complex - the same place the civic notice names, "
                "and where `declare_candidacy` is done. The election_id and "
                "who is standing are both in your civic notice."
            ),
            params_model=VoteParams,
            handler=vote_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=LOCATION_TOWN_HALL,
            # GOV-13: no `min_age` here, deliberately.
            #
            # It used to carry `min_age=MIN_CIVIC_AGE_YEARS`, a constant in
            # this file, while `elections.check_suffrage` reads
            # `charter.suffrage_min_age`. Two rules for one question, and
            # the stricter won - so an amendment LOWERING suffrage to 16
            # changed nothing at all, because the registry refused a
            # sixteen-year-old before the charter was ever consulted.
            # (Raising it worked, which is the sort of asymmetry nobody
            # notices until the town votes for the other one.)
            #
            # The charter decides who may vote. `cast_vote` calls
            # `check_suffrage` on every ballot, and since TOOL-08 its
            # refusal is a logged one that names the rule.
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="petition",
            description="File a land-expansion petition to the President.",
            params_model=PetitionParams,
            handler=petition_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="register_business",
            description="Register a new business with government, for a fee.",
            params_model=RegisterBusinessParams,
            handler=register_business_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(min_age=MIN_CIVIC_AGE_YEARS),
        ),
        ToolSpec(
            name="admit_to_org",
            description=(
                "Admit somebody standing with you into an organisation you "
                "founded. They can leave whenever they choose."
            ),
            params_model=AdmitToOrgParams,
            handler=admit_to_org_handler,
            namespace=ToolNamespace.PUBLIC,
            # GOV-10: wherever the two of you are, like `teach_skill` and
            # `trade`. An organisation has no premises of its own.
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="leave_org",
            description="Leave an organisation you belong to.",
            params_model=LeaveOrgParams,
            handler=leave_org_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="pay_tax",
            description="Pay down your outstanding head-tax debt to the treasury.",
            params_model=PayTaxParams,
            handler=pay_tax_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="register_org",
            description="Register a party, cult, society, or religion with government.",
            params_model=RegisterOrgParams,
            handler=register_org_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(min_age=MIN_CIVIC_AGE_YEARS),
        ),
        ToolSpec(
            name="file_complaint",
            description="File a crime-reporting complaint at the government complaints desk.",
            params_model=FileComplaintParams,
            handler=file_complaint_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="hire_employee",
            description="Grant an agent employee access to a business you own.",
            params_model=HireEmployeeParams,
            handler=hire_employee_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="fire_employee",
            description="Revoke an agent's employee access to a business you own.",
            params_model=FireEmployeeParams,
            handler=fire_employee_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="transfer_business",
            description="Transfer ownership of a business you own to another agent (sale or inheritance).",
            params_model=TransferBusinessParams,
            handler=transfer_business_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
