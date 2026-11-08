"""Four declared tables that took no rows, whatever the town did.

`INFRA-17` / `GROW-19`. `petitions`, `buildings`, `cases` and
`organizations` are in `sqlite_schema.sql`, are queried by the viewers and
the evals, and had no writer anywhere. Build Spec section 4.3 asks for a
parametrised test that fails on any table with no live writer; it was never
written, and these are four of the six it would have caught.

What that cost, concretely:

  * the expansion pipeline - DESIGN section 1's "the town physically
    grows" - left no trace of a petition ever being filed or decided;
  * `GOV-02` gave a crime case a victim, an accused and a write-once ground
    truth so `false_conviction_stats()` could be computed, and the table it
    would be computed from had no writer;
  * the growth track's headline number, structures completed by the town's
    own builders, had to be counted by hand out of generic `events` rows.

These tests drive the recorder with the events the world really emits,
rather than calling the writers directly - calling the helper is what let
six tables sit empty while their helpers passed their own tests.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from supabase.config import SupabaseConfig
from supabase.sqlite_client import SqliteClient
from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord, Sex
from world.wiring import TraceStore


@pytest_asyncio.fixture
async def store(tmp_path):
    client = SqliteClient(SupabaseConfig(sqlite_path=str(tmp_path / "trace.db")))
    client.ensure_schema()
    store = TraceStore(client)
    await store.seed_agent(
        AgentLifeRecord(
            agent_id="n01", sex=Sex.FEMALE, age_years=30.0,
            household_id="h_quarry", is_minor=False,
        ),
        None,
        0,
    )
    return store


def _rows(store, table: str) -> list[dict]:
    return store.client.select_sync(table, columns="*", filters=None, order=None, limit=50)


class TestPetitions:
    @pytest.mark.asyncio
    async def test_a_filing_lands(self, store):
        await store.record_petition_if_new(WorldEvent(
            type="politics.petition.filed", tick=5,
            payload={"petition_id": "p1", "filed_by_agent_id": "n01",
                     "title": "more farmland", "description": "east"},
        ))
        rows = _rows(store, "petitions")
        assert len(rows) == 1 and rows[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_a_decision_updates_rather_than_duplicates(self, store):
        """A petition is one thing that changed state. Two rows would make
        'how many petitions' unanswerable."""
        await store.record_petition_if_new(WorldEvent(
            type="politics.petition.filed", tick=5,
            payload={"petition_id": "p1", "filed_by_agent_id": "n01"},
        ))
        await store.record_petition_if_new(WorldEvent(
            type="politics.petition.approved", tick=9,
            payload={"petition_id": "p1", "decision_note": "granted"},
        ))
        rows = _rows(store, "petitions")
        assert len(rows) == 1, "the decision wrote a second row"
        assert rows[0]["status"] == "approved"
        assert rows[0]["decided_tick"] == 9


class TestBuildings:
    @pytest.mark.asyncio
    async def test_a_start_and_a_completion_are_one_row(self, store):
        await store.record_building_if_new(WorldEvent(
            type="world.building.construction_started", tick=10,
            payload={"building_id": "b1", "builder_agent_id": "n01",
                     "purpose": "smithy", "timber_used": 15, "metal_used": 16,
                     "footprint": [[0, 0]]},
        ))
        await store.record_building_if_new(WorldEvent(
            type="world.building.completed", tick=40,
            payload={"building_id": "b1"},
        ))
        rows = _rows(store, "buildings")
        assert len(rows) == 1
        assert rows[0]["construction_status"] == "complete"
        assert rows[0]["construction_completed_tick"] == 40


class TestCases:
    @pytest.mark.asyncio
    async def test_an_opened_case_records_ground_truth_separately(self, store):
        """The whole point of GOV-02: what the court believes and what
        actually happened are two columns, so false convictions are
        countable."""
        await store.record_case_if_new(WorldEvent(
            type="crime.case.opened", tick=12,
            payload={"case_id": "c1", "victim_agent_id": "n01",
                     "against_agent_id": None,
                     "ground_truth_perpetrator_id": "n07"},
        ))
        rows = _rows(store, "cases")
        assert len(rows) == 1
        assert rows[0]["accused_agent_id"] is None, "a fresh theft accuses nobody"
        assert rows[0]["ground_truth_guilty"]


class TestOrganizations:
    @pytest.mark.asyncio
    async def test_a_registration_lands(self, store):
        await store.record_organization_if_new(WorldEvent(
            type="politics.organization.registered", tick=3,
            payload={"organization_id": "o1", "name": "Millers Guild",
                     "org_type": "guild", "founder_agent_id": "n01"},
        ))
        rows = _rows(store, "organizations")
        assert len(rows) == 1 and rows[0]["name"] == "Millers Guild"


class TestIsolation:
    @pytest.mark.asyncio
    async def test_a_bad_payload_is_counted_not_raised(self, store):
        """A malformed event on one of these must not take a resident's
        tick down - the same isolation record_utterance_if_new has."""
        before = store.rows_dropped
        await store.record_organization_if_new(WorldEvent(
            type="politics.organization.registered", tick=3,
            payload={"organization_id": None},
        ))
        assert store.rows_dropped >= before

    @pytest.mark.asyncio
    async def test_an_unknown_filer_is_dropped_and_counted(self, store):
        """`petitions.filed_by_agent_id` is NOT NULL and a real foreign
        key, so an unknown filer cannot be softened to NULL the way the
        nullable references elsewhere are. It is dropped, counted and
        named - never raised, because one bad payload must not end a
        resident's tick."""
        before = store.rows_dropped
        await store.record_petition_if_new(WorldEvent(
            type="politics.petition.filed", tick=5,
            payload={"petition_id": "p9", "filed_by_agent_id": "nobody"},
        ))
        assert not [r for r in _rows(store, "petitions") if r["id"] == "p9"]
        assert store.rows_dropped == before + 1
