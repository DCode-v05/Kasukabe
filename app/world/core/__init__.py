"""world-core: the tick engine (DESIGN.md section 9, task "world-core").

Owns the one authoritative notion of game time and the event-driven
scheduling loop built on top of it: the game clock and calendar, the
world event bus every mutation publishes to, the per-agent decision
scheduler, the perception system, and checkpoint/restore of world-core's
own state. Everything else in world/ (ledger, charter, health, crime,
genesis) is expected to publish its mutations onto the same EventBus and
register its own state with the same CheckpointManager pattern used here
- see checkpoint.py's module docstring.

This package does not execute agent LLM turns and does not own the map,
ledger, or tool registry. Its job is exactly "when does time pass, who
gets to act, and what do they perceive" - the scaffolding those other
concerns run inside.

Module boundary (app/CONVENTIONS.md): part of world/, so it owns world
state and must never import boto3.
"""

from __future__ import annotations

from world.core.checkpoint import (
    Checkpointable,
    CheckpointError,
    CheckpointManager,
    CheckpointSink,
    InMemoryCheckpointSink,
    WorldCheckpoint,
)
from world.core.clock import (
    DAY_START_HOUR,
    MINUTES_PER_DAY,
    NIGHT_START_HOUR,
    TICK_MINUTES,
    TICKS_PER_DAY,
    GameClock,
    GameTimestamp,
    is_day_boundary,
    timestamp_for,
)
from world.core.engine import TickEngine, TickResult
from world.core.events import EventBus, WorldEvent, validate_event_type
from world.core.movement import (
    Journey,
    LocationIdResolver,
    MovementSystem,
    TICKS_PER_OFF_STREET_TILE,
    TICKS_PER_STREET_TILE,
    default_location_id,
    estimate_travel_ticks,
    tile_step_cost,
)
from world.core.pacing import PacingConfig, PacingController, PacingMode
from world.core.perception import (
    AmbientInfo,
    LocationIndex,
    Perception,
    PerceivedEvent,
    PerceptionSystem,
    default_interrupt_policy,
)
from world.core.scheduler import AgentScheduler, ScheduleEntry, SchedulerConfig

__all__ = [
    "Checkpointable",
    "CheckpointError",
    "CheckpointManager",
    "CheckpointSink",
    "InMemoryCheckpointSink",
    "WorldCheckpoint",
    "DAY_START_HOUR",
    "MINUTES_PER_DAY",
    "NIGHT_START_HOUR",
    "TICK_MINUTES",
    "TICKS_PER_DAY",
    "GameClock",
    "GameTimestamp",
    "is_day_boundary",
    "timestamp_for",
    "TickEngine",
    "TickResult",
    "EventBus",
    "WorldEvent",
    "validate_event_type",
    "Journey",
    "LocationIdResolver",
    "MovementSystem",
    "TICKS_PER_OFF_STREET_TILE",
    "TICKS_PER_STREET_TILE",
    "default_location_id",
    "estimate_travel_ticks",
    "tile_step_cost",
    "PacingConfig",
    "PacingController",
    "PacingMode",
    "AmbientInfo",
    "LocationIndex",
    "Perception",
    "PerceivedEvent",
    "PerceptionSystem",
    "default_interrupt_policy",
    "AgentScheduler",
    "ScheduleEntry",
    "SchedulerConfig",
]
