"""set_intention, drop_intention: what a resident set out to do.

Tool spec B2.

Perception carries what others said to you and what you owe an answer to,
and nothing about what you set out to do. Aldous Vance decided to go to
Oak Hill College on four consecutive ticks and never went - not because
he changed his mind, but because each tick he arrived at the decision
fresh, with no record that he had already made it. Most of the churn the
reports call "thinking instead of living" is that.

**Nothing enforces it.** This is a note to yourself, not a contract with
the world: the resident can ignore it, and the ratio of intentions set to
intentions fulfilled is exactly the business-readiness figure that makes
that interesting (T4, R4). A tool that forced follow-through would answer
the question it exists to ask.

Declared rather than inferred, for the same reason `keep_doing` is: a
stated intention that goes unfulfilled is a fact the trace can count,
where an inferred one is a guess the report would have to defend.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from world.core.events import WorldEvent
from world.life.models import AgentLifeRecord
from world.tools.errors import ToolValidationError
from world.tools.models import Intention
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState

MAX_INTENTION_CHARS = 200


class SetIntentionParams(BaseModel):
    text: str = Field(description="The intention, as you would say it to yourself.")
    done_when_tool: str | None = Field(
        default=None, description="A tool whose success clears this."
    )


class DropIntentionParams(BaseModel):
    pass


async def set_intention_handler(
    world: WorldState, agent: AgentLifeRecord, params: SetIntentionParams
) -> ToolOutcome:
    text = params.text.strip()
    if not text:
        raise ToolValidationError("set_intention", ["say what you set out to do."])
    if len(text) > MAX_INTENTION_CHARS:
        raise ToolValidationError(
            "set_intention",
            [f"{MAX_INTENTION_CHARS} characters at most. Yours is {len(text)}."],
        )
    # TOOL-13, the same fold `keep_doing` needs: run 23 refused
    # `set_intention(done_when_tool="world_cook")` twice, for a verb the
    # resident had been shown under exactly that name.
    done_when_tool = params.done_when_tool
    if done_when_tool is not None:
        registry = world.tool_registry
        if registry is not None:
            from world.tools.meta import canonical_tool_ref

            done_when_tool = canonical_tool_ref(registry, done_when_tool)
    if done_when_tool is not None and done_when_tool not in (
        world.registry_tool_names or ()
    ):
        # Named a tool that does not exist, so nothing could ever clear it.
        # Better to say so now than to leave a resident carrying an
        # intention that outlives every possible act.
        known = ", ".join(sorted(world.registry_tool_names or ())[:12])
        raise ToolValidationError(
            "set_intention",
            [
                f"no tool called {params.done_when_tool!r}, so nothing could "
                f"ever clear this. Some that exist: {known}. Leave "
                "done_when_tool unset to clear it yourself."
            ],
        )

    previous = world.intentions.get(agent.agent_id)
    intention = Intention(
        text=text,
        set_tick=world.tick(),
        done_when_tool=done_when_tool,
        set_clock=world.game_clock_label(),
    )
    world.intentions[agent.agent_id] = intention

    event = WorldEvent(
        type="agent.intention.set",
        tick=world.tick(),
        payload={
            "agent_id": agent.agent_id,
            "text": text,
            "done_when_tool": params.done_when_tool,
            "replaced": previous.text if previous else None,
        },
    )
    return ToolOutcome(
        result={
            "intention": text,
            "set_tick": world.tick(),
            "clears_on": params.done_when_tool,
            "replaced": previous.text if previous else None,
        },
        events=[event],
    )


async def drop_intention_handler(
    world: WorldState, agent: AgentLifeRecord, params: DropIntentionParams
) -> ToolOutcome:
    dropped = world.drop_intention(agent.agent_id, "dropped")
    if dropped is None:
        raise ToolValidationError(
            "drop_intention", ["you have not set out to do anything."]
        )
    return ToolOutcome(
        result={"dropped": dropped.text},
        events=[
            WorldEvent(
                type="agent.intention.dropped",
                tick=world.tick(),
                payload={
                    "agent_id": agent.agent_id,
                    "text": dropped.text,
                    "reason": "dropped",
                    "ticks_held": world.tick() - dropped.set_tick,
                },
            )
        ],
    )


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="set_intention",
            description=(
                # Addendum section 7, 56 words. Says plainly that nothing
                # enforces it, because a resident that believed otherwise
                # would be reading a contract into a note.
                "Write down, in your own words, what you have set out to do. It is "
                "shown back to you at the start of every turn until you drop it, "
                "until the tool you name succeeds, or until the day ends. Nothing "
                "enforces it and nobody else sees it. `drop_intention` clears it. "
                "Cost: 0."
            ),
            params_model=SetIntentionParams,
            handler=set_intention_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="drop_intention",
            description=(
                "Clear what you had set out to do, because you have changed your "
                "mind or it no longer applies. Cost: 0."
            ),
            params_model=DropIntentionParams,
            handler=drop_intention_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
