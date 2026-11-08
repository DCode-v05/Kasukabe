"""Tests for world/charter/store.py: CharterStore's async single-writer
surface, independent of any sub-engine.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from world.charter.models import (
    Charter,
    ComplaintEntry,
    Election,
    ElectionPhase,
    GovernmentPolicy,
    GovernmentState,
    Job,
    JobStatus,
    LandPetition,
    License,
    Organization,
    OrganizationKind,
    Patent,
    PatentApplication,
    Tender,
)
from world.charter.store import CHARTER_STORE_FIELDS, CharterStore


@pytest.mark.asyncio
async def test_current_charter_returns_latest_seeded_version() -> None:
    store = CharterStore()
    await store.seed_charter(Charter(version=0))
    await store.push_charter(Charter(version=1, pm_seats=1, minister_seats=2))

    charter = await store.get_charter()
    assert charter.version == 1
    assert charter.minister_seats == 2


@pytest.mark.asyncio
async def test_charter_history_keeps_every_version() -> None:
    store = CharterStore()
    await store.seed_charter(Charter(version=0))
    await store.push_charter(Charter(version=1))
    await store.push_charter(Charter(version=2))

    assert [c.version for c in store.charter_history] == [0, 1, 2]


@pytest.mark.asyncio
async def test_current_policy_returns_latest_seeded_version() -> None:
    store = CharterStore()
    await store.seed_policy(GovernmentPolicy(version=0))
    await store.push_policy(GovernmentPolicy(version=1, tax_rate_bps=500))

    policy = await store.get_policy()
    assert policy.version == 1
    assert policy.tax_rate_bps == 500


@pytest.mark.asyncio
async def test_government_state_round_trips_and_is_a_copy() -> None:
    store = CharterStore()
    state = GovernmentState(pm_agent_id="agent-1")
    await store.set_government_state(state)

    fetched = await store.get_government_state()
    assert fetched.pm_agent_id == "agent-1"
    fetched.pm_agent_id = "mutated-locally"
    refetched = await store.get_government_state()
    assert refetched.pm_agent_id == "agent-1"


@pytest.mark.asyncio
async def test_job_round_trip() -> None:
    store = CharterStore()
    job = Job(
        title="baker",
        job_type="business",
        salary_sim=1000,
        created_by_agent_id="agent-1",
        created_tick=0,
    )
    await store.put_job(job)

    fetched = await store.get_job(job.job_id)
    assert fetched is not None
    assert fetched.title == "baker"
    assert fetched.status == JobStatus.OPEN


@pytest.mark.asyncio
async def test_get_job_missing_returns_none() -> None:
    store = CharterStore()
    assert await store.get_job("does-not-exist") is None


@pytest.mark.asyncio
async def test_list_jobs_returns_copies_not_live_objects() -> None:
    store = CharterStore()
    job = Job(
        title="baker",
        job_type="business",
        salary_sim=1000,
        created_by_agent_id="agent-1",
        created_tick=0,
    )
    await store.put_job(job)

    jobs = await store.list_jobs()
    jobs[0].title = "mutated-locally"
    refetched = await store.get_job(job.job_id)
    assert refetched is not None
    assert refetched.title == "baker"


@pytest.mark.asyncio
async def test_append_complaint_assigns_sequential_ledger_index() -> None:
    store = CharterStore()
    first = await store.append_complaint("a1", "gov", "government", "first", 0)
    second = await store.append_complaint("a2", "gov", "government", "second", 1)

    assert first.ledger_index == 0
    assert second.ledger_index == 1
    ledger = await store.list_complaints()
    assert len(ledger) == 2


@pytest.mark.asyncio
async def test_append_complaint_is_race_safe_under_concurrency() -> None:
    """Two concurrent complaints must never collide on the same
    ledger_index: this is the exact race append_complaint's docstring
    calls out (index assigned inside the same lock as the append).
    """
    store = CharterStore()
    await asyncio.gather(
        *[
            store.append_complaint(f"agent-{i}", "gov", "government", "concurrent", 0)
            for i in range(50)
        ]
    )
    ledger = await store.list_complaints()
    indices = sorted(entry.ledger_index for entry in ledger)
    assert indices == list(range(50))


@pytest.mark.asyncio
async def test_list_complaints_returns_a_copy() -> None:
    store = CharterStore()
    await store.append_complaint("a1", "gov", "government", "first", 0)

    ledger = await store.list_complaints()
    ledger.append(ledger[0])  # mutate the returned list itself
    refetched = await store.list_complaints()
    assert len(refetched) == 1


# ── snapshot()/restore() (STATUS.md risk #4: checkpoint round-trip) ─────


class TestCharterStoreFieldRegistryMatchesInit:
    def test_charter_store_fields_names_every_attribute_init_sets(self) -> None:
        """The self-check CharterStore.__init__ runs on every construction
        (see its own docstring) already fails loudly on drift; this test
        pins the same invariant down in the test suite too, matching
        STATUS.md risk #3's "add a test asserting they match by name"
        remedy applied here to CHARTER_STORE_FIELDS instead of
        WorldState's OWNED_STATE_FIELDS."""
        store = CharterStore()
        declared = {f.name for f in CHARTER_STORE_FIELDS}
        actual = set(vars(store)) - {"lock"}
        assert declared == actual

    def test_constructing_a_store_with_undeclared_state_raises(self) -> None:
        """Direct repro of the drift CharterStore.__init__ guards
        against: monkeypatch CHARTER_STORE_FIELDS down to a strict subset
        and confirm construction fails loudly instead of silently
        producing a store some of whose state snapshot()/restore() would
        never touch."""
        import world.charter.store as store_module

        original = store_module.CHARTER_STORE_FIELDS
        try:
            store_module.CHARTER_STORE_FIELDS = tuple(
                f for f in original if f.name != "organizations"
            )
            with pytest.raises(AssertionError, match="organizations"):
                CharterStore()
        finally:
            store_module.CHARTER_STORE_FIELDS = original


