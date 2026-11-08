"""A skill step may name a tool the way the agent was shown it.

The world registers its tools bare - "move", "harvest", "fell_timber" -
but an agent never sees them that way. The engine presents them
namespaced, and every tool call in a session log reads "world:move". So a
resident writing down a workflow wrote the name it had been shown and
`skillify` refused it against the name the registry keeps.

Measured live: Marta Thorne filed a six-step gathering route as
world_move / world_harvest / world_move / world_move / world_fell_timber
/ world_move and lost all six - "no tool named 'world_move'" - for a
workflow whose every step was a real tool she had used that same morning.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from world.tools.bootstrap import build_world
from world.tools.meta import canonical_tool_ref, normalize_skill_steps
from world.tools.models import SkillStep


@pytest_asyncio.fixture
async def registry():
    _world, reg = await build_world(genesis_tick=0, seed=1)
    return reg


def test_the_registry_really_is_bare(registry) -> None:
    """The premise. If tools ever become namespaced in the registry too,
    this says so rather than letting the fold quietly become a no-op."""
    assert registry.get("move") is not None
    assert registry.get("world:move") is None


@pytest.mark.parametrize("written", ["world:move", "world_move", "move"])
def test_every_spelling_reaches_the_same_tool(registry, written) -> None:
    assert canonical_tool_ref(registry, written) == "move"


def test_a_tool_whose_own_name_has_an_underscore_survives(registry) -> None:
    """"fell_timber" is an exact hit and must never be split into
    "timber" - a real underscore is not a namespace."""
    assert canonical_tool_ref(registry, "fell_timber") == "fell_timber"
    assert canonical_tool_ref(registry, "world_fell_timber") == "fell_timber"
    assert canonical_tool_ref(registry, "describe_place") == "describe_place"


def test_an_invented_tool_is_still_refused(registry) -> None:
    """Folding is not inventing. A name that leaves nothing real behind it
    passes through unchanged and fails validation as before."""
    assert canonical_tool_ref(registry, "world_teleport") == "world_teleport"
    assert canonical_tool_ref(registry, "craft_planks") == "craft_planks"
    assert canonical_tool_ref(registry, "") == ""


def test_the_steps_are_rewritten_in_place(registry) -> None:
    """The corrected name has to reach what gets STORED - a skill filed as
    "world:move" would pass a lenient check and then never run."""
    steps = [
        SkillStep(order=1, kind="tool", ref="world_move"),
        SkillStep(order=2, kind="tool", ref="world_fell_timber"),
    ]
    normalize_skill_steps(registry, steps)
    assert [s.ref for s in steps] == ["move", "fell_timber"]


def test_non_tool_steps_are_left_alone(registry) -> None:
    """A skill step referring to another skill is not a tool name."""
    steps = [
        SkillStep(order=1, kind="skill", ref="world_move"),
        SkillStep(order=2, kind="check", ref="something"),
    ]
    normalize_skill_steps(registry, steps)
    assert steps[0].ref == "world_move"
    assert steps[1].ref == "something"
