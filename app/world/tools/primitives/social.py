"""marry, pray, teach_skill: DESIGN.md section 2's social primitives.

`marry` is a thin wrapper over `world.life.household.marry` - see that
module's own docstring: "the tool-registry handler for a 'marry' tool
call ... has a single entry point." This module supplies the two facts
life/ deliberately does not look up itself (both agents' current
`households`, taken from this package's `WorldState.households`) and the
`EconomyPort` life/ needs to charge the temple fee (`economy_bridge`).

`pray` and `teach_skill` have no sibling-module implementation to wrap -
DESIGN.md section 7's temple ("Weddings, prayers, donations" plus the
god-tab "prayer feed with grant / silent-deny") and section 4's skill
economy are both still-unbuilt systems this package only provides the
tool-call surface for; see models.py's `PrayerRequest` docstring and this
module's `TEACH_PROFICIENCY_RETENTION` for what is deliberately deferred.
"""

from __future__ import annotations

from typing import Any

import re
import uuid

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.ledger.models import Currency, TransactionReason
from world.life.constants import MARRIAGE_MIN_AGE_YEARS, MINOR_AGE_LIMIT_YEARS
from world.life.birth import submit_birth_request
from world.life.models import BirthRequestStatus
from world.life.household import ComingOfAgeChoice, come_of_age, marry
from world.life.models import AgentLifeRecord
from world.tools.economy_bridge import (
    LedgerEconomyPort,
    LedgerGuardianshipPort,
    life_event_to_world_event,
)
from world.tools.errors import ActorRequirementError, LocationGateError, NotFoundError
from world.tools.locations import LOCATION_TEMPLE
from world.tools.models import PrayerRequest
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.place_aliases import satisfies_gate
from world.tools.state import WorldState

# ASSUMPTION (DESIGN.md section 4 describes "transfers a copy at reduced
# proficiency" with no number): a taught skill lands at 70% of the
# teacher's own proficiency, comfortably "reduced" without gutting the
# point of teaching.
TEACH_PROFICIENCY_RETENTION = 0.7

# How long an exchange between the same two people stays the same exchange.
#
# 12 ticks is two game-hours at core/'s "1 tick = 10 game-minutes". It has to
# be wider than the gap a reply actually takes: an agent perceives a remark on
# its NEXT scheduled turn, and the scheduler runs each agent every third tick
# at the default cadence, so even a prompt answer lands 3-6 ticks later.
# Observed live: Tobias spoke to Wren at tick 39 and Wren answered him at tick
# 43. Anything tighter than that filed the answer as a brand-new conversation.
# Re-exported from world.tools.state, which is where the registry that
# enforces it lives now. Kept importable from here because every existing
# caller and test reads it from this module.
from world.tools.state import CONVERSATION_WINDOW_TICKS  # noqa: E402


def _continuing_conversation(
    world: Any, speaker_id: str, listener_id: str | None, location_id: str | None, now: int
) -> str | None:
    """The id of the conversation already happening in this room, or None.

    A ROOM's talk, not a pair's. Everyone standing in one place and
    speaking within the window is in the same conversation, whoever each
    remark is addressed to and whether or not it names anybody.

    This used to match an exact pair and skip every public remark, which
    made a group conversation structurally impossible. Measured across one
    run: 135 utterances, 30 broadcasts that each became a thread of one
    line, 44 two-person threads, and not a single thread with three people
    in it.

    What it did to a real moment - four residents on one street, four
    remarks inside two ticks, recorded as three unrelated conversations:

        Liora  -> Perrin:  "Why didn't you answer me?"
        Marta  -> Tobias:  "Tobias. Sit down. Please."
        Tobias -> Liora:   "Liora - sorry. Right, so I was walking..."
        Perrin -> Liora:   "I didn't hear you. I was... somewhere else."

    Tobias answers Liora while she is asking Perrin, and Perrin answers her
    as well. That is one conversation in a street.

    The window still bounds it, so a room that fell quiet an hour ago
    starts a fresh conversation rather than reopening a stale one, and the
    place is compared with satisfies_gate so two spellings of one room are
    one room.
    """
    # No history is not a failure to find one - it is the first remark in
    # this room, which genuinely starts a conversation.
    history = getattr(world, "utterances", None) or ()
    for row in reversed(history):
        tick = row.get("tick")
        if not isinstance(tick, int) or now - tick > CONVERSATION_WINDOW_TICKS:
            # The tail is in tick order, so the first row too old to join
            # means every row behind it is older still.
            break
        if not satisfies_gate(row.get("location_id") or "", location_id or ""):
            continue
        existing = row.get("conversation_id")
        if existing:
            return str(existing)
    return None



def others_here(world: Any, agent_id: str, location_id: "str | None") -> list[str]:
    """Everybody else standing in this room, by id.

    The operator's rule, and the one the whole conversation registry now
    turns on: "If there are people around, only then we will have chat
    option available." A room with nobody in it cannot hold a
    conversation, so nothing in it may open one.
    """
    return [
        other
        for other in (world.agents or {})
        if other != agent_id
        and satisfies_gate(world.location_of(other) or "", location_id or "")
    ]


