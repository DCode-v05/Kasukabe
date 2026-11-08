"""A scenario: when the sim starts, and what is already true when it does.

Every run this project has made booted the same way - the default town, at
tick 0, with nothing yet decided - and then waited for the thing being measured
to come round. The first election is counted at tick 432, so "does the town
elect a government, and does that government do anything" has cost three
game-days of thinking before the question is even asked. At the measured
$2.27-2.88 a resident-day that is $129-163 at twenty residents, which is most of
a month's budget for one answer.

A scenario moves the question to the front of the run. It says which cast, which
tick the world starts on, when it stops, when the election opens, how long its
phases are, and who is already sitting in government when the run begins. An
election that opens as the run does, with incumbents already in their seats,
asks a sharper question than a fresh one does - the seats are being defended,
not just filled - and it asks it inside the first game-day.

WHAT THIS MODULE IS. Reading, validating and describing. Nothing else. It boots
no world, writes no charter and imports no runner, so it can be read, tested and
changed on its own, and the wiring that applies it lives with the thing being
wired. The one thing it imports is the length of a game-day.

WHY IT REFUSES SO MUCH. A scenario is read seconds before a paid run and then
not looked at again. Every mistake worth catching is catchable here: a stop tick
before the start, an election whose phases are longer than the gap between
elections, an incumbent who is not in the cast or is too young to hold the seat,
an election that opens before the run does - which is the bug this file was
written for, because candidacy that opens at tick 0 in a run that starts at tick
36 is an election nobody can stand in. All of them are collected and raised
together, so one load reports every problem rather than one per attempt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from world.core.clock import TICKS_PER_DAY

TICKS_PER_GAME_HOUR = TICKS_PER_DAY // 24

SEATS = ("prime_minister", "minister")

# Charter defaults (world/charter/models.py). Repeated here only as the
# numbers a scenario is checked against when it does not set them itself.
DEFAULT_PM_SEATS = 1
DEFAULT_MINISTER_SEATS = 4
DEFAULT_SUFFRAGE_MIN_AGE = 18
DEFAULT_WINDOW_HOURS = 24.0
DEFAULT_INTERVAL_HOURS = 72.0


def _hours_to_days(hours: float) -> float:
    return hours / 24.0


def _hours_to_ticks(hours: float) -> int:
    return round(hours * TICKS_PER_GAME_HOUR)


@dataclass(frozen=True)
class WorldPlan:
    """The run itself: which town, from when to when, and how fast."""

    genesis: str | None = None
    start_tick: int = 0
    stop_tick: int | None = None
    cadence: float | None = None          # scheduler_target_fraction
    budget_cap_usd: float | None = None
    max_game_days: int | None = None
    tick_interval_s: float | None = None


@dataclass(frozen=True)
class CharterPlan:
    """The rules the genesis charter is seeded with, in game-hours.

    Hours, not days, because a scenario's whole point is an election that fits
    inside one game-day: "4" reads better than "0.1666666".
    """

    candidacy_window_hours: float = DEFAULT_WINDOW_HOURS
    campaign_period_hours: float = DEFAULT_WINDOW_HOURS
    voting_window_hours: float = DEFAULT_WINDOW_HOURS
    election_interval_hours: float = DEFAULT_INTERVAL_HOURS
    pm_seats: int = DEFAULT_PM_SEATS
    minister_seats: int = DEFAULT_MINISTER_SEATS
    suffrage_min_age: int = DEFAULT_SUFFRAGE_MIN_AGE
    compulsory_voting: bool = False

    def as_charter_fields(self) -> dict[str, Any]:
        """The same rules in the units `world/charter/models.py` stores."""
        return {
            "candidacy_window_days": _hours_to_days(self.candidacy_window_hours),
            "campaign_period_days": _hours_to_days(self.campaign_period_hours),
            "voting_window_days": _hours_to_days(self.voting_window_hours),
            "election_interval_days": _hours_to_days(self.election_interval_hours),
            "pm_seats": self.pm_seats,
            "minister_seats": self.minister_seats,
            "suffrage_min_age": self.suffrage_min_age,
            "compulsory_voting": self.compulsory_voting,
        }


@dataclass(frozen=True)
class Seat:
    agent_id: str
    seat: str


@dataclass(frozen=True)
class Scenario:
    """One run's situation, loaded from a JSON file."""

    name: str
    world: WorldPlan
    description: str = ""
    charter: CharterPlan | None = None
    election_opens_at: int | str = "start"
    incumbents: tuple[Seat, ...] = ()
    recording: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""

    # ---------------------------------------------------------------- reading

    @classmethod
    def load(cls, path: str | Path) -> "Scenario":
        text = Path(path).read_text()
        try:
            body = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: not valid JSON ({exc})") from exc
        return cls.from_dict(body, source_path=str(path))

    @classmethod
    def from_dict(cls, body: dict[str, Any], source_path: str = "") -> "Scenario":
        errors: list[str] = []
        _refuse_unknown(body, {"name", "description", "world", "charter", "election",
                               "government", "recording", "report"}, "top level", errors)

        world_block = dict(body.get("world") or {})
        _refuse_unknown(world_block, set(WorldPlan.__dataclass_fields__), "world", errors)
        world = WorldPlan(**{k: v for k, v in world_block.items()
                             if k in WorldPlan.__dataclass_fields__})

        charter: CharterPlan | None = None
        if "charter" in body:
            charter_block = dict(body.get("charter") or {})
            _refuse_unknown(charter_block, set(CharterPlan.__dataclass_fields__), "charter", errors)
            charter = CharterPlan(**{k: v for k, v in charter_block.items()
                                     if k in CharterPlan.__dataclass_fields__})

        election_block = dict(body.get("election") or {})
        _refuse_unknown(election_block, {"opens_at"}, "election", errors)
        opens_at = election_block.get("opens_at", "start")

        government_block = dict(body.get("government") or {})
        _refuse_unknown(government_block, {"incumbents"}, "government", errors)
        incumbents: list[Seat] = []
        for row in government_block.get("incumbents") or []:
            if not isinstance(row, dict) or "agent_id" not in row:
                errors.append(f"government.incumbents: {row!r} needs an agent_id and a seat")
                continue
            _refuse_unknown(row, {"agent_id", "seat"}, "government.incumbents", errors)
            incumbents.append(Seat(str(row["agent_id"]), str(row.get("seat", "minister"))))

        scenario = cls(
            name=str(body.get("name") or Path(source_path).stem or "scenario"),
            description=str(body.get("description") or ""),
            world=world,
            charter=charter,
            election_opens_at=opens_at,
            incumbents=tuple(incumbents),
            recording=dict(body.get("recording") or {}),
            report=dict(body.get("report") or {}),
            source_path=source_path,
        )
        errors.extend(scenario.problems())
        if errors:
            raise ValueError("; ".join(errors))
        return scenario

    # ------------------------------------------------------------- validating

    def problems(self) -> list[str]:
        """Every reason this scenario could not be run, in one list."""
        out: list[str] = []
        w = self.world
        if w.start_tick < 0:
            out.append(f"world.start_tick={w.start_tick} must be >= 0")
        if w.stop_tick is not None and w.stop_tick <= w.start_tick:
            out.append(f"world.stop_tick={w.stop_tick} must be after world.start_tick={w.start_tick}")
        if w.cadence is not None and not 0 < w.cadence <= 1:
            out.append(f"world.cadence={w.cadence} must be in (0, 1]")
        if w.budget_cap_usd is not None and w.budget_cap_usd <= 0:
            out.append(f"world.budget_cap_usd={w.budget_cap_usd} must be > 0")
        if w.max_game_days is not None and w.max_game_days < 1:
            out.append(f"world.max_game_days={w.max_game_days} must be >= 1")
        if w.tick_interval_s is not None and w.tick_interval_s < 0:
            out.append(f"world.tick_interval_s={w.tick_interval_s} must be >= 0")

        opens = self.election_opens_at
        if isinstance(opens, bool) or not isinstance(opens, (int, str)):
            out.append(f"election.opens_at={opens!r} must be 'start', 'genesis' or a tick")
        elif isinstance(opens, str) and opens not in ("start", "genesis"):
            out.append(f"election.opens_at={opens!r} must be 'start', 'genesis' or a tick")
        elif isinstance(opens, int) and opens < w.start_tick:
            out.append(
                f"election.opens_at={opens} is before world.start_tick={w.start_tick}: "
                "candidacy would open in a world nobody is living in yet. Use 'genesis' to ask "
                "for that on purpose."
            )

        c = self.charter
        if c is not None:
            for name, value in (("candidacy_window_hours", c.candidacy_window_hours),
                                ("campaign_period_hours", c.campaign_period_hours),
                                ("voting_window_hours", c.voting_window_hours),
                                ("election_interval_hours", c.election_interval_hours)):
                if value <= 0:
                    out.append(f"charter.{name}={value} must be > 0")
            windows = c.candidacy_window_hours + c.campaign_period_hours + c.voting_window_hours
            if windows > c.election_interval_hours:
                out.append(
                    f"charter: the three windows total {windows} hours, more than "
                    f"election_interval_hours={c.election_interval_hours}; an election could not "
                    "finish before the next one is due"
                )
            if c.pm_seats < 1:
                out.append(f"charter.pm_seats={c.pm_seats} must be >= 1")
            if c.minister_seats < 0:
                out.append(f"charter.minister_seats={c.minister_seats} must be >= 0")
            if c.suffrage_min_age < 0:
                out.append(f"charter.suffrage_min_age={c.suffrage_min_age} must be >= 0")

        out.extend(self._seat_problems())
        return out

    def _seat_problems(self) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        counts = {seat: 0 for seat in SEATS}
        for row in self.incumbents:
            if row.seat not in SEATS:
                out.append(f"government.incumbents: seat {row.seat!r} must be one of {SEATS}")
                continue
            if row.agent_id in seen:
                out.append(f"government.incumbents: {row.agent_id} is named twice")
            seen.add(row.agent_id)
            counts[row.seat] += 1

        pm_seats = self.charter.pm_seats if self.charter else DEFAULT_PM_SEATS
        minister_seats = self.charter.minister_seats if self.charter else DEFAULT_MINISTER_SEATS
        if counts["prime_minister"] > pm_seats:
            out.append(f"government.incumbents: {counts['prime_minister']} prime ministers for "
                       f"{pm_seats} seat(s)")
        if counts["minister"] > minister_seats:
            out.append(f"government.incumbents: {counts['minister']} ministers for "
                       f"{minister_seats} minister seat(s)")

        cast = self.cast()
        if cast is None or not self.incumbents:
            return out
        min_age = self.charter.suffrage_min_age if self.charter else DEFAULT_SUFFRAGE_MIN_AGE
        for row in self.incumbents:
            if row.agent_id not in cast:
                out.append(f"government.incumbents: {row.agent_id} is not in {self.world.genesis}")
                continue
            age = cast[row.agent_id]
            if age is not None and age < min_age:
                out.append(f"government.incumbents: {row.agent_id} is {age:g}, under the charter's "
                           f"suffrage_min_age of {min_age}, and could not hold a seat")
        return out

    # ----------------------------------------------------------------- asking

    def genesis_path(self) -> Path | None:
        """The cast file, found from the working directory or the app root."""
        if not self.world.genesis:
            return None
        given = Path(self.world.genesis)
        here = Path(__file__).resolve().parents[1]      # app/
        for candidate in (given, Path.cwd() / given, here / given):
            if candidate.is_file():
                return candidate
        return None

    def cast(self) -> dict[str, float | None] | None:
        """`{agent_id: age}` from the genesis file, or None if it cannot be read."""
        path = self.genesis_path()
        if path is None:
            return None
        try:
            body = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        agents = body.get("agents")
        if not isinstance(agents, list):
            return None
        out: dict[str, float | None] = {}
        for agent in agents:
            if isinstance(agent, dict) and agent.get("agent_id"):
                age = agent.get("age_years", agent.get("age"))
                out[str(agent["agent_id"])] = float(age) if isinstance(age, (int, float)) else None
        return out

    def election_opening_tick(self) -> int:
        if self.election_opens_at == "genesis":
            return 0
        if self.election_opens_at == "start":
            return self.world.start_tick
        return int(self.election_opens_at)

    def election_phase_ticks(self) -> tuple[int, int, int, int]:
        """(opens, candidacy closes, voting opens, voting closes = the count)."""
        c = self.charter or CharterPlan()
        opens = self.election_opening_tick()
        candidacy_closes = opens + _hours_to_ticks(c.candidacy_window_hours)
        voting_opens = candidacy_closes + _hours_to_ticks(c.campaign_period_hours)
        voting_closes = voting_opens + _hours_to_ticks(c.voting_window_hours)
        return opens, candidacy_closes, voting_opens, voting_closes

    def charter_overrides(self) -> dict[str, Any]:
        """What to seed the genesis charter with. Empty means: leave it alone."""
        return self.charter.as_charter_fields() if self.charter else {}

    def prime_minister(self) -> str | None:
        for row in self.incumbents:
            if row.seat == "prime_minister":
                return row.agent_id
        return None

    def ministers(self) -> tuple[str, ...]:
        return tuple(row.agent_id for row in self.incumbents if row.seat == "minister")

    def describe(self) -> list[str]:
        """Plain lines for the manifest and the run report."""
        w = self.world
        lines = [f"Scenario **{self.name}**" + (f" - {self.description}" if self.description else "")]
        lines.append(f"Cast: `{w.genesis or 'the default genesis'}`.")
        stop = f"stops at tick {w.stop_tick}" if w.stop_tick is not None else "runs until told to stop"
        lines.append(f"The world starts at tick {w.start_tick} and {stop}.")
        opens, candidacy_closes, voting_opens, count = self.election_phase_ticks()
        lines.append(
            f"The election opens at tick {opens}: candidacy closes {candidacy_closes}, "
            f"voting opens {voting_opens}, and the vote is counted at tick {count}."
        )
        if self.incumbents:
            pm = self.prime_minister()
            held = f"prime minister {pm}" if pm else "no prime minister"
            ministers = ", ".join(self.ministers())
            lines.append(f"Already in office when it starts: {held}"
                         + (f"; ministers {ministers}" if ministers else "")
                         + " - they may stand again, against anyone who stands for the first time.")
        if w.budget_cap_usd is not None:
            lines.append(f"Spend cap ${w.budget_cap_usd:.2f}.")
        return lines


def _refuse_unknown(block: dict[str, Any], allowed: set[str], where: str, errors: list[str]) -> None:
    for key in block:
        if key not in allowed:
            errors.append(f"{where}: unknown key {key!r} (allowed: {', '.join(sorted(allowed))})")
