"""CharterEngine: the single object `world/`'s eventual tool dispatcher
and HTTP routes wire up to get all of politics-as-data.

One `CharterStore` shared by one instance each of every sub-engine
(elections, amendments, government actions, patent office, land
petitions, organizations, complaints), all given the same `EventSink` so
every mutation in the module funnels through one place for tracing. This
is the module's only public entry point that callers outside charter/
should construct directly; `create_genesis_charter_engine` is the
constructor `world/`'s startup code is expected to call once, at boot.
"""

from __future__ import annotations

from world.charter.amendments import AmendmentEngine
from world.charter.complaints import ComplaintLedger
from world.charter.elections import ElectionEngine
from world.charter.events import EventSink
from world.charter.genesis import seed_genesis
from world.charter.government import GovernmentActions
from world.charter.land_petitions import LandPetitionQueue
from world.charter.organizations import OrganizationRegistry
from world.charter.patent_office import PatentOffice
from world.charter.store import CharterStore


class CharterEngine:
    """Facade over every charter/ sub-engine, sharing one `CharterStore`.

    Construct via `create_genesis_charter_engine`, not directly - a
    `CharterEngine` built around a store that was never seeded with a
    genesis charter will fail the first time anything reads
    `store.current_charter()`.
    """

    def __init__(self, store: CharterStore, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.elections = ElectionEngine(store, event_sink)
        self.amendments = AmendmentEngine(store, event_sink)
        self.government = GovernmentActions(store, event_sink)
        self.patent_office = PatentOffice(store, event_sink)
        self.land_petitions = LandPetitionQueue(store, event_sink)
        self.organizations = OrganizationRegistry(store, event_sink)
        self.complaints = ComplaintLedger(store, event_sink)


async def create_genesis_charter_engine(
    genesis_tick: int = 0,
    event_sink: EventSink | None = None,
    caretaker_pm_agent_id: str | None = None,
    charter_fields: dict[str, object] | None = None,
) -> CharterEngine:
    """Build a fresh `CharterEngine` seeded with the genesis charter and
    government policy (DESIGN.md section 7: "Genesis Town Charter:
    free-market democracy"), government seats vacant until the first
    election. Callers that also want a first election scheduled should
    call `engine.elections.schedule_election(genesis_tick)` themselves -
    that is a world/ tick-loop decision (exactly when genesis hands off to
    "the clock is running"), not something this factory should assume.
    """
    store = CharterStore()
    await seed_genesis(store, genesis_tick, event_sink, caretaker_pm_agent_id, charter_fields)
    return CharterEngine(store, event_sink)
