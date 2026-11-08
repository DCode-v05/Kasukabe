// Client-side mirror of game/position.py's day_night_tint()/
// game_day_and_time(). Duplicated deliberately, same reasoning
// position.py's own module docstring gives for copying (not importing)
// world/core/clock.py's constants: DESIGN.md section 1 fixes these
// numbers ("tick = 10 game-minutes; 144 ticks/game-day"), so both the
// Python and the JS side of game/ read the same law directly rather than
// one importing the other across a browser/server boundary that can't
// share Python modules anyway.

const TICK_MINUTES = 10;
const MINUTES_PER_DAY = 24 * 60;
const DAY_START_HOUR = 6;
const NIGHT_START_HOUR = 22;

export function dayNightTint(tick) {
  const minuteOfDay = (tick * TICK_MINUTES) % MINUTES_PER_DAY;
  const hour = minuteOfDay / 60;
  const rampHours = 2;
  let alpha;

  if (hour >= DAY_START_HOUR && hour < DAY_START_HOUR + rampHours) {
    alpha = 1 - (hour - DAY_START_HOUR) / rampHours;
  } else if (hour >= DAY_START_HOUR + rampHours && hour < NIGHT_START_HOUR - rampHours) {
    alpha = 0;
  } else if (hour >= NIGHT_START_HOUR - rampHours && hour < NIGHT_START_HOUR) {
    alpha = (hour - (NIGHT_START_HOUR - rampHours)) / rampHours;
  } else {
    alpha = 1;
  }
  alpha = Math.max(0, Math.min(1, alpha));
  // 0.38 ceiling, not 0.72: the overlay now covers the whole map in world
  // space (see town.js), and at 0.72 midnight rendered the town as a solid
  // black rectangle with nothing visible. Night must READ as night while
  // leaving the streets, buildings and sprites legible - an operator has to
  // be able to watch the town at 03:00, not stare at a black screen.
  return { color: 0x0b1230, alpha: Math.round(alpha * 0.38 * 1000) / 1000 };
}

export function gameDayAndTime(tick) {
  const totalMinutes = tick * TICK_MINUTES;
  const gameDay = Math.floor(totalMinutes / MINUTES_PER_DAY);
  const minuteOfDay = totalMinutes % MINUTES_PER_DAY;
  const hour = Math.floor(minuteOfDay / 60);
  const minute = minuteOfDay % 60;
  return { gameDay, hour, minute };
}

export function formatGameClock(tick) {
  const { gameDay, hour, minute } = gameDayAndTime(tick);
  const hh = String(hour).padStart(2, "0");
  const mm = String(minute).padStart(2, "0");
  return `Day ${gameDay + 1} · ${hh}:${mm}`;
}
