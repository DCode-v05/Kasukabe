"""Tests for world/charter/genesis.py."""

from __future__ import annotations

import pytest

from world.charter.events import EVENT_CHARTER_GENESIS_SEEDED
from world.charter.genesis import seed_genesis
from world.charter.store import CharterStore
from world.charter.tests.conftest import EventRecorder


@pytest.mark.asyncio
async def test_seed_genesis_populates_version_zero_charter_and_policy() -> None:
    store = CharterStore()
    await seed_genesis(store, genesis_tick=0)

    charter = await store.get_charter()
    policy = await store.get_policy()
    assert charter.version == 0
    assert charter.genesis_tick == 0
    assert policy.version == 0


@pytest.mark.asyncio
async def test_seed_genesis_government_starts_vacant() -> None:
    store = CharterStore()
    await seed_genesis(store, genesis_tick=0)

    state = await store.get_government_state()
    assert state.pm_agent_id is None
    assert state.minister_agent_ids == []


@pytest.mark.asyncio
async def test_seed_genesis_emits_one_event() -> None:
    store = CharterStore()
    recorder = EventRecorder()
    await seed_genesis(store, genesis_tick=42, event_sink=recorder.sink)

    assert recorder.types() == [EVENT_CHARTER_GENESIS_SEEDED]
    assert recorder.events[0]["payload"]["genesis_tick"] == 42


@pytest.mark.asyncio
async def test_seed_genesis_without_sink_does_not_raise() -> None:
    store = CharterStore()
    await seed_genesis(store, genesis_tick=0, event_sink=None)
    assert (await store.get_charter()).version == 0
