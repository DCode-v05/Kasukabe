"""The rules that move goals without a resident deciding anything.

ENG-24, the other half of the substrate. `needs.py` reads pressure;
`primitives/goals.py` lets a resident say what it wants; this file is
what happens in between, and it is deliberately mechanical.

Four rules, all deterministic:

1. **A pressing need with nothing being done about it produces a goal.**
   Not the resident's wording - the world's, marked `origin=need`, so a
   report can tell a goal a resident chose from a goal the world put in
   front of it. That distinction is the measurement: a town where every
   goal is need-spawned is a town of reflexes, and one where residents
   set their own is the thing the project claims to be building.

2. **A tool that succeeds credits the goals whose `done_when` names
   it.** Progress arrives from acts, never from a resident asserting it.
   `update_goal` exists for the part a resident genuinely knows better -
   how far along it feels - but a goal can complete without ever being
   touched by hand.

3. **A refusal becomes a belief.** "You cannot craft at the farmland" is
   a fact learned by being stopped, and the reason the same wall gets
   walked into every tick is that there was nowhere to put it.

   This is also ENG-05's answer. That finding traced
   `step11_skill_evolution` and found it can never fire in the sim - it
   reads `source='user'` and the seam writes `source='engine'` - and its
   conclusion was "do not chase step 11; put the world-as-corrector loop
   in the seam". The loop is here instead of in the seam, for a reason
   the finding itself allows: the refusal happens in the world, the
   world already knows why, and routing it through the learning pipeline
   to come back as a consolidated belief would put a model call between
   a resident and a fact it was just told to its face.

4. **Goals expire with their horizon.** `now` and `today` goals do not
   survive the night; `week` and `life` goals do. Without this the goal
   list is an append-only pile and the perception block silts up.

None of this asks a model anything. That is the point - every one of
these transitions is replayable from the trace.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from world.core.events import WorldEvent
from world.tools.models import (
    Belief,
    Goal,
    GoalHorizon,
    GoalOrigin,
    GoalStatus,
    Need,
    NeedKind,
)
from world.tools.needs import NEED_PRESSING, pressing_needs, refresh_needs

if TYPE_CHECKING:  # pragma: no cover - typing only
    from world.tools.state import WorldState

logger = logging.getLogger(__name__)

MAX_ACTIVE_GOALS = 5
MAX_BELIEFS_PER_AGENT = 40

NEED_GOAL_TEXT: dict[NeedKind, str] = {
    NeedKind.FOOD: "Get something to eat.",
    NeedKind.MONEY: "Earn enough to cover what I owe.",
    NeedKind.ENERGY: "Earn back the money that buys my thinking.",
    NeedKind.REST: "Rest before the day turns.",
    NeedKind.SHELTER: "Find somewhere to live.",
    NeedKind.SOCIAL: "Speak to someone.",
    NeedKind.SAFETY: "Deal with what is hanging over me.",
}

NEED_GOAL_PRIORITY: dict[NeedKind, int] = {
    NeedKind.FOOD: 5,
    NeedKind.ENERGY: 5,
    NeedKind.SAFETY: 4,
    NeedKind.MONEY: 4,
    NeedKind.SHELTER: 3,
    NeedKind.REST: 2,
    NeedKind.SOCIAL: 2,
}


def _note_outcome(world: "WorldState", goal: Goal, reason: str) -> None:
    """Queue one line for this resident's next turn, then forget it.

    Only for transitions the WORLD made. A resident that called
    `retire_goal` does not need to be told what it just did - and being
    told would spend a line of every prompt restating the last one.
    """
    world.last_goal_outcome.setdefault(goal.agent_id, []).append((goal, reason))


def active_goals(world: "WorldState", agent_id: str) -> list[Goal]:
    """This resident's live goals, most important first."""
    live = [
        g
        for g in (world.goals or {}).values()
        if g.agent_id == agent_id and g.status == GoalStatus.ACTIVE
    ]
    return sorted(live, key=lambda g: (-g.priority, g.created_tick))


def _stage_now(world: "WorldState", agent_id: str) -> str | None:
    """The life stage a goal is being formed in (LIFE-01)."""
    from world.life.stages import life_stage

    stage = life_stage(world, agent_id)
    return stage.value if stage is not None else None


