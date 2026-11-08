"""The fight mechanic (DESIGN.md section 6: "Fights injure (feeds health
system) - killing is excluded by world physics").

Structural, not just probabilistic, non-lethality: this module never
imports anything death-related, and `HealthPort.apply_injury` (ports.py)
has no parameter and no return value that could carry a fatality - there
is no code path here that could kill an agent even if every random roll
came up worst-case. "Excluded by world physics" reads as an invariant on
the tool registry, not a low-probability event, and this file honors that
literally.

Like theft.py, this is one of the two places in crime/ allowed to know
the truth about a crime as it happens - the `WitnessEvent` it writes
always names the real aggressor, whether or not a witness happened to be
around. See models.py's module docstring.

DESIGN.md pins theft's success/witness rates exactly (70%/30%) but does
not give fight-specific numbers the same way. `FIGHT_INJURY_PROBABILITY`
and `INJURY_SEVERITY_WEIGHTS` below are game-balance placeholders,
flagged as such per app/CONVENTIONS.md's guidance for unpinned constants
(see `world/life/constants.py`'s own header for the same convention).
`FIGHT_WITNESS_PROBABILITY` reuses theft's 30% as the closest documented
analogue rather than inventing an unrelated number.
"""

from __future__ import annotations

import random

from world.crime.errors import CannotTargetSelf
from world.crime.events import EVENT_FIGHT_OCCURRED, EventSink, build_event, noop_sink
from world.crime.models import CrimeKind, FightInjury, FightResult, InjurySeverity, WitnessEvent
from world.crime.ports import HealthPort, LocationPort, require_co_located
from world.crime.store import CrimeStore
from world.crime.theft import THEFT_WITNESS_PROBABILITY

# Placeholder pending game-balance data (see module docstring): not every
# fight draws blood.
FIGHT_INJURY_PROBABILITY = 0.5
FIGHT_WITNESS_PROBABILITY = THEFT_WITNESS_PROBABILITY

# Ordered rarest-first, same walk-the-cumulative-bands shape as
# world/life/health.py's INCIDENCE_ROLL_ORDER, so a severe injury isn't
# crowded out by a common minor one landing on the same draw.
INJURY_SEVERITY_WEIGHTS: tuple[tuple[InjurySeverity, float], ...] = (
    (InjurySeverity.SEVERE, 0.15),
    (InjurySeverity.MODERATE, 0.35),
    (InjurySeverity.MINOR, 0.50),
)


def roll_severity(rng: random.Random) -> InjurySeverity:
    draw = rng.random()
    cumulative = 0.0
    for severity, weight in INJURY_SEVERITY_WEIGHTS:
        cumulative += weight
        if draw < cumulative:
            return severity
    return INJURY_SEVERITY_WEIGHTS[-1][0]


class FightRing:
    def __init__(
        self,
        store: CrimeStore,
        health: HealthPort,
        location: LocationPort,
        event_sink: EventSink | None = None,
    ) -> None:
        self.store = store
        self.health = health
        self.location = location
        self.event_sink = event_sink if event_sink is not None else noop_sink

    async def resolve_fight(
        self,
        aggressor_agent_id: str,
        defendant_agent_id: str,
        tick: int,
        rng: random.Random,
        mutual: bool = False,
        potential_witness_agent_ids: list[str] | None = None,
    ) -> FightResult:
        """Resolve one fight. `mutual=True` rolls an injury for the
        aggressor too (a brawl, not a one-sided assault) - never death
        either way, see module docstring.
        """
        if aggressor_agent_id == defendant_agent_id:
            raise CannotTargetSelf(aggressor_agent_id)

        location_id = require_co_located(self.location, aggressor_agent_id, defendant_agent_id)
        candidates = list(potential_witness_agent_ids or [])

        injuries: list[FightInjury] = []
        if rng.random() < FIGHT_INJURY_PROBABILITY:
            injuries.append(FightInjury(agent_id=defendant_agent_id, severity=roll_severity(rng)))
        if mutual and rng.random() < FIGHT_INJURY_PROBABILITY:
            injuries.append(FightInjury(agent_id=aggressor_agent_id, severity=roll_severity(rng)))

        witnessed = bool(candidates) and rng.random() < FIGHT_WITNESS_PROBABILITY
        witness_agent_ids = candidates if witnessed else []

        witness_event = WitnessEvent(
            kind=CrimeKind.ASSAULT,
            tick=tick,
            location_id=location_id,
            perpetrator_agent_id=aggressor_agent_id,
            victim_agent_id=defendant_agent_id,
            witnessed=witnessed,
            witness_agent_ids=witness_agent_ids,
            narrative=(
                f"fight between {aggressor_agent_id} and {defendant_agent_id} at {location_id}"
                f"{', mutual' if mutual else ''}"
            ),
        )
        await self.store.append_witness_event(witness_event)

        for injury in injuries:
            await self.health.apply_injury(injury.agent_id, injury.severity, tick)

        await self.event_sink(
            build_event(
                EVENT_FIGHT_OCCURRED,
                {
                    "witness_event_id": witness_event.witness_event_id,
                    "location_id": location_id,
                    "involved_agent_ids": [aggressor_agent_id, defendant_agent_id],
                    "witness_agent_ids": witness_agent_ids,
                    "mutual": mutual,
                    "injuries": [
                        {"agent_id": i.agent_id, "severity": i.severity.value} for i in injuries
                    ],
                },
            )
        )

        return FightResult(witness_event_id=witness_event.witness_event_id, injuries=injuries)
