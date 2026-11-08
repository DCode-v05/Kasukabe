"""Event emission for the crime module.

Same seam as `world/charter/events.py`: a `type` string of
`<domain>.<subject>.<past_tense_verb>` (app/CONVENTIONS.md's "Event
naming scheme"), an `EventSink` async callable a caller supplies, and a
no-op default so this package is fully usable and testable standalone.
`world/`'s eventual HTTP/tick wiring is expected to pass a sink that
forwards into `world.core.events.EventBus` (which already validates this
exact format and domain list) and from there to Supabase/Realtime.

Every payload below carries `location_id` plus whichever of
`target_agent_id` / `involved_agent_ids` / `witness_agent_ids` applies,
the payload-key convention `world/core/perception.py` documents for its
`default_interrupt_policy` - so once a real EventBus sink is wired in,
crime events correctly interrupt the right agents' schedules and show up
in the right agents' perception for free, no perception.py change
required.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


async def noop_sink(event: dict[str, Any]) -> None:
    """Default `EventSink`: discards the event."""
    return None


def build_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Assemble one event row. `event_type` must already be one of the
    EVENT_* constants below; this function does not validate the format
    (world.core.events.WorldEvent does, once a real sink wires this
    package's events onto that bus)."""
    return {"type": event_type, "payload": payload}


# All in the `crime` domain (app/CONVENTIONS.md's event domain table:
# "crime | world/ | crime.theft.attempted, crime.warrant.issued"). Both
# of those two literal examples are reproduced verbatim below - they are
# not a coincidence, CONVENTIONS.md was written anticipating this module.
EVENT_THEFT_ATTEMPTED = "crime.theft.attempted"
EVENT_FIGHT_OCCURRED = "crime.fight.occurred"
EVENT_COMPLAINT_FILED = "crime.complaint.filed"
EVENT_CASE_OPENED = "crime.case.opened"
EVENT_INTERVIEW_LOGGED = "crime.interview.logged"
EVENT_WARRANT_REQUESTED = "crime.warrant.requested"
EVENT_WARRANT_ISSUED = "crime.warrant.issued"
EVENT_WARRANT_DENIED = "crime.warrant.denied"
EVENT_ARREST_MADE = "crime.arrest.made"
EVENT_FINE_IMPOSED = "crime.fine.imposed"
EVENT_JAIL_IMPOSED = "crime.jail.imposed"
EVENT_CASE_DISMISSED = "crime.case.dismissed"
EVENT_CONFINEMENT_RELEASED = "crime.confinement.released"
