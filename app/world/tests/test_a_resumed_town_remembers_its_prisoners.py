"""A town that resumes still knows who is in its jail.

`GOV-12`. Every store the runner owns round-trips a checkpoint - the
ledger, the charter, the locations, the hunger ladder, the head-tax
debts, the records. The crime store did not exist as far as a checkpoint
was concerned, so a resumed town forgot every open case, every warrant
and every unreleased confinement.

That is not a lenient town. A resident serving a sentence walked free at
the resume AND the case it was serving for was gone, so the release was
not even recorded as one; `get_active_confinement` answered None and the
location lock that a confinement puts on a resident's tools simply
stopped applying, with nothing anywhere saying why.

The finding named three gaps. Two were closed by other work and are
asserted here so they cannot silently reopen: head-tax debt rides in the
`stakes` adapter (CORE-08) and the charter has its own (STATUS risk #4).
The third is this file's subject.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from world.crime.models import CaseFile, ConfinementRecord, Warrant
from world.crime.store import CrimeStore
from world.run_genesis import GenesisRunner, GenesisRunnerConfig


def _through_disk(snapshot):
    """What a checkpoint file holds: JSON, with no idea what a model is."""
    return json.loads(json.dumps(snapshot))


@pytest_asyncio.fixture
async def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACE_SQLITE_PATH", str(tmp_path / "trace.db"))
    runner = GenesisRunner(GenesisRunnerConfig(
        mock_mode=True,
        checkpoint_dir=tmp_path / "checkpoints",
        world_mode="embedded",
        world_port=0,
    ))
    await runner.boot()
    return runner


@pytest.mark.asyncio
async def test_an_unreleased_sentence_survives_a_resume():
    store = CrimeStore()
    await store.put_confinement(ConfinementRecord(
        confinement_id="c1", case_id="case1", agent_id="n01",
        jail_location_id="sector:900004", start_tick=10, release_tick=40,
    ))
    assert await store.get_active_confinement("n01") is not None

    resumed = CrimeStore()
    resumed.restore(_through_disk(store.snapshot()))

    still_held = await resumed.get_active_confinement("n01")
    assert still_held is not None, "a resumed town let its prisoner out"
    assert still_held.case_id == "case1"
    assert still_held.release_tick == 40


@pytest.mark.asyncio
async def test_a_released_sentence_stays_released():
    """The other direction: a resume must not re-jail anyone."""
    store = CrimeStore()
    await store.put_confinement(ConfinementRecord(
        confinement_id="c1", case_id="case1", agent_id="n01",
        jail_location_id="sector:900004", start_tick=10, release_tick=40, released=True,
    ))
    resumed = CrimeStore()
    resumed.restore(_through_disk(store.snapshot()))
    assert await resumed.get_active_confinement("n01") is None


@pytest.mark.asyncio
async def test_open_cases_and_warrants_survive_a_resume():
    store = CrimeStore()
    await store.put_case(CaseFile(
        case_id="case1", kind="theft", opened_tick=5, opened_by_agent_id="n02",
    ))
    await store.put_warrant(Warrant(
        warrant_id="w1", case_id="case1", target_agent_id="n01",
        requested_by_officer_agent_id="n02", justification="seen taking it",
        requested_tick=6,
    ))
    await store.link_ground_truth("case1", "we1")

    resumed = CrimeStore()
    resumed.restore(_through_disk(store.snapshot()))

    assert await resumed.get_case("case1") is not None
    assert await resumed.get_warrant("w1") is not None
    assert await resumed.list_ground_truth_links() == {"case1": "we1"}


@pytest.mark.asyncio
async def test_what_comes_back_is_a_model_not_a_dict():
    """A disk checkpoint is JSON. `test_a_resumed_town_is_not_made_of_dicts`
    is the whole reason this assertion is written out."""
    store = CrimeStore()
    await store.put_case(CaseFile(
        case_id="case1", kind="theft", opened_tick=5, opened_by_agent_id="n02",
    ))
    resumed = CrimeStore()
    resumed.restore(_through_disk(store.snapshot()))
    assert isinstance(resumed.cases["case1"], CaseFile)
    # Reachable through the async API, which is what production uses.
    assert (await resumed.get_case("case1")).kind == "theft"


@pytest.mark.asyncio
async def test_the_runner_actually_registers_it(runner):
    """The finding, in its own terms: a store with a snapshot method and
    no `checkpoints.register` line is a store that is never saved. That
    was true of this one for the whole life of the project."""
    registered = set(runner.tick_engine.checkpoints.components)
    assert "crime" in registered, "the crime store has a snapshot nothing calls"
    # And the two GOV-12 named that other work closed - asserted so they
    # cannot quietly reopen.
    assert "stakes" in registered, "head-tax debt is not checkpointed (CORE-08)"
    assert "charter" in registered, "the charter is not checkpointed"


@pytest.mark.asyncio
async def test_a_whole_checkpoint_round_trip_keeps_the_prisoner(runner):
    """Through the registry the runner actually uses, not the store alone."""
    await runner.crime.store.put_confinement(ConfinementRecord(
        confinement_id="c1", case_id="case1", agent_id="n01",
        jail_location_id="sector:900004", start_tick=10, release_tick=40,
    ))
    from world.core.checkpoint import WorldCheckpoint

    written = await runner.tick_engine.checkpoints.write(tick_index=12, game_day=0)
    on_disk = WorldCheckpoint.model_validate(_through_disk(written.model_dump(mode="json")))

    runner.crime.store.cases.clear()
    runner.crime.store.confinements.clear()
    runner.crime.store.active_confinement_by_agent.clear()

    await runner.tick_engine.checkpoints.restore(on_disk)

    assert await runner.crime.store.get_active_confinement("n01") is not None
