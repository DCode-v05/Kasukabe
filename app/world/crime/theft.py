"""The `steal` primitive's resolution (DESIGN.md section 2 primitive list;
section 6: "Theft: proximity tool on unattended cash/inventory - ~70%
success, ~30% witness memory generated").

This is one of exactly two places in the whole crime/ package allowed to
know the truth about a crime as it happens (fights.py is the other) -
every `WitnessEvent` this module writes names the real thief whether or
not the attempt succeeds and whether or not anyone was there to see it.
Everything downstream of this module (desk.py's complaints, warrants.py's
justifications, outcomes.py's convictions) works from what agents claim
afterward, never from this record; see models.py's module docstring.

Scoped to sim-currency wallets only ("unattended cash"). DESIGN.md's
"inventory" half of "unattended cash/inventory" has no item/inventory
system anywhere in this codebase yet to steal from - `WitnessEvent`'s
`amount_sim` field is where that would need to grow an `item_kind`
sibling once one exists; nothing here or in models.py has to change
shape to add it, only to start populating it.
"""

from __future__ import annotations

import random

from world.crime.errors import CannotTargetSelf, InvalidCrimeAmount
from world.crime.events import EVENT_THEFT_ATTEMPTED, EventSink, build_event, noop_sink
from world.crime.models import CrimeKind, WitnessEvent
from world.crime.ports import EconomyPort, LocationPort, require_co_located
from world.crime.store import CrimeStore

THEFT_SUCCESS_PROBABILITY = 0.70
THEFT_WITNESS_PROBABILITY = 0.30


class TheftDesk:
    def __init__(
        self,
        store: CrimeStore,
        economy: EconomyPort,
        location: LocationPort,
        event_sink: EventSink | None = None,
    ) -> None:
        self.store = store
        self.economy = economy
        self.location = location
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def attempt_theft(
        self,
        thief_agent_id: str,
        victim_agent_id: str,
        amount_sim: int,
        tick: int,
        rng: random.Random,
        potential_witness_agent_ids: list[str] | None = None,
    ) -> WitnessEvent:
        """Resolve one steal attempt. `potential_witness_agent_ids` is
        whatever set of other agents the caller (the future world/ tool
        dispatcher) considers close enough to plausibly notice - this
        module doesn't own proximity beyond the thief/victim pair itself,
        it only rolls whether one of the supplied candidates actually
        does. An empty or omitted list means the theft is structurally
        unwitnessable regardless of the 30% roll (DESIGN.md's rate is a
        probability a witness exists among bystanders, not a probability
        one is conjured from nowhere).
        """
        if thief_agent_id == victim_agent_id:
            raise CannotTargetSelf(thief_agent_id)
        if amount_sim <= 0:
            raise InvalidCrimeAmount(amount_sim)

        location_id = require_co_located(self.location, thief_agent_id, victim_agent_id)
        candidates = list(potential_witness_agent_ids or [])

        succeeded = rng.random() < THEFT_SUCCESS_PROBABILITY
        witnessed = bool(candidates) and rng.random() < THEFT_WITNESS_PROBABILITY
        witness_agent_ids = candidates if witnessed else []

        if succeeded:
            await self.economy.transfer_sim(
                victim_agent_id,
                thief_agent_id,
                amount_sim,
                tick,
                memo=f"theft proceeds ({thief_agent_id} from {victim_agent_id})",
            )

        witness_event = WitnessEvent(
            kind=CrimeKind.THEFT,
            tick=tick,
            location_id=location_id,
            perpetrator_agent_id=thief_agent_id,
            victim_agent_id=victim_agent_id,
            amount_sim=amount_sim,
            theft_succeeded=succeeded,
            witnessed=witnessed,
            witness_agent_ids=witness_agent_ids,
            narrative=(
                f"{thief_agent_id} attempted to steal {amount_sim} sim from "
                f"{victim_agent_id} at {location_id}"
                f"{' and succeeded' if succeeded else ' and failed'}"
            ),
        )
        await self.store.append_witness_event(witness_event)

        await self.event_sink(
            build_event(
                EVENT_THEFT_ATTEMPTED,
                {
                    "witness_event_id": witness_event.witness_event_id,
                    "location_id": location_id,
                    "target_agent_id": victim_agent_id,
                    "involved_agent_ids": [thief_agent_id, victim_agent_id],
                    "witness_agent_ids": witness_agent_ids,
                    "amount_sim": amount_sim,
                    "succeeded": succeeded,
                },
            )
        )
        return witness_event
