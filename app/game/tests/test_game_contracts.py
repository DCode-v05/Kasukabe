"""contracts.py's pydantic wire models: construction, validation, and
round-trip JSON serialization - the shapes game/server.py hands to the
browser and world_client.py expects back from world/."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from game.contracts import (
    AgentDetail,
    AgentPosition,
    AgentSummary,
    RelationshipRecord,
    Snapshot,
    UtteranceRecord,
    Wallets,
    WireEvent,
)


def test_wallets_reject_negative_balances() -> None:
    with pytest.raises(ValidationError):
        Wallets(usd_wallet_cents=-1, sim_balance=0)
    with pytest.raises(ValidationError):
        Wallets(usd_wallet_cents=0, sim_balance=-1)


def test_agent_position_allows_all_fields_optional() -> None:
    position = AgentPosition()
    assert position.location_id is None
    assert position.tile_x is None
    assert position.tile_y is None


def test_agent_summary_round_trips_through_json() -> None:
    summary = AgentSummary(
        agent_id="a-1",
        display_name="Ada Lovelace",
        sprite_key="Abigail_Chen",
        position=AgentPosition(location_id="Hobbs Cafe", tile_x=10, tile_y=20),
        current_action="brewing coffee",
        current_utterance="Morning!",
    )
    restored = AgentSummary.model_validate_json(summary.model_dump_json())
    assert restored == summary


def test_utterance_record_listener_is_optional() -> None:
    record = UtteranceRecord(
        speaker_agent_id="a-1", speaker_name="Ada", content="hello", tick=5
    )
    assert record.listener_agent_id is None
    assert record.is_public is True


def test_relationship_record_defaults() -> None:
    record = RelationshipRecord(other_agent_id="a-2", relationship_type="friend")
    assert record.interaction_count == 1
    assert record.sentiment is None


def test_agent_detail_requires_wallets_and_position() -> None:
    with pytest.raises(ValidationError):
        AgentDetail(
            agent_id="a-1",
            display_name="Ada",
            sprite_key="Abigail_Chen",
            bio="a bio",
        )  # type: ignore[call-arg]


def test_agent_detail_full_round_trip() -> None:
    detail = AgentDetail(
        agent_id="a-1",
        display_name="Ada Lovelace",
        sprite_key="Abigail_Chen",
        bio="Runs the cafe.",
        occupation="cafe owner",
        age_years=34.0,
        wallets=Wallets(usd_wallet_cents=150, sim_balance=4200),
        position=AgentPosition(location_id="Hobbs Cafe", tile_x=10, tile_y=20),
        current_action="brewing coffee",
        recent_utterances=[
            UtteranceRecord(speaker_agent_id="a-1", speaker_name="Ada", content="hi", tick=3)
        ],
        relationships=[RelationshipRecord(other_agent_id="a-2", relationship_type="friend")],
    )
    restored = AgentDetail.model_validate_json(detail.model_dump_json())
    assert restored == detail


def test_snapshot_carries_map_json_verbatim() -> None:
    map_json = {"width": 140, "height": 100, "layers": []}
    snapshot = Snapshot(tick=12, map_tiled_json=map_json, agents=[])
    restored = Snapshot.model_validate_json(snapshot.model_dump_json())
    assert restored.map_tiled_json == map_json
    assert restored.tick == 12


def test_wire_event_requires_event_id_and_published_at() -> None:
    with pytest.raises(ValidationError):
        WireEvent(type="agent.moved", tick=1, payload={})  # type: ignore[call-arg]


def test_wire_event_defaults_empty_payload() -> None:
    event = WireEvent(type="world.tick.advanced", tick=1, event_id="e1", published_at=0.0)
    assert event.payload == {}
