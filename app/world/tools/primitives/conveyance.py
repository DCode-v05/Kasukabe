"""license_tool, revoke_license, sell_tool, teach_tool: how capability spreads.

Tool spec C1, and the roadmap's T5.

A tool invented once could never reach a third resident, so **diffusion
was zero by construction** - not as a finding about the residents, but as
a property of the code. `toolify`, `skillify` and the `revise_*` pair all
existed; nothing licensed, sold, granted, taught or inherited what they
produced. C4 ("a tool held by three residents") could not be non-zero.

Three verbs, deliberately distinct, because the difference between them
is the interesting part:

* `license_tool` - you keep it, they may call it, for a term, revocably
  if you said so. A rent.
* `sell_tool`    - they have it and you do not. A sale.
* `teach_tool`   - they have their own copy and you still have yours, and
  nobody can take theirs away. A gift of knowledge.

**What had to change underneath.** An approved invented tool was
registered PUBLIC and callable by anyone, with every step dispatched
under the CREATOR's identity and wallet. So a licence would have granted
nothing (everyone already had it) and a licensee's call would have spent
the inventor's money. Holding is now real: an invented tool is callable
by its owner, its current licensees, and anyone taught their own copy -
and its steps run under whoever called it, at their cost, which is what
the addendum means by "under their own name".
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from world.core.clock import TICKS_PER_DAY
from world.core.events import WorldEvent
from world.ledger.models import Currency, TransactionReason
from world.life.models import AgentLifeRecord
from world.tools.errors import (
    ActorRequirementError,
    AlreadyExistsError,
    LocationGateError,
    NotFoundError,
    ToolValidationError,
)
from world.tools.models import ToolLicence
from world.tools.place_aliases import satisfies_gate
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState


def _owned_tool(world: WorldState, tool_name: str, agent: AgentLifeRecord, verb: str):
    definition = world.custom_tools.get(tool_name)
    if definition is None:
        raise NotFoundError(
            "invented tool", tool_name, sorted(world.custom_tools)
        )
    owner_id = world.tool_owner(tool_name)
    if owner_id != agent.agent_id:
        owner_name = world.agent_names.get(owner_id, owner_id)
        raise ActorRequirementError(
            verb, [f"{tool_name} is owned by {owner_name}, not you."]
        )
    return definition


def _standing_with(world: WorldState, verb: str, agent: AgentLifeRecord, other_id: str):
    if other_id == agent.agent_id:
        raise ToolValidationError(verb, ["name somebody other than yourself."])
    if other_id not in world.agents:
        raise NotFoundError("agent", other_id, sorted(world.agents))
    here = world.location_of(agent.agent_id)
    there = world.location_of(other_id)
    if not satisfies_gate(there, here or ""):
        raise LocationGateError(verb, there, here)
    return world.agents[other_id]


async def _pay(world: WorldState, payer: str, payee: str, amount: int, memo: str) -> None:
    if amount <= 0:
        return
    await world.ledger.move(
        from_account_id=payer, to_account_id=payee, amount=amount,
        currency=Currency.SIM, reason=TransactionReason.TRANSFER,
        tick=world.tick(), memo=memo,
    )


class LicenseToolParams(BaseModel):
    tool_name: str
    licensee_id: str
    fee_sim: int = Field(default=0, ge=0)
    term_days: int = Field(default=1, ge=1)
    revocable: bool = True


class RevokeLicenseParams(BaseModel):
    tool_name: str
    licensee_id: str


class SellToolParams(BaseModel):
    tool_name: str
    buyer_id: str
    price_sim: int = Field(gt=0)


class TeachToolParams(BaseModel):
    tool_name: str
    student_id: str
    fee_sim: int = Field(default=0, ge=0)
    student_consent: bool = Field(
        description="True once they have agreed, in conversation, to be taught.",
    )


async def license_tool_handler(
    world: WorldState, agent: AgentLifeRecord, params: LicenseToolParams
) -> ToolOutcome:
    """Rent out the right to call something you invented."""
    _owned_tool(world, params.tool_name, agent, "license_tool")
    _standing_with(world, "license_tool", agent, params.licensee_id)

    existing = world.licence_for(params.tool_name, params.licensee_id, world.tick())
    if existing is not None:
        raise AlreadyExistsError(
            "licence",
            f"{params.licensee_id} already holds one until tick {existing.until_tick}; "
            "`revoke_license` first",
        )

    await _pay(
        world, params.licensee_id, agent.agent_id, params.fee_sim,
        f"licence for {params.tool_name}",
    )
    until = world.tick() + params.term_days * TICKS_PER_DAY
    licence = ToolLicence(
        tool_name=params.tool_name,
        licensee_id=params.licensee_id,
        granted_by=agent.agent_id,
        granted_tick=world.tick(),
        until_tick=until,
        revocable=params.revocable,
        fee_sim=params.fee_sim,
    )
    world.grant_licence(licence)

    return ToolOutcome(
        result={
            "licensed": params.tool_name,
            "to": params.licensee_id,
            "until_tick": until,
            "fee_paid": params.fee_sim,
            "revocable": params.revocable,
        },
        events=[WorldEvent(
            type="business.tool.licensed",
            tick=world.tick(),
            payload={
                "tool_name": params.tool_name,
                "owner_id": agent.agent_id,
                "agent_id": agent.agent_id,
                "licensee_id": params.licensee_id,
                "until_tick": until,
                "fee_sim": params.fee_sim,
                "revocable": params.revocable,
                "involved_agent_ids": [agent.agent_id, params.licensee_id],
            },
        )],
    )


async def revoke_license_handler(
    world: WorldState, agent: AgentLifeRecord, params: RevokeLicenseParams
) -> ToolOutcome:
    _owned_tool(world, params.tool_name, agent, "revoke_license")
    licence = world.licence_for(params.tool_name, params.licensee_id, world.tick())
    if licence is None:
        raise NotFoundError(
            "licence", f"{params.tool_name} to {params.licensee_id}",
            hint=(
                f"{params.licensee_id} holds no current licence for "
                f"{params.tool_name}."
            ),
        )
    if not licence.revocable:
        raise ActorRequirementError(
            "revoke_license",
            [
                f"you granted this without revocation. It runs to tick "
                f"{licence.until_tick}."
            ],
        )
    world.end_licence(params.tool_name, params.licensee_id)
    return ToolOutcome(
        result={"revoked": params.tool_name, "from": params.licensee_id},
        events=[WorldEvent(
            type="business.tool.license_revoked",
            tick=world.tick(),
            payload={
                "tool_name": params.tool_name,
                "owner_id": agent.agent_id,
                "agent_id": agent.agent_id,
                "licensee_id": params.licensee_id,
                "involved_agent_ids": [agent.agent_id, params.licensee_id],
            },
        )],
    )


async def sell_tool_handler(
    world: WorldState, agent: AgentLifeRecord, params: SellToolParams
) -> ToolOutcome:
    """Hand over ownership for good. Existing licences survive the sale.

    A licence is a promise the tool's holder made to somebody else, and a
    change of owner is not a reason for that promise to lapse - the
    licensee did not sell anything.
    """
    _owned_tool(world, params.tool_name, agent, "sell_tool")
    _standing_with(world, "sell_tool", agent, params.buyer_id)

    await _pay(
        world, params.buyer_id, agent.agent_id, params.price_sim,
        f"bought the {params.tool_name} tool",
    )
    world.transfer_tool_ownership(params.tool_name, params.buyer_id)

    return ToolOutcome(
        result={
            "sold": params.tool_name,
            "to": params.buyer_id,
            "price_sim": params.price_sim,
            "note": (
                "You no longer hold it. Existing licences you granted still run."
            ),
        },
        events=[WorldEvent(
            type="business.tool.sold",
            tick=world.tick(),
            payload={
                "tool_name": params.tool_name,
                "seller_id": agent.agent_id,
                "agent_id": agent.agent_id,
                "buyer_id": params.buyer_id,
                "price_sim": params.price_sim,
                "involved_agent_ids": [agent.agent_id, params.buyer_id],
            },
        )],
    )


async def teach_tool_handler(
    world: WorldState, agent: AgentLifeRecord, params: TeachToolParams
) -> ToolOutcome:
    """Give somebody a copy of their own that nobody can revoke.

    Consent is the caller's assertion, exactly as `teach_skill` has always
    handled it: DESIGN.md section 4 makes teaching "consensual, refusable",
    the agreement is secured in conversation, and the flag is the
    settlement. The addendum names an `accept_teaching` tool; no such tool
    exists and `teach_skill`'s real consent path is this flag, which is
    what "reuse teach_skill's consent path" resolves to on this tree.

    You may teach a tool you hold, not only one you invented: that is the
    whole of diffusion, and a copy that could only ever come from the
    inventor would cap the town at one hop.
    """
    if not params.student_consent:
        raise ActorRequirementError(
            "teach_tool",
            [
                f"{params.student_id} has not consented. Ask them first - "
                "teaching is refusable."
            ],
        )
    if params.tool_name not in world.custom_tools:
        raise NotFoundError("invented tool", params.tool_name, sorted(world.custom_tools))
    holds_it = agent.agent_id == world.tool_owner(params.tool_name) or (
        agent.agent_id in (world.tool_copies.get(params.tool_name) or set())
    )
    if not holds_it:
        raise ActorRequirementError(
            "teach_tool",
            [
                f"you do not hold {params.tool_name}, so you cannot teach it. "
                "A licence lets you call it, not pass it on."
            ],
        )
    _standing_with(world, "teach_tool", agent, params.student_id)

    await _pay(
        world, params.student_id, agent.agent_id, params.fee_sim,
        f"tuition for {params.tool_name}",
    )
    world.grant_tool_copy(params.tool_name, params.student_id)

    return ToolOutcome(
        result={
            "taught": params.tool_name,
            "to": params.student_id,
            "fee_sim": params.fee_sim,
            "note": "Theirs now, under their own name. You cannot take it back.",
        },
        events=[WorldEvent(
            type="business.tool.taught",
            tick=world.tick(),
            payload={
                "tool_name": params.tool_name,
                "teacher_id": agent.agent_id,
                "agent_id": agent.agent_id,
                "student_id": params.student_id,
                "fee_sim": params.fee_sim,
                "holders_after": len(world.holders_of(params.tool_name, world.tick())),
                "involved_agent_ids": [agent.agent_id, params.student_id],
            },
        )],
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="license_tool",
            description=(
                "Grant a person standing with you the right to call a tool you "
                "invented, for a fee now and for a term in game-days. You keep "
                "ownership and may revoke it if you said so. They call it under "
                "their own name at their own cost. To hand over ownership for good "
                "use `sell_tool`; to give them a copy of their own that nobody can "
                "revoke use `teach_tool`. Cost: 0."
            ),
            params_model=LicenseToolParams,
            handler=license_tool_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="revoke_license",
            description=(
                "End a revocable licence you granted. The licensee can no longer "
                "call the tool from the next tick. Cost: 0."
            ),
            params_model=RevokeLicenseParams,
            handler=revoke_license_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="sell_tool",
            description=(
                "Transfer ownership of a tool you invented to a person standing "
                "with you, for a price paid now. The patent record follows them; "
                "existing licences survive. You may no longer call it unless they "
                "license or teach it back. Cost: 0."
            ),
            params_model=SellToolParams,
            handler=sell_tool_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="teach_tool",
            description=(
                "Teach a person standing with you a tool you hold, for a fee if you "
                "set one. They must have agreed first. They receive their own copy, "
                "under their own name, that you cannot revoke. Teaching a skill "
                "rather than a tool is `teach_skill`; for a loan of the right to "
                "call, use `license_tool`. Cost: 0."
            ),
            params_model=TeachToolParams,
            handler=teach_tool_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
