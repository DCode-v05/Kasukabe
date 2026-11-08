"""Tests for world/charter/amendments.py: propose, vote to 3/5 threshold,
Presidential sign or veto.
"""

from __future__ import annotations

import json

import pytest

from world.charter.amendments import (
    AMENDMENT_ENGINE_CHECKPOINTED_FIELDS,
    AMENDMENT_ENGINE_REFERENCE_FIELDS,
    AmendmentEngine,
)
from world.charter.errors import (
    AmendmentNotPending,
    CharterValidationError,
    NotSittingGovernment,
    UnknownCharterField,
)
from world.charter.events import (
    EVENT_CHARTER_AMENDED,
    EVENT_CHARTER_AMENDMENT_PASSED,
    EVENT_CHARTER_VETOED,
)
from world.charter.genesis import seed_genesis
from world.charter.models import AmendmentStatus, GovernmentState
from world.charter.store import CharterStore
from world.charter.tests.conftest import EventRecorder

GOVERNMENT = GovernmentState(pm_agent_id="pm", minister_agent_ids=["m1", "m2", "m3", "m4"])


async def make_engine(
    recorder: EventRecorder | None = None,
) -> tuple[AmendmentEngine, CharterStore]:
    store = CharterStore()
    sink = recorder.sink if recorder is not None else None
    await seed_genesis(store, genesis_tick=0, event_sink=sink)
    await store.set_government_state(GOVERNMENT)
    return AmendmentEngine(store, event_sink=sink), store


@pytest.mark.asyncio
async def test_propose_amendment_requires_sitting_government() -> None:
    engine, _ = await make_engine()

    with pytest.raises(NotSittingGovernment):
        await engine.propose_amendment(
            "election_interval_days",
            5.0,
            "slower elections",
            "why not",
            "not-a-minister",
            now_tick=0,
        )


@pytest.mark.asyncio
async def test_propose_amendment_rejects_unknown_field() -> None:
    engine, _ = await make_engine()

    with pytest.raises(UnknownCharterField):
        await engine.propose_amendment("not_a_real_field", 1, "t", "r", "pm", now_tick=0)


@pytest.mark.asyncio
async def test_propose_amendment_starts_proposed() -> None:
    engine, _ = await make_engine()

    proposal = await engine.propose_amendment(
        "election_interval_days", 5.0, "t", "r", "pm", now_tick=0
    )
    assert proposal.status == AmendmentStatus.PROPOSED


@pytest.mark.asyncio
async def test_cast_amendment_vote_requires_sitting_government() -> None:
    engine, _ = await make_engine()
    proposal = await engine.propose_amendment(
        "election_interval_days", 5.0, "t", "r", "pm", now_tick=0
    )

    with pytest.raises(NotSittingGovernment):
        await engine.cast_amendment_vote(proposal.proposal_id, "outsider", True, now_tick=1)


@pytest.mark.asyncio
async def test_three_of_five_yes_votes_passes_to_awaiting_president() -> None:
    recorder = EventRecorder()
    engine, _ = await make_engine(recorder)
    proposal = await engine.propose_amendment(
        "election_interval_days", 5.0, "t", "r", "pm", now_tick=0
    )

    await engine.cast_amendment_vote(proposal.proposal_id, "pm", True, now_tick=1)
    await engine.cast_amendment_vote(proposal.proposal_id, "m1", True, now_tick=1)
    updated = await engine.cast_amendment_vote(proposal.proposal_id, "m2", True, now_tick=1)

    assert updated.status == AmendmentStatus.PASSED_AWAITING_PRESIDENT
    assert recorder.of_type(EVENT_CHARTER_AMENDMENT_PASSED)


@pytest.mark.asyncio
async def test_two_of_five_yes_and_rest_no_is_rejected() -> None:
    engine, _ = await make_engine()
    proposal = await engine.propose_amendment(
        "election_interval_days", 5.0, "t", "r", "pm", now_tick=0
    )

    await engine.cast_amendment_vote(proposal.proposal_id, "pm", True, now_tick=1)
    await engine.cast_amendment_vote(proposal.proposal_id, "m1", True, now_tick=1)
    await engine.cast_amendment_vote(proposal.proposal_id, "m2", False, now_tick=1)
    await engine.cast_amendment_vote(proposal.proposal_id, "m3", False, now_tick=1)
    updated = await engine.cast_amendment_vote(proposal.proposal_id, "m4", False, now_tick=1)

    assert updated.status == AmendmentStatus.REJECTED