class TestCharterStoreSnapshotRestore:
    @pytest.mark.asyncio
    async def test_round_trips_every_field_through_json(self) -> None:
        store = CharterStore()
        await store.seed_charter(Charter(version=0))
        await store.push_charter(Charter(version=1, minister_seats=3))
        await store.seed_policy(GovernmentPolicy(version=0))
        await store.push_policy(GovernmentPolicy(version=1, tax_rate_bps=250))
        await store.set_government_state(
            GovernmentState(
                pm_agent_id="pm-1",
                minister_agent_ids=["min-a", "min-b"],
                terms_served={"pm-1": 2, "min-a": 1},
            )
        )
        election = Election(
            charter_version_at_scheduling=1,
            seats_at_scheduling=5,
            scheduled_tick=0,
            candidacy_opens_tick=0,
            candidacy_closes_tick=144,
            voting_opens_tick=288,
            voting_closes_tick=432,
            phase=ElectionPhase.CANDIDACY_OPEN,
        )
        await store.put_election(election)
        await store.put_job(
            Job(title="baker", job_type="business", salary_sim=500,
                created_by_agent_id="pm-1", created_tick=0)
        )
        await store.put_license(
            License(license_type="permit:build", holder_agent_id="agent-1",
                    fee_paid_sim=200, issued_by_agent_id="pm-1", issued_tick=0)
        )
        await store.put_tender(
            Tender(title="road", description="pave it", budget_sim=1000,
                   posted_by_agent_id="pm-1", posted_tick=0)
        )
        application = PatentApplication(
            inventor_agent_id="agent-2", tool_name="auto-plow", description="d",
            fee_paid_sim=1000, filed_tick=0,
        )
        await store.put_patent_application(application)
        await store.put_patent(
            Patent(application_id=application.application_id, tool_name="auto-plow",
                   owner_agent_id="agent-2", granted_by_agent_id="pm-1", granted_tick=1)
        )
        await store.put_land_petition(
            LandPetition(filed_by_agent_id="pm-1", title="expand", description="d",
                         tile_count_requested=10, filed_tick=0)
        )
        await store.put_organization(
            Organization(kind=OrganizationKind.PARTY, name="Freepeople",
                         founder_agent_id="agent-3", charter_text="c", founded_tick=0,
                         members={"agent-3": "founder"})
        )
        await store.append_complaint("agent-4", "pm-1", "government", "unfair", 0)

        snapshot = json.loads(json.dumps(store.snapshot()))  # real JSON round-trip

        restored = CharterStore()
        restored.restore(snapshot)

        assert [c.version for c in restored.charter_history] == [0, 1]
        assert restored.charter_history[1].minister_seats == 3
        assert [p.version for p in restored.policy_history] == [0, 1]
        assert restored.policy_history[1].tax_rate_bps == 250
        assert restored.government_state == store.government_state
        assert restored.elections[election.election_id] == election
        assert restored.elections[election.election_id].phase == ElectionPhase.CANDIDACY_OPEN
        assert {j.title for j in restored.jobs.values()} == {"baker"}
        assert {lic.license_type for lic in restored.licenses.values()} == {"permit:build"}
        assert {t.title for t in restored.tenders.values()} == {"road"}
        assert {a.tool_name for a in restored.patent_applications.values()} == {"auto-plow"}
        assert {p.owner_agent_id for p in restored.patents.values()} == {"agent-2"}
        assert {p.title for p in restored.land_petitions.values()} == {"expand"}
        assert {o.name for o in restored.organizations.values()} == {"Freepeople"}
        assert [c.description for c in restored.complaint_ledger] == ["unfair"]

        # Every model instance is real (not a leftover dict) - callers
        # like elections.py read `.phase`/`.candidates` as attributes.
        assert isinstance(restored.charter_history[0], Charter)
        assert isinstance(restored.elections[election.election_id], Election)
        assert isinstance(restored.complaint_ledger[0], ComplaintEntry)

    def test_restore_raises_on_a_checkpoint_missing_a_declared_field(self) -> None:
        """No legacy-checkpoint tolerance here (unlike
        WorldStateCheckpointAdapter/RunnerCountersCheckpointAdapter's
        `.get(name, default)` pattern for fields added after those
        adapters already shipped) - CHARTER_STORE_FIELDS did not exist
        before this fix, so a checkpoint claiming to carry charter state
        but missing one of these keys is corrupt, not old, and restore()
        must fail loudly rather than silently leave that field empty."""
        store = CharterStore()
        complete = store.snapshot()
        del complete["organizations"]
        with pytest.raises(KeyError):
            store.restore(complete)
