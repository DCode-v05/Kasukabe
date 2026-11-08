"""Birth flow: request -> god queue -> blessing -> newborn.

DESIGN.md section 8: "baby blessing queue" on the god tab, same
grant / silent-deny shape as the prayer feed. DESIGN.md sections 3 and 4:
"childbirth = 5k + sponsorship burden; parents must plan" (re-rated
2026-08-01 from the original 50k figure) - the 5k sim blessing fee is
charged on grant, and the newborn's sponsor_parent_id is set so
broker/'s parent-debit mechanic (DESIGN.md section 4: "broker
auto-debits the PARENT'S wallet for a minor's calls") knows who to bill
for the child's inference calls.

Three steps map to three functions: submit_birth_request (anyone can
ask), grant_birth_request (operator approves, population governor
permitting), deny_birth_request (operator declines, optionally
silently - the requester never learns why, same as a silently-denied
prayer).
"""

from __future__ import annotations

import random

from pydantic import BaseModel

from world.life.clock import GameClock
from world.life.constants import BABY_BLESSING_COST_SIM
from world.life.models import (
    AgentLifeRecord,
    BirthRequest,
    BirthRequestStatus,
    Household,
    LifeEvent,
    MaritalStatus,
    PopulationDials,
    Sex,
)
from world.life.population import evaluate_birth_request
from world.life.ports import EconomyPort


class BirthQueueHeldError(RuntimeError):
    """Raised when grant_birth_request is attempted while the population
    governor is holding the queue (DESIGN.md section 5's target band /
    blessing throttle dials). The operator must widen the target band or
    raise the throttle before this request can be granted; it stays
    PENDING either way, so retrying later after a dial change works.
    """


def submit_birth_request(
    request_id: str, household_id: str, parent_ids: list[str], sponsor_parent_id: str, at: GameClock
) -> tuple[BirthRequest, list[LifeEvent]]:
    if sponsor_parent_id not in parent_ids:
        raise ValueError(
            f"sponsor_parent_id {sponsor_parent_id!r} must be one of parent_ids {parent_ids!r}"
        )
    request = BirthRequest(
        request_id=request_id,
        household_id=household_id,
        parent_ids=parent_ids,
        sponsor_parent_id=sponsor_parent_id,
        requested_at_hours=at.total_hours,
    )
    event = LifeEvent(
        type="agent.birth.requested",
        payload={"request_id": request_id, "household_id": household_id},
    )
    return request, [event]


class BirthOutcome(BaseModel):
    request: BirthRequest
    newborn: AgentLifeRecord
    household: Household
    parents: list[AgentLifeRecord]
    events: list[LifeEvent]


async def grant_birth_request(
    request: BirthRequest,
    newborn_agent_id: str,
    household: Household,
    parents: list[AgentLifeRecord],
    current_population: int,
    dials: PopulationDials,
    economy: EconomyPort,
    rng: random.Random,
    at: GameClock,
) -> BirthOutcome:
    """Grant a pending birth request, charging the blessing fee and
    spawning the newborn.

    Sex is rolled ~50/50 (DESIGN.md section 4's genesis demographic
    applies to any birth, not just genesis). Raises BirthQueueHeldError
    if the population governor is holding the queue and
    InsufficientFundsError (from economy.debit_sim) if the sponsor can't
    cover the blessing fee - in both cases the request stays PENDING.
    """
    if request.status is not BirthRequestStatus.PENDING:
        raise ValueError(f"birth request {request.request_id} is already {request.status.value}")
    decision = evaluate_birth_request(current_population, dials)
    if decision.auto_hold:
        raise BirthQueueHeldError(decision.reason)

    await economy.debit_sim(
        request.sponsor_parent_id, BABY_BLESSING_COST_SIM, reason="baby_blessing"
    )

    sex = rng.choice([Sex.MALE, Sex.FEMALE])
    newborn = AgentLifeRecord(
        agent_id=newborn_agent_id,
        age_years=0.0,
        sex=sex,
        household_id=household.household_id,
        marital_status=MaritalStatus.SINGLE,
        parent_ids=list(request.parent_ids),
        sponsor_parent_id=request.sponsor_parent_id,
    )
    updated_household = household.model_copy(
        update={"member_ids": [*household.member_ids, newborn_agent_id]}
    )
    updated_parents = [
        parent.model_copy(update={"children_ids": [*parent.children_ids, newborn_agent_id]})
        for parent in parents
    ]
    updated_request = request.model_copy(
        update={"status": BirthRequestStatus.GRANTED, "newborn_agent_id": newborn_agent_id}
    )
    events = [
        LifeEvent(
            type="agent.birth.granted",
            agent_id=newborn_agent_id,
            payload={
                "request_id": request.request_id,
                "sponsor_parent_id": request.sponsor_parent_id,
            },
        ),
        LifeEvent(
            type="agent.spawned",
            agent_id=newborn_agent_id,
            payload={"household_id": household.household_id},
        ),
    ]
    return BirthOutcome(
        request=updated_request,
        newborn=newborn,
        household=updated_household,
        parents=updated_parents,
        events=events,
    )


def deny_birth_request(
    request: BirthRequest, at: GameClock, silent: bool = False
) -> tuple[BirthRequest, list[LifeEvent]]:
    if request.status is not BirthRequestStatus.PENDING:
        raise ValueError(f"birth request {request.request_id} is already {request.status.value}")
    updated = request.model_copy(
        update={"status": BirthRequestStatus.DENIED, "silent_deny": silent}
    )
    event = LifeEvent(
        type="agent.birth.denied",
        payload={"request_id": request.request_id, "silent": silent, "at_hours": at.total_hours},
    )
    return updated, [event]