@pytest.mark.asyncio
async def test_vote_can_be_changed_before_resolution() -> None:
    engine, _ = await make_engine()
    proposal = await engine.propose_amendment(
        "election_interval_days", 5.0, "t", "r", "pm", now_tick=0
    )

    await engine.cast_amendment_vote(proposal.proposal_id, "pm", False, now_tick=1)
    await engine.cast_amendment_vote(proposal.proposal_id, "pm", True, now_tick=2)
    reloaded = await engine.load_proposal(proposal.proposal_id)
    assert reloaded.votes["pm"] is True


@pytest.mark.asyncio
async def test_sign_amendment_requires_passed_status() -> None:
    engine, _ = await make_engine()
    proposal = await engine.propose_amendment(
        "election_interval_days", 5.0, "t", "r", "pm", now_tick=0
    )

    with pytest.raises(AmendmentNotPending):
        await engine.sign_amendment(proposal.proposal_id, now_tick=1)


@pytest.mark.asyncio
async def test_sign_amendment_applies_new_value_and_bumps_version() -> None:
    recorder = EventRecorder()
    engine, store = await make_engine(recorder)
    proposal = await engine.propose_amendment(
        "election_interval_days", 5.0, "t", "r", "pm", now_tick=0
    )
    for seat in ("pm", "m1", "m2"):
        await engine.cast_amendment_vote(proposal.proposal_id, seat, True, now_tick=1)

    signed = await engine.sign_amendment(proposal.proposal_id, now_tick=2)
    assert signed.status == AmendmentStatus.ENACTED
    assert signed.resulting_charter_version == 1

    charter = await store.get_charter()
    assert charter.election_interval_days == 5.0
    assert charter.version == 1
    assert recorder.of_type(EVENT_CHARTER_AMENDED)


@pytest.mark.asyncio
async def test_sign_amendment_with_invalid_value_raises_and_does_not_mutate_charter() -> None:
    engine, store = await make_engine()
    proposal = await engine.propose_amendment("pm_seats", -1, "t", "r", "pm", now_tick=0)
    for seat in ("pm", "m1", "m2"):
        await engine.cast_amendment_vote(proposal.proposal_id, seat, True, now_tick=1)

    with pytest.raises(CharterValidationError):
        await engine.sign_amendment(proposal.proposal_id, now_tick=2)

    charter = await store.get_charter()
    assert charter.version == 0
    assert charter.pm_seats == 1


@pytest.mark.asyncio
async def test_veto_amendment_leaves_charter_unchanged() -> None:
    recorder = EventRecorder()
    engine, store = await make_engine(recorder)
    proposal = await engine.propose_amendment(
        "election_interval_days", 5.0, "t", "r", "pm", now_tick=0
    )
    for seat in ("pm", "m1", "m2"):
        await engine.cast_amendment_vote(proposal.proposal_id, seat, True, now_tick=1)

    vetoed = await engine.veto_amendment(proposal.proposal_id, "not now", now_tick=2)
    assert vetoed.status == AmendmentStatus.VETOED

    charter = await store.get_charter()
    assert charter.version == 0
    assert charter.election_interval_days == 3.0
    assert recorder.of_type(EVENT_CHARTER_VETOED)


@pytest.mark.asyncio
async def test_collapse_to_one_person_rule_is_reachable_through_amendments() -> None:
    """DESIGN.md section 7: authoritarianism "must be reachable, never
    scripted." This drives the charter there using nothing but the
    ordinary amendment path, then proves the collapse is real by having
    the PM alone pass a second amendment with no other seat holder's
    vote at all.
    """
    engine, store = await make_engine()

    remove_ministers = await engine.propose_amendment(
        "minister_seats", 0, "t", "r", "pm", now_tick=0
    )
    for seat in ("pm", "m1", "m2"):
        await engine.cast_amendment_vote(remove_ministers.proposal_id, seat, True, now_tick=1)
    await engine.sign_amendment(remove_ministers.proposal_id, now_tick=2)

    charter_after_first = await store.get_charter()
    assert charter_after_first.minister_seats == 0
    assert charter_after_first.total_seats == 1
    assert charter_after_first.amendment_votes_required == 1

    solo_amendment = await engine.propose_amendment(
        "election_interval_days", 30.0, "t", "r", "pm", now_tick=3
    )
    passed = await engine.cast_amendment_vote(solo_amendment.proposal_id, "pm", True, now_tick=4)
    assert passed.status == AmendmentStatus.PASSED_AWAITING_PRESIDENT
    await engine.sign_amendment(solo_amendment.proposal_id, now_tick=5)

    charter = await store.get_charter()
    assert charter.election_interval_days == 30.0


