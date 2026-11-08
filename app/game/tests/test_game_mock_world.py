"""mock_world.py's pure half: build_mock_agents() and step(). No asyncio,
no wall-clock sleep - see mock_world.py's module docstring for why the
town-simulation logic is split out of MockWorldClient specifically so it
can be tested this way."""

from __future__ import annotations

import random

import pytest

from game.config import GameSettings
from game.mock_world import (
    MOCK_LOCATIONS,
    MockTownState,
    MockWorldClient,
    build_mock_agents,
    load_mock_map_json,
    step,
)
from game.position import SPRITE_ROSTER


def test_build_mock_agents_is_deterministic_for_same_seed() -> None:
    first = build_mock_agents(12, seed=42)
    second = build_mock_agents(12, seed=42)
    assert {a.display_name for a in first.values()} == {a.display_name for a in second.values()}
    assert {a.location_name for a in first.values()} == {a.location_name for a in second.values()}


def test_build_mock_agents_differs_for_different_seed() -> None:
    first = build_mock_agents(12, seed=1)
    second = build_mock_agents(12, seed=2)
    first_names = [a.display_name for a in first.values()]
    second_names = [a.display_name for a in second.values()]
    assert first_names != second_names


def test_build_mock_agents_rejects_nonpositive_count() -> None:
    with pytest.raises(ValueError):
        build_mock_agents(0, seed=1)


def test_build_mock_agents_produces_unique_names() -> None:
    agents = build_mock_agents(20, seed=5)
    names = [a.display_name for a in agents.values()]
    assert len(names) == len(set(names))


def test_build_mock_agents_uses_real_locations_and_sprites() -> None:
    agents = build_mock_agents(15, seed=9)
    known_locations = {name for name, _, _ in MOCK_LOCATIONS}
    for agent in agents.values():
        assert agent.location_name in known_locations
        assert agent.sprite_key in SPRITE_ROSTER


def test_build_mock_agents_seeds_relationships() -> None:
    agents = build_mock_agents(10, seed=3)
    for agent in agents.values():
        assert 1 <= len(agent.relationships) <= 3
        for rel in agent.relationships:
            assert rel.other_agent_id in agents
            assert rel.other_agent_id != agent.agent_id


def test_step_advances_tick_by_exactly_one() -> None:
    state = MockTownState(tick=0, agents=build_mock_agents(5, seed=1))
    rng = random.Random(1)
    new_state, events = step(state, rng)
    assert new_state.tick == 1
    assert events[0].type == "world.tick.advanced"
    assert events[0].tick == 1


def test_step_is_deterministic_for_same_rng_state() -> None:
    state_a = MockTownState(tick=0, agents=build_mock_agents(6, seed=11))
    state_b = MockTownState(tick=0, agents=build_mock_agents(6, seed=11))
    rng_a = random.Random(99)
    rng_b = random.Random(99)

    _, events_a = step(state_a, rng_a)
    _, events_b = step(state_b, rng_b)

    assert [e.type for e in events_a] == [e.type for e in events_b]
    assert [e.payload for e in events_a] == [e.payload for e in events_b]


def test_step_emits_agent_moved_with_valid_coordinates() -> None:
    state = MockTownState(tick=0, agents=build_mock_agents(5, seed=2))
    rng = random.Random(2)
    _, events = step(state, rng)
    moved = [e for e in events if e.type == "agent.moved"]
    assert len(moved) == 1
    payload = moved[0].payload
    assert payload["agent_id"] in state.agents
    assert isinstance(payload["tile_x"], int)
    assert isinstance(payload["tile_y"], int)


def test_step_every_event_type_is_a_valid_convention_string() -> None:
    # app/CONVENTIONS.md's naming scheme: <domain>.<verb> or
    # <domain>.<subject>.<verb>, lowercase snake_case segments.
    state = MockTownState(tick=0, agents=build_mock_agents(8, seed=123))
    rng = random.Random(123)
    for _ in range(50):
        state, events = step(state, rng)
        for event in events:
            parts = event.type.split(".")
            assert len(parts) in (2, 3)
            for part in parts:
                assert part == part.lower()
                assert " " not in part


def test_step_handles_empty_agent_roster_without_crashing() -> None:
    state = MockTownState(tick=0, agents={})
    rng = random.Random(1)
    new_state, events = step(state, rng)
    assert new_state.tick == 1
    assert any(e.type == "world.tick.advanced" for e in events)


def test_load_mock_map_json_matches_static_asset() -> None:
    data = load_mock_map_json()
    assert data["width"] == 140
    assert data["height"] == 100
    assert any(layer["name"] == "Collisions" for layer in data["layers"])


@pytest.mark.asyncio
async def test_mock_world_client_snapshot_has_map_and_agents() -> None:
    settings = GameSettings(mock_agent_count=6, mock_seed=1)
    client = MockWorldClient(settings)
    try:
        snapshot = await client.get_snapshot()
        assert snapshot.tick == 0
        assert len(snapshot.agents) == 6
        assert snapshot.map_tiled_json["width"] == 140
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mock_world_client_agent_detail_known_and_unknown() -> None:
    settings = GameSettings(mock_agent_count=4, mock_seed=2)
    client = MockWorldClient(settings)
    try:
        snapshot = await client.get_snapshot()
        agent_id = snapshot.agents[0].agent_id

        detail = await client.get_agent_detail(agent_id)
        assert detail is not None
        assert detail.agent_id == agent_id
        assert detail.wallets.usd_wallet_cents >= 0

        missing = await client.get_agent_detail("does-not-exist")
        assert missing is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mock_world_client_get_places_returns_the_genesis_catalog() -> None:
    from game.world_client import GENESIS_PLACES

    settings = GameSettings(mock_agent_count=3, mock_seed=5)
    client = MockWorldClient(settings)
    try:
        places = await client.get_places()
        assert places == list(GENESIS_PLACES)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mock_world_client_get_spend_status_moves_with_tick() -> None:
    settings = GameSettings(mock_agent_count=3, mock_seed=6)
    client = MockWorldClient(settings)
    try:
        first = await client.get_spend_status()
        assert first.available is True
        assert first.total_usd_cents == 100_000
        assert first.burned_usd_cents == 0  # tick 0

        for _ in range(20):
            client.state, _events = step(client.state, client.rng)
        second = await client.get_spend_status()
        assert second.burned_usd_cents > first.burned_usd_cents
        assert second.burned_usd_cents <= second.total_usd_cents
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mock_world_client_streams_tick_events() -> None:
    settings = GameSettings(mock_agent_count=3, mock_seed=3)
    client = MockWorldClient(settings, tick_interval_s=0.01)
    try:
        stream = client.stream_events()
        first = await stream.__anext__()
        assert first.type in {"world.tick.advanced", "agent.moved", "agent.spoke"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mock_world_client_close_stops_background_task() -> None:
    settings = GameSettings(mock_agent_count=3, mock_seed=4)
    client = MockWorldClient(settings, tick_interval_s=0.01)
    await client.get_snapshot()  # starts the background tick task
    await client.close()
    assert client.closed is True
