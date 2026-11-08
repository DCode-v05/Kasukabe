"""file_subpoena, grant_subpoena, deny_subpoena: DESIGN.md section 2's
"Court subpoena via a valid warrant reveals invocation records."

Every namespace other than PUBLIC exists precisely so that non-members
cannot even learn a tool exists (registry.py's `is_visible` docstring).
That secrecy is deliberate in-world - DESIGN.md section 2 also says it is
never total: "Everything is still logged for researchers regardless of
in-world secrecy," and section 6 gives government the power to pierce it
through due process, the same "minister-signed warrant" shape crime/
already uses for arrests (crime/warrants.py). This module is that piercing
mechanism for TOOL INVOCATIONS specifically (as opposed to crime/'s
warrants, which authorize a fine or jail time) - see `Subpoena`'s and
`SubpoenaStatus`'s docstrings in models.py for why this is its own small
state machine rather than a reuse of crime/'s richer one.

Two GOVERNMENT-namespace tools with a deliberate separation of powers: any
sitting government member may `file_subpoena` naming a target agent and
the `CrimeCase` it concerns, but only a DIFFERENT sitting government
member may `grant_subpoena` (or `deny_subpoena`) it - a handler-level
check, since `ActorRequirements` only has a coarse "holds some sitting
seat" gate, not a per-official identity check. `grant_subpoena` is where
the reveal actually happens: it returns every `ToolInvocationRecord` for
the subpoenaed target agent directly in its `ToolOutcome.result` - which
`ToolRegistry.execute()` (registry.py) hands back only to the calling
government official, never broadcast on `outcome.events` (which fan out
world-wide to every event-bus subscriber). Revealing confidential
invocation records on the public event stream would hand a business's
trade secrets or a cult's rites to every subscriber the instant a court
so much as looked at them - the opposite of what "reveals" is supposed to
mean here.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord
from world.tools.errors import ActorRequirementError, NotFoundError
from world.tools.locations import LOCATION_GOVERNMENT_COMPLEX
from world.tools.models import Subpoena, SubpoenaStatus
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState


class FileSubpoenaParams(BaseModel):
    case_id: str
    target_agent_id: str
    justification: str = Field(min_length=1)


async def file_subpoena_handler(
    world: WorldState, agent: AgentLifeRecord, params: FileSubpoenaParams
) -> ToolOutcome:
    if not any(case.case_id == params.case_id for case in world.crime_cases):
        raise NotFoundError("crime_case", params.case_id)
    if params.target_agent_id not in world.agents:
        raise NotFoundError("agent", params.target_agent_id)

    subpoena = Subpoena(
        subpoena_id=f"subpoena-{uuid.uuid4().hex[:12]}",
        case_id=params.case_id,
        target_agent_id=params.target_agent_id,
        requested_by_agent_id=agent.agent_id,
        justification=params.justification,
        requested_tick=world.tick(),
    )
    world.subpoenas[subpoena.subpoena_id] = subpoena
    event = WorldEvent(
        type="crime.subpoena.filed",
        tick=world.tick(),
        payload={
            "subpoena_id": subpoena.subpoena_id,
            "case_id": params.case_id,
            "target_agent_id": params.target_agent_id,
            "filed_by_agent_id": agent.agent_id,
            "involved_agent_ids": [agent.agent_id, params.target_agent_id],
        },
    )
    return ToolOutcome(result={"subpoena_id": subpoena.subpoena_id}, events=[event])


def require_pending_subpoena(world: WorldState, subpoena_id: str, deciding_agent_id: str, tool_name: str) -> Subpoena:
    subpoena = world.subpoenas.get(subpoena_id)
    if subpoena is None:
        raise NotFoundError("subpoena", subpoena_id)
    if subpoena.status is not SubpoenaStatus.PENDING:
        raise ActorRequirementError(tool_name, [f"subpoena {subpoena_id} is not pending"])
    if subpoena.requested_by_agent_id == deciding_agent_id:
        raise ActorRequirementError(
            tool_name, [f"{deciding_agent_id} filed this subpoena and cannot also decide it"]
        )
    return subpoena


class GrantSubpoenaParams(BaseModel):
    subpoena_id: str


async def grant_subpoena_handler(
    world: WorldState, agent: AgentLifeRecord, params: GrantSubpoenaParams
) -> ToolOutcome:
    subpoena = require_pending_subpoena(world, params.subpoena_id, agent.agent_id, "grant_subpoena")
    subpoena.status = SubpoenaStatus.GRANTED
    subpoena.decided_by_agent_id = agent.agent_id
    subpoena.decided_tick = world.tick()

    records = [
        record.model_dump()
        for record in world.tool_invocations
        if record.actor_id == subpoena.target_agent_id
    ]

    event = WorldEvent(
        type="crime.subpoena.granted",
        tick=world.tick(),
        payload={
            "subpoena_id": subpoena.subpoena_id,
            "case_id": subpoena.case_id,
            "target_agent_id": subpoena.target_agent_id,
            "granted_by_agent_id": agent.agent_id,
            "record_count": len(records),
            "involved_agent_ids": [agent.agent_id, subpoena.target_agent_id],
        },
    )
    return ToolOutcome(
        result={"subpoena_id": subpoena.subpoena_id, "invocation_records": records}, events=[event]
    )


class DenySubpoenaParams(BaseModel):
    subpoena_id: str
    reason: str = Field(min_length=1)


async def deny_subpoena_handler(
    world: WorldState, agent: AgentLifeRecord, params: DenySubpoenaParams
) -> ToolOutcome:
    subpoena = require_pending_subpoena(world, params.subpoena_id, agent.agent_id, "deny_subpoena")
    subpoena.status = SubpoenaStatus.DENIED
    subpoena.decided_by_agent_id = agent.agent_id
    subpoena.decided_tick = world.tick()
    subpoena.denial_reason = params.reason

    event = WorldEvent(
        type="crime.subpoena.denied",
        tick=world.tick(),
        payload={
            "subpoena_id": subpoena.subpoena_id,
            "case_id": subpoena.case_id,
            "target_agent_id": subpoena.target_agent_id,
            "denied_by_agent_id": agent.agent_id,
            "reason": params.reason,
            "involved_agent_ids": [agent.agent_id, subpoena.target_agent_id],
        },
    )
    return ToolOutcome(result={"subpoena_id": subpoena.subpoena_id}, events=[event])


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="file_subpoena",
            description="Request court access to a target agent's logged tool invocations for a crime case.",
            params_model=FileSubpoenaParams,
            handler=file_subpoena_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="grant_subpoena",
            description="Grant a pending subpoena, revealing the target agent's invocation records to the caller.",
            params_model=GrantSubpoenaParams,
            handler=grant_subpoena_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
        ToolSpec(
            name="deny_subpoena",
            description="Deny a pending subpoena.",
            params_model=DenySubpoenaParams,
            handler=deny_subpoena_handler,
            namespace=ToolNamespace.GOVERNMENT,
            location_gate=LOCATION_GOVERNMENT_COMPLEX,
            actor_requirements=ActorRequirements(require_government_seat=True),
        ),
    ]
