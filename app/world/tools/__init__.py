"""world/tools/: DESIGN.md section 2, "the law of physics."

"World state mutates ONLY through tool calls." This package is the tool
registry that sentence describes: every mutation-capable action in the
simulation (move, harvest/mine/fell_timber, craft/cook, buy/sell/
transfer/store_item/withdraw_item, marry/pray/teach_skill, vote/
petition/register_business/register_org/file_complaint, steal,
build_structure, and the meta-tool define_tool) is a `ToolSpec`
registered in a `ToolRegistry` (registry.py): a name, a pydantic params
schema, a location gate, actor requirements, and an `execute()` path that
mutates `WorldState` (state.py) atomically and publishes the
`world.core.events.WorldEvent`s it caused. No other path in this package
mutates world state - see registry.py's module docstring for the exact
dispatch order every call goes through.

This package does not duplicate money (world.ledger), politics
(world.charter), demographics/health/marriage (world.life), or map
geometry (world.map) - it is the integration layer those four packages'
own docstrings describe wanting but not yet having ("the future tool
dispatcher", life/ports.py's EconomyPort placeholder, charter/events.py's
EventSink seam). It owns exactly the state nothing else does: physical
inventory, resource-zone yields, businesses' non-financial identity,
storage containers, building plots, skills, prayer requests, crime cases,
and invented-tool definitions - see models.py and state.py.

`bootstrap.build_world()` is the one call site that wires all of the
above into a running `(WorldState, ToolRegistry)` pair.

Module boundary (app/CONVENTIONS.md): this package is part of world/, so
it owns world state and must never import boto3 - model inference is
broker/'s job, reached (if a tool handler ever needed it, none currently
do) through broker's HTTP API, never a direct import.
"""

from __future__ import annotations

from world.tools.bootstrap import build_world
from world.tools.registry import (
    ActorRequirements,
    ToolNamespace,
    ToolOutcome,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from world.tools.state import WorldState

__all__ = [
    "build_world",
    "ActorRequirements",
    "ToolNamespace",
    "ToolOutcome",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "WorldState",
]
