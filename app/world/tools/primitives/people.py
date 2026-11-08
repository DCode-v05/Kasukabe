"""People discovery: who is in this town, and who can help with this.

The companion to places.py. That module exists because the town had "27
tools and no way to answer where can I go"; this one exists because it still
had no way to answer **who can help me**, which is the question a resident
holding four units of copper ore actually has.

Bram Holloway, one tick, five remarks:

    "Any word who's working metal?"  ... "You know anyone trading copper?"
    "Copper ore here. Four units. Looking for a buyer."
    "Four units from the seam today. You know anyone buying copper raw?"

The town had a trader standing two buildings away. Shouting at a room is
what an agent does when it cannot look anybody up.

Two tools, both knowledge rather than physical action, exactly as places.py's
three are:

- `find_people`      who can help with a thing, best first
- `describe_person`  one resident's card: trade, workplace, shop, skills

Neither mutates the world, and neither is location-gated. Knowing that the
town has a trader is not the same as reaching one: walking there is `move`,
which is gated, costs energy, and takes real ticks. Discovery tells an agent
where to spend that walk.

The scoring, the alias table and the privacy line all live in
world/tools/cards.py; see that module's docstring for why a card carries a
trade and never a wallet.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from world.life.models import AgentLifeRecord
from world.tools.cards import DEFAULT_LIMIT, agent_card, rank_people
from world.tools.registry import ActorRequirements, ToolNamespace, ToolOutcome, ToolSpec
from world.tools.state import WorldState

MAX_RESULTS = 8


class FindPeopleParams(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=200,
        description="What you need, in your own words: a trade, a skill, a good, a shop.",
    )
    limit: int = Field(default=DEFAULT_LIMIT, ge=1, le=MAX_RESULTS)


class DescribePersonParams(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)


async def find_people_handler(
    world: WorldState, agent: AgentLifeRecord, params: FindPeopleParams
) -> ToolOutcome:
    """Rank residents against what the caller says it needs.

    Returns an empty list rather than the whole town when nothing matches.
    A discovery tool that answers every question with everybody has told the
    agent nothing and charged it a paragraph of context for the privilege -
    and would send it walking across town on a false lead.
    """
    matches = rank_people(
        world, params.query, exclude=agent.agent_id, limit=params.limit
    )
    result: dict[str, Any] = {"query": params.query, "found": matches}
    if not matches:
        result["note"] = (
            "Nobody in town matches that. Try the trade rather than the "
            "goods - 'trader', 'miner', 'baker', 'forester', 'artisan' - or "
            "ask someone directly with `say`."
        )
    return ToolOutcome(result=result, events=[])


async def describe_person_handler(
    world: WorldState, agent: AgentLifeRecord, params: DescribePersonParams
) -> ToolOutcome:
    """One resident's card: what they do, where, and what they are good at.

    The A2A agent card, scoped to what a neighbour could observe. No wallet,
    no secret, nothing from the private half of a persona - see cards.py.
    """
    card = agent_card(world, params.agent_id)
    if card is None:
        return ToolOutcome(
            result={
                "found": False,
                "note": (
                    f"No resident called {params.agent_id!r} lives here. "
                    "Use `find_people` to see who does."
                ),
            },
            events=[],
        )
    return ToolOutcome(result={"found": True, **card}, events=[])


def build_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="find_people",
            description=(
                "Find residents who can help with something: a trade, a skill, "
                "a good, or a shop. Say what you need in your own words - "
                "'who buys copper', 'someone who mills flour' - and get back "
                "who does that, where they work, and where they are now."
            ),
            params_model=FindPeopleParams,
            handler=find_people_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
        ToolSpec(
            name="describe_person",
            description=(
                "What one resident does for a living, where they work, the shop "
                "they own or work at, and what they are known to be good at."
            ),
            params_model=DescribePersonParams,
            handler=describe_person_handler,
            namespace=ToolNamespace.PUBLIC,
            location_gate=None,
            actor_requirements=ActorRequirements(),
        ),
    ]
