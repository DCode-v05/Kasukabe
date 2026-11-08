"""CrimeEngine: the single object world/'s eventual tool dispatcher wires
up to get all of crime and justice, mirroring
`world.charter.engine.CharterEngine`'s facade shape exactly.

One `CrimeStore` shared by one instance each of every sub-desk (theft,
fights, complaints, investigation, warrants, arrests, outcomes), all
given the same ports and `EventSink` so every mutation in the module
funnels through one place for tracing.
"""

from __future__ import annotations

from world.crime.arrests import ArrestDesk
from world.crime.desk import ComplaintDesk
from world.crime.events import EventSink
from world.crime.fights import FightRing
from world.crime.investigation import InvestigationBoard
from world.crime.metrics import compute_false_conviction_stats
from world.crime.models import FalseConvictionStats
from world.crime.outcomes import OutcomeDesk
from world.crime.ports import EconomyPort, GovernmentPort, HealthPort, LocationPort
from world.crime.store import CrimeStore
from world.crime.theft import TheftDesk
from world.crime.warrants import WarrantOffice


class CrimeEngine:
    def __init__(
        self,
        store: CrimeStore,
        economy: EconomyPort,
        health: HealthPort,
        government: GovernmentPort,
        location: LocationPort,
        event_sink: EventSink | None = None,
    ) -> None:
        self.store = store
        self.theft = TheftDesk(store, economy, location, event_sink)
        self.fights = FightRing(store, health, location, event_sink)
        self.desk = ComplaintDesk(store, event_sink)
        self.investigation = InvestigationBoard(store, government, location, event_sink)
        self.warrants = WarrantOffice(store, government, event_sink)
        self.arrests = ArrestDesk(store, government, location, event_sink)
        self.outcomes = OutcomeDesk(store, economy, government, event_sink)

    async def compute_metrics(self) -> FalseConvictionStats:
        """DESIGN.md section 6/10's flagship false-conviction metric,
        computed from everything this engine's store has recorded so far.
        Safe to call at any point in a run, not just at the end - a
        research job (research/ is read-only, app/CONVENTIONS.md) can
        poll this over time to chart the rate's trajectory.
        """
        witness_events = await self.store.list_witness_events()
        outcomes = await self.store.list_outcomes()
        links = await self.store.list_ground_truth_links()
        return compute_false_conviction_stats(witness_events, outcomes, links)


def create_crime_engine(
    economy: EconomyPort,
    health: HealthPort,
    government: GovernmentPort,
    location: LocationPort,
    event_sink: EventSink | None = None,
) -> CrimeEngine:
    """Build a fresh `CrimeEngine` over a new, empty `CrimeStore`. Unlike
    `charter.engine.create_genesis_charter_engine`, there is no genesis
    seed step - crime/ starts with no cases, no crimes, nothing to
    pre-populate, until the world starts running.
    """
    store = CrimeStore()
    return CrimeEngine(store, economy, health, government, location, event_sink)