def _thread_for(
    world: Any, speaker_id: str, named: str | None, location_id: str | None, now: int
) -> tuple[str | None, str | None]:
    """(conversation this remark belongs to, a note if the named one could
    not be used).

    The conversation is None when the speaker is alone in the room: see
    the last branch, and world/tools/tests/test_no_chat_without_people.py
    for why an unanswerable thread of one is worse than no thread.

    Three cases, in the order a person would resolve them:

      * an id was named - use it, unless it is closed or is happening in
        another room, in which case say so and fall through;
      * no id, and the speaker is already in an open conversation here -
        carry on with that one, which is what "you were talking to
        somebody" means;
      * otherwise the most recent open conversation in this room, or a new
        one if the room is quiet.
    """
    note: str | None = None
    if named:
        record = world.conversations.get(named)
        if record is None:
            note = f"There is no conversation {named!r}. This was said to the room instead."
        elif not record["open"]:
            note = (
                "That conversation is closed - it went quiet too long ago to "
                "answer into. This was said to the room instead."
            )
        elif not satisfies_gate(record["place"] or "", location_id or ""):
            note = (
                f"That conversation is happening at {record['place']}, and you are "
                f"at {location_id}. Walk there to join it. This was said "
                "where you are standing instead."
            )
        else:
            return named, None

    # The conversation this resident is IN, if any. See
    # WorldState.conversation_pointer: it prefers the most recently opened
    # thread the speaker is on the roster of, so a deliberately convened
    # conversation beats the room's older ambient one.
    #
    # This used to scan `open_conversations_at` in last-spoken order and
    # take the first the speaker was a participant of - which in final4
    # was the implicit thread the room had been carrying, so Perrin's
    # convened table kept its single remark while the family held the
    # conversation he wanted somewhere else.
    pointer = world.conversation_pointer(speaker_id)
    if pointer is not None:
        return pointer, note

    here = world.open_conversations_at(location_id, now)
    for record in here:
        if speaker_id in record["participants"]:
            return record["conversation_id"], note
    if here:
        return here[0]["conversation_id"], note
    if not others_here(world, speaker_id, location_id):
        # Nobody to have it with, so there is nothing to open. Speaking
        # alone still happens - the event fires, the utterance is recorded
        # and somebody arriving inside the window still perceives it - but
        # a thread with one participant is a conversation nobody can ever
        # answer into, and the perception, the Talk book and the township
        # screen all draw it as though somebody could.
        return None, note
    return world.open_conversation(
        place=location_id, topic="", participants=[speaker_id], now=now
    ), note