def supersede_goals_of_a_life_left_behind(
    world: "WorldState", agent_id: str
) -> list[WorldEvent]:
    """LIFE-01. When a life moves on, the goals of the old one close.

    Called once a turn, beside the need refresh. A resident that came of
    age, took work, opened a shop or reached retirement is a different
    person to the town in a way the town can check - and the goals it
    formed as the person it was are marked SUPERSEDED rather than
    abandoned, because it did not give them up: they stopped being its.

    NOT retired silently. The resident is told once, in the same place
    it is told a goal lapsed, because a goal disappearing without a word
    is how a town starts feeling arbitrary.

    `life` and `week` goals go too. A horizon is about how long a thing
    takes, not about whether it still belongs to you - somebody who
    wanted to finish school for life does not still want it as a
    retiree.
    """
    from world.life.stages import note_stage_change

    moved = note_stage_change(world, agent_id)
    if moved is None:
        return []
    was, now = moved

    events: list[WorldEvent] = []
    for goal in active_goals(world, agent_id):
        if goal.set_in_stage is None or goal.set_in_stage != was.value:
            continue
        goal.status = GoalStatus.SUPERSEDED
        goal.evidence.append(f"set as a {was.value}; now {now.value}")
        _note_outcome(world, goal, f"you are no longer a {was.value}")
        events.append(
            WorldEvent(
                type="agent.goal.superseded",
                tick=world.tick(),
                payload={
                    "agent_id": agent_id,
                    "goal_id": goal.goal_id,
                    "text": goal.text,
                    "was_stage": was.value,
                    "now_stage": now.value,
                    "progress": round(goal.progress, 3),
                    "ticks_held": world.tick() - goal.created_tick,
                },
            )
        )
    events.append(
        WorldEvent(
            type="agent.life.stage_changed",
            tick=world.tick(),
            payload={
                "agent_id": agent_id,
                "was": was.value,
                "now": now.value,
                "goals_superseded": len(events),
            },
        )
    )
    return events


def _goal_id(world: "WorldState", agent_id: str) -> str:
    return f"goal_{agent_id}_{world.tick()}_{len(world.goals or {})}"


def _addresses_need(goal: Goal, kind: NeedKind) -> bool:
    return goal.done_when.get("need") == kind.value


def spawn_goals_from_needs(world: "WorldState", agent_id: str) -> list[WorldEvent]:
    """Rule 1. A need at or above NEED_PRESSING with nothing on it.

    One goal per need, and never a second while the first is active -
    otherwise a resident that stays hungry for ten ticks acquires ten
    identical goals and the list stops meaning anything.
    """
    events: list[WorldEvent] = []
    live = active_goals(world, agent_id)
    if len(live) >= MAX_ACTIVE_GOALS:
        return events
    for need in pressing_needs(world, agent_id):
        if any(_addresses_need(g, need.kind) for g in live):
            continue
        goal = Goal(
            goal_id=_goal_id(world, agent_id),
            agent_id=agent_id,
            text=NEED_GOAL_TEXT[need.kind],
            horizon=GoalHorizon.NOW,
            priority=NEED_GOAL_PRIORITY[need.kind],
            origin=GoalOrigin.NEED,
            created_tick=world.tick(),
            done_when={"need": need.kind.value, "below": NEED_PRESSING},
            set_in_stage=_stage_now(world, agent_id),
            evidence=[f"{need.kind.value} at {need.level:.2f} since tick {need.since_tick}"],
        )
        world.goals[goal.goal_id] = goal
        live.append(goal)
        events.append(
            WorldEvent(
                type="agent.goal.set",
                tick=world.tick(),
                payload={
                    "agent_id": agent_id,
                    "goal_id": goal.goal_id,
                    "text": goal.text,
                    "origin": goal.origin.value,
                    "horizon": goal.horizon.value,
                    "priority": goal.priority,
                    "need": need.kind.value,
                    "need_level": round(need.level, 3),
                },
            )
        )
        if len(live) >= MAX_ACTIVE_GOALS:
            break
    return events


def relieve_need_goals(world: "WorldState", agent_id: str) -> list[WorldEvent]:
    """The other side of rule 1: a need-goal whose need has dropped is done.

    A resident that ate does not have to notice it ate. The goal closes
    because the pressure closed, which is the only honest reading -
    a `done` that required a self-report would be measuring candour.
    """
    events: list[WorldEvent] = []
    held = (world.needs or {}).get(agent_id) or {}
    for goal in active_goals(world, agent_id):
        kind = goal.done_when.get("need")
        if not kind:
            continue
        need = held.get(kind)
        if need is None or need.level >= NEED_PRESSING:
            continue
        goal.status = GoalStatus.DONE
        goal.progress = 1.0
        goal.evidence.append(f"{kind} fell to {need.level:.2f} at tick {world.tick()}")
        _note_outcome(world, goal, "relieved")
        events.append(
            WorldEvent(
                type="agent.goal.progressed",
                tick=world.tick(),
                payload={
                    "agent_id": agent_id,
                    "goal_id": goal.goal_id,
                    "text": goal.text,
                    "progress": 1.0,
                    "status": GoalStatus.DONE.value,
                    "because": f"{kind} relieved",
                },
            )
        )
    return events


