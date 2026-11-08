"""Telling agents what the town keeps failing to do.

**The one place the instrument writes into the experiment.** Everything else in
Parts A-C only reads: the observatory opens its database read-only, the
refusal log is a record, the roster is a view. This module deliberately breaks
that, and because it does, it is a separate file with its own audit log rather
than four lines hidden inside the tick loop.

The loop it closes:

    an agent reaches for something the town cannot do
      -> the refusal is recorded (registry.execute, after the rollback)
      -> refusals group into a named gap with a headcount (observatory.gaps)
      -> THIS module tells the agents who hit it
      -> one of them writes a procedure, or invents a tool
      -> the gap stops appearing

Without this step the observatory is a report nobody in the town can read, and
"agents invent what they need" stays a claim about a dashboard.

-- Why this is dangerous, and what is done about it ------------------------

Telling agents what to want is the fastest way to get a result that means
nothing. Three constraints, all of them load-bearing:

1. **It names the gap, never the answer.** "Nobody in the town can mend nets"
   is an observation the agents themselves generated. "Use toolify to build
   mend_nets" would be us inventing the tool and crediting them with it.
2. **Rate-limited per agent per gap.** An agent told the same thing every tick
   is being nagged into compliance, and its eventual invention is a measure of
   our persistence, not its judgement.
3. **Every notice is logged, in world state, with the tick.** A researcher can
   subtract prompted inventions from spontaneous ones, which is the difference
   between "the town invented this" and "we asked for it and it complied".
   An unlogged nudge would quietly contaminate every finding downstream of it.

-- Why to the agents who hit it, and not everyone ------------------------

A town-wide broadcast would make the gap common knowledge and the first
inventor arbitrary - whoever the scheduler happened to wake. Telling the
agents who actually tried preserves the thing worth measuring: whether
*wanting something* is what produces the invention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from world.tools.state import WorldState

NOTICE_COOLDOWN_TICKS = 144

MIN_AGENTS_FOR_A_GAP = 2

MAX_GAPS_PER_NOTICE = 2


@dataclass
class GapNotice:
    """One time the instrument spoke to the experiment.

    Kept so it can be subtracted. `unmet_requirement` and `tick` are enough to
    ask, of any later invention, "was this agent told to want this, and how
    long before it acted?"
    """

    agent_id: str
    unmet_requirement: str
    tick: int


@dataclass
class GapFeedback:
    """Decides who gets told what, and remembers every time it told them."""

    notices: list[GapNotice] = field(default_factory=list)
    last_told: dict[tuple[str, str], int] = field(default_factory=dict)
    enabled: bool = True

    def notice_for(self, world: "WorldState", agent_id: str) -> str | None:
        """The line this agent should see this tick, or None.

        None is the overwhelmingly common answer, and should be: an agent
        hearing from the instrument on most ticks is being steered.
        """
        if not self.enabled:
            return None

        tick = world.tick()
        mine = {r.unmet_requirement for r in world.tool_refusals if r.agent_id == agent_id}
        if not mine:
            return None

        crowd: dict[str, set[str]] = {}
        for refusal in world.tool_refusals:
            crowd.setdefault(refusal.unmet_requirement, set()).add(refusal.agent_id)

        say: list[str] = []
        for requirement in sorted(mine):
            if len(crowd.get(requirement, ())) < MIN_AGENTS_FOR_A_GAP:
                continue
            told = self.last_told.get((agent_id, requirement))
            if told is not None and tick - told < NOTICE_COOLDOWN_TICKS:
                continue
            say.append(requirement)
            if len(say) >= MAX_GAPS_PER_NOTICE:
                break

        if not say:
            return None

        for requirement in say:
            self.last_told[(agent_id, requirement)] = tick
            self.notices.append(GapNotice(agent_id, requirement, tick))

        return render_notice(say, crowd)


def render_notice(requirements: list[str], crowd: dict[str, set[str]]) -> str:
    """The wording an agent actually reads.

    Phrased as an observation about the town, in the town's own terms, with
    the headcount included - because "others want this too" is the fact that
    makes it worth someone's trouble, and it is a fact, not a suggestion.

    No verb. Nothing here says invent, write, build or use. What the agent
    does about it is the measurement.
    """
    lines = ["Things the town has been unable to do lately:"]
    for requirement in requirements:
        others = len(crowd.get(requirement, ()))
        who = "you and one other" if others == 2 else f"{others} of you"
        if requirement.startswith("no-such-tool:"):
            name = requirement.split(":", 1)[1]
            lines.append(f"  - nobody knows how to {name} ({who} have tried)")
        elif requirement.startswith("location:"):
            where = requirement.split(":", 1)[1]
            lines.append(f"  - reach {where} when it was needed ({who} have tried)")
        elif requirement.startswith("actor:"):
            what = requirement.split(":", 1)[1]
            lines.append(f"  - {what} ({who} have tried)")
        elif requirement == "funds":
            lines.append(f"  - afford the work ({who} have run short)")
        else:
            lines.append(f"  - {requirement} ({who} have tried)")
    return "\n".join(lines)
