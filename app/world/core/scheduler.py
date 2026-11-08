"""Event-driven agent scheduler - DESIGN.md section 9: "Event-driven agent
scheduler: each agent has a next-decision time; only agents due (or
interrupted by perception) get an LLM turn - target ~30% of agents per
tick."

Why this matters: DESIGN.md section 9's throughput math ("100 RPM per
model ... -> event-driven scheduling (~30% of agents need a call per
tick) -> ~40s/tick") is the whole reason 250 agents fit inside a 100 RPM
Bedrock quota at all. If every agent took a decision every tick, one tick
alone would need 250 calls - more than two full minutes of quota. The
scheduler exists to keep that number near 30% on average, tick after
tick, without any agent needing to know how many other agents exist.

How it hits ~30%: SchedulerConfig.target_fraction (default 0.3) derives
a cadence_ticks (round(1 / target_fraction); 0.3 -> 3). register_agent()
assigns each agent a round-robin offset in [1, cadence_ticks] at
registration time (see the comment in register_agent for why it starts
at 1, not 0), so across any cadence_ticks-long window every registered
agent decides exactly once and the agents due on any single tick are,
by construction, close to 1/cadence_ticks of the population, stably,
every tick from the first one - no randomness, no per-agent knowledge of
the total population, and the same schedule replays identically from a
checkpoint. Perception
interrupts (AgentScheduler.interrupt) ride on top of that baseline: an
agent who is not yet due but was just spoken to, robbed, or otherwise
directly involved in something becomes due immediately, exactly as
DESIGN.md's "or interrupted by perception" describes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    """Tunes the baseline (non-interrupt) fraction of agents due per tick."""

    target_fraction: float = 0.3
    min_cadence_ticks: int = 1

    def __post_init__(self) -> None:
        if not (0 < self.target_fraction <= 1):
            raise ValueError(f"target_fraction must be in (0, 1], got {self.target_fraction}")
        if self.min_cadence_ticks < 1:
            raise ValueError(f"min_cadence_ticks must be >= 1, got {self.min_cadence_ticks}")

    @property
    def cadence_ticks(self) -> int:
        """Ticks between one agent's own decisions. round(), not floor/ceil:
        for target_fraction=0.3 that is round(3.33) = 3 (33.3% per tick),
        which is closer to 0.3 than either neighboring integer cadence
        gives (cadence 4 -> 25%, further from 0.3 than 33.3% is)."""
        return max(self.min_cadence_ticks, round(1 / self.target_fraction))


@dataclass
class ScheduleEntry:
    """One agent's schedule state. Plain dataclass (internal to this
    module; AgentScheduler.snapshot() is the pydantic-free boundary
    checkpoint.py's WorldCheckpoint carries as a plain dict)."""

    agent_id: str
    cadence_ticks: int
    next_decision_tick: int
    interrupted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "cadence_ticks": self.cadence_ticks,
            "next_decision_tick": self.next_decision_tick,
            "interrupted": self.interrupted,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleEntry":
        return cls(
            agent_id=data["agent_id"],
            cadence_ticks=data["cadence_ticks"],
            next_decision_tick=data["next_decision_tick"],
            interrupted=data.get("interrupted", False),
        )


class AgentScheduler:
    """Decides, tick by tick, which agents get an LLM turn.

    due_agents(tick) is a pure read (no side effects, safe to call more
    than once for the same tick); the caller is expected to run a turn
    for each due agent and then call mark_decided() exactly once per
    agent that actually got one, which both clears any interrupt flag
    and reschedules that agent's next baseline decision.
    """

    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or SchedulerConfig()
        self.entries: dict[str, ScheduleEntry] = {}
        self.registration_count = 0

    def register_agent(
        self, agent_id: str, current_tick: int, cadence_ticks: int | None = None
    ) -> None:
        """Enrolls a new agent. Raises if agent_id is already registered -
        re-registration would silently reset an agent's schedule bucket,
        which is exactly the kind of scheduling-behavior drift the
        round-robin design exists to avoid; call unregister_agent() first
        if that is genuinely intended (e.g. the agent died and a new one
        is reusing the id)."""
        if agent_id in self.entries:
            raise ValueError(f"agent {agent_id!r} is already registered with the scheduler")
        cadence = cadence_ticks if cadence_ticks is not None else self.config.cadence_ticks
        if cadence < 1:
            raise ValueError(f"cadence_ticks must be >= 1, got {cadence}")

        offset = (self.registration_count % cadence) + 1
        self.registration_count += 1
        self.entries[agent_id] = ScheduleEntry(
            agent_id=agent_id,
            cadence_ticks=cadence,
            next_decision_tick=current_tick + offset,
        )

    def unregister_agent(self, agent_id: str) -> None:
        self.entries.pop(agent_id, None)

    def interrupt(self, agent_id: str) -> None:
        """Marks an agent due regardless of its schedule. Silently ignores
        unknown agent ids (error isolation: this is normally called from
        an EventBus subscriber reacting to arbitrary payload contents, and
        one stray id in a payload must not break event dispatch for every
        other subscriber - see events.py's EventBus.publish)."""
        entry = self.entries.get(agent_id)
        if entry is None:
            logger.debug("interrupt() for unregistered agent %s ignored", agent_id)
            return
        entry.interrupted = True

    def due_agents(self, current_tick: int) -> list[str]:
        """Agent ids due this tick: scheduled (next_decision_tick <= current_tick)
        or interrupted. Sorted for deterministic, reproducible tick output."""
        due = [
            agent_id
            for agent_id, entry in self.entries.items()
            if entry.interrupted or entry.next_decision_tick <= current_tick
        ]
        return sorted(due)

    def mark_decided(self, agent_id: str, current_tick: int) -> None:
        """Records that an agent's decision for this tick happened: clears
        the interrupt flag and reschedules next_decision_tick relative to
        current_tick (not to the old next_decision_tick), so an agent
        interrupted early doesn't get double-scheduled soon after."""
        entry = self.entries.get(agent_id)
        if entry is None:
            raise KeyError(f"agent {agent_id!r} is not registered with the scheduler")
        entry.interrupted = False
        entry.next_decision_tick = current_tick + entry.cadence_ticks

    @property
    def agent_count(self) -> int:
        return len(self.entries)

    def snapshot(self) -> dict[str, Any]:
        """Checkpointable.snapshot - see checkpoint.py."""
        return {
            "registration_count": self.registration_count,
            "config": {
                "target_fraction": self.config.target_fraction,
                "min_cadence_ticks": self.config.min_cadence_ticks,
            },
            "entries": [entry.to_dict() for entry in self.entries.values()],
        }

    def restore(self, data: dict[str, Any]) -> None:
        """Checkpointable.restore - see checkpoint.py."""
        self.config = SchedulerConfig(**data["config"])
        self.registration_count = data["registration_count"]
        self.entries = {
            entry["agent_id"]: ScheduleEntry.from_dict(entry) for entry in data["entries"]
        }
