"""The world event bus - DESIGN.md section 9: "World event bus (in-process
pub/sub) that every mutation publishes to."

app/CONVENTIONS.md's "Event naming scheme" is the contract this module
enforces at construction time (WorldEvent.type is validated, not just
documented): every mutation, from every world/ submodule, publishes one
WorldEvent whose type is `<domain>.<subject>.<past_tense_verb>` (or the
2-segment `<domain>.<past_tense_verb>` form CONVENTIONS.md's own table
uses for `agent.moved` / `business.registered`). A bad event type is a
bug in the caller, so it fails at publish time rather than silently
reaching a Supabase writer or a game/ Realtime subscriber with a type
string nothing downstream recognizes.

EventBus itself is deliberately in-process only (no Supabase writes, no
network). CONVENTIONS.md gives world/ ownership of the `events` table;
the actual system-of-record write is a subscriber wired up by whoever
owns that persistence path, exactly like this module's CheckpointSink
seam in checkpoint.py. What EventBus guarantees is narrower and more
fundamental: the instant a mutation happens anywhere in-process, every
in-process subscriber (the scheduler's perception-driven interrupts,
the perception system's location index, a future Supabase writer, the
game console's local mirror) observes it in the same asyncio task turn.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import deque
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

KNOWN_DOMAINS = frozenset(
    {
        "world",
        "agent",
        "economy",
        "business",
        "crime",
        "politics",
        "religion",
        "health",
        "broker",
        "research",
        "system",
    }
)

SEGMENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_event_type(event_type: str) -> str | None:
    """Checks one event type string against the CONVENTIONS.md naming scheme.

    Returns None when valid, else a human-readable reason. A pure
    function (not raising) so WorldEvent's validator, tests, and any
    future linter can all call it the same way.
    """
    parts = event_type.split(".")
    if len(parts) not in (2, 3):
        return (
            f"{event_type!r} must have 2 or 3 dot-separated segments "
            f"(<domain>.<verb> or <domain>.<subject>.<verb>), got {len(parts)}"
        )
    for part in parts:
        if not SEGMENT_PATTERN.match(part):
            return f"{event_type!r}: segment {part!r} must be lowercase snake_case"
    domain = parts[0]
    if domain not in KNOWN_DOMAINS:
        return (
            f"{event_type!r}: domain {domain!r} is not one of "
            f"{sorted(KNOWN_DOMAINS)} (app/CONVENTIONS.md 'Event naming scheme')"
        )
    return None


class WorldEvent(BaseModel):
    """One entry on the world event bus - the in-process mirror of a Supabase
    `events` row (app/CONVENTIONS.md).

    `type` always names something that already happened (past tense),
    because a WorldEvent is only ever published after the mutation it
    describes has already landed - conversation and intent never appear
    here, only completed tool-call effects (DESIGN.md section 2: "World
    state mutates ONLY through tool calls").
    """

    type: str
    tick: int
    payload: dict[str, Any] = Field(default_factory=dict)
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    published_at: float = Field(default_factory=time.time)

    @field_validator("type")
    @classmethod
    def check_type(cls, value: str) -> str:
        error = validate_event_type(value)
        if error is not None:
            raise ValueError(error)
        return value

    @property
    def domain(self) -> str:
        return self.type.split(".", 1)[0]


Subscriber = Callable[[WorldEvent], Awaitable[None]]


class EventBus:
    """In-process async pub/sub. Mirrors engine's EventEmitter
    (engine/src/engine_core/events.py): concurrent fan-out via
    asyncio.gather with return_exceptions=True, so one broken subscriber
    (a bug in a research probe's listener, say) logs and is skipped
    rather than blocking or crashing every other subscriber - including
    the scheduler's own interrupt wiring, which must never go dark
    because an unrelated subscriber raised.

    Subscriptions are per-domain, matching the `kasukabe:<domain>`
    Realtime channel split in CONVENTIONS.md, plus a "*" wildcard for
    subscribers that need the full firehose (perception's interrupt
    detection has to see every domain: a crime event, a health event, a
    politics event can all interrupt an agent's schedule).
    """

    DEFAULT_BUFFER = 2000

    def __init__(self, buffer_size: int = DEFAULT_BUFFER) -> None:
        self.subscribers: dict[str, list[Subscriber]] = {}
        self.buffer: deque[WorldEvent] = deque(maxlen=buffer_size)

    def size_for_population(self, agents: int) -> None:
        """Grow the ring to fit a town of this size (CORE-09).

        Called once the population exists, because the bus is constructed
        before anybody is in it. 200 events per resident covers a full
        cadence of walking plus everything else at that scale; never
        shrinks, so a fixture keeps the default.
        """
        wanted = max(self.buffer.maxlen or 0, self.DEFAULT_BUFFER, agents * 200)
        if wanted != (self.buffer.maxlen or 0):
            self.buffer = deque(self.buffer, maxlen=wanted)

    def subscribe(self, domain: str, callback: Subscriber) -> None:
        if domain != "*" and domain not in KNOWN_DOMAINS:
            raise ValueError(
                f"unknown domain {domain!r}, expected '*' or one of {sorted(KNOWN_DOMAINS)}"
            )
        self.subscribers.setdefault(domain, []).append(callback)

    def unsubscribe(self, domain: str, callback: Subscriber) -> None:
        callbacks = self.subscribers.get(domain)
        if callbacks is None:
            return
        try:
            callbacks.remove(callback)
        except ValueError:
            pass

    async def publish(self, event: WorldEvent) -> None:
        """Buffers the event, then fans it out to every matching subscriber concurrently."""
        self.buffer.append(event)
        callbacks = list(self.subscribers.get(event.domain, ())) + list(
            self.subscribers.get("*", ())
        )
        if not callbacks:
            return
        results = await asyncio.gather(*(cb(event) for cb in callbacks), return_exceptions=True)
        for cb, result in zip(callbacks, results):
            if isinstance(result, Exception):
                logger.exception(
                    "event subscriber %s failed for %s",
                    getattr(cb, "__name__", cb),
                    event.type,
                    exc_info=result,
                )

    async def emit(
        self, event_type: str, tick: int, payload: dict[str, Any] | None = None
    ) -> WorldEvent:
        """Convenience: builds a WorldEvent and publishes it in one call.

        This is the call site every world/ mutation is expected to use:
        `await event_bus.emit("agent.moved", tick=t,
        payload={"agent_id": ..., "location_id": ...})`.
        """
        event = WorldEvent(type=event_type, tick=tick, payload=payload or {})
        await self.publish(event)
        return event

    def recent(self, since_tick: int | None = None, domain: str | None = None) -> list[WorldEvent]:
        """Reads the replay buffer, optionally filtered. Used by PerceptionSystem
        to find what happened at an agent's location recently; also handy in tests."""
        events = list(self.buffer)
        if since_tick is not None:
            events = [e for e in events if e.tick >= since_tick]
        if domain is not None:
            events = [e for e in events if e.domain == domain]
        return events

    async def replay(self, callback: Subscriber) -> None:
        """Delivers every buffered event to one callback in order. For a
        subscriber that attaches after some events already happened
        (e.g. a newly connected dashboard) and wants to catch up."""
        for event in list(self.buffer):
            try:
                await callback(event)
            except Exception:
                logger.exception("event replay callback failed for %s", event.type)
