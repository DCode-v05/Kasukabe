"""Seeding twice into one store must not silence the second run.

`CORE-13`. `TraceStore.seed_agent` is the ONLY place `known_agent_ids` is
filled, and it filled it after a successful insert. Seeding against a store
that already holds the cast raises `UNIQUE constraint failed: agents.slug`;
`seed_genesis` swallows that and logs it as benign - "that is a re-run, not
a fault".

True of the INSERT. False of everything after it: the recorder then did not
know the resident existed, so every utterance and transaction naming them
for the rest of the run wrote NULL agent references or was dropped, and the
report read the hole as silence. That is the same shape as the run-16 loss
the method's own docstring describes, arriving by a different door.

The row is there - that is what the error means - so an already-present
resident is registered exactly like a newly-inserted one.
"""
from __future__ import annotations

import pytest

from supabase.config import SupabaseConfig
from supabase.sqlite_client import SqliteClient
from world.life.models import AgentLifeRecord, Sex
from world.wiring import TraceStore


def _agent(agent_id: str = "n01") -> AgentLifeRecord:
    return AgentLifeRecord(
        agent_id=agent_id, sex=Sex.FEMALE, age_years=30.0,
        household_id="h_quarry", is_minor=False,
    )


@pytest.fixture
def store(tmp_path):
    client = SqliteClient(SupabaseConfig(sqlite_path=str(tmp_path / "trace.db")))
    client.ensure_schema()
    return TraceStore(client)


@pytest.mark.asyncio
async def test_the_first_seed_registers(store):
    await store.seed_agent(_agent(), None, 0)
    assert "n01" in store.known_agent_ids


@pytest.mark.asyncio
async def test_seeding_the_same_cast_twice_keeps_them_known(store):
    """The regression. A second boot against the same trace file used to
    leave `known_agent_ids` empty."""
    await store.seed_agent(_agent(), None, 0)

    second = TraceStore(store.client)
    await second.seed_agent(_agent(), None, 0)

    assert "n01" in second.known_agent_ids, (
        "CORE-13: a re-run left the recorder not knowing the resident "
        "exists, so every later row naming them is orphaned"
    )
    assert "n01" in second.seeded_agent_ids


@pytest.mark.asyncio
async def test_a_genuinely_refused_row_is_still_not_registered(store, monkeypatch):
    """The guard on the guard. Registering on ANY failure would put back
    the bug the method's docstring describes - a recorder confidently
    naming somebody the database never heard of."""
    async def always_fails(*a, **kw):
        raise RuntimeError("disk is gone")

    monkeypatch.setattr(store, "insert_locked", always_fails)
    monkeypatch.setattr(store, "_agent_row_exists", lambda _id: _false())

    with pytest.raises(RuntimeError):
        await store.seed_agent(_agent("n99"), None, 0)
    assert "n99" not in store.known_agent_ids


async def _false() -> bool:
    return False