def credit_tool_success(
    world: "WorldState", agent_id: str, tool_name: str, result: dict[str, Any] | None = None
) -> list[WorldEvent]:
    """Rule 2. A tool that succeeded closes the goals that named it.

    `done_when={"tool": "world_cook"}` completes on the first success.
    `done_when={"tool": "world_sell", "times": 3}` needs three, and the
    count lives in `progress` so the resident can see it move.
    """
    events: list[WorldEvent] = []
    for goal in active_goals(world, agent_id):
        wanted = goal.done_when.get("tool")
        if not wanted or wanted != tool_name:
            continue
        times = int(goal.done_when.get("times") or 1)
        done_so_far = int(round(goal.progress * times)) + 1
        goal.progress = min(1.0, done_so_far / float(times))
        goal.evidence.append(f"{tool_name} at tick {world.tick()}")
        if goal.progress >= 1.0:
            goal.status = GoalStatus.DONE
        events.append(
            WorldEvent(
                type="agent.goal.progressed",
                tick=world.tick(),
                payload={
                    "agent_id": agent_id,
                    "goal_id": goal.goal_id,
                    "text": goal.text,
                    "progress": round(goal.progress, 3),
                    "status": goal.status.value,
                    "because": tool_name,
                },
            )
        )
    return events


def _belief_id(world: "WorldState", agent_id: str, subject: str) -> str:
    return f"belief_{agent_id}_{subject}"


def note_refusal_belief(
    world: "WorldState",
    agent_id: str,
    tool_name: str,
    reason: str,
    location_id: str | None = None,
) -> list[WorldEvent]:
    """Rule 3. Being stopped is evidence, so write it down.

    Keyed on tool-and-place, so the second refusal of the same thing in
    the same place raises confidence rather than filling the list. A
    resident refused four times is more sure, not four times as noisy.
    """
    try:
        return _note_refusal_belief(world, agent_id, tool_name, reason, location_id)
    except Exception:
        logger.exception(
            "ENG-24: could not write a refusal belief for %s/%s", agent_id, tool_name
        )
        return []


def _note_refusal_belief(
    world: "WorldState",
    agent_id: str,
    tool_name: str,
    reason: str,
    location_id: str | None = None,
) -> list[WorldEvent]:
    subject = f"{tool_name}@{location_id or 'anywhere'}"
    belief_id = _belief_id(world, agent_id, subject)
    existing = (world.beliefs or {}).get(belief_id)
    statement = f"{tool_name} does not work here: {reason}"[:160]
    if existing is not None:
        existing.confidence = min(0.95, existing.confidence + 0.15)
        existing.statement = statement
        existing.last_updated_tick = world.tick()
        if len(existing.evidence) < 5:
            existing.evidence.append(f"refused at tick {world.tick()}")
        belief = existing
    else:
        belief = Belief(
            belief_id=belief_id,
            agent_id=agent_id,
            subject=subject,
            statement=statement,
            confidence=0.6,
            evidence=[f"refused at tick {world.tick()}"],
            last_updated_tick=world.tick(),
        )
        world.beliefs[belief_id] = belief
        _trim_beliefs(world, agent_id)
    return [
        WorldEvent(
            type="agent.belief.updated",
            tick=world.tick(),
            payload={
                "agent_id": agent_id,
                "belief_id": belief.belief_id,
                "subject": belief.subject,
                "statement": belief.statement,
                "confidence": round(belief.confidence, 3),
                "source": "refusal",
            },
        )
    ]


def _trim_beliefs(world: "WorldState", agent_id: str) -> None:
    """Keep the belief list bounded: oldest, least-confident go first."""
    mine = [b for b in world.beliefs.values() if b.agent_id == agent_id]
    if len(mine) <= MAX_BELIEFS_PER_AGENT:
        return
    doomed = sorted(mine, key=lambda b: (b.confidence, b.last_updated_tick))
    for belief in doomed[: len(mine) - MAX_BELIEFS_PER_AGENT]:
        world.beliefs.pop(belief.belief_id, None)


EXPIRING_HORIZONS = frozenset({GoalHorizon.NOW, GoalHorizon.TODAY})


def expire_goals(world: "WorldState", reason: str = "the day ended") -> list[WorldEvent]:
    """Rule 4. Called at the day boundary, beside expire_intentions."""
    events: list[WorldEvent] = []
    for goal in (world.goals or {}).values():
        if goal.status != GoalStatus.ACTIVE or goal.horizon not in EXPIRING_HORIZONS:
            continue
        goal.status = GoalStatus.EXPIRED
        _note_outcome(world, goal, reason)
        events.append(
            WorldEvent(
                type="agent.goal.retired",
                tick=world.tick(),
                payload={
                    "agent_id": goal.agent_id,
                    "goal_id": goal.goal_id,
                    "text": goal.text,
                    "status": GoalStatus.EXPIRED.value,
                    "reason": reason,
                    "progress": round(goal.progress, 3),
                    "origin": goal.origin.value,
                    "ticks_held": world.tick() - goal.created_tick,
                },
            )
        )
    return events


def refresh_and_spawn(world: "WorldState", agent_id: str) -> list[WorldEvent]:
    """One call per resident per turn: read pressure, then act on it.

    Order matters. Needs are recomputed first so a goal spawned this
    tick is spawned against this tick's facts; relief is checked before
    spawning so a resident that just ate does not acquire a hunger goal
    on the same tick it stopped being hungry.
    """
    refresh_needs(world, agent_id)
    events = supersede_goals_of_a_life_left_behind(world, agent_id)
    events.extend(relieve_need_goals(world, agent_id))
    events.extend(spawn_goals_from_needs(world, agent_id))
    return events