class StartConversationParams(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    # Everyone you want in it from the start. They must be standing where
    # you are - a conversation happens in a room, it is not a broadcast.
    # Empty is legitimate: it is how you open a table and wait for people.
    with_agent_ids: list[str] = Field(default_factory=list)


async def start_conversation_handler(
    world: WorldState, agent: AgentLifeRecord, params: StartConversationParams
) -> ToolOutcome:
    """Convene a conversation, with a roster, before anybody has spoken.

    The gap this fills, from a run of twenty residents: a resident who
    wanted the household round a table had no way to ask for one. Speech
    was the only way to start a conversation, so "I want to talk to the
    four of you about the boundary" could only be shouted into a room and
    threaded by whoever happened to answer inside the window. Convening
    makes the group the deliberate thing it obviously is, and puts people
    who are listening rather than talking on the record as present.
    """
    location_id = world.location_of(agent.agent_id)

    invited: list[str] = []
    absent: list[str] = []
    for other_id in params.with_agent_ids:
        if other_id == agent.agent_id or other_id in invited:
            continue  # naming yourself, or twice, is not two people
        other = world.agents.get(other_id)
        if other is None:
            absent.append(other_id)
            continue
        if not satisfies_gate(world.location_of(other_id), location_id or ""):
            absent.append(other_id)
            continue
        invited.append(other_id)

    present = others_here(world, agent.agent_id, location_id)
    if not present:
        # The operator's rule: a chat option exists only when there are
        # people around. Opening one alone used to be legitimate and
        # answered "You have opened this on your own. Anyone who speaks
        # here joins it." - a table laid in an empty room, sitting open in
        # the registry the perception, the Talk book and the township
        # screen all read, so a run looked full of chatter that never
        # happened.
        return ToolOutcome(
            result={
                "opened": False,
                "topic": params.topic,
                "place": location_id,
                "note": (
                    "Nobody is here, so there is nobody to have this "
                    "conversation with. Walk to where somebody is, or speak "
                    "and let whoever arrives hear it."
                ),
                "here_now": [],
            },
            events=[],
        )

    conversation_id = world.open_conversation(
        place=location_id,
        topic=params.topic,
        participants=[agent.agent_id, *invited],
        now=world.tick(),
    )

    result: dict[str, Any] = {
        "opened": True,
        "conversation_id": conversation_id,
        "topic": params.topic,
        "place": location_id,
        "participants": [
            world.agent_names.get(other, other)
            for other in world.conversations[conversation_id]["participants"]
        ],
    }
    if absent:
        # Named and not here. Said the way `say` says it, because the
        # correction an agent can act on is "they are somewhere else",
        # not "your roster is shorter than you asked for".
        where = []
        for other_id in absent:
            name = world.agent_names.get(other_id, other_id)
            place = world.location_of(other_id) if other_id in world.agents else None
            where.append(f"{name} ({place})" if place else f"{name} (not in this town)")
        result["not_here"] = where
        here_now = ", ".join(world.agent_names.get(other, other) for other in present)
        result["note"] = (
            f"{', '.join(where)} could not join - a conversation happens where you "
            f"are standing. Here with you: {here_now}."
        )
    if not invited:
        # An empty roster is not an empty room. Opening a table where
        # people are standing is exactly what convening is for, and the
        # note says who can walk up to it.
        nearby = ", ".join(world.agent_names.get(other, other) for other in present)
        opened_alone = (
            f"You named nobody, so the table is open. Here to join it: {nearby}."
        )
        result["note"] = (
            f"{result['note']} {opened_alone}" if result.get("note") else opened_alone
        )
    # On the record, because `world.conversations` is in-memory world state
    # and the run report reads events. Six real trades once ran and the
    # trace said they never happened, for exactly this reason: the mechanic
    # worked, the agents used it, and nothing wrote it down.
    event = WorldEvent(
        type="agent.conversation.opened",
        tick=world.tick(),
        payload={
            "conversation_id": conversation_id,
            "topic": params.topic,
            "opened_by_agent_id": agent.agent_id,
            "location_id": location_id,
            "involved_agent_ids": [agent.agent_id, *invited],
        },
    )
    return ToolOutcome(result=result, events=[event])


class MarryParams(BaseModel):
    spouse_agent_id: str
    new_household_id: str


async def marry_handler(world: WorldState, agent: AgentLifeRecord, params: MarryParams) -> ToolOutcome:
    spouse = world.require_agent(params.spouse_agent_id)
    # satisfies_gate, not !=: an agent who walked to the map's own
    # "Kasukabe Temple" is standing in the temple. See place_aliases.py.
    if not satisfies_gate(world.location_of(spouse.agent_id), LOCATION_TEMPLE):
        raise LocationGateError("marry", LOCATION_TEMPLE, world.location_of(spouse.agent_id))

    household_a = world.households.get(agent.household_id) if agent.household_id else None
    household_b = world.households.get(spouse.household_id) if spouse.household_id else None
    economy = LedgerEconomyPort(world.ledger, world.tick, world)

    outcome = await marry(
        agent, spouse, household_a, household_b, params.new_household_id, world.game_clock(), economy
    )

    world.agents[outcome.agent_a.agent_id] = outcome.agent_a
    world.agents[outcome.agent_b.agent_id] = outcome.agent_b
    world.households[outcome.household.household_id] = outcome.household
    if outcome.dissolved_household_id is not None:
        world.households.pop(outcome.dissolved_household_id, None)

    events = [life_event_to_world_event(e, world.tick()) for e in outcome.events]
    return ToolOutcome(result={"household_id": outcome.household.household_id}, events=events)


class AskForAChildParams(BaseModel):
    """Who is asking with you. Your spouse, normally - `world.households`
    decides who else is in the household, and the request names the
    household rather than a couple, because a child is born into one."""

    with_agent_id: str | None = None


async def ask_for_a_child_handler(
    world: WorldState, agent: AgentLifeRecord, params: AskForAChildParams
) -> ToolOutcome:
    """Put a birth request in the operator's blessing queue.

    UI-05, and ECON-23 word for word ("births are unreachable in every
    run"). The queue, the console panel, the grant and deny routes, the
    population governor and the whole of `world/life/birth.py` were built
    and tested, and on the host every run actually uses there was NO WAY
    TO PUT ANYTHING IN IT. `submit_birth_request` existed only as a method
    on the standalone `WorldServer` - no HTTP route, no tool - and the
    embedded runner builds `AdminExtras(operator_token=...)` with an empty
    `birth_requests` dict that nothing ever writes to. So the God
    console's baby-blessing queue was empty on every run ever made, and
    read as "nobody wants a child".

    This is the missing caller, and it is a TOOL rather than a route
    because a birth is something residents ask for. DESIGN section 3 puts
    the decision with the operator; it does not put the wanting there.

    The world does not decide whether the child is born - that is the
    operator's, through the console, subject to the population dials. This
    only records that somebody asked.
    """
    household_id = agent.household_id
    if not household_id:
        raise ActorRequirementError(
            "ask_for_a_child",
            ["you have no household - a child is born into one; marry, or "
             "come of age into a household of your own, first"],
        )
    household = world.households.get(household_id)
    if household is None:
        raise NotFoundError("household", household_id)

    parent_ids = [agent.agent_id]
    if params.with_agent_id:
        other = world.require_agent(params.with_agent_id)
        if other.household_id != household_id:
            raise ActorRequirementError(
                "ask_for_a_child",
                [f"{params.with_agent_id} is not in your household"],
            )
        parent_ids.append(other.agent_id)
    elif agent.spouse_id:
        parent_ids.append(agent.spouse_id)

    # One pending request per household. Without this a resident that
    # reaches for the tool every turn fills the operator's queue with the
    # same ask, and the queue stops being readable.
    for existing in world.birth_requests.values():
        if existing.household_id == household_id and existing.status is BirthRequestStatus.PENDING:
            return ToolOutcome(
                result={
                    "request_id": existing.request_id,
                    "status": existing.status,
                    "note": "your household has already asked; it is still waiting",
                },
                events=[],
            )

    request_id = f"birth-{uuid.uuid4().hex[:12]}"
    request, life_events = submit_birth_request(
        request_id, household_id, parent_ids, agent.agent_id, world.game_clock()
    )
    world.birth_requests[request_id] = request
    events = [life_event_to_world_event(e, world.tick()) for e in life_events]
    return ToolOutcome(
        result={"request_id": request_id, "status": request.status},
        events=events,
    )


class ComeOfAgeParams(BaseModel):
    """What the new adult decided. `partner_agent_id` is only read for
    PAIR_OFF, `new_household_id` for the two choices that form one."""

    choice: ComingOfAgeChoice
    new_household_id: str | None = None
    partner_agent_id: str | None = None


async def come_of_age_handler(
    world: WorldState, agent: AgentLifeRecord, params: ComeOfAgeParams
) -> ToolOutcome:
    """The tool that makes section 26's "what they do next is not scripted"
    true. Reaching eighteen is decided by the aging clock; what happens
    next is decided here, by the agent, on its own turn.
    """
    partner = (
        world.require_agent(params.partner_agent_id) if params.partner_agent_id else None
    )
    childhood = world.households.get(agent.household_id) if agent.household_id else None
    partner_household = (
        world.households.get(partner.household_id)
        if partner is not None and partner.household_id
        else None
    )

    outcome = await come_of_age(
        agent,
        childhood,
        params.choice,
        at=world.game_clock(),
        guardianship=LedgerGuardianshipPort(world.ledger),
        new_household_id=params.new_household_id,
        partner=partner,
        partner_household=partner_household,
        economy=LedgerEconomyPort(world.ledger, world.tick, world),
    )

    world.agents[outcome.agent.agent_id] = outcome.agent
    if outcome.partner is not None:
        world.agents[outcome.partner.agent_id] = outcome.partner
    world.households[outcome.household.household_id] = outcome.household
    # The childhood household is written back one member shorter - unless the
    # last person just left it, in which case it is dropped rather than kept
    # as an empty record nobody lives in.
    left = outcome.childhood_household
    if left is not None and left.household_id != outcome.household.household_id:
        if left.member_ids:
            world.households[left.household_id] = left
        else:
            world.households.pop(left.household_id, None)
    if outcome.dissolved_household_id is not None:
        world.households.pop(outcome.dissolved_household_id, None)

    events = [life_event_to_world_event(e, world.tick()) for e in outcome.events]
    return ToolOutcome(
        result={"choice": outcome.choice.value, "household_id": outcome.household.household_id},
        events=events,
    )


class PrayParams(BaseModel):
    message: str = ""
    donation_sim: int = Field(default=0, ge=0)


async def pray_handler(world: WorldState, agent: AgentLifeRecord, params: PrayParams) -> ToolOutcome:
    if params.donation_sim > 0:
        from world.ledger.money import TREASURY_ACCOUNT_ID

        await world.ledger.move(
            from_account_id=agent.agent_id,
            to_account_id=TREASURY_ACCOUNT_ID,
            amount=params.donation_sim,
            currency=Currency.SIM,
            reason=TransactionReason.TRANSFER,
            tick=world.tick(),
            memo="temple donation",
        )

    request = PrayerRequest(
        request_id=f"prayer-{uuid.uuid4().hex[:12]}",
        agent_id=agent.agent_id,
        tick=world.tick(),
        message=params.message,
        donation_sim=params.donation_sim,
    )
    world.prayer_requests.append(request)
    event = WorldEvent(
        type="religion.prayer.offered",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "request_id": request.request_id,
            "donation_sim": params.donation_sim,
            "target_agent_id": agent.agent_id,
        },
    )
    return ToolOutcome(result={"request_id": request.request_id}, events=[event])


