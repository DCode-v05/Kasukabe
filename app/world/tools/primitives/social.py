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

TEACH_PROFICIENCY_RETENTION = 0.7

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
    history = getattr(world, "utterances", None) or ()
    for row in reversed(history):
        tick = row.get("tick")
        if not isinstance(tick, int) or now - tick > CONVERSATION_WINDOW_TICKS:
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
        return None, note
    return world.open_conversation(
        place=location_id, topic="", participants=[speaker_id], now=now
    ), note


class StartConversationParams(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
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
            continue
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
        nearby = ", ".join(world.agent_names.get(other, other) for other in present)
        opened_alone = (
            f"You named nobody, so the table is open. Here to join it: {nearby}."
        )
        result["note"] = (
            f"{result['note']} {opened_alone}" if result.get("note") else opened_alone
        )
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
        raise ActorRequirementError("teach_skill", [f"{params.student_agent_id} has not consented"])

    student = world.require_agent(params.student_agent_id)
    teacher_location = world.location_of(agent.agent_id)
    if not satisfies_gate(world.location_of(student.agent_id), teacher_location or ""):
        raise LocationGateError("teach_skill", teacher_location, world.location_of(student.agent_id))

    mine = {name: level for name, level in world.skills.get(agent.agent_id, {}).items() if level > 0}
    proficiency = mine.get(params.skill, 0)
    if proficiency <= 0:
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
    listener_agent_id: str | None = None
    conversation_id: str | None = None
    intent: str | None = Field(
        default=None, max_length=200,
        description="Why you are saying it, in a few words. Kept on the record; nobody in the town hears it.",
    )


_VOCATIVE_AFTER = ",;:!?—-"

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

    hits: list[tuple[int, str]] = []
    joint = False
    for agent_id in here:
        full = (world.agent_names.get(agent_id) or "").strip()
        if not full:
            continue
        for candidate in (full, full.split()[0]):
            if len(candidate) < 2:
                continue
            for match in re.finditer(
                rf"(?<![A-Za-z]){re.escape(candidate)}(?![A-Za-z])", content
            ):
                after = content[match.end():match.end() + 1]
                before = content[max(0, match.start() - 2):match.start()]
                lead_text = content[:match.start()].rstrip()
                opens = (
                    not lead_text
                    or lead_text.endswith(tuple(SENTENCE_END))
                    or lead_text.endswith(tuple(_VOCATIVE_AFTER))
                )
                vocative = (
                    (after in _VOCATIVE_AFTER and opens)
                    or before.strip().endswith(tuple(_VOCATIVE_AFTER))
                )
                if not vocative and after in SENTENCE_END:
                    vocative = not lead_text or lead_text.endswith(tuple(SENTENCE_END))
                if not vocative:
                    continue
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
        resolved = addressee_from_content(world, agent.agent_id, location_id, params.content)
        if resolved is not None:
            listener_id, addressed_by_name = resolved, True
    if listener_id == agent.agent_id:
        listener_id, addressed_by_name = None, False
    spoke_to_absent: str | None = None
    if listener_id is not None:
        listener = world.require_agent(listener_id)
        if not satisfies_gate(world.location_of(listener.agent_id), location_id or ""):
            spoke_to_absent = world.agent_names.get(listener.agent_id, listener.agent_id)
            listener, listener_id, addressed_by_name = None, None, False

    conversation_id, thread_note = _thread_for(
        world, agent.agent_id, params.conversation_id, location_id, world.tick()
    )
    if conversation_id is not None:
        world.join_conversation(conversation_id, agent.agent_id)
        if listener is not None:
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
            "addressed_by_name": addressed_by_name,
            "involved_agent_ids": involved,
            "target_agent_id": listener.agent_id if listener else None,
        },
    )
    if params.intent and params.intent.strip():
        event.payload["intent"] = params.intent.strip()
    heard_by = [
        world.agent_names.get(other, other)
        for other in sorted(world.agents)
        if other != agent.agent_id and world.location_of(other) == location_id
    ]
    addressed_ids: list[str] = []
    if listener is not None:
        addressed_ids.append(listener.agent_id)
    for other_id in everyone_addressed(world, agent.agent_id, location_id, params.content):
        if other_id != agent.agent_id and other_id not in addressed_ids:
            addressed_ids.append(other_id)
    event.payload["addressed_agent_ids"] = list(addressed_ids)
    for other_id in addressed_ids:
        if other_id not in event.payload["involved_agent_ids"]:
            event.payload["involved_agent_ids"].append(other_id)
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
    absent = _named_but_absent(world, agent.agent_id, location_id, params.content)
    if spoke_to_absent and spoke_to_absent not in absent:
        absent = [*absent, spoke_to_absent]
    if absent:
        result["not_here"] = absent
        result["note"] = (
            f"{', '.join(absent)} did not hear that - not in this place. "
            "Walk to them, or ask someone here."
        )
    for other_id in addressed_ids:
        world.wake_if_working(
            other_id,
            f"{world.agent_names.get(agent.agent_id, agent.agent_id)} spoke your name.",
        )

    if thread_note:
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
