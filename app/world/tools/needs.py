"""What a resident is under pressure from, computed - never declared.

ENG-24. The review's charge against the seam was that "agents that
develop their own goals" had no substrate to develop them in. Half of
that substrate is this file, and it is the half a resident must *not*
control: a need is the world's reading of a resident's situation, taken
from facts the world already holds, and the resident's only say in it is
what it does about it.

The split matters. If a resident could set its own needs, "is hungry"
would mean "says it is hungry", and every downstream measurement - does
a need produce a goal, does a goal produce an act, does the act relieve
the need - would be measuring the model's willingness to role-play
rather than the town. So nothing here reads a stated preference. It
reads the hunger ladder, the wallets, the arrears, the ailment, the
utterance log, the household roll: things that got there by something
happening.

Every level is a float in [0, 1] and every one of them is derived, so
the same world at the same tick computes the same needs twice. That is
deliberate too - a need that wandered would make the goal it spawned
unattributable.

The seven kinds are fixed (`NeedKind`), because the axes of pressure in
this town are fixed by its own mechanics: you starve, you run out of
inference money, you run out of sim money, you tire, you have nowhere to
sleep, nobody answers you, something is coming for you. Goals are open
text; needs are a closed list. A resident invents what to want, not what
hurts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from world.life.upkeep import (
    HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD,
    UPKEEP_TOTAL_SIM_PER_DAY,
)
from world.tools.models import Need, NeedKind

if TYPE_CHECKING:  # pragma: no cover - typing only
    from world.tools.state import WorldState

# A need at or above this is worth acting on, and is what the goal
# trigger and the perception block both key off. Below it the resident
# is not told, because a perception that listed seven mild pressures
# every tick would be noise that crowds out what changed.
NEED_PRESSING = 0.7
NEED_WORTH_SHOWING = 0.5

# How many days of upkeep in the wallet counts as "not worried about
# money". Two days is the ladder's own horizon - malnutrition at 2
# unpaid days, dormancy at 3 - so a resident with less than that is
# genuinely close to the edge, not merely poorer than its neighbour.
MONEY_COMFORTABLE_DAYS = 2.0

# The USD wallet is the cognitive budget: at zero the resident goes
# dormant and stops thinking at all. Half a dollar is roughly a day of
# turns at run-24 prices, so that is where the pressure starts.
ENERGY_COMFORTABLE_CENTS = 50

# A day is 144 ticks. A resident that has taken a turn every tick since
# the last night is tired in the only sense this town models - it has
# been awake and acting - and the number is the fraction of a day.
REST_FULL_DAY_TICKS = 144

# Nobody has spoken to you in this many ticks and the social need is at
# its ceiling. Half a game-day: long enough that a quiet morning does
# not register, short enough that a resident nobody talks to notices
# within the run.
SOCIAL_SILENCE_TICKS = 72


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _food_pressure(world: "WorldState", agent_id: str) -> float:
    """Days since a real meal, against the day the ladder kills you."""
    states = world.hunger_by_agent or {}
    state = states.get(agent_id)
    if state is None:
        return 0.0
    # Read straight off the model. `HungerState` declares both counters
    # with defaults, so a three-argument getattr here would only ever
    # hide a rename - which is exactly what test_no_silent_defaults is
    # for, and it caught this.
    days = float(state.days_since_meal)
    return _clamp(days / float(HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD))


def _money_pressure(world: "WorldState", agent_id: str) -> float:
    """Runway in days of upkeep, plus what is already owed."""
    try:
        sim = float(world.ledger.sim_balance(agent_id))
    except Exception:
        # A resident with no account is not rich; it is un-banked, and
        # the honest reading of that is maximum money pressure.
        return 1.0
    runway_days = sim / float(UPKEEP_TOTAL_SIM_PER_DAY or 1)
    shortfall = _clamp(1.0 - (runway_days / MONEY_COMFORTABLE_DAYS))

    states = world.hunger_by_agent or {}
    state = states.get(agent_id)
    unpaid = float(state.consecutive_unpaid_days) if state is not None else 0.0
    arrears = _clamp(unpaid / float(HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD))

    # Debt is real pressure but not the same kind - it has no deadline
    # in this town - so it lifts the floor rather than setting it.
    # `debts_of` returns owed_by_you / owed_to_you / due_today_sim - there
    # is no "total_owed" key, and reading for one would have made every
    # debt weigh nothing.
    owed = 0.0
    try:
        debts = world.debts_of(agent_id) or {}
        owed = float(sum(row.get("sim") or 0 for row in debts.get("owed_by_you") or []))
    except Exception:
        owed = 0.0
    debt_weight = _clamp(owed / (UPKEEP_TOTAL_SIM_PER_DAY * 10.0)) * 0.3

    return _clamp(max(shortfall, arrears) + debt_weight)


def _energy_pressure(world: "WorldState", agent_id: str) -> float:
    """The inference wallet. At zero the resident stops existing."""
    agent = world.agents.get(agent_id)
    if agent is not None and agent.dormancy.is_dormant:
        return 1.0
    try:
        payer = world.ledger.resolve_usd_payer(agent_id)
        cents = float(world.ledger.usd_balance(payer))
    except Exception:
        return 1.0
    return _clamp(1.0 - (cents / float(ENERGY_COMFORTABLE_CENTS)))


def _rest_pressure(world: "WorldState", agent_id: str) -> float:
    """Ticks acted since the last night, as a fraction of a day."""
    turns = (world.turn_ticks or {}).get(agent_id) or []
    if not turns:
        return 0.0
    now = world.tick()
    day_start = now - (now % REST_FULL_DAY_TICKS)
    acted_today = sum(1 for t in turns if t >= day_start)
    return _clamp(acted_today / float(REST_FULL_DAY_TICKS))


def _shelter_pressure(world: "WorldState", agent_id: str) -> float:
    """No household is no roof; a household with no home is half a roof."""
    agent = world.agents.get(agent_id)
    if agent is None:
        return 1.0
    household_id = agent.household_id
    if not household_id:
        return 1.0
    if household_id not in (world.households or {}):
        return 0.6
    return 0.0


def _social_pressure(world: "WorldState", agent_id: str) -> float:
    """Silence toward you, and questions you have not answered."""
    now = world.tick()
    last_heard: int | None = None
    # Keys per bootstrap.on_utterance, which is what actually writes
    # these rows: speaker_agent_id / listener_agent_id /
    # addressed_agent_ids. Guessing at "heard_by" or "to_agent_id" would
    # have made this need silently zero for everyone forever - the
    # EVAL-01 failure mode, a reader keyed to names nothing writes.
    for record in reversed(world.utterances or []):
        if record.get("speaker_agent_id") == agent_id:
            continue
        spoken_to = record.get("listener_agent_id")
        addressed = record.get("addressed_agent_ids") or []
        if spoken_to == agent_id or agent_id in addressed:
            when = record.get("tick")
            if isinstance(when, int):
                last_heard = when
                break
    silence = now if last_heard is None else now - last_heard
    pressure = _clamp(silence / float(SOCIAL_SILENCE_TICKS))

    # An unanswered question is a live thread, not silence: it lowers
    # the pressure toward zero, because somebody is in fact talking to
    # you and you owe them a turn.
    try:
        if world.awaiting_reply(agent_id, now):
            pressure = min(pressure, 0.3)
    except Exception:
        pass
    return pressure


def _safety_pressure(world: "WorldState", agent_id: str) -> float:
    """Sickness, confinement, a subpoena, an open case with your name on it."""
    agent = world.agents.get(agent_id)
    pressure = 0.0
    if agent is not None and agent.health.is_sick:
        pressure = max(pressure, 0.8)
    if agent_id in (world.confined or {}):
        return 1.0
    # Field names per world.tools.models: a Subpoena names its
    # target_agent_id, a CrimeCase its against_agent_id and
    # victim_agent_id, and a case is open until it has a verdict.
    for subpoena in (world.subpoenas or {}).values():
        if subpoena.target_agent_id == agent_id and subpoena.status in {
            "requested",
            "granted",
        }:
            pressure = max(pressure, 0.6)
    for case in world.crime_cases or []:
        if case.verdict is not None:
            continue
        if case.against_agent_id == agent_id:
            # Accused of something with no verdict yet: the sharpest
            # form this town has of "something is coming for you".
            pressure = max(pressure, 0.7)
        elif case.victim_agent_id == agent_id:
            pressure = max(pressure, 0.5)
    return _clamp(pressure)


_READINGS = {
    NeedKind.FOOD: _food_pressure,
    NeedKind.MONEY: _money_pressure,
    NeedKind.ENERGY: _energy_pressure,
    NeedKind.REST: _rest_pressure,
    NeedKind.SHELTER: _shelter_pressure,
    NeedKind.SOCIAL: _social_pressure,
    NeedKind.SAFETY: _safety_pressure,
}


def compute_needs(world: "WorldState", agent_id: str) -> dict[str, Need]:
    """Read this resident's seven pressures off the world, now.

    Pure with respect to the world: it writes nothing. `refresh_needs`
    is the one that stores, and it keeps `since_tick` from the previous
    reading so "hungry since tick 40" survives being recomputed at 41.
    """
    now = world.tick()
    previous = (world.needs or {}).get(agent_id) or {}
    out: dict[str, Need] = {}
    for kind, reading in _READINGS.items():
        try:
            level = float(reading(world, agent_id))
        except Exception:
            # A reading that cannot be taken is not a pressure of zero -
            # but it is also not a reason to fail a resident's turn, so
            # it carries forward whatever was last known.
            was = previous.get(kind.value)
            level = float(was.level) if was is not None else 0.0
        level = _clamp(level)
        was = previous.get(kind.value)
        # since_tick answers "how long has this been pressing", so it
        # resets only when the need crosses up into pressing, not on
        # every wobble underneath it.
        if was is not None and was.level >= NEED_WORTH_SHOWING and level >= NEED_WORTH_SHOWING:
            since = was.since_tick
        else:
            since = now
        out[kind.value] = Need(kind=kind, level=level, since_tick=since)
    return out


def refresh_needs(world: "WorldState", agent_id: str) -> dict[str, Need]:
    """Compute and store. Called once per resident per turn."""
    fresh = compute_needs(world, agent_id)
    if world.needs is None:  # pragma: no cover - dataclass default
        world.needs = {}
    world.needs[agent_id] = fresh
    return fresh


def pressing_needs(world: "WorldState", agent_id: str) -> list[Need]:
    """Needs at or above NEED_PRESSING, worst first."""
    held = (world.needs or {}).get(agent_id) or {}
    return sorted(
        (n for n in held.values() if n.level >= NEED_PRESSING),
        key=lambda n: (-n.level, n.kind.value),
    )


def shown_needs(world: "WorldState", agent_id: str, limit: int = 2) -> list[Need]:
    """The worst few worth putting in front of the resident."""
    held = (world.needs or {}).get(agent_id) or {}
    ranked = sorted(
        (n for n in held.values() if n.level >= NEED_WORTH_SHOWING),
        key=lambda n: (-n.level, n.kind.value),
    )
    return ranked[:limit]
