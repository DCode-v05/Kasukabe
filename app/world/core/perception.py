"""Perception system - DESIGN.md section 9: "Perception system: what an
agent sees/hears at its location each tick."

Two things live here, both driven entirely off the event bus rather than
a direct write API, so a location or a perception-worthy happening is
always something world/'s tool execution *published*, never something
this module mutated on anyone's behalf (DESIGN.md section 2: "World state
mutates ONLY through tool calls"):

- LocationIndex: a live agent<->location map, kept current purely by
  subscribing to `agent.moved` / `agent.spawned` / `agent.died` on the
  EventBus (events.py). Nothing else may write to it directly.
- PerceptionSystem: for a due agent, builds what they currently see
  (other agents at their location) and what they've recently heard
  (buffered events tagged with their location, or events that name them
  directly). It also subscribes to the bus to interrupt the scheduler
  (scheduler.py) the instant something perception-worthy happens to an
  agent who was not otherwise due - the "or interrupted by perception"
  half of DESIGN.md section 9's scheduler.

Cross-module contract: any world/ submodule that wants its events to
show up in perception or to interrupt an agent's schedule tags its
event payload with the well-known keys below. This is the one place
that convention is spelled out; see default_interrupt_policy.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from pydantic import BaseModel, Field

from world.core.clock import timestamp_for
from world.core.events import EventBus, WorldEvent

logger = logging.getLogger(__name__)

# Payload key convention other world/ submodules follow so their events
# participate in perception and scheduler interrupts (see
# default_interrupt_policy and PerceptionSystem.build_perception below):
#   "location_id"          - str; where the event happened. Present on
#                             any event an agent could plausibly overhear.
#   "target_agent_id"      - str; the one agent this event is principally
#                             about (e.g. crime.theft.attempted's victim).
#   "involved_agent_ids"   - list[str]; every agent directly party to the
#                             event (e.g. both sides of a trade).
#   "witness_agent_ids"    - list[str]; agents who saw it happen but were
#                             not principals (DESIGN.md section 6's "~30%
#                             witness memory generated" on theft).
INTERRUPT_SINGLE_KEYS: tuple[str, ...] = ("target_agent_id",)
INTERRUPT_LIST_KEYS: tuple[str, ...] = ("involved_agent_ids", "witness_agent_ids")


def default_interrupt_policy(event: WorldEvent) -> set[str]:
    """Extracts the agent ids a WorldEvent directly involves, per the
    payload key convention documented above. Pure function of the event;
    callers needing different semantics can pass their own policy into
    PerceptionSystem instead of this default."""
    agents: set[str] = set()
    for key in INTERRUPT_SINGLE_KEYS:
        value = event.payload.get(key)
        if isinstance(value, str) and value:
            agents.add(value)
    for key in INTERRUPT_LIST_KEYS:
        values = event.payload.get(key)
        if isinstance(values, list):
            agents.update(v for v in values if isinstance(v, str) and v)
    return agents


InterruptPolicy = Callable[[WorldEvent], "set[str]"]


class LocationIndex:
    """Bidirectional agent<->location map, kept live by subscribing to the
    event bus rather than exposing its own mutation API - see module
    docstring. Every world/ submodule that moves or spawns an agent is
    expected to publish `agent.moved` / `agent.spawned` with a string
    `agent_id` and `location_id` payload (and `agent.died` with just
    `agent_id`, to remove them); this index reacts, it never initiates.
    """

    def __init__(self) -> None:
        self.agent_location: dict[str, str] = {}
        self.location_agents: dict[str, set[str]] = {}

    def place(self, agent_id: str, location_id: str) -> None:
        self.remove(agent_id)
        self.agent_location[agent_id] = location_id
        self.location_agents.setdefault(location_id, set()).add(agent_id)

    def remove(self, agent_id: str) -> None:
        old_location = self.agent_location.pop(agent_id, None)
        if old_location is None:
            return
        bucket = self.location_agents.get(old_location)
        if bucket is not None:
            bucket.discard(agent_id)
            if not bucket:
                del self.location_agents[old_location]

    def location_of(self, agent_id: str) -> str | None:
        return self.agent_location.get(agent_id)

    def agents_at(self, location_id: str) -> frozenset[str]:
        return frozenset(self.location_agents.get(location_id, ()))

    def attach(self, event_bus: EventBus) -> None:
        """Subscribes to the "agent" domain so this index stays current for
        the lifetime of the process without any caller having to remember
        to update it on every move."""

        # The three event types that can move an agent between locations.
        # Everything else in the "agent" domain is none of this index's
        # business.
        placement_events = ("agent.moved", "agent.spawned", "agent.died")

        async def handle(event: WorldEvent) -> None:
            # Filter by TYPE first, then validate the payload. The other
            # order looks equivalent and is not: this index subscribes to
            # the whole "agent" domain, so it also receives events it has
            # no interest in - agent.utterance.spoken, for one, which
            # carries speaker_agent_id rather than agent_id because an
            # utterance has a speaker, not a subject. Validating first
            # logged a WARNING for every one of them saying it had been
            # "ignored by LocationIndex". Ignoring it is CORRECT - speech
            # moves nobody - so the warning reported right behaviour as a
            # fault, in a log an operator is scanning for real faults.
            #
            # The genuine warnings below are untouched: an agent.moved with
            # no agent_id really is broken and must still say so.
            if event.type not in placement_events:
                return
            agent_id = event.payload.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id:
                logger.warning(
                    "%s event missing a string agent_id payload field, ignored by LocationIndex",
                    event.type,
                )
                return
            if event.type in ("agent.moved", "agent.spawned"):
                location_id = event.payload.get("location_id")
                if not isinstance(location_id, str) or not location_id:
                    logger.warning(
                        "%s for %s missing a string location_id payload field, "
                        "ignored by LocationIndex",
                        event.type,
                        agent_id,
                    )
                    return
                self.place(agent_id, location_id)
            elif event.type == "agent.died":
                self.remove(agent_id)

        event_bus.subscribe("agent", handle)

    def snapshot(self) -> dict[str, Any]:
        """Checkpointable.snapshot - see checkpoint.py."""
        return {"agent_location": dict(self.agent_location)}

    def restore(self, data: dict[str, Any]) -> None:
        """Checkpointable.restore - see checkpoint.py."""
        self.agent_location = {}
        self.location_agents = {}
        for agent_id, location_id in data["agent_location"].items():
            self.place(agent_id, location_id)


class AmbientInfo(BaseModel):
    """The time-of-day facts every perception carries regardless of location."""

    game_day: int
    hour: int
    minute: int
    is_daytime: bool


class PerceivedEvent(BaseModel):
    """One event as heard by a particular agent - a WorldEvent plus whether
    it was heard because it happened at the agent's own location or
    because the agent was directly named in it (involves_self); both can
    be true, and involves_self events reach the agent even from a
    different location (DESIGN.md's witness/target mechanics are not
    purely proximity-based, e.g. a warrant naming you)."""

    type: str
    tick: int
    payload: dict[str, Any]
    involves_self: bool
    # WHO is reading this. `involves_self` is already a per-reader field,
    # so the reader's own id belongs on the same object rather than being
    # threaded separately through every renderer.
    #
    # Without it, "was this said to me?" could only be asked as
    # `involves_self and there_is_any_listener`, which answers True for
    # the SPEAKER about their own remark - measured, and it did.
    #
    # Optional so every existing construction still parses; the renderer
    # treats a missing reader as "cannot tell", which is the safe answer.
    for_agent_id: str | None = None


class Perception(BaseModel):
    """What one agent perceives at one tick - the context a caller building
    an LLM turn for a due agent is expected to pass in (world-core does
    not itself construct prompts; that is out of this module's scope)."""

    agent_id: str
    tick: int
    location_id: str | None
    visible_agents: list[str] = Field(default_factory=list)
    heard_events: list[PerceivedEvent] = Field(default_factory=list)
    ambient: AmbientInfo


# The one event type that is a person speaking to another person. Named
# here rather than typed inline twice: perception is the only place that
# has to tell "said to you" apart from every other thing that involves you.
UTTERANCE_EVENT = "agent.utterance.spoken"

# How long a question addressed to you keeps being put back in front of you
# while you have not answered it.
#
# 30 ticks is five game-hours. A question does not stop being open because
# the person asked happened to be busy on the turn it arrived, and one
# delivery is exactly one chance to notice it - measured, agents took that
# chance about a third of the time. Beyond this, it lapses: an unanswered
# question from half a day ago is no longer a live obligation, and a
# perception that accumulated every one of them forever would crowd out the
# present.
UNANSWERED_LOOKBACK_TICKS = 30


def _addressees_of(payload: "dict[str, Any] | None") -> set[str]:
    """Everyone one remark named.

    CORE-15. `say` records a primary `listener_agent_id` AND the full
    `addressed_agent_ids` list, and the town had two definitions of "said
    to you": this module read the first, `WorldState.open_questions` read
    both. Two mechanisms scoring one question is how a resident ended up
    being shown something it had already answered.

    One rule, used by both directions here - who was asked, and who has
    answered - so the question cannot be open by one measure and closed by
    the other.
    """
    if not payload:
        return set()
    named = {str(x) for x in (payload.get("addressed_agent_ids") or []) if x}
    listener = payload.get("listener_agent_id")
    if listener:
        named.add(str(listener))
    return named


class PerceptionSystem:
    """Builds per-agent Perception snapshots and drives scheduler interrupts.

    hearing_window_ticks controls how far back "recently heard" reaches;
    the default of 1 means only events published in the current tick
    (DESIGN.md's "at its location each tick" is a per-tick snapshot, not
    a standing memory - memory is a separate, agent-side concern).

    Ambient time-of-day is derived from the `tick` argument passed to
    build_perception() via clock.timestamp_for(), not from a live
    GameClock reference - a Perception is always for a specific tick
    (which may not be "now" when replaying or testing), so there is
    nothing a stored clock would add.
    """

    def __init__(
        self,
        locations: LocationIndex,
        event_bus: EventBus,
        interrupt_policy: InterruptPolicy = default_interrupt_policy,
        hearing_window_ticks: int = 1,
    ) -> None:
        if hearing_window_ticks < 1:
            raise ValueError(
                f"hearing_window_ticks must be >= 1, got {hearing_window_ticks}"
            )
        self.locations = locations
        self.event_bus = event_bus
        self.interrupt_policy = interrupt_policy
        self.hearing_window_ticks = hearing_window_ticks
        # The last tick each agent was actually given a perception for.
        #
        # Only build_perception() writes it, and the one production caller
        # (core/engine.py's tick loop) builds perceptions solely for the
        # agents the scheduler says are due THIS tick - so this advances
        # once per turn taken, which is exactly what "until you next think"
        # has to mean for it to be true.
        self._last_perceived: dict[str, int] = {}

    def attach(self, scheduler: Any) -> None:
        """Subscribes to every domain ("*") so any world/ submodule's event
        can interrupt the agent(s) it names, per the payload key
        convention in this module's docstring. `scheduler` is typed Any
        to avoid an import-time dependency on scheduler.py's concrete
        class; it only needs an `interrupt(agent_id: str) -> None`
        method (AgentScheduler satisfies this)."""

        async def handle(event: WorldEvent) -> None:
            for agent_id in self.interrupt_policy(event):
                scheduler.interrupt(agent_id)

        self.event_bus.subscribe("*", handle)

    def build_perception(self, agent_id: str, tick: int) -> Perception:
        """Builds one agent's Perception for the given tick. Safe to call for
        any agent_id, registered with the scheduler or not - perception
        and scheduling are independent concerns wired together only via
        attach()."""
        location_id = self.locations.location_of(agent_id)
        visible_agents = (
            sorted(a for a in self.locations.agents_at(location_id) if a != agent_id)
            if location_id is not None
            else []
        )

        window_start = max(0, tick - self.hearing_window_ticks + 1)

        # Something said TO you waits until you next think. Ambient noise
        # does not.
        #
        # The window is measured in ticks; an agent's turns are not. At the
        # default cadence a resident acts on roughly one tick in three, but
        # MEASURED on a live ten-resident run the gap between one agent's
        # turns ran to a median of 33 ticks and a maximum of 207 - every
        # single gap longer than the four-tick window. So a remark expired
        # before the person it was aimed at ever thought again, and only 7
        # of 615 perceptions in that run carried a remark at all.
        #
        # What that looked like from inside: Ainsley told Perrin "Perrin.
        # Walk home. Tell Bram." while the two of them stood alone together
        # in the pharmacy. Perrin's next perception said "With you, and
        # nobody else: Ainsley Holloway" and, directly underneath,
        # "Nothing notable happened recently." He was never told she had
        # spoken. Nobody ever replied to anybody, and the reason was not
        # that they would not - it was that they were never asked.
        #
        # Overheard chatter still expires on the window: you do not walk
        # into a room and hear yesterday's gossip. Being addressed by name
        # is different, and is what "Said to you, and not yet answered"
        # has always claimed to mean.
        last_seen = self._last_perceived.get(agent_id)
        scan_start = window_start
        if last_seen is not None:
            scan_start = min(scan_start, last_seen + 1, max(0, tick - UNANSWERED_LOOKBACK_TICKS))
        scan_start = max(0, scan_start)

        candidates = self.event_bus.recent(since_tick=scan_start)

        # When this agent last said something back to each person, so a
        # question it HAS answered stops being put in front of it.
        answered_at: dict[str, int] = {}
        for event in candidates:
            payload = event.payload or {}
            if event.type != UTTERANCE_EVENT:
                continue
            if payload.get("speaker_agent_id") != agent_id:
                continue
            # CORE-15 / EVAL-11. Everyone this reply named, not just its
            # primary listener.
            #
            # `WorldState.open_questions` has always read
            # `addressed_agent_ids`; this loop read `listener_agent_id`
            # alone, so the two mechanisms disagreed about whether a
            # question had been answered. A reply naming two people closed
            # the question for one of them and left it open for the other,
            # and the resident was handed a question it had already
            # answered - which reads, from the outside, as a resident
            # ignoring somebody.
            #
            # Run 23 carried more than one addressee on 172 of 229
            # utterances, so this was the common case rather than an edge.
            for spoken_to in _addressees_of(payload):
                answered_at[spoken_to] = max(answered_at.get(spoken_to, -1), event.tick)

        heard: list[PerceivedEvent] = []
        for event in candidates:
            involved = self.interrupt_policy(event)
            involves_self = agent_id in involved
            event_location = event.payload.get("location_id")
            heard_here = location_id is not None and event_location == location_id
            in_window = event.tick >= window_start
            # Addressed to me, by someone else, and not yet delivered. The
            # speaker check matters: involved_agent_ids carries the speaker
            # too, and an agent must not be handed back its own remarks.
            speaker = event.payload.get("speaker_agent_id")
            # CORE-15: named by the remark, by any of the ways a remark can
            # name somebody. `format_addressed_to_you` and
            # `state.open_questions` both already count every addressee;
            # this counted only the first, so a remark aimed at three
            # residents reached the second and third as background chatter
            # under "Recently" rather than as a question they were asked.
            addressed_to_me = (
                event.type == UTTERANCE_EVENT
                and involves_self
                and speaker != agent_id
                and agent_id in _addressees_of(event.payload)
            )
            # Never yet put in front of me. This is the guarantee that
            # matters most and it has NO age limit: the measured gap between
            # one agent's turns ran to a median of 33 ticks and a maximum of
            # 207, so any cutoff short of that silently reinstates the
            # original bug - a remark expiring before its listener ever
            # thought again.
            never_delivered = addressed_to_me and (
                last_seen is None or event.tick > last_seen
            )
            # Already seen, still open: I have said nothing back to them
            # since. "Not yet answered" is a claim the perception makes in
            # those words, and it should stay true for as long as it is made -
            # bounded, so a question from half a day ago lapses instead of
            # crowding out the present.
            still_unanswered = (
                addressed_to_me
                and event.tick > answered_at.get(str(speaker), -1)
                and (tick - event.tick) <= UNANSWERED_LOOKBACK_TICKS
            )
            if (heard_here or involves_self) and (
                in_window or never_delivered or still_unanswered
            ):
                heard.append(
                    PerceivedEvent(
                        type=event.type,
                        tick=event.tick,
                        payload=event.payload,
                        involves_self=involves_self,
                        for_agent_id=agent_id,
                    )
                )

        self._last_perceived[agent_id] = tick

        timestamp = timestamp_for(tick)
        ambient = AmbientInfo(
            game_day=timestamp.game_day,
            hour=timestamp.hour,
            minute=timestamp.minute,
            is_daytime=timestamp.is_daytime,
        )

        return Perception(
            agent_id=agent_id,
            tick=tick,
            location_id=location_id,
            visible_agents=visible_agents,
            heard_events=heard,
            ambient=ambient,
        )
