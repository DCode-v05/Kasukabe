"""Game clock and calendar - DESIGN.md section 1: "tick = 10 game-minutes;
144 ticks/game-day; day/night cycle."

Everything about game time is a pure function of one integer: the count
of ticks elapsed since the simulation started. That is deliberate -
GameClock holds that single number, GameTimestamp is derived from it by
timestamp_for(), and nothing else in world-core keeps its own copy of
"what time is it." A checkpoint only needs to persist tick_index; restore
only needs to set it back. Two GameClocks with the same tick_index are,
by construction, in exactly the same moment of game time.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

TICK_MINUTES = 10
TICKS_PER_DAY = 144
MINUTES_PER_DAY = 24 * 60

DAY_START_HOUR = 6
NIGHT_START_HOUR = 22


class GameTimestamp(BaseModel):
    """One instant of game time. Immutable and fully derived - see timestamp_for().

    day_tick is the 0-based position within the current day (0..143);
    it is 0 exactly once per day, at the moment DESIGN.md's "checkpoint
    full world state every game-day" fires (see is_day_boundary()).
    """

    model_config = ConfigDict(frozen=True)

    tick_index: int
    game_day: int
    day_tick: int
    hour: int
    minute: int
    is_daytime: bool


def timestamp_for(tick_index: int) -> GameTimestamp:
    """Derives the full GameTimestamp for a tick_index. Pure function, no state.

    tick_index counts ticks elapsed since simulation start (0 = the
    initial moment, before any tick has run). Because TICK_MINUTES
    divides MINUTES_PER_DAY evenly, every tick lands on an exact minute
    boundary with no rounding.
    """
    if tick_index < 0:
        raise ValueError(f"tick_index must be >= 0, got {tick_index}")

    total_minutes = tick_index * TICK_MINUTES
    game_day, minute_of_day = divmod(total_minutes, MINUTES_PER_DAY)
    day_tick = minute_of_day // TICK_MINUTES
    hour, minute = divmod(minute_of_day, 60)
    is_daytime = DAY_START_HOUR <= hour < NIGHT_START_HOUR

    return GameTimestamp(
        tick_index=tick_index,
        game_day=game_day,
        day_tick=day_tick,
        hour=hour,
        minute=minute,
        is_daytime=is_daytime,
    )


def game_time_label(tick_index: int) -> str:
    """A tick as a resident reads the clock: "game-day 1, 00:00".

    The same words the perception opens with ("It is game-day 1, 06:40"),
    so a deadline and the present can be compared without arithmetic.
    """
    ts = timestamp_for(max(0, int(tick_index)))
    return f"game-day {ts.game_day}, {ts.hour:02d}:{ts.minute:02d}"


def is_day_boundary(tick_index: int) -> bool:
    """True the instant tick_index completes the 144th tick of a game-day.

    tick_index=0 is the sim's initial moment, not a completed day, so it
    is deliberately excluded (a fresh, un-ticked world has nothing to
    checkpoint yet).
    """
    return tick_index > 0 and tick_index % TICKS_PER_DAY == 0


class GameClock:
    """The one piece of mutable time state in world-core.

    now() and advance() are the only ways to read or move time; every
    other calendar fact (day/night, hour, day_tick) is recomputed from
    tick_index on demand rather than cached, so there is never a stale
    derived value to worry about.
    """

    def __init__(self, tick_index: int = 0) -> None:
        if tick_index < 0:
            raise ValueError(f"tick_index must be >= 0, got {tick_index}")
        self.tick_index = tick_index

    def now(self) -> GameTimestamp:
        return timestamp_for(self.tick_index)

    def advance(self) -> GameTimestamp:
        """Moves time forward by exactly one tick (10 game-minutes) and returns it."""
        self.tick_index += 1
        return self.now()

    def snapshot(self) -> dict[str, Any]:
        """Checkpointable.snapshot - see checkpoint.py."""
        return {"tick_index": self.tick_index}

    def restore(self, data: dict[str, Any]) -> None:
        """Checkpointable.restore - see checkpoint.py."""
        tick_index = data["tick_index"]
        if not isinstance(tick_index, int) or tick_index < 0:
            raise ValueError(
                f"checkpoint tick_index must be a non-negative int, got {tick_index!r}"
            )
        self.tick_index = tick_index