@pytest.mark.asyncio
async def test_list_pending_only_returns_passed_awaiting_president() -> None:
    engine, _ = await make_engine()
    proposed = await engine.propose_amendment(
        "election_interval_days", 5.0, "t", "r", "pm", now_tick=0
    )
    passed = await engine.propose_amendment(
        "patent_filing_fee_sim", 2000, "t2", "r2", "pm", now_tick=0
    )
    for seat in ("pm", "m1", "m2"):
        await engine.cast_amendment_vote(passed.proposal_id, seat, True, now_tick=1)

    pending = engine.list_pending()
    pending_ids = {p.proposal_id for p in pending}
    assert pending_ids == {passed.proposal_id}
    assert proposed.proposal_id not in pending_ids


@pytest.mark.asyncio
async def test_snapshot_restore_round_trips_proposals_at_every_status() -> None:
    engine, _ = await make_engine()

    still_proposed = await engine.propose_amendment(
        "election_interval_days", 5.0, "t1", "r1", "pm", now_tick=0
    )
    to_enact = await engine.propose_amendment(
        "patent_filing_fee_sim", 2000, "t2", "r2", "pm", now_tick=0
    )
    for seat in ("pm", "m1", "m2"):
        await engine.cast_amendment_vote(to_enact.proposal_id, seat, True, now_tick=1)
    enacted = await engine.sign_amendment(to_enact.proposal_id, now_tick=2)

    to_veto = await engine.propose_amendment(
        "permit_fee_sim", 400, "t3", "r3", "pm", now_tick=0
    )
    for seat in ("pm", "m1", "m2"):
        await engine.cast_amendment_vote(to_veto.proposal_id, seat, True, now_tick=1)
    vetoed = await engine.veto_amendment(to_veto.proposal_id, "not now", now_tick=2)

    snapshot = json.loads(json.dumps(engine.snapshot()))

    restored_engine = AmendmentEngine(engine.store)
    restored_engine.restore(snapshot)

    assert set(restored_engine.proposals) == {
        still_proposed.proposal_id,
        to_enact.proposal_id,
        to_veto.proposal_id,
    }
    assert restored_engine.proposals[still_proposed.proposal_id] == still_proposed
    assert restored_engine.proposals[to_enact.proposal_id] == enacted
    assert restored_engine.proposals[to_veto.proposal_id] == vetoed
    assert restored_engine.proposals[to_enact.proposal_id].status == AmendmentStatus.ENACTED
    assert restored_engine.proposals[to_veto.proposal_id].status == AmendmentStatus.VETOED


@pytest.mark.asyncio
async def test_snapshot_of_engine_with_no_proposals_round_trips_empty() -> None:
    engine, _ = await make_engine()
    restored_engine = AmendmentEngine(engine.store)
    restored_engine.restore(engine.snapshot())
    assert restored_engine.proposals == {}


class TestAmendmentEngineFieldRegistryMatchesInit:
    @pytest.mark.asyncio
    async def test_checkpointed_fields_names_every_owned_attribute_init_sets(self) -> None:
        engine, _ = await make_engine()
        actual_owned = set(vars(engine)) - AMENDMENT_ENGINE_REFERENCE_FIELDS
        assert actual_owned == set(AMENDMENT_ENGINE_CHECKPOINTED_FIELDS)

    @pytest.mark.asyncio
    async def test_constructing_an_engine_with_undeclared_state_raises(self) -> None:
        """Direct repro of the drift AmendmentEngine.__init__ guards
        against: monkeypatch AMENDMENT_ENGINE_CHECKPOINTED_FIELDS down to
        an empty set (so `proposals` looks unowned) and confirm
        construction fails loudly instead of silently producing an engine
        whose state snapshot()/restore() would never touch."""
        import world.charter.amendments as amendments_module

        store = CharterStore()
        await seed_genesis(store, genesis_tick=0)

        original = amendments_module.AMENDMENT_ENGINE_CHECKPOINTED_FIELDS
        try:
            amendments_module.AMENDMENT_ENGINE_CHECKPOINTED_FIELDS = frozenset()
            with pytest.raises(AssertionError, match="proposals"):
                AmendmentEngine(store)
        finally:
            amendments_module.AMENDMENT_ENGINE_CHECKPOINTED_FIELDS = original
