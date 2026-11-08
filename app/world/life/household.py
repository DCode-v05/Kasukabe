"""Marriage records and household formation (DESIGN.md section 4).

"Marriage: temple ceremony (fee to temple), joint household, enables
children." One function, marry(), does the whole thing in one call so
the tool-registry handler for a "marry" tool call (world/'s tool
registry, not built yet - DESIGN.md section 2) has a single entry point:
validate, charge the temple fee, merge households, and return everything
updated plus the events to emit.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from enum import Enum
from typing import Any

from world.life.clock import GameClock
from world.life.constants import (
    MARRIAGE_MIN_AGE_YEARS,
    MINOR_AGE_LIMIT_YEARS,
    TEMPLE_WEDDING_FEE_SIM,
)
from world.life.models import AgentLifeRecord, Household, LifeEvent, MaritalStatus, MarriageRecord
from world.life.ports import EconomyPort, GuardianshipPort

logger = logging.getLogger(__name__)


class MarriageIneligibleError(ValueError):
    """Raised when a marry() call violates a marriage precondition.

    Aggregates every violated precondition into one error (see
    engine/src/config.py's ConfigError for the pattern this follows) so
    a caller sees everything wrong with the attempt at once, not one
    rejection at a time across repeated tool calls.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("cannot marry: " + "; ".join(self.reasons))


def merge_households(
    household_a: Household | None,
    household_b: Household | None,
    new_member_ids: list[str],
    new_household_id: str,
    at_hours: float,
) -> tuple[Household, str | None]:
    """Combine two (possibly absent) households plus new members into one.

    Returns the resulting household and, if two pre-existing households
    were merged, the id of the one that is now dissolved (the caller's
    household store should drop that id; this function has no store to
    mutate itself).
    """
    if household_a is None and household_b is None:
        return Household(
            household_id=new_household_id, member_ids=list(new_member_ids), formed_at_hours=at_hours
        ), None
    if household_a is not None and household_b is None:
        merged = list(dict.fromkeys([*household_a.member_ids, *new_member_ids]))
        return household_a.model_copy(update={"member_ids": merged}), None
    if household_a is None and household_b is not None:
        merged = list(dict.fromkeys([*household_b.member_ids, *new_member_ids]))
        return household_b.model_copy(update={"member_ids": merged}), None
    assert household_a is not None and household_b is not None
    merged = list(
        dict.fromkeys([*household_a.member_ids, *household_b.member_ids, *new_member_ids])
    )
    return household_a.model_copy(update={"member_ids": merged}), household_b.household_id


class MarriageOutcome(BaseModel):
    agent_a: AgentLifeRecord
    agent_b: AgentLifeRecord
    household: Household
    dissolved_household_id: str | None
    marriage_record: MarriageRecord
    events: list[LifeEvent]


async def marry(
    agent_a: AgentLifeRecord,
    agent_b: AgentLifeRecord,
    household_a: Household | None,
    household_b: Household | None,
    new_household_id: str,
    at: GameClock,
    economy: EconomyPort,
    fee_payer_id: str | None = None,
) -> MarriageOutcome:
    """Marry two agents. Charges TEMPLE_WEDDING_FEE_SIM to ``fee_payer_id``
    (defaults to agent_a) before anything else changes - DESIGN.md
    section 4 doesn't say who pays, so the initiating party (agent_a, by
    convention) is charged unless the caller names someone else.
    """
    reasons = []
    if agent_a.agent_id == agent_b.agent_id:
        reasons.append("cannot marry oneself")
    if agent_a.age_years < MARRIAGE_MIN_AGE_YEARS:
        reasons.append(f"{agent_a.agent_id} is under the {MARRIAGE_MIN_AGE_YEARS:.0f}-year minimum")
    if agent_b.age_years < MARRIAGE_MIN_AGE_YEARS:
        reasons.append(f"{agent_b.agent_id} is under the {MARRIAGE_MIN_AGE_YEARS:.0f}-year minimum")
    if agent_a.marital_status is MaritalStatus.MARRIED:
        reasons.append(f"{agent_a.agent_id} is already married")
    if agent_b.marital_status is MaritalStatus.MARRIED:
        reasons.append(f"{agent_b.agent_id} is already married")
    if reasons:
        raise MarriageIneligibleError(reasons)

    payer = fee_payer_id or agent_a.agent_id
    await economy.debit_sim(payer, TEMPLE_WEDDING_FEE_SIM, reason="temple_wedding_fee")

    household, dissolved_id = merge_households(
        household_a,
        household_b,
        [agent_a.agent_id, agent_b.agent_id],
        new_household_id,
        at.total_hours,
    )

    updated_a = agent_a.model_copy(
        update={
            "marital_status": MaritalStatus.MARRIED,
            "spouse_id": agent_b.agent_id,
            "household_id": household.household_id,
        }
    )
    updated_b = agent_b.model_copy(
        update={
            "marital_status": MaritalStatus.MARRIED,
            "spouse_id": agent_a.agent_id,
            "household_id": household.household_id,
        }
    )
    record = MarriageRecord(
        agent_a_id=agent_a.agent_id,
        agent_b_id=agent_b.agent_id,
        household_id=household.household_id,
        married_at_hours=at.total_hours,
        temple_fee_sim=TEMPLE_WEDDING_FEE_SIM,
    )
    events = [
        LifeEvent(
            type="agent.marriage.recorded",
            agent_id=agent_a.agent_id,
            payload={"spouse_id": agent_b.agent_id, "household_id": household.household_id},
        )
    ]
    return MarriageOutcome(
        agent_a=updated_a,
        agent_b=updated_b,
        household=household,
        dissolved_household_id=dissolved_id,
        marriage_record=record,
        events=events,
    )


# --- coming of age (The 250 section 26) ------------------------------------
# "At 18 (MINOR_AGE_LIMIT_YEARS), a former minor crosses into the working-age
# population. Mechanically, this is the point where the parent-billing
# relationship for their own thinking should end... What they do next is not
# scripted."
#
# aging.py already does the arithmetic half of that transition - it caps the
# age at 18, clears the life record's sponsor_parent_id and fires
# agent.aged_to_adult. What it could not do is the half that has consequences:
# the ledger account kept its is_minor flag (so the parent went on paying for
# the new adult's thinking indefinitely), and the household was left exactly
# as it was, because nothing existed to change it. This section is that
# missing half.


class ComingOfAgeChoice(str, Enum):
    """The three futures section 26 names, and no others.

    Deliberately an enum rather than free text: these are the only shapes
    the household model can actually represent, and a new adult who
    "decides" something outside them would leave the town's household
    records describing a living arrangement that does not exist.
    """

    STAY_AND_CONTRIBUTE = "stay_and_contribute"
    MOVE_OUT_ALONE = "move_out_alone"
    PAIR_OFF = "pair_off"


class ComingOfAgeError(ValueError):
    """Raised when a come_of_age() call violates a precondition.

    Aggregates every violated precondition into one error, same as
    MarriageIneligibleError above.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = list(reasons)
        super().__init__("cannot come of age: " + "; ".join(reasons))


class ComingOfAgeOutcome(BaseModel):
    agent: AgentLifeRecord
    choice: ComingOfAgeChoice
    # The household the new adult grew up in, as it stands afterwards: the
    # same object when they stayed, one member shorter when they left, and
    # None when they had no childhood household on file.
    childhood_household: Household | None
    # Where they live now. Same as childhood_household for STAY_AND_CONTRIBUTE.
    household: Household
    dissolved_household_id: str | None = None
    partner: AgentLifeRecord | None = None
    marriage_record: MarriageRecord | None = None
    events: list[LifeEvent]


def leave_household(household: Household | None, agent_id: str) -> Household | None:
    """The childhood household minus one grown child.

    Returns None only when there was no household to leave. An emptied
    household is still returned (as an empty-membered record) rather than
    dropped here: this module has no store to delete from, exactly as
    merge_households explains, so the caller decides what an empty
    household means.
    """
    if household is None:
        return None
    remaining = [m for m in household.member_ids if m != agent_id]
    return household.model_copy(update={"member_ids": remaining})


async def come_of_age(
    agent: AgentLifeRecord,
    childhood_household: Household | None,
    choice: ComingOfAgeChoice,
    *,
    at: GameClock,
    guardianship: GuardianshipPort,
    new_household_id: str | None = None,
    partner: AgentLifeRecord | None = None,
    partner_household: Household | None = None,
    economy: EconomyPort | None = None,
) -> ComingOfAgeOutcome:
    """Run one former minor's transition into the working-age population.

    Ends the parent-billing relationship first, then applies whichever of
    section 26's three futures the agent chose. Ordering matters and is
    the same discipline `marry` follows: the fallible external call
    (guardianship, and the temple fee for PAIR_OFF) happens before any
    record is rewritten, so a failure leaves the town unchanged rather
    than half-transitioned.

    PAIR_OFF delegates to `marry` rather than reimplementing it - a
    newly-adult couple forming a household is section 25's newlywed shape
    "one generation later", not a different mechanic - which is why it
    needs `partner` and an `economy` to charge the temple fee to.
    """
    reasons: list[str] = []
    if agent.age_years < MINOR_AGE_LIMIT_YEARS:
        reasons.append(
            f"{agent.agent_id} is {agent.age_years:.1f}, under the "
            f"{MINOR_AGE_LIMIT_YEARS:.0f}-year threshold"
        )
    if not agent.alive:
        reasons.append(f"{agent.agent_id} is not alive")
    if choice is ComingOfAgeChoice.MOVE_OUT_ALONE and not new_household_id:
        reasons.append("moving out alone needs a new_household_id")
    if choice is ComingOfAgeChoice.PAIR_OFF:
        if partner is None:
            reasons.append("pairing off needs a partner")
        if not new_household_id:
            reasons.append("pairing off needs a new_household_id")
        if economy is None:
            reasons.append("pairing off needs an economy to charge the temple fee to")
    if reasons:
        raise ComingOfAgeError(reasons)

    # The wallet half of the transition, before anything else changes. From
    # here the new adult funds their own cognition, the same as any other
    # working-age agent (section 26).
    await guardianship.end_parent_billing(agent.agent_id)
    adult = agent.model_copy(update={"sponsor_parent_id": None})

    support_ended = LifeEvent(
        type="agent.parent_support.ended",
        agent_id=agent.agent_id,
        payload={
            "former_sponsor_id": agent.sponsor_parent_id,
            "age_years": agent.age_years,
            "choice": choice.value,
        },
    )

    if choice is ComingOfAgeChoice.STAY_AND_CONTRIBUTE:
        # Éamon's early start in section 12, formalized: the household
        # membership does not change, but the agent is now a contributor to
        # the shared bill instead of a line on it.
        if childhood_household is None:
            raise ComingOfAgeError([f"{agent.agent_id} has no household to stay in"])
        stayed = childhood_household
        if agent.agent_id not in stayed.member_ids:
            stayed = stayed.model_copy(
                update={"member_ids": [*stayed.member_ids, agent.agent_id]}
            )
        adult = adult.model_copy(update={"household_id": stayed.household_id})
        return ComingOfAgeOutcome(
            agent=adult,
            choice=choice,
            childhood_household=stayed,
            household=stayed,
            events=[
                support_ended,
                LifeEvent(
                    type="agent.household.contributor_added",
                    agent_id=agent.agent_id,
                    payload={"household_id": stayed.household_id},
                ),
            ],
        )

    if choice is ComingOfAgeChoice.MOVE_OUT_ALONE:
        assert new_household_id is not None  # guarded above
        left = leave_household(childhood_household, agent.agent_id)
        own = Household(
            household_id=new_household_id,
            member_ids=[agent.agent_id],
            formed_at_hours=at.total_hours,
        )
        adult = adult.model_copy(update={"household_id": own.household_id})
        return ComingOfAgeOutcome(
            agent=adult,
            choice=choice,
            childhood_household=left,
            household=own,
            events=[
                support_ended,
                LifeEvent(
                    type="agent.household.formed",
                    agent_id=agent.agent_id,
                    payload={
                        "household_id": own.household_id,
                        "left_household_id": (
                            childhood_household.household_id if childhood_household else None
                        ),
                        "archetype": "single_no_dependents",
                    },
                ),
            ],
        )

    # PAIR_OFF
    assert partner is not None and new_household_id is not None and economy is not None
    left = leave_household(childhood_household, agent.agent_id)
    # The leaver is no longer a member of the childhood household by the time
    # marry() merges anything, so a stale membership cannot follow them into
    # the new one.
    marriage = await marry(
        adult.model_copy(update={"household_id": None}),
        partner,
        None,
        partner_household,
        new_household_id,
        at,
        economy,
    )
    return ComingOfAgeOutcome(
        agent=marriage.agent_a,
        choice=choice,
        childhood_household=left,
        household=marriage.household,
        dissolved_household_id=marriage.dissolved_household_id,
        partner=marriage.agent_b,
        marriage_record=marriage.marriage_record,
        events=[
            support_ended,
            *marriage.events,
            LifeEvent(
                type="agent.household.formed",
                agent_id=agent.agent_id,
                payload={
                    "household_id": marriage.household.household_id,
                    "left_household_id": (
                        childhood_household.household_id if childhood_household else None
                    ),
                    "archetype": "newlyweds",
                    "partner_id": partner.agent_id,
                },
            ),
        ],
    )


async def bring_of_age(
    world: Any,
    guardianship: GuardianshipPort,
    agent: AgentLifeRecord,
    total_hours: float,
) -> tuple[AgentLifeRecord, list[LifeEvent]]:
    """The floor under `come_of_age`, shared by both composition roots.

    ECON-22. The CHOICE belongs to the agent - `come_of_age` is a
    registered tool it can call on its own turn. This is what happens
    anyway: the parent-billing relationship ends the moment they turn
    eighteen whether or not they ever think about it, because the
    alternative is an adult whose parent funds their cognition
    indefinitely simply because the agent never got round to deciding
    anything. An agent that does call the tool overrides the default
    household outcome; it cannot un-end the billing, which is not its
    decision to make.

    Lives here rather than on a runner because it was on a runner, and
    that is exactly why only one of the two tick loops had it: the hourly
    tick in `run_genesis.py` reacted to `agent.aged_to_adult` and the one
    in `wiring.py` did not, so on the second root a minor crossing
    eighteen changed nothing at all - the parent kept paying for their
    thinking for ever and the household never moved. `world` is
    duck-typed to a `.households` mapping so this file still needs to
    know nothing about `WorldState`'s shape, the same way every other
    function in this package takes the facts it needs rather than
    importing them.

    Staying is the default because it is the one outcome that needs no
    resources the agent may not have: no new household id, no partner, no
    temple fee. A failure to transition is logged and swallowed - it
    leaves a new adult in their childhood household, which is a recover-
    able state, where raising would take the whole tick down for one
    birthday.
    """
    household = world.households.get(agent.household_id) if agent.household_id else None
    choice = (
        ComingOfAgeChoice.STAY_AND_CONTRIBUTE
        if household is not None
        else ComingOfAgeChoice.MOVE_OUT_ALONE
    )
    try:
        outcome = await come_of_age(
            agent,
            household,
            choice,
            at=GameClock(total_hours=total_hours),
            guardianship=guardianship,
            new_household_id=f"hh-{agent.agent_id}",
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("could not bring %s of age: %s", agent.agent_id, exc)
        return agent, []

    world.households[outcome.household.household_id] = outcome.household
    left = outcome.childhood_household
    if left is not None and left.household_id != outcome.household.household_id:
        if left.member_ids:
            world.households[left.household_id] = left
        else:
            world.households.pop(left.household_id, None)
    return outcome.agent, outcome.events
