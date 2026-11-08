"""steal: DESIGN.md section 6, "Theft: proximity tool on unattended
cash/inventory - ~70% success, ~30% witness memory generated."

"Unattended" is modeled precisely: a Storage (models.py) is a container
sitting at a fixed location_id, separate from its owner's personal,
always-on-them inventory - stealing targets a Storage whose owner is not
currently standing at that same location. Stealing sim-currency directly
out of a wallet is deliberately not modeled: a wallet has no location to
be unattended *at* (world.tools.inventory and world.ledger both track
holdings per-owner, not per-place), so "cash" here means sim-currency an
owner has physically stored in a Storage, same as any other item -
flagged as an open question in this task's report for whoever eventually
wants pocket-picking too.

Two independent rolls per DESIGN.md's own wording ("~70% success, ~30%
witness") rather than one roll deciding both: a witness can see a
theft that still fails, and a theft can succeed unseen. `world.rng` is
an injected `random.Random` (state.py) so tests can seed it for
deterministic success/witness outcomes instead of asserting on
probabilities.
"""

from __future__ import annotations

from types import SimpleNamespace

import uuid

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord
from world.tools.errors import ActorRequirementError, InsufficientInventoryError, LocationGateError, NotFoundError
from world.tools.models import CrimeCase, CrimeCaseKind
from world.tools.place_aliases import satisfies_gate
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState

STEAL_SUCCESS_PROBABILITY = 0.70
STEAL_WITNESS_PROBABILITY = 0.30


class StealParams(BaseModel):
    storage_id: str
    item: str
    quantity: int = Field(gt=0)


def _stealable(world: WorldState, target_id: str):
    """The container `target_id` names - a Storage, or a shop's shelf.

    TOOL-11.8. `steal` reached `world.storages` only. Shops did not have a
    shelf at all until TOOL-03, and the moment they got one the town had
    goods sitting in a public room that theft could not see: a resident
    could `buy` from a shelf but not take from it, which makes shoplifting
    the one crime the world forbids by omission rather than by rule.

    A business's shelf is `world.inventory` under its business id, and the
    premises are its location, so it has everything a Storage has. Returned
    as the same shape so the handler below needs no second code path - and
    so a case filed over a stolen loaf names the shopkeeper as victim
    exactly the way GOV-02 requires.
    """
    storage = world.storages.get(target_id)
    if storage is not None:
        return storage

    business = world.businesses.get(target_id)
    if business is None:
        return None
    return SimpleNamespace(
        storage_id=business.business_id,
        owner_id=business.owner_agent_id,
        location_id=business.location_id,
    )


async def steal_handler(world: WorldState, agent: AgentLifeRecord, params: StealParams) -> ToolOutcome:
    storage = _stealable(world, params.storage_id)
    if storage is None:
        from world.tools.primitives.commerce import storages_worth_naming

        raise NotFoundError(
            "storage", params.storage_id, storages_worth_naming(world, agent.agent_id)
        )
    if storage.owner_id == agent.agent_id:
        raise ActorRequirementError("steal", [f"{agent.agent_id} already owns {params.storage_id}"])

    thief_location = world.location_of(agent.agent_id)
    if not satisfies_gate(thief_location, storage.location_id):
        raise LocationGateError("steal", storage.location_id, thief_location)
    if satisfies_gate(world.location_of(storage.owner_id), storage.location_id):
        raise ActorRequirementError(
            "steal", [f"{storage.owner_id} is present at {storage.location_id}, nothing is unattended"]
        )

    available = world.inventory.quantity(storage.storage_id, params.item)
    if available < params.quantity:
        raise InsufficientInventoryError(storage.storage_id, params.item, params.quantity, available)

    succeeded = world.rng.random() < STEAL_SUCCESS_PROBABILITY
    witnessed = world.rng.random() < STEAL_WITNESS_PROBABILITY
    witness_agent_ids = sorted(
        a for a in world.locations.agents_at(storage.location_id) if a != agent.agent_id
    )

    case = CrimeCase(
        case_id=f"case-{uuid.uuid4().hex[:12]}",
        kind=CrimeCaseKind.THEFT,
        tick=world.tick(),
        filer_or_actor_agent_id=agent.agent_id,
        against_agent_id="",
        victim_agent_id=storage.owner_id,
        ground_truth_perpetrator_id=agent.agent_id,
        description=f"attempted theft of {params.quantity} {params.item} from {params.storage_id}",
        outcome="succeeded" if succeeded else "failed",
    )
    world.crime_cases.append(case)

    if succeeded:
        world.inventory.move(storage.storage_id, agent.agent_id, params.item, params.quantity)

    payload = {
        "case_id": case.case_id,
        "victim_agent_id": storage.owner_id,
        "item": params.item,
        "quantity": params.quantity,
        "succeeded": succeeded,
        "target_agent_id": storage.owner_id,
    }
    if witnessed:
        payload["witness_agent_ids"] = witness_agent_ids
        payload["thief_agent_id"] = agent.agent_id
    events = [WorldEvent(type="crime.theft.attempted", tick=world.tick(), payload=payload)]
    if witnessed:
        events.append(
            WorldEvent(
                type="crime.theft.witnessed",
                tick=world.tick(),
                payload={
                    "case_id": case.case_id,
                    "location_id": storage.location_id,
                    "witness_agent_ids": witness_agent_ids,
                },
            )
        )

    return ToolOutcome(
        result={"succeeded": succeeded, "witnessed": witnessed, "case_id": case.case_id}, events=events
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="steal",
            description="Attempt to take an item from an unattended storage container.",
            params_model=StealParams,
            handler=steal_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
