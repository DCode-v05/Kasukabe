"""What the town keeps trying to do and cannot.

The view this whole service exists for. Everything else here is diagnostics;
this is a finding.

A capability gap with **a name and a headcount** - "14 agents tried to treat
illness and lacked the skill" - is the only form in which "the town is missing
something" becomes actionable, and on Part D it is exactly what gets fed back
to those agents as the prompt for a `toolify`.

Source is `world.tool_refusals`, written by `ToolRegistry.execute()` after its
rollback (see the comment there for why after). Refusals are read over HTTP
from the world server rather than from a table, because they live in world
state: the observatory owns no database of its own and writes nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class CapabilityGap:
    unmet_requirement: str
    agents: int
    attempts: int
    tools: list[str]
    example: str

    @property
    def is_missing_verb(self) -> bool:
        """A gap of the form "nothing in the town does X" - the kind an agent
        could close by inventing a tool, rather than by learning or walking
        somewhere. Ranked first in the view for that reason."""
        return self.unmet_requirement.startswith("no-such-tool:")


def summarise(refusals: Iterable[Mapping[str, Any]]) -> list[CapabilityGap]:
    """Group refusals into gaps, worst first.

    Ordered by headcount before attempt count, deliberately: one agent
    hammering the same impossible call 400 times is a stuck agent, while
    fourteen agents each hitting it twice is a town-wide gap. The second is
    the more interesting row and must not be buried under the first.
    """
    agents: dict[str, set[str]] = defaultdict(set)
    attempts: dict[str, int] = defaultdict(int)
    tools: dict[str, set[str]] = defaultdict(set)
    example: dict[str, str] = {}

    for refusal in refusals:
        key = refusal.get("unmet_requirement") or "unknown"
        agents[key].add(refusal.get("agent_id") or "?")
        attempts[key] += 1
        if refusal.get("tool_name"):
            tools[key].add(refusal["tool_name"])
        example.setdefault(key, refusal.get("detail") or "")

    gaps = [
        CapabilityGap(
            unmet_requirement=key,
            agents=len(agents[key]),
            attempts=attempts[key],
            tools=sorted(tools[key]),
            example=example.get(key, ""),
        )
        for key in attempts
    ]
    gaps.sort(key=lambda g: (g.is_missing_verb, g.agents, g.attempts), reverse=True)
    return gaps
