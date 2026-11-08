"""Checkpoint/restore - DESIGN.md section 9: "Checkpoint: full world state
serialization every game-day (spot-interruption safety; burst or
continuous pacing)."

world/'s host (m5.metal spot, DESIGN.md section 9) can be reclaimed at
any time; a day's worth of ticks (up to 144 ticks of scheduling and
perception state, and - once other world/ submodules wire in the same
way - ledger, health, crime, charter state) must be recoverable without
replaying the whole simulation from tick 0. CheckpointManager is the
seam: any stateful piece of world/ registers itself once, and one
`write()` call produces one complete snapshot covering everything
registered, matching the `world_snapshots` Supabase table CONVENTIONS.md
gives world/ ownership of.

This module deliberately does not talk to Supabase. It produces and
restores a WorldCheckpoint (a plain pydantic model that round-trips
through model_dump_json / model_validate_json unchanged) and hands
finished checkpoints to an injected CheckpointSink - a Supabase-writing
sink is the persistence owner's responsibility to supply; tests and
single-process dev runs use InMemoryCheckpointSink below.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Checkpointable(Protocol):
    """Anything CheckpointManager can freeze and thaw. clock.GameClock,
    scheduler.AgentScheduler, and perception.LocationIndex all satisfy
    this structurally (no inheritance needed); any other world/ submodule
    that wants to be included in the daily checkpoint just needs the same
    two methods."""

    def snapshot(self) -> dict[str, Any]: ...

    def restore(self, data: dict[str, Any]) -> None: ...


class WorldCheckpoint(BaseModel):
    """Full checkpoint at one game-day boundary: one dict entry per
    registered component, keyed by the name it registered under."""

    tick_index: int
    game_day: int
    written_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    components: dict[str, Any] = Field(default_factory=dict)


CheckpointSink = Callable[[WorldCheckpoint], Awaitable[None]]


class InMemoryCheckpointSink:
    """Default sink: keeps every checkpoint in a list. Enough for tests and
    a single-process dev run; production wires in a Supabase-writing sink
    with this exact `async def __call__(self, checkpoint)` shape instead."""

    def __init__(self) -> None:
        self.checkpoints: list[WorldCheckpoint] = []

    async def __call__(self, checkpoint: WorldCheckpoint) -> None:
        self.checkpoints.append(checkpoint)

    @property
    def latest(self) -> WorldCheckpoint | None:
        return self.checkpoints[-1] if self.checkpoints else None


class CheckpointError(RuntimeError):
    """Aggregated checkpoint failure - every failing component in one
    report, same shape as engine/src/config.py's ConfigError (see
    app/CONVENTIONS.md on aggregating errors instead of failing on the
    first one). A checkpoint that silently omitted a broken component
    would be worse than one that fails loudly: it would restore into an
    inconsistent world with no sign anything was missing."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        joined = "\n  - ".join(self.errors)
        super().__init__(f"checkpoint failed ({len(self.errors)} error(s)):\n  - {joined}")


class CheckpointManager:
    """Coordinates snapshot/restore across every registered component."""

    def __init__(self, sink: CheckpointSink | None = None) -> None:
        self.sink = sink
        self.components: dict[str, Checkpointable] = {}

    def register(self, name: str, component: Checkpointable) -> None:
        if name in self.components:
            raise ValueError(f"a component named {name!r} is already registered for checkpointing")
        self.components[name] = component

    def unregister(self, name: str) -> None:
        self.components.pop(name, None)

    async def write(self, tick_index: int, game_day: int) -> WorldCheckpoint:
        """Snapshots every registered component and, if a sink is
        configured, hands the result to it. Raises CheckpointError
        (aggregating every failing component) rather than writing a
        partial checkpoint."""
        data: dict[str, Any] = {}
        errors: list[str] = []
        for name, component in self.components.items():
            try:
                data[name] = component.snapshot()
            except Exception as exc:  # noqa: BLE001 - aggregated, not swallowed
                errors.append(f"{name}: {exc}")
        if errors:
            raise CheckpointError(errors)

        checkpoint = WorldCheckpoint(tick_index=tick_index, game_day=game_day, components=data)
        if self.sink is not None:
            await self.sink(checkpoint)
        return checkpoint

    async def restore(self, checkpoint: WorldCheckpoint) -> None:
        """Restores every registered component from a checkpoint. A
        registered component with no matching data in the checkpoint is a
        CheckpointError (silent data loss); checkpoint data for a
        component nobody registered this run is logged and skipped (the
        checkpoint may have been written by a process with more
        components wired in than this one - e.g. a research replay that
        only cares about the clock and scheduler)."""
        errors: list[str] = []
        for name, component in self.components.items():
            if name not in checkpoint.components:
                errors.append(
                    f"{name}: registered for checkpointing but missing from this "
                    f"checkpoint (game_day={checkpoint.game_day})"
                )
                continue
            try:
                component.restore(checkpoint.components[name])
            except Exception as exc:  # noqa: BLE001 - aggregated, not swallowed
                errors.append(f"{name}: {exc}")

        unknown = set(checkpoint.components) - set(self.components)
        for name in sorted(unknown):
            logger.warning("checkpoint has data for unregistered component %r, skipped", name)

        if errors:
            raise CheckpointError(errors)