class TeachSkillParams(BaseModel):
    student_agent_id: str
    skill: str
    student_consent: bool
    price_sim: int = Field(default=0, ge=0)


async def teach_skill_handler(
    world: WorldState, agent: AgentLifeRecord, params: TeachSkillParams
) -> ToolOutcome:
    if not params.student_consent:
        # DESIGN.md section 4: teach is "consensual, refusable" - the
        # calling agent is expected to have secured consent through
        # ordinary conversation (DESIGN.md section 3: "negotiation is
        # conversation; settlement is tool calls") and this flag is that
        # settlement; a call without it is refused the same way any other
        # unmet actor requirement is.
        raise ActorRequirementError("teach_skill", [f"{params.student_agent_id} has not consented"])

    student = world.require_agent(params.student_agent_id)
    teacher_location = world.location_of(agent.agent_id)
    if not satisfies_gate(world.location_of(student.agent_id), teacher_location or ""):
        raise LocationGateError("teach_skill", teacher_location, world.location_of(student.agent_id))

    mine = {name: level for name, level in world.skills.get(agent.agent_id, {}).items() if level > 0}
    proficiency = mine.get(params.skill, 0)
    if proficiency <= 0:
        # The refusal has to name what WOULD have worked, and it has to be
        # about the teacher rather than the world.
        #
        # `teach_skill` has been called twice in the project's history, in
        # two different runs, and both were the same call: teach "Reading"
        # to n18 for 15 sim. Both got `no skill with id 'Reading'`, which
        # says the world has no such skill - untrue, and not the reason.
        # The reason is that the caller does not have it. Neither resident
        # tried again, and nothing has ever been taught for money (ECON-7).
        if mine:
            raise NotFoundError(
                "skill",
                params.skill,
                hint=(
                    f"You cannot teach {params.skill!r} because you do not have it. "
                    "You can teach: "
                    + ", ".join(f"{name} ({level})" for name, level in sorted(mine.items()))
                    + "."
                ),
            )
        raise NotFoundError(
            "skill",
            params.skill,
            hint=(
                f"You cannot teach {params.skill!r}: you have no skills to teach yet. "
                "Skills come from doing the work - harvesting, mining, crafting - or "
                "from being taught by someone who has one."
            ),
        )

    # Part D: a trade secret stays inside the business that owns it.
    #
    # Enforced HERE, at the lesson, rather than at authoring - teaching is the
    # only way a skill leaves the person who has it, so this is the one door
    # that needs a lock. Without it `namespace` would be a label the world
    # ignored, and "a business that knows something others do not" would last
    # exactly until the first employee talked.
    definition = world.skill_definitions.get(params.skill)
    if definition is not None and definition.namespace == "business":
        business = world.businesses.get(definition.owner_id or "")
        allowed = set()
        if business is not None:
            allowed = {business.owner_agent_id, *business.employee_agent_ids}
        if student.agent_id not in allowed:
            raise ActorRequirementError(
                "teach_skill",
                [
                    f"{params.skill!r} belongs to {definition.owner_id} - "
                    f"{student.agent_id} does not work there"
                ],
            )

    if params.price_sim > 0:
        await world.ledger.move(
            from_account_id=student.agent_id,
            to_account_id=agent.agent_id,
            amount=params.price_sim,
            currency=Currency.SIM,
            reason=TransactionReason.TRANSFER,
            tick=world.tick(),
            memo=f"tuition for {params.skill}",
        )

    transferred = max(1, int(proficiency * TEACH_PROFICIENCY_RETENTION))
    student_skills = world.skills.setdefault(student.agent_id, {})
    already = student_skills.get(params.skill, 0)
    student_skills[params.skill] = max(already, transferred)
    # B6 (section 22): record WHO it came from, not just the level. Only
    # when the lesson actually raised the student's proficiency - being
    # taught something you already knew better does not make you their
    # student, and recording it would put a false teacher on the sheet.
    if transferred > already:
        world.skill_sources.setdefault(student.agent_id, {})[params.skill] = agent.agent_id

    event = WorldEvent(
        type="agent.skill.taught",
        tick=world.tick(),
        payload={
            "teacher_agent_id": agent.agent_id,
            "student_agent_id": student.agent_id,
            "skill": params.skill,
            "proficiency": transferred,
            "involved_agent_ids": [agent.agent_id, student.agent_id],
        },
    )
    return ToolOutcome(result={"skill": params.skill, "proficiency": transferred}, events=[event])


