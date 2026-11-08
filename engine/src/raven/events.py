"""Typed event dataclasses for the observability subsystem.

Every event emitted through the tracking API is one of these frozen
dataclasses. The ``Event`` union type covers all variants. Each carries
a ``timestamp`` field (Unix seconds, float) and a ``kind`` class attribute
that doubles as the ``kind`` column value in ``obs_events``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Union


@dataclass(frozen=True)
class TokenUsageEvent:
    kind: str = field(default="token_usage", init=False)
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    agent_step_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ToolCallEvent:
    kind: str = field(default="tool_call", init=False)
    tool_name: str = ""
    duration_ms: float = 0.0
    success: bool = True
    agent_step_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class AgentStepEvent:
    kind: str = field(default="agent_step", init=False)
    step_type: str = ""
    step_id: str = ""
    parent_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class RouteDecisionEvent:
    kind: str = field(default="route_decision", init=False)
    chosen_model: str = ""
    tier: str = ""
    reason: str = ""
    alternatives: list[str] = field(default_factory=list)
    task_type: str = ""
    provider: str = ""
    routing_active: bool = True
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CacheEvent:
    kind: str = field(default="cache", init=False)
    model: str = ""
    cache_hit_ratio: float = 0.0
    tokens_cached: int = 0
    tokens_fresh: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class RetentionRunEvent:
    kind: str = field(default="retention_run", init=False)
    rollup_count: int = 0
    drop_count: int = 0
    duration_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ActivityTransitionEvent:
    """Session lifecycle transition recorded for ops visibility.

    Mirrors ``ActivityStateChanged`` from the engine event bus but persists
    into obs_events so Raven can answer questions like "how long does an
    average session stay active before idling" across restarts.
    """

    kind: str = field(default="activity_transition", init=False)
    old_state: str = ""
    new_state: str = ""
    idle_seconds: float = 0.0
    tool_calls: int = 0
    errors: int = 0
    user_active_seconds: float = 0.0
    engine_active_seconds: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class UntypedEvent:
    """Free-form event for ad-hoc telemetry."""

    kind: str = ""
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


Event = Union[
    TokenUsageEvent,
    ToolCallEvent,
    AgentStepEvent,
    RouteDecisionEvent,
    CacheEvent,
    RetentionRunEvent,
    ActivityTransitionEvent,
    UntypedEvent,
]

KIND_MAP: dict[str, type] = {
    "token_usage": TokenUsageEvent,
    "tool_call": ToolCallEvent,
    "agent_step": AgentStepEvent,
    "route_decision": RouteDecisionEvent,
    "cache": CacheEvent,
    "retention_run": RetentionRunEvent,
    "activity_transition": ActivityTransitionEvent,
}


def event_to_payload(event: Event) -> str:
    """Serialize event fields to a JSON string for the ``payload`` column."""
    if isinstance(event, UntypedEvent):
        return json.dumps(event.metadata, default=str)
    d = asdict(event)
    d.pop("kind", None)
    d.pop("timestamp", None)
    return json.dumps(d, default=str)


def event_from_row(kind: str, timestamp: float, payload: str) -> Event:
    """Reconstruct an Event from an ``obs_events`` row."""
    cls = KIND_MAP.get(kind)
    if cls is None:
        data = json.loads(payload) if payload else {}
        return UntypedEvent(kind=kind, metadata=data, timestamp=timestamp)
    data = json.loads(payload)
    data["timestamp"] = timestamp
    return cls(**data)
