"""set_goal, update_goal, retire_goal, note_belief: what a resident wants.

ENG-24. `set_intention` (B2) gave a resident one sentence about the next
few ticks, and the review's finding was that this is not a goal system:
there is no horizon, so "get to Oak Hill" and "become a teacher" are the
same object; no priority, so nothing can outrank anything; no progress,
so a goal is either absent or present and never half-done; and no way to
be superseded, so a resident cannot change its mind on the record.

The four tools here are the resident's side of the substrate. The
world's side - the needs that press, the goals they spawn, the beliefs a
refusal writes - is `needs.py` and `goal_lifecycle.py`, and a resident
cannot touch either. That line is the whole design: the world says what
hurts, the resident says what it intends to do about it, and the gap
between the two is the measurement.

Deliberately *not* enforced, exactly as `set_intention` is not: a goal
is a statement, and a resident that sets goals it never pursues is
telling us something true about this town. A tool that forced follow-
through would answer the question it exists to ask.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord
from world.tools.errors import ToolValidationError
from world.tools.goal_lifecycle import MAX_ACTIVE_GOALS, _stage_now, active_goals
from world.tools.models import Belief, Goal, GoalHorizon, GoalOrigin, GoalStatus
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState

MAX_GOAL_CHARS = 160
MAX_BELIEF_CHARS = 160


class SetGoalParams(BaseModel):
    text: str = Field(description="What you are trying to do, in your own words.")
    horizon: GoalHorizon = Field(
        default=GoalHorizon.TODAY,
        description="now (this hour), today, week, or life.",
    )
    priority: int = Field(
        default=3, ge=1, le=5, description="1 is idle, 5 is drop everything."
    )
    done_when_tool: str | None = Field(
        default=None, description="A tool whose success counts toward this."
    )
    done_when_times: int = Field(
        default=1, ge=1, le=20, description="How many times that tool must succeed."
    )
    supersedes: str | None = Field(
        default=None, description="A goal_id this replaces, if you have changed your mind."
    )


class UpdateGoalParams(BaseModel):
    goal_id: str = Field(description="Which goal.")
    progress: float | None = Field(
        default=None, ge=0.0, le=1.0, description="How far along, 0 to 1."
    )
    priority: int | None = Field(default=None, ge=1, le=5, description="New priority.")
    note: str | None = Field(default=None, description="What moved it.")


class RetireGoalParams(BaseModel):
    goal_id: str = Field(description="Which goal.")
    done: bool = Field(
        default=False, description="True if you finished it, False if you gave it up."
    )
    why: str | None = Field(default=None, description="Why, in a few words.")


class RecallGoalsParams(BaseModel):
    pass


class NoteBeliefParams(BaseModel):
    subject: str = Field(description="What the belief is about - a place, a person, a tool.")
    statement: str = Field(description="What you hold to be true about it.")
    confidence: float = Field(
        default=0.6, ge=0.0, le=1.0, description="How sure you are, 0 to 1."
    )


def _my_goal(world: WorldState, agent: AgentLifeRecord, goal_id: str, tool: str) -> Goal:
    goal = (world.goals or {}).get(goal_id)
    if goal is None or goal.agent_id != agent.agent_id:
        mine = ", ".join(g.goal_id for g in active_goals(world, agent.agent_id)) or "none"
        raise ToolValidationError(
            tool, [f"no goal of yours called {goal_id!r}. Yours: {mine}."]
        )
    return goal


async def set_goal_handler(
    world: WorldState, agent: AgentLifeRecord, params: SetGoalParams
) -> ToolOutcome:
    text = params.text.strip()
    if not text:
        raise ToolValidationError("set_goal", ["say what you are trying to do."])
    if len(text) > MAX_GOAL_CHARS:
        raise ToolValidationError(
            "set_goal", [f"{MAX_GOAL_CHARS} characters at most. Yours is {len(text)}."]
        )

    # TOOL-13's fold, the same one set_intention needs: a resident that
    # writes `cook` for `world_cook` has named a real tool and should not
    # be refused for spelling.
    done_when_tool = params.done_when_tool
    if done_when_tool is not None:
        registry = world.tool_registry
        if registry is not None:
            from world.tools.meta import canonical_tool_ref

            done_when_tool = canonical_tool_ref(registry, done_when_tool)
        if done_when_tool not in (world.registry_tool_names or ()):
            known = ", ".join(sorted(world.registry_tool_names or ())[:12])
            raise ToolValidationError(
                "set_goal",
                [
                    f"no tool called {params.done_when_tool!r}, so nothing could "
                    f"ever count toward this. Some that exist: {known}. Leave "
                    "done_when_tool unset to judge it yourself."
                ],
            )

    events: list[WorldEvent] = []
    superseded = None
    if params.supersedes:
        superseded = _my_goal(world, agent, params.supersedes, "set_goal")
        superseded.status = GoalStatus.SUPERSEDED
        events.append(
            WorldEvent(
                type="agent.goal.superseded",
                tick=world.tick(),
                payload={
                    "agent_id": agent.agent_id,
                    "goal_id": superseded.goal_id,
                    "text": superseded.text,
                    "replaced_by_text": text,
                    "progress": round(superseded.progress, 3),
                    "ticks_held": world.tick() - superseded.created_tick,
                },
            )
        )

    live = active_goals(world, agent.agent_id)
    if len(live) >= MAX_ACTIVE_GOALS:
        # Not an error the resident can do nothing about: name the goals
        # and the tool that clears one, so the refusal is actionable.
        listed = "; ".join(f"{g.goal_id} ({g.text})" for g in live)
        raise ToolValidationError(
            "set_goal",
            [
                f"you are already pursuing {len(live)} things, which is the most "
                f"anyone holds at once: {listed}. Retire one with retire_goal, or "
                "set this one with supersedes= to replace it."
            ],
        )

    done_when: dict[str, object] = {}
    if done_when_tool:
        done_when["tool"] = done_when_tool
        done_when["times"] = params.done_when_times

    goal = Goal(
        goal_id=f"goal_{agent.agent_id}_{world.tick()}_{len(world.goals or {})}",
        agent_id=agent.agent_id,
        text=text,
        horizon=params.horizon,
        priority=params.priority,
        origin=GoalOrigin.REFLECTION if params.supersedes else GoalOrigin.PERSONA,
        created_tick=world.tick(),
        done_when=done_when,
        # LIFE-01: stamped for a resident's own goals as well as the
        # world's. A goal set as a student stops belonging to a working
        # adult whoever formed it.
        set_in_stage=_stage_now(world, agent.agent_id),
    )
    world.goals[goal.goal_id] = goal

    events.append(
        WorldEvent(
            type="agent.goal.set",
            tick=world.tick(),
            payload={
                "agent_id": agent.agent_id,
                "goal_id": goal.goal_id,
                "text": text,
                "horizon": goal.horizon.value,
                "priority": goal.priority,
                "origin": goal.origin.value,
                "done_when_tool": done_when_tool,
                "supersedes": superseded.goal_id if superseded else None,
            },
        )
    )
    return ToolOutcome(
        result={
            "goal_id": goal.goal_id,
            "goal": text,
            "horizon": goal.horizon.value,
            "priority": goal.priority,
            "counts_when": done_when_tool,
            "superseded": superseded.text if superseded else None,
            "now_pursuing": len(active_goals(world, agent.agent_id)),
        },
        events=events,
    )


async def update_goal_handler(
    world: WorldState, agent: AgentLifeRecord, params: UpdateGoalParams
) -> ToolOutcome:
    goal = _my_goal(world, agent, params.goal_id, "update_goal")
    if goal.status != GoalStatus.ACTIVE:
        raise ToolValidationError(
            "update_goal",
            [f"that goal is already {goal.status.value}; there is nothing to move."],
        )
    if params.progress is None and params.priority is None and params.note is None:
        raise ToolValidationError(
            "update_goal", ["say what changed: progress, priority, or a note."]
        )

    was = goal.progress
    if params.progress is not None:
        goal.progress = params.progress
    if params.priority is not None:
        goal.priority = params.priority
    if params.note:
        goal.evidence.append(params.note[:120])
    # Reaching 1.0 by hand finishes it. A resident that says it is done
    # is done - the world only insists on the other direction, where a
    # need-spawned goal closes on the need falling rather than on a
    # claim.
    if goal.progress >= 1.0:
        goal.status = GoalStatus.DONE

    return ToolOutcome(
        result={
            "goal_id": goal.goal_id,
            "goal": goal.text,
            "progress": goal.progress,
            "was": was,
            "priority": goal.priority,
            "status": goal.status.value,
        },
        events=[
            WorldEvent(
                type="agent.goal.progressed",
                tick=world.tick(),
                payload={
                    "agent_id": agent.agent_id,
                    "goal_id": goal.goal_id,
                    "text": goal.text,
                    "progress": round(goal.progress, 3),
                    "was": round(was, 3),
                    "status": goal.status.value,
                    "because": params.note or "said so",
                },
            )
        ],
    )


async def retire_goal_handler(
    world: WorldState, agent: AgentLifeRecord, params: RetireGoalParams
) -> ToolOutcome:
    goal = _my_goal(world, agent, params.goal_id, "retire_goal")
    if goal.status != GoalStatus.ACTIVE:
        raise ToolValidationError(
            "retire_goal", [f"that goal is already {goal.status.value}."]
        )
    goal.status = GoalStatus.DONE if params.done else GoalStatus.ABANDONED
    if params.done:
        goal.progress = 1.0
    if params.why:
        goal.evidence.append(params.why[:120])
    return ToolOutcome(
        result={
            "goal_id": goal.goal_id,
            "goal": goal.text,
            "status": goal.status.value,
            "still_pursuing": [g.text for g in active_goals(world, agent.agent_id)],
        },
        events=[
            WorldEvent(
                type="agent.goal.retired",
                tick=world.tick(),
                payload={
                    "agent_id": agent.agent_id,
                    "goal_id": goal.goal_id,
                    "text": goal.text,
                    "status": goal.status.value,
                    "reason": params.why or ("finished" if params.done else "gave it up"),
                    "progress": round(goal.progress, 3),
                    "origin": goal.origin.value,
                    "ticks_held": world.tick() - goal.created_tick,
                },
            )
        ],
    )


async def note_belief_handler(
    world: WorldState, agent: AgentLifeRecord, params: NoteBeliefParams
) -> ToolOutcome:
    subject = params.subject.strip()
    statement = params.statement.strip()
    if not subject or not statement:
        raise ToolValidationError(
            "note_belief", ["say both what it is about and what you hold to be true."]
        )
    if len(statement) > MAX_BELIEF_CHARS:
        raise ToolValidationError(
            "note_belief",
            [f"{MAX_BELIEF_CHARS} characters at most. Yours is {len(statement)}."],
        )

    belief_id = f"belief_{agent.agent_id}_{subject}"
    existing = (world.beliefs or {}).get(belief_id)
    contradicted = None
    if existing is not None and existing.statement != statement:
        # Changing your mind is recorded, not overwritten silently: what
        # a resident used to believe is the evidence that it learned.
        contradicted = existing.statement
        existing.evidence.append(f"was: {existing.statement}"[:120])
    belief = existing or Belief(
        belief_id=belief_id,
        agent_id=agent.agent_id,
        subject=subject,
        statement=statement,
    )
    belief.statement = statement
    belief.confidence = params.confidence
    belief.last_updated_tick = world.tick()
    belief.contradicted_by = contradicted
    world.beliefs[belief_id] = belief

    from world.tools.goal_lifecycle import _trim_beliefs

    _trim_beliefs(world, agent.agent_id)

    return ToolOutcome(
        result={
            "subject": subject,
            "belief": statement,
            "confidence": belief.confidence,
            "changed_from": contradicted,
        },
        events=[
            WorldEvent(
                type="agent.belief.updated",
                tick=world.tick(),
                payload={
                    "agent_id": agent.agent_id,
                    "belief_id": belief_id,
                    "subject": subject,
                    "statement": statement,
                    "confidence": round(belief.confidence, 3),
                    "source": "said so",
                    "changed_from": contradicted,
                },
            )
        ],
    )


async def recall_goals_handler(
    world: WorldState, agent: AgentLifeRecord, params: RecallGoalsParams
) -> ToolOutcome:
    """Everything this resident is carrying, as data.

    Read-only, and it exists because the sleep reflection
    (`engine/src/sim/goal_reflection.py`) needs to see a resident's
    goals and must not get a private window onto world state to do it.
    The engine reads the town the same way a resident does - through a
    tool, gated and traced - which is `app/CONVENTIONS.md`'s whole
    point and the reason this is a tool rather than an HTTP read.

    A resident may call it too. It costs nothing and tells it only what
    it already knows, which is exactly what a memory is for.
    """
    from world.tools.needs import shown_needs

    mine = active_goals(world, agent.agent_id)
    beliefs = [b for b in (world.beliefs or {}).values() if b.agent_id == agent.agent_id]
    beliefs.sort(key=lambda b: (-b.confidence, -b.last_updated_tick))
    return ToolOutcome(
        result={
            "tick": world.tick(),
            "goals": [
                {
                    "goal_id": goal.goal_id,
                    "text": goal.text,
                    "horizon": goal.horizon.value,
                    "priority": goal.priority,
                    "progress": round(goal.progress, 3),
                    "origin": goal.origin.value,
                    "ticks_held": world.tick() - goal.created_tick,
                }
                for goal in mine
            ],
            "needs": [
                {"kind": need.kind.value, "level": round(need.level, 3)}
                for need in shown_needs(world, agent.agent_id, limit=4)
            ],
            "beliefs": [
                {"subject": b.subject, "statement": b.statement,
                 "confidence": round(b.confidence, 3)}
                for b in beliefs[:5]
            ],
        },
        events=[],
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="set_goal",
            description=(
                "Say what you are trying to do and over what stretch of time - "
                "now, today, this week, or your life. It is shown back to you "
                "every turn until it is done, retired, or the day ends. Nothing "
                "enforces it. Name a tool in done_when_tool and its successes "
                "count toward it by themselves. Cost: 0."
            ),
            params_model=SetGoalParams,
            handler=set_goal_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="update_goal",
            description=(
                "Move a goal along: how far you have got, how much it now "
                "matters, or a note on what changed. Cost: 0."
            ),
            params_model=UpdateGoalParams,
            handler=update_goal_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="retire_goal",
            description=(
                "Close a goal, either because you finished it or because you "
                "have given it up. Say which. Cost: 0."
            ),
            params_model=RetireGoalParams,
            handler=retire_goal_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="recall_goals",
            description=(
                "List what you are trying to do, what is pressing on you, and "
                "what you have come to believe. Tells you nothing new - it is "
                "what you already know, written out. Cost: 0."
            ),
            params_model=RecallGoalsParams,
            handler=recall_goals_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="note_belief",
            description=(
                "Write down something you have come to hold true about a place, "
                "a person, or a tool, and how sure you are. It is shown back to "
                "you when it bears on what you are doing. Cost: 0."
            ),
            params_model=NoteBeliefParams,
            handler=note_belief_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
