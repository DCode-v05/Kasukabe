"""Amendment engine: the ONE path by which any Charter field changes
(DESIGN.md section 7: "charter amendment by 3/5 vote, including election
rules and collapse to one-person rule").

Flow: `propose_amendment` (a sitting seat holder proposes a new value for
one field) -> `cast_amendment_vote` (each sitting seat holder votes yes or
no; reaching the charter's threshold moves the proposal to
`PASSED_AWAITING_PRESIDENT`, exhausting all votes without reaching it
moves it to `REJECTED`) -> `sign_amendment` or `veto_amendment` (the
President - DESIGN.md section 8's "President" operator hat - has the sole
say over whether a passed amendment actually takes effect).

That last step is deliberately a genuine gate, not a formality: DESIGN.md
section 7 says "Genesis checks are deliberately minimal: immutable
complaint ledger + Presidential veto" - the veto is named as one of
exactly two built-in checks on government power, so a passed amendment
must wait for the President to sign it, not apply itself and merely be
revocable afterward. Everything else (further checks and balances) is
left for the town to invent on top of this.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from world.charter.errors import (
    AmendmentNotFound,
    AmendmentNotPending,
    CharterValidationError,
    NotSittingGovernment,
    UnknownCharterField,
)
from world.charter.events import (
    EVENT_CHARTER_AMENDED,
    EVENT_CHARTER_AMENDMENT_PASSED,
    EVENT_CHARTER_AMENDMENT_PROPOSED,
    EVENT_CHARTER_AMENDMENT_REJECTED,
    EVENT_CHARTER_VETOED,
    EventSink,
    build_event,
    noop_sink,
)
from world.charter.models import (
    AMENDABLE_CHARTER_FIELDS,
    AmendmentProposal,
    AmendmentStatus,
    Charter,
)
from world.charter.store import CharterStore


AMENDMENT_ENGINE_REFERENCE_FIELDS: frozenset[str] = frozenset({"store", "event_sink"})
AMENDMENT_ENGINE_CHECKPOINTED_FIELDS: frozenset[str] = frozenset({"proposals"})


class AmendmentEngine:
    def __init__(self, store: CharterStore, event_sink: EventSink | None = None) -> None:
        self.store = store
        self.event_sink = event_sink if event_sink is not None else noop_sink
        self.proposals: dict[str, AmendmentProposal] = {}

        actual_owned = set(vars(self)) - AMENDMENT_ENGINE_REFERENCE_FIELDS
        if actual_owned != set(AMENDMENT_ENGINE_CHECKPOINTED_FIELDS):
            missing = sorted(actual_owned - AMENDMENT_ENGINE_CHECKPOINTED_FIELDS)
            stale = sorted(AMENDMENT_ENGINE_CHECKPOINTED_FIELDS - actual_owned)
            raise AssertionError(
                "AmendmentEngine.AMENDMENT_ENGINE_CHECKPOINTED_FIELDS has drifted from "
                f"AmendmentEngine.__init__: attributes set by __init__ but not checkpointed="
                f"{missing}, checkpointed fields no longer set by __init__={stale}. Update "
                "world/charter/amendments.py's AMENDMENT_ENGINE_CHECKPOINTED_FIELDS and this "
                "class's snapshot()/restore() so a resume keeps covering every piece of "
                "amendment state."
            )


    def snapshot(self) -> dict[str, Any]:
        return {proposal_id: p.model_dump(mode="json") for proposal_id, p in self.proposals.items()}

    def restore(self, data: dict[str, Any]) -> None:
        self.proposals = {
            proposal_id: AmendmentProposal.model_validate(raw) for proposal_id, raw in data.items()
        }

    async def load_proposal(self, proposal_id: str) -> AmendmentProposal:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise AmendmentNotFound(proposal_id)
        return proposal

    def list_pending(self) -> list[AmendmentProposal]:
        """Amendments passed by the government and awaiting the
        President's signature or veto. This is the console-facing read the
        President's operator view needs, the same way `land_petitions.py`
        exposes `list_pending` for the President's petition queue
        (DESIGN.md section 8).
        """
        return [
            p.model_copy(deep=True)
            for p in self.proposals.values()
            if p.status == AmendmentStatus.PASSED_AWAITING_PRESIDENT
        ]

    async def propose_amendment(
        self,
        field_name: str,
        new_value: object,
        title: str,
        rationale: str,
        proposed_by_agent_id: str,
        now_tick: int,
    ) -> AmendmentProposal:
        if field_name not in AMENDABLE_CHARTER_FIELDS:
            raise UnknownCharterField(field_name)
        government_state = await self.store.get_government_state()
        if not government_state.is_sitting(proposed_by_agent_id):
            raise NotSittingGovernment(proposed_by_agent_id)

        proposal = AmendmentProposal(
            field_name=field_name,
            new_value=new_value,
            title=title,
            rationale=rationale,
            proposed_by_agent_id=proposed_by_agent_id,
            proposed_tick=now_tick,
        )
        self.proposals[proposal.proposal_id] = proposal
        await self.event_sink(
            build_event(
                EVENT_CHARTER_AMENDMENT_PROPOSED,
                {
                    "proposal_id": proposal.proposal_id,
                    "field_name": field_name,
                    "proposed_by_agent_id": proposed_by_agent_id,
                    "title": title,
                },
            )
        )
        return proposal

    async def cast_amendment_vote(
        self, proposal_id: str, voter_agent_id: str, support: bool, now_tick: int
    ) -> AmendmentProposal:
        proposal = await self.load_proposal(proposal_id)
        if proposal.status != AmendmentStatus.PROPOSED:
            raise AmendmentNotPending(proposal_id, proposal.status.value)
        government_state = await self.store.get_government_state()
        if not government_state.is_sitting(voter_agent_id):
            raise NotSittingGovernment(voter_agent_id)

        proposal.votes[voter_agent_id] = support

        charter = await self.store.get_charter()
        yes_count = sum(1 for v in proposal.votes.values() if v)
        cast_count = len(proposal.votes)
        total_seats = len(government_state.all_seat_holders())
        required = charter.amendment_votes_required

        if yes_count >= required:
            proposal.status = AmendmentStatus.PASSED_AWAITING_PRESIDENT
            proposal.passed_tick = now_tick
            await self.event_sink(
                build_event(
                    EVENT_CHARTER_AMENDMENT_PASSED,
                    {"proposal_id": proposal_id, "yes_count": yes_count, "required": required},
                )
            )
        elif cast_count >= total_seats:
            proposal.status = AmendmentStatus.REJECTED
            await self.event_sink(
                build_event(
                    EVENT_CHARTER_AMENDMENT_REJECTED,
                    {"proposal_id": proposal_id, "yes_count": yes_count, "required": required},
                )
            )

        return proposal

    async def sign_amendment(self, proposal_id: str, now_tick: int) -> AmendmentProposal:
        """Presidential signature: the amendment takes effect. Produces a
        new `Charter` snapshot (version + 1) rather than mutating the
        current one in place, per `models.py`'s frozen-snapshot design.
        """
        proposal = await self.load_proposal(proposal_id)
        if proposal.status != AmendmentStatus.PASSED_AWAITING_PRESIDENT:
            raise AmendmentNotPending(proposal_id, proposal.status.value)

        current = await self.store.get_charter()
        candidate_data = current.model_dump()
        candidate_data[proposal.field_name] = proposal.new_value
        candidate_data["version"] = current.version + 1
        try:
            new_charter = Charter(**candidate_data)
        except ValidationError as exc:
            raise CharterValidationError([str(err["msg"]) for err in exc.errors()]) from exc

        await self.store.push_charter(new_charter)
        proposal.status = AmendmentStatus.ENACTED
        proposal.enacted_tick = now_tick
        proposal.resulting_charter_version = new_charter.version

        await self.event_sink(
            build_event(
                EVENT_CHARTER_AMENDED,
                {
                    "proposal_id": proposal_id,
                    "field_name": proposal.field_name,
                    "old_value": getattr(current, proposal.field_name),
                    "new_value": proposal.new_value,
                    "version": new_charter.version,
                },
            )
        )
        return proposal

    async def veto_amendment(
        self, proposal_id: str, veto_note: str, now_tick: int
    ) -> AmendmentProposal:
        proposal = await self.load_proposal(proposal_id)
        if proposal.status != AmendmentStatus.PASSED_AWAITING_PRESIDENT:
            raise AmendmentNotPending(proposal_id, proposal.status.value)

        proposal.status = AmendmentStatus.VETOED
        proposal.vetoed_tick = now_tick
        proposal.veto_note = veto_note
        await self.event_sink(
            build_event(
                EVENT_CHARTER_VETOED,
                {
                    "proposal_id": proposal_id,
                    "field_name": proposal.field_name,
                    "veto_note": veto_note,
                },
            )
        )
        return proposal
