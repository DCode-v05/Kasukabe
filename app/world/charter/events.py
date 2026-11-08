"""Event emission for the charter module.

Every mutation charter/ makes should be traceable the way app/CONVENTIONS.md
describes: a `type` string of `<domain>.<subject>.<past_tense_verb>`,
written to Supabase's `events` table and broadcast on a `kasukabe:<domain>`
Realtime channel. The Supabase writer and the Realtime broadcaster don't
exist yet (that's world/'s wiring, not charter/'s), so this module defines
the event shape and an `EventSink` seam instead of talking to Supabase
directly - the same seam pattern app/CONVENTIONS.md describes for how
`game/` reads live state ("It reads live state from Supabase Realtime, not
by polling world's internals") without charter/ needing to know that
detail today.

`EventSink` is async because app/CONVENTIONS.md requires async for
"Supabase reads/writes, world-state mutation", and every public method
that emits an event is itself a world-state mutation. The default sink
used by `genesis.py` when the caller supplies none is a no-op, so the
module is fully usable (and testable) standalone; `world/`'s eventual
HTTP/tick wiring supplies a real sink that writes to Supabase and
publishes to the Realtime channel.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


async def noop_sink(event: dict[str, Any]) -> None:
    """Default `EventSink`: discards the event. Used when a caller (tests,
    standalone scripts) doesn't need to observe emitted events.
    """
    return


def build_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Assemble one event row matching app/CONVENTIONS.md's event shape.

    `event_type` must already be a valid `<domain>.<subject>.<verb>`
    string; this function does not validate the format, callers in this
    module always pass a literal from the `EVENT_*` constants below so
    there is nothing to typo at the call site.
    """
    if "agent_id" not in payload:
        named = [k for k in payload if k.endswith("_agent_id")]
        if len(named) == 1:
            payload = {**payload, "agent_id": payload[named[0]]}
    return {"type": event_type, "payload": payload}


EVENT_CHARTER_GENESIS_SEEDED = "politics.charter.genesis_seeded"
EVENT_CHARTER_AMENDMENT_PROPOSED = "politics.charter.amendment_proposed"
EVENT_CHARTER_AMENDMENT_PASSED = "politics.charter.amendment_passed"
EVENT_CHARTER_AMENDMENT_REJECTED = "politics.charter.amendment_rejected"
EVENT_CHARTER_AMENDED = "politics.charter.amended"
EVENT_CHARTER_VETOED = "politics.charter.vetoed"

EVENT_ELECTION_SCHEDULED = "politics.election.scheduled"
EVENT_CANDIDACY_DECLARED = "politics.candidacy.declared"
EVENT_ELECTION_PHASE_ADVANCED = "politics.election.phase_advanced"
EVENT_VOTE_CAST = "politics.vote.cast"
EVENT_ELECTION_TALLIED = "politics.election.tallied"
EVENT_REGIME_CHANGED = "politics.regime.changed"

EVENT_TAX_RATE_SET = "politics.tax_rate.set"
EVENT_CREDIT_STORE_MARGIN_SET = "politics.credit_store_margin.set"
EVENT_PENSION_CONFIG_SET = "politics.pension_config.set"
EVENT_PRICE_CONTROL_SET = "politics.price_control.set"
EVENT_BUSINESS_LICENSING_SET = "politics.business_licensing.set"
EVENT_JOB_CREATED = "politics.job.created"
EVENT_JOB_CUT = "politics.job.cut"
EVENT_POLICE_HIRED = "politics.police.hired"
EVENT_LICENSE_ISSUED = "politics.license.issued"
EVENT_LICENSE_REVOKED = "politics.license.revoked"
EVENT_TENDER_POSTED = "politics.tender.posted"
EVENT_TENDER_AWARDED = "politics.tender.awarded"
EVENT_TENDER_CANCELLED = "politics.tender.cancelled"

EVENT_PATENT_FILED = "politics.patent.filed"
EVENT_PATENT_APPROVED = "politics.patent.approved"
EVENT_PATENT_REJECTED = "politics.patent.rejected"
EVENT_PATENT_REVOKED = "politics.patent.revoked"

EVENT_PETITION_FILED = "politics.petition.filed"
EVENT_PETITION_APPROVED = "politics.petition.approved"
EVENT_PETITION_DENIED = "politics.petition.denied"

EVENT_ORGANIZATION_REGISTERED = "politics.organization.registered"
EVENT_ORGANIZATION_MEMBER_ADDED = "politics.organization.member_added"
EVENT_ORGANIZATION_MEMBER_REMOVED = "politics.organization.member_removed"

EVENT_COMPLAINT_FILED = "politics.complaint.filed"