class SayParams(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    # None: spoken aloud where the caller is standing, to whoever else is
    # there. Set: addressed to one specific co-located agent (still
    # overhearable by anyone else present - Kasukabe has no whispering
    # primitive, see this function's docstring).
    listener_agent_id: str | None = None
    # Which conversation this remark belongs to. Leave unset and the
    # remark joins the one already happening where you are standing,
    # which is what it has always done. Set it to answer into a specific
    # thread when more than one is open in the same room - two pairs
    # talking in one market used to be recorded as one conversation, with
    # each answer filed under the other's question.
    conversation_id: str | None = None
    # A2A's message intent (docs/A2A_CONFORMANCE.md §2, "Intent on a
    # message": Atlas carries motivation, purpose and scope; a Kasukabe
    # remark carried only its words). Optional, and FOR THE RECORD ONLY:
    # it goes onto the utterance row, the event and the trace, and never
    # into anybody's perception - a listener hears what was said, not why.
    # Keeping it out of the room is what stops it becoming a channel for
    # telling other residents what to think. Guarded by
    # world/tests/test_a_remark_can_say_why.py.
    intent: str | None = Field(
        default=None, max_length=200,
        description="Why you are saying it, in a few words. Kept on the record; nobody in the town hears it.",
    )


# Names an agent may be addressing, and the punctuation that makes it a
# vocative rather than a mention. "Greta, can I help?" is addressed to Greta;
# "I saw Greta yesterday" is not, and must not be.
_VOCATIVE_AFTER = ",;:!?—-"

# A full stop is the same signal, but ONLY when the name opens a sentence.
#
# Measured on run4: thirteen remarks went to the room, nine named somebody
# standing there, and three resolved. The six that did not share one shape
# - the name is its own sentence, which is how a person gets somebody's
# attention across a room:
#
#     'Wren Thorne. I have wheat in my hand and words that need saying.'
#     'Bram Holloway. I worked all morning and have nothing.'
#     'Liora Thorne. You were looking for Perrin Holloway this morning.'
#
# A remark with no addressee can never be answered, never appears in that
# person's open questions and never puts them under any obligation. The
# reply rate sat between 25% and 45% across four runs while the town was,
# in plain English, talking to each other by name.
#
# The full stop is left OUT of _VOCATIVE_AFTER on purpose, because it is
# also just a sentence ending in a name - "I saw Greta." addresses nobody.
# What separates them is POSITION: a vocative full stop opens the remark
# or follows another sentence, and never closes a clause the name is the
# object of. Hence SENTENCE_END rather than another entry above.
SENTENCE_END = ".!?"


def addressee_from_content(
    world: WorldState, speaker_id: str, location_id: str | None, content: str
) -> str | None:
    """Who this remark is addressed to, read out of the words themselves.

    Agents address each other by name and leave `listener_agent_id` unset:
    in a live run 8 of 15 remarks opened by naming somebody and exactly 1
    set the parameter. The intent was in the text and the world threw it
    away, so every remark became a broadcast and nothing was ever a reply.

    Deliberately conservative - a wrong addressee is worse than none, because
    it puts words in one resident's ear that were meant for another:

      * only agents standing in the same place are considered at all;
      * the name must sit against vocative punctuation ("Greta, ...",
        "Ezra—good morning", "...to you, Milo."), so a mention in passing
        ("I saw Greta yesterday") resolves to nobody;
      * two co-located people whose names both match means ambiguity, and
        ambiguity returns None rather than a guess.

    Returns the resolved agent_id, or None to leave the remark public.
    """
    if not content or not location_id:
        return None

    hits, joint = _vocative_hits(world, speaker_id, location_id, content)
    # Joined by a conjunction the remark belongs to the room; otherwise the
    # first person named is the one being spoken to.
    if joint or not hits:
        return None
    return min(hits)[1]


def everyone_addressed(
    world: WorldState, speaker_id: str, location_id: str | None, content: str
) -> list[str]:
    """EVERY co-located resident this remark speaks to, in the order named.

    `addressee_from_content` answers "who is this remark for", singular,
    and that is right for `listener_agent_id`, which holds one id. But a
    remark can address more than one person, and run 19 has the case:
    Ezra Okonjo answered Father Anselm and Tova Okonjo in one utterance -
    "Anselm. You want temple bread, walk to temple... Girl. Two sim for
    walking, you said?" - recorded against Anselm alone. Tova had asked
    the question he was answering, so the world kept telling her she was
    unanswered.

    Returns everyone in vocative position, including the conjunction case
    ("Greta and Ezra, both of you listen") that `addressee_from_content`
    deliberately declines: that remark really is addressed to two people,
    and the only reason it resolves to nobody there is that one id cannot
    hold two names.
    """
    if not content or not location_id:
        return []
    hits, _joint = _vocative_hits(world, speaker_id, location_id, content)
    seen: list[str] = []
    for _position, agent_id in sorted(hits):
        if agent_id not in seen:
            seen.append(agent_id)
    return seen


def _vocative_hits(
    world: WorldState, speaker_id: str, location_id: str | None, content: str
) -> tuple[list[tuple[int, str]], bool]:
    """(position, agent_id) for every co-located name in vocative position,
    plus whether the names were joined by a conjunction.

    Extracted from `addressee_from_content` so the singular answer and the
    full list are read off exactly the same rules - two copies of a
    vocative test would drift, and the wrong one would be the one deciding
    who is owed a reply.
    """
    here = [
        agent_id
        for agent_id in world.agents
        if agent_id != speaker_id and world.location_of(agent_id) == location_id
    ]
    if not here:
        return [], False

    # (position, agent_id) - the EARLIEST vocative is the addressee.
    # "Greta, have you seen Ezra?" is addressed to Greta and asks about
    # Ezra; treating both as candidates and giving up made every such
    # remark public, which is most of how people actually talk.
    hits: list[tuple[int, str]] = []
    joint = False
    for agent_id in here:
        full = (world.agent_names.get(agent_id) or "").strip()
        if not full:
            continue
        # Full name first, then the given name - "Greta Quarshie" and
        # "Greta" both address Greta, and a household of Quarshies means the
        # surname alone addresses nobody in particular.
        for candidate in (full, full.split()[0]):
            if len(candidate) < 2:
                continue
            for match in re.finditer(
                rf"(?<![A-Za-z]){re.escape(candidate)}(?![A-Za-z])", content
            ):
                after = content[match.end():match.end() + 1]
                before = content[max(0, match.start() - 2):match.start()]
                # Position alone is not evidence: "Greta's wheat was the
                # best" opens with the name and addresses nobody. Only the
                # punctuation around it decides.
                lead_text = content[:match.start()].rstrip()
                # A name is in vocative POSITION when it opens the remark,
                # opens a sentence, or is itself preceded by vocative
                # punctuation ("Now then, Wren.").
                opens = (
                    not lead_text
                    or lead_text.endswith(tuple(SENTENCE_END))
                    or lead_text.endswith(tuple(_VOCATIVE_AFTER))
                )
                vocative = (
                    # Trailing punctuation counts only in vocative
                    # position. Without that, "You can go find Perrin, or
                    # go home to Nell" addressed Perrin, who is being
                    # talked about - found as a false positive in the same
                    # run4 measurement that produced the full-stop rule.
                    (after in _VOCATIVE_AFTER and opens)
                    or before.strip().endswith(tuple(_VOCATIVE_AFTER))
                )
                if not vocative and after in SENTENCE_END:
                    # "Wren Thorne. I have wheat in my hand." The name ends
                    # a sentence, and it is a vocative only if it also
                    # STARTS one - at the beginning of the remark, or after
                    # something else that finished. "I saw Greta." fails
                    # both and stays a mention.
                    vocative = not lead_text or lead_text.endswith(tuple(SENTENCE_END))
                if not vocative:
                    continue
                # "Greta and Ezra, both of you listen." addresses two people,
                # and only Ezra carries the comma. Joined by a conjunction,
                # the remark belongs to the room - not to whichever name
                # happened to land next to the punctuation.
                lead = content[:match.start()].rstrip()
                if lead.lower().endswith((" and", " &")):
                    joint = True
                hits.append((match.start(), agent_id))
                break
            if agent_id in hits:
                break

    return hits, joint


def _named_but_absent(
    world: WorldState, speaker_id: str, location_id: str | None, content: str
) -> list[str]:
    """Residents this remark names who are not standing here.

    Uses the same vocative test as addressee_from_content, so a remark that
    merely MENTIONS somebody ("has Greta got wheat?") is not reported as a
    failed address - only one that calls to them.
    """
    if not content:
        return []
    absent: list[str] = []
    for agent_id, name in (world.agent_names or {}).items():
        if agent_id == speaker_id or not name:
            continue
        if world.location_of(agent_id) == location_id:
            continue
        for candidate in (name, name.split()[0]):
            if len(candidate) < 2:
                continue
            for match in re.finditer(
                rf"(?<![A-Za-z]){re.escape(candidate)}(?![A-Za-z])", content
            ):
                after = content[match.end():match.end() + 1]
                before = content[max(0, match.start() - 2):match.start()]
                if after in _VOCATIVE_AFTER or before.strip().endswith(tuple(_VOCATIVE_AFTER)):
                    absent.append(name)
                    break
            if name in absent:
                break
    return sorted(set(absent))


async def say_handler(world: WorldState, agent: AgentLifeRecord, params: SayParams) -> ToolOutcome:
    """RC-2 (ROADMAP.md section 1.2): the town's only act-of-speech
    primitive. DESIGN.md section 3 says "negotiation is conversation;
    settlement is tool calls" - this is what makes the conversation half
    real instead of silent. No location_gate: speech itself is never
    restricted by place (unlike `marry`/`pray`'s temple gate); what IS
    enforced is that an addressed listener must actually be standing
    where the speaker is, same as any face-to-face exchange.

    Delivered as one complete block, never a fragment: this handler only
    ever runs once ExecutionThread.execute_turn() has a finished
    tool_use block in hand - the engine's own turn-loop (frozen, per
    ROADMAP.md's Law 1) already waits out the model's full response
    before dispatching any tool call, `say` included. `params.content` is
    therefore always the whole utterance, never a partial stream; the
    WorldEvent below carries it as a single string, and a listener's
    perception (world.core.perception) hands it to them whole on their
    next turn. There is no path in this design where a receiving agent
    could see a stream "in progress" or need to guess when it ends.

    Deliberately not read back by the caller as a return value beyond a
    conversation id - the model finds out what was said back to it the
    normal way, by perceiving the reply next tick (world.core.perception's
    location_id/involved_agent_ids convention, which the WorldEvent below
    feeds directly).
    """
    location_id = world.location_of(agent.agent_id)
    listener: AgentLifeRecord | None = None
    listener_id = params.listener_agent_id
    addressed_by_name = False
    if listener_id is None:
        # The agent named somebody standing here. Believe the words.
        resolved = addressee_from_content(world, agent.agent_id, location_id, params.content)
        if resolved is not None:
            listener_id, addressed_by_name = resolved, True
    if listener_id == agent.agent_id:
        # A model naming itself as the listener. Observed live: 1 remark in
        # 26. Treated as speech to the room rather than refused - the words
        # were really said and the room really heard them - but never
        # recorded as a conversation with oneself, which is what the Talk
        # book then had to render as "aloud" to avoid reading as a bug.
        listener_id, addressed_by_name = None, False
    spoke_to_absent: str | None = None
    if listener_id is not None:
        listener = world.require_agent(listener_id)
        # Two residents in one room who spelled it differently are still in
        # one room. This comparison is why speech to a co-located neighbour
        # could be refused outright.
        if not satisfies_gate(world.location_of(listener.agent_id), location_id or ""):
            # Said to the room, and reported as unheard by the person named -
            # not refused.
            #
            # This used to raise LocationGateError, which threw the whole
            # turn away: the model had already composed the remark, the room
            # it was standing in really did hear it, and what came back was a
            # tool failure with no usable correction in it. The by-name path
            # two blocks down has always handled the same situation the other
            # way (`not_here`, "walk to them, or ask someone here"), so the
            # same mistake got a teachable answer or a hard error depending
            # only on whether the model set the parameter or wrote a name.
            # Observed live: three of Tobias's four remarks in one morning
            # died this way.
            spoke_to_absent = world.agent_names.get(listener.agent_id, listener.agent_id)
            listener, listener_id, addressed_by_name = None, None, False

    # Any remark joins the conversation already happening in this room -
    # directed or not. Only a room that was quiet gets a new id.
    #
    # This used to run for directed speech only, so a broadcast always
    # minted a fresh thread: 30 of them in one run, each a conversation of
    # one line, none of which anybody could answer into.
    conversation_id, thread_note = _thread_for(
        world, agent.agent_id, params.conversation_id, location_id, world.tick()
    )
    if conversation_id is not None:
        world.join_conversation(conversation_id, agent.agent_id)
        if listener is not None:
            # Being spoken to is being in the conversation. A resident who
            # is addressed by name and says nothing was, until now, in no
            # conversation at all - there was no roster to be on.
            world.join_conversation(conversation_id, listener.agent_id)
        world.note_conversation_turn(conversation_id, world.tick())
    involved = [agent.agent_id] + ([listener.agent_id] if listener else [])
    event = WorldEvent(
        type="agent.utterance.spoken",
        tick=world.tick(),
        payload={
            "speaker_agent_id": agent.agent_id,
            "listener_agent_id": listener.agent_id if listener else None,
            "conversation_id": conversation_id,
            "location_id": location_id,
            "content": params.content,
            "is_public": listener is None,
            # True when the world read the addressee out of the words rather
            # than the parameter. The record must not claim the model set
            # something it did not.
            "addressed_by_name": addressed_by_name,
            "involved_agent_ids": involved,
            "target_agent_id": listener.agent_id if listener else None,
        },
    )
    # Only when given, so a remark without one reads exactly as it always
    # did. Perception renders speech from the speaker and `content` alone
    # (retrieval_depth.format_heard_event), which is what keeps this private.
    if params.intent and params.intent.strip():
        event.payload["intent"] = params.intent.strip()
    # Who actually heard it.
    #
    # `say` used to return a conversation id and nothing else, so an agent
    # could shout at somebody who was not in the room and get a success back.
    # Observed: Liora Thorne, whose perception read "With you: Wren Thorne,
    # Marta Thorne, Idris Thorne", called out "Perrin! You walking or what?"
    # twice. Perrin was across town. Nothing in the result told her, so she
    # had no way to learn it and every reason to try again.
    #
    # Names, not ids: the agent is about to read this back and act on it.
    heard_by = [
        world.agent_names.get(other, other)
        for other in sorted(world.agents)
        if other != agent.agent_id and world.location_of(other) == location_id
    ]
    # Everyone this remark actually spoke to, not only the one the
    # parameter named.
    #
    # Run 19: Ezra Okonjo answered Father Anselm and Tova Okonjo in one
    # utterance addressed to Anselm - "Anselm. You want temple bread, walk
    # to temple... Girl. Two sim for walking, you said?" - and the record
    # showed one listener. Tova had asked the question he answered, so the
    # world went on telling her she was unanswered and the roster credited
    # her reply to somebody else.
    addressed_ids: list[str] = []
    if listener is not None:
        addressed_ids.append(listener.agent_id)
    for other_id in everyone_addressed(world, agent.agent_id, location_id, params.content):
        if other_id != agent.agent_id and other_id not in addressed_ids:
            addressed_ids.append(other_id)
    event.payload["addressed_agent_ids"] = list(addressed_ids)
    # And they are INVOLVED, which is the field that decides whether the
    # event reaches them at all.
    #
    # `involved_agent_ids` is built above from [speaker, listener], before
    # this block has worked out who was really addressed, and used to be
    # left that way. core/perception.py sets `involves_self` from it and
    # carries an involving event to an agent EVEN FROM ANOTHER LOCATION -
    # so a resident selected by a vocative rather than by the parameter
    # was not involved, and stopped hearing the remark the moment they
    # walked out of the room. Being named is precisely when that must not
    # happen.
    for other_id in addressed_ids:
        if other_id not in event.payload["involved_agent_ids"]:
            event.payload["involved_agent_ids"].append(other_id)
    # The roster, so the perception can tell somebody IN the conversation
    # from somebody merely within earshot - Goffman's ratified-unaddressed
    # against his bystander. See retrieval_depth.participation_role.
    if conversation_id is not None:
        record = world.conversations.get(conversation_id)
        if record is not None:
            event.payload["conversation_participants"] = list(record["participants"])

    result: dict[str, Any] = {
        "conversation_id": conversation_id,
        "heard_by": heard_by,
        "addressed": [world.agent_names.get(a, a) for a in addressed_ids],
        "location_id": location_id,
    }
    if not heard_by:
        result["note"] = (
            "Nobody was here to hear that. Speech only reaches people "
            "standing in the same place."
        )
    # The specific correction: they named somebody, and that somebody is
    # somewhere else. Said plainly, because "heard_by: []" is a fact the
    # agent still has to interpret.
    absent = _named_but_absent(world, agent.agent_id, location_id, params.content)
    if spoke_to_absent and spoke_to_absent not in absent:
        # Named by parameter rather than in the words, so the vocative test
        # above cannot have found them.
        absent = [*absent, spoke_to_absent]
    if absent:
        result["not_here"] = absent
        result["note"] = (
            f"{', '.join(absent)} did not hear that - not in this place. "
            "Walk to them, or ask someone here."
        )
    # B1 interruption rule 3: being named by somebody in your hearing wakes
    # a working resident, and ambient speech does not. That difference is
    # the whole of "working while people talk around you" - a resident who
    # cannot be interrupted by its own name is not present in the town, and
    # one woken by every remark in the room never finishes a job.
    for other_id in addressed_ids:
        world.wake_if_working(
            other_id,
            f"{world.agent_names.get(agent.agent_id, agent.agent_id)} spoke your name.",
        )

    if thread_note:
        # Last, and folded into whatever else the result had to say. The
        # remark itself still happened - it was spoken in a real room to
        # real people - so this is an addition to the outcome, not a
        # replacement for it. Setting it earlier meant "nobody was here to
        # hear that" quietly overwrote "that conversation is closed", and
        # the agent was told the less useful of two true things.
        result["note"] = f"{result['note']} {thread_note}" if result.get("note") else thread_note
    return ToolOutcome(result=result, events=[event])


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="say",
            description=(
                "Speak aloud where you are currently standing. Leave listener_agent_id "
                "unset to address everyone present, or set it to speak to one "
                "co-located agent specifically."
            ),
            params_model=SayParams,
            handler=say_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="start_conversation",
            description=(
                "Call people together for a conversation about something, here "
                "where you are standing. Name everyone you want in it - they must "
                "be in this place - or name nobody and wait for whoever comes. "
                "Then use say with the conversation_id it gives you, so a room "
                "holding two conversations keeps them apart."
            ),
            params_model=StartConversationParams,
            handler=start_conversation_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="marry",
            description="Marry another agent in a temple ceremony, merging households.",
            params_model=MarryParams,
            handler=marry_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=LOCATION_TEMPLE,
            actor_requirements=ActorRequirements(min_age=MARRIAGE_MIN_AGE_YEARS),
        ),
        ToolSpec(
            name="ask_for_a_child",
            description=(
                "Ask for a child to be born into your household. It is not "
                "granted here - the request waits for a blessing, and the town "
                "may be at its population limit."
            ),
            params_model=AskForAChildParams,
            handler=ask_for_a_child_handler,
            namespace=ToolNamespace.PUBLIC,
            # No location gate. Wanting a child is not a thing you do at a
            # counter, and the temple gate on `marry` is about the
            # ceremony, not about the wanting.
            location_gate=None,
            actor_requirements=ActorRequirements(min_age=MARRIAGE_MIN_AGE_YEARS),
        ),
        ToolSpec(
            name="come_of_age",
            description=(
                "Decide what happens now you are eighteen: stay in the family "
                "household and contribute to its bill, move out on your own, or "
                "pair off and start a household together."
            ),
            params_model=ComeOfAgeParams,
            handler=come_of_age_handler,
            namespace=ToolNamespace.PUBLIC,
            # No location gate: leaving home is not a thing you do at a
            # counter. PAIR_OFF charges the temple fee through marry(), which
            # keeps its own temple gate on the marry tool.
            location_gate=None,
            actor_requirements=ActorRequirements(min_age=MINOR_AGE_LIMIT_YEARS),
        ),
        ToolSpec(
            name="pray",
            description="Pray at the temple, with an optional donation.",
            params_model=PrayParams,
            handler=pray_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=LOCATION_TEMPLE,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="teach_skill",
            description="Teach a skill to a co-located, consenting student.",
            params_model=TeachSkillParams,
            handler=teach_skill_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
