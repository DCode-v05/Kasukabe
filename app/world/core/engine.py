"""TickEngine - wires clock, event bus, scheduler, perception, checkpoint,
pacing, and (optionally) movement into the single loop DESIGN.md section 9
describes as world-core's job.

What TickEngine does NOT do: execute agent LLM turns, own the map beyond
a bare location index, or own the ledger/tools/charter. Those are other
world/ submodules' or other layers' responsibility per app/CONVENTIONS.md's
module boundaries. tick() advances time by exactly one tick and hands
back a TickResult naming which agents are due and what each perceives;
the caller (an agent-runtime layer sitting above world-core) runs turns
for the due agents and reports each one back via mark_agent_decided()
before the next tick. Every piece TickEngine wires together (GameClock,
EventBus, AgentScheduler, PerceptionSystem, CheckpointManager,
PacingController) is also usable standalone - this class is a
convenience default wiring, not a required entry point.

Movement (task "walking"): world.core.movement.MovementSystem is a
self-contained state machine (its own module's docstring) that a tick
loop is expected to `advance()` once per tick, publishing whatever
WorldEvents that returns onto the same EventBus every other world/
mutation uses - this is that tick loop. `movement_system` is an optional
constructor argument (default None, exactly today's behavior with zero
movement wiring) rather than something this class constructs itself:
building the real WorldMap MovementSystem paths against is
world.tools.bootstrap.build_world()'s job (see that module's docstring),
and world.tools.state.WorldState.movement is expected to be the SAME
instance handed in here, so a `move` tool call (world.tools.primitives.
movement.move_handler) and this tick loop's own advance() calls operate
on one shared set of in-progress journeys - see run_genesis.py's
GenesisRunner.boot() and wiring.py's build_world_server() for the two
current callers that construct both a WorldState and a TickEngine; each
needs exactly one line added, `movement_system=world_state.movement`
(or `world.movement`), to actually receive real ticked walking - this
class is ready for that argument the moment either supplies it, and does
nothing different than before when neither does.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from world.core.checkpoint import CheckpointManager, CheckpointSink, WorldCheckpoint
from world.core.clock import GameClock, GameTimestamp, is_day_boundary
from world.core.events import EventBus
from world.core.movement import MovementSystem
from world.core.pacing import PacingConfig, PacingController
from world.core.perception import LocationIndex, Perception, PerceptionSystem
from world.core.scheduler import AgentScheduler, SchedulerConfig


class TickResult(BaseModel):
    """Everything one call to TickEngine.tick() produced."""

    tick: GameTimestamp
    due_agents: list[str] = Field(default_factory=list)
    perceptions: dict[str, Perception] = Field(default_factory=dict)
    checkpoint: WorldCheckpoint | None = None


class TickEngine:
    """The world-core tick engine (DESIGN.md section 9 / task "world-core")."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        scheduler_config: SchedulerConfig | None = None,
        checkpoint_sink: CheckpointSink | None = None,
        pacing_config: PacingConfig | None = None,
        tick_index: int = 0,
        hearing_window_ticks: int = 1,
        movement_system: MovementSystem | None = None,
    ) -> None:
        self.event_bus = event_bus or EventBus()
        self.clock = GameClock(tick_index)
        self.scheduler = AgentScheduler(scheduler_config)
        self.locations = LocationIndex()
        self.perception = PerceptionSystem(
            self.locations,
            self.event_bus,
            hearing_window_ticks=hearing_window_ticks,
        )
        self.checkpoints = CheckpointManager(checkpoint_sink)
        # See module docstring's "Movement" section: None (the default)
        # reproduces today's exact behavior with no movement wiring at
        # all - every existing caller/test that never passes this stays
        # unaffected.
        self.movement = movement_system

        # Event-driven wiring: locations track themselves from agent.*
        # events, and any event naming an agent (per perception.py's
        # payload key convention) can interrupt that agent's schedule -
        # both happen automatically from here on, with no further calls
        # required as other world/ submodules publish their own events.
        self.locations.attach(self.event_bus)
        self.perception.attach(self.scheduler)

        self.checkpoints.register("clock", self.clock)
        self.checkpoints.register("scheduler", self.scheduler)
        self.checkpoints.register("locations", self.locations)
        if self.movement is not None:
            # MovementSystem already implements the Checkpointable shape
            # (its own module docstring: "Forward-compatible with
            # CheckpointManager.register('movement', ...)") - registering
            # it here means a day-boundary checkpoint (and a resume)
            # covers every in-progress journey too, with no separate
            # adapter needed the way run_genesis.py needed one for
            # ledger/charter (those live outside world/core entirely).
            self.checkpoints.register("movement", self.movement)

        self.pacing = PacingController(self.tick, pacing_config)

    def register_agent(self, agent_id: str, location_id: str | None = None) -> None:
        """Enrolls an agent in the scheduler and, if given, places them on
        the map. Raises ValueError if agent_id is already registered
        (AgentScheduler.register_agent's guard)."""
        self.scheduler.register_agent(agent_id, self.clock.tick_index)
        if location_id is not None:
            self.locations.place(agent_id, location_id)

    def mark_agent_decided(self, agent_id: str) -> None:
        """Caller reports an agent's LLM turn is done; reschedules their
        next baseline decision and clears any interrupt flag."""
        self.scheduler.mark_decided(agent_id, self.clock.tick_index)

    async def tick(self) -> TickResult:
        """Advances the world by one tick (10 game-minutes).

        Order matters: the clock advances first so due_agents() and
        perception both see the new tick's time; movement is advanced
        next (still before due_agents()/perception are computed) so an
        agent who steps onto a new tile - or arrives - this very tick is
        already reflected in self.locations (movement's own agent.moved
        events flow straight through the same attach() wiring every
        other world/ mutation's agent.moved event already does) by the
        time perception snapshots who is visible to whom; world.tick.
        advanced publishes before the day-boundary checkpoint so a
        checkpoint subscriber sees the tick that completed the day
        before the checkpoint-written event for it.
        """
        timestamp = self.clock.advance()

        if self.movement is not None:
            for event in self.movement.advance(timestamp.tick_index):
                await self.event_bus.publish(event)

        due = self.scheduler.due_agents(timestamp.tick_index)
        perceptions = {
            agent_id: self.perception.build_perception(agent_id, timestamp.tick_index)
            for agent_id in due
        }

        await self.event_bus.emit(
            "world.tick.advanced",
            tick=timestamp.tick_index,
            payload={
                "game_day": timestamp.game_day,
                "day_tick": timestamp.day_tick,
                "is_daytime": timestamp.is_daytime,
                "due_agent_count": len(due),
            },
        )

        checkpoint: WorldCheckpoint | None = None
        if is_day_boundary(timestamp.tick_index):
            checkpoint = await self.checkpoints.write(timestamp.tick_index, timestamp.game_day)
            await self.event_bus.emit(
                "system.checkpoint.written",
                tick=timestamp.tick_index,
                payload={
                    "game_day": timestamp.game_day,
                    "components": sorted(checkpoint.components),
                },
            )

        return TickResult(
            tick=timestamp, due_agents=due, perceptions=perceptions, checkpoint=checkpoint
        )

    async def restore_from_checkpoint(self, checkpoint: WorldCheckpoint) -> None:
        """Restores clock/scheduler/locations from a prior write(). Event
        bus subscriptions and pacing configuration are process wiring,
        not world state, so they are untouched."""
        await self.checkpoints.restore(checkpoint)

    def stop(self) -> None:
        self.pacing.request_stop()

    async def run_continuous(self) -> None:
        await self.pacing.run_continuous()

    async def run_burst(self, num_ticks: int) -> list[TickResult]:
        return await self.pacing.run_burst(num_ticks)
