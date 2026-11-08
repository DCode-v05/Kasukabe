"""Game time as a single scalar, so health (day-granularity), aging and
dormancy (hour-granularity) all agree on what "now" means.

DESIGN.md section 1: "tick = 10 game-minutes; 144 ticks/game-day" ->
144 * 10 / 60 = 24 game-hours per game-day, so day and hour-of-day are
just floor division and modulo of one running hour count. Representing
time as a single ``total_hours`` float (rather than a separate day int
and hour int) means aging's "1 year per game-hour" and dormancy's
"24 game-hours" both add directly to it with no unit conversion at the
call site.
"""

from __future__ import annotations

from pydantic import BaseModel

from world.life.constants import GAME_HOURS_PER_DAY


class GameClock(BaseModel):
    total_hours: float = 0.0

    @property
    def day(self) -> int:
        return int(self.total_hours // GAME_HOURS_PER_DAY)

    @property
    def hour_of_day(self) -> float:
        return self.total_hours % GAME_HOURS_PER_DAY

    def advanced_by(self, hours: float) -> GameClock:
        if hours < 0:
            raise ValueError(f"cannot advance a game clock by a negative amount ({hours})")
        return GameClock(total_hours=self.total_hours + hours)

    @classmethod
    def at_day(cls, day: int) -> GameClock:
        return cls(total_hours=day * GAME_HOURS_PER_DAY)
