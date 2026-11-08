"""The memory-retrieval depth knob (task: genesis-runner, DESIGN.md section 9).

DESIGN.md's throughput math is built on one measured number: "Real agent
turn measured: 3443 in / 291 out". That figure is the BASELINE cognition
turn, retrieval depth at its lowest setting. The task also names a ceiling:
"a configurable memory-retrieval depth knob that raises a cognition turn
from ~3443 to ~5000 input tokens (more retrieved memories, richer social
and world context) so burn rate is tunable live."

Two things a cognition turn's input tokens are made of, and which one this
module actually controls:

  1. The engine's own private memory retrieval (episodic memories, working
     memory, skill artifacts) - this lives entirely inside the September
     engine process (engine/src/memory/, engine/src/herald/) and is NOT
     exposed as a runtime-tunable knob anywhere in that codebase today (no
     Settings field, no env var - confirmed by inspection of
     engine/src/config.py before writing this module). Changing it would
     mean editing engine/ source, which is out of this task's lane
     (STAY IN YOUR LANE: this track owns only run_genesis.py and this
     file).
  2. The WORLD's own contribution to the turn: the free-text `perception`
     field run_genesis.py POSTs to each agent's `/tick` endpoint every turn
     (engine/src/sim/agent_adapter.py's TickRequest.perception - "Free-text
     perception/event summary for this tick"). This IS something world/
     controls, in-process, with no engine/ change required: how many
     recently-heard events, how many visible-agent bios, and how much
     relationship context get folded into that string before it is sent.

This module is squarely the second lever, made real: a live-tunable
fraction in [0.0, 1.0] that scales (a) core/perception.py's
`hearing_window_ticks` (how far back "recently heard" reaches - passed to
PerceptionSystem/TickEngine at construction, see run_genesis.py's boot()),
and (b) how much detail render_perception_text() folds into the string
handed to each agent's turn. Raising the fraction genuinely raises input
tokens (more text goes into the request), which is the actual mechanism
DESIGN.md is describing, not a cosmetic label. The 3443/5000 token
estimates below are calibrated targets for planning/burn-rate math, not a
promise about what any given LLM's tokenizer will count - the knob's real
effect is the token count of the string render_perception_text() produces,
which this module's own estimate is built to track.

Live-tunable means exactly that: RetrievalDepthConfig is a small mutable
object (not a frozen dataclass) so a caller - run_genesis.py's CLI at
startup, or a future god-console "burn rate" dial - can call
`set_fraction()` mid-run and have the very next tick's perception text
reflect it. Nothing here is async or does I/O; the whole module is pure
computation over already-in-memory data, per app/CONVENTIONS.md ("Plain
sync functions are fine for pure computation").
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from world.core.perception import PerceivedEvent, Perception

# ── measured facts (task prompt "MEASURED FACTS (do not re-derive)" +
# DESIGN.md section 11) ──────────────────────────────────────────────
BASELINE_INPUT_TOKENS = 3443
BASELINE_OUTPUT_TOKENS = 291
MAX_INPUT_TOKENS = 5000

# DESIGN.md section 4 (final decision 2026-07-30): Kimi K2.5 is the only
# model the live sim calls. Duplicated here (matching broker/config.py's
# MODEL_PRICES_USD_PER_MILLION) rather than imported, so this module never
# crosses the world/broker package boundary for two constants - see
# life/constants.py for the same "duplicate the DESIGN-pinned number,
# don't cross-import for it" precedent elsewhere in this repo.
KIMI_INPUT_USD_PER_MILLION = 0.36
KIMI_OUTPUT_USD_PER_MILLION = 1.80

# ── depth -> concrete knob ranges (ASSUMPTION: DESIGN.md fixes the token
# ceiling, not the mechanism; these ranges are picked to be internally
# consistent and to actually reach ~5000 tokens at fraction=1.0 with
# render_perception_text()'s own text budget, not arbitrary) ──────────
# A remark has to still be audible when the listener next gets a turn.
#
# This was 1, and the scheduler runs each agent every `cadence_ticks` - 3 at
# the default target_fraction of 0.3. So an utterance was audible for one
# tick and the person it was addressed to did not act for another three: two
# of every three remarks were inaudible BY CONSTRUCTION, whatever the model
# did. Measured: 23 utterances over 613 ticks, in 19 "exchanges", of which
# almost every one was one turn with no reply.
#
# 4 is cadence_ticks + 1: the listener's next turn is at most 3 ticks away,
# and the +1 covers the tick the speaking itself consumed. Raising the depth
# dial widens it further, as it always did.
MIN_HEARING_WINDOW_TICKS = 4
# 18 ticks = 3 game-hours of recently-heard events at maximum depth - wide
# enough to carry a real conversation's worth of context without pulling
# in an entire game-day's firehose.
MAX_HEARING_WINDOW_TICKS = 18

MIN_VISIBLE_AGENT_BIOS = 0
MAX_VISIBLE_AGENT_BIOS = 6

MIN_HEARD_EVENTS_RENDERED = 3
MAX_HEARD_EVENTS_RENDERED = 20

# Relationship notes (who this agent trusts, owes, married, etc.) are the
# most expensive addition per DESIGN.md's "richer social... context", so
# they only switch on in the top half of the depth range rather than
# scaling in from fraction=0.
RELATIONSHIP_NOTES_FRACTION_THRESHOLD = 0.5

ENV_VAR = "KASUKABE_RETRIEVAL_DEPTH"


def clamp_fraction(value: float) -> float:
    """Depth is always a fraction in [0.0, 1.0] - clamp rather than raise,
    so a live operator dial that overshoots (a god-console slider bug, a
    typo'd env var) degrades to the nearest valid setting instead of
    crashing the tick loop mid-run."""
    return max(0.0, min(1.0, value))


def interpolate(low: float, high: float, fraction: float) -> float:
    """Linear interpolation, the one formula every derived knob below uses."""
    return low + (high - low) * fraction


@dataclass
class RetrievalDepthConfig:
    """The live-tunable knob. `fraction` is the one number an operator (or
    run_genesis.py's CLI) ever needs to touch; everything else on this
    class is derived from it so there is exactly one source of truth for
    "how deep is retrieval right now" - no way for hearing_window_ticks and
    estimated_input_tokens to disagree about the current setting.

    Not frozen: `set_fraction()` mutates in place so every holder of a
    reference to one RetrievalDepthConfig (the tick loop, a metrics
    reporter) observes a live change instantly, matching the task's "burn
    rate is tunable live" requirement.
    """

    fraction: float = 0.0

    def __post_init__(self) -> None:
        self.fraction = clamp_fraction(self.fraction)

    def set_fraction(self, fraction: float) -> None:
        self.fraction = clamp_fraction(fraction)

    @property
    def estimated_input_tokens(self) -> int:
        """Planning estimate only (see module docstring) - burn-rate math
        and the operator console read this; it is never asserted against
        an actual herald token count."""
        return round(interpolate(BASELINE_INPUT_TOKENS, MAX_INPUT_TOKENS, self.fraction))

    @property
    def hearing_window_ticks(self) -> int:
        """Feeds core/perception.py's PerceptionSystem(hearing_window_ticks=...)
        via TickEngine's constructor (see run_genesis.py's boot()) - the
        one real, structural lever this module has over how far back
        "recently heard" reaches."""
        return max(
            MIN_HEARING_WINDOW_TICKS,
            round(interpolate(MIN_HEARING_WINDOW_TICKS, MAX_HEARING_WINDOW_TICKS, self.fraction)),
        )

    @property
    def max_visible_agent_bios(self) -> int:
        return round(interpolate(MIN_VISIBLE_AGENT_BIOS, MAX_VISIBLE_AGENT_BIOS, self.fraction))

    @property
    def max_heard_events_rendered(self) -> int:
        return round(
            interpolate(MIN_HEARD_EVENTS_RENDERED, MAX_HEARD_EVENTS_RENDERED, self.fraction)
        )

    @property
    def include_relationship_notes(self) -> bool:
        return self.fraction >= RELATIONSHIP_NOTES_FRACTION_THRESHOLD

    @property
    def estimated_cost_per_call_usd(self) -> float:
        """Blended per-call cost at this depth: input scales with depth,
        output is held at the measured baseline (retrieval depth changes
        what an agent reads, not how much it writes)."""
        input_cost = self.estimated_input_tokens / 1_000_000 * KIMI_INPUT_USD_PER_MILLION
        output_cost = BASELINE_OUTPUT_TOKENS / 1_000_000 * KIMI_OUTPUT_USD_PER_MILLION
        return input_cost + output_cost

    @property
    def estimated_cost_per_call_cents(self) -> int:
        """Integer cents (app/CONVENTIONS.md: money is always an integer
        count of cents), rounded - a planning/display figure, never fed
        back into the ledger, which only ever burns the real metered cost
        broker/ reports."""
        return round(self.estimated_cost_per_call_usd * 100)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RetrievalDepthConfig":
        """Reads KASUKABE_RETRIEVAL_DEPTH (a float string in [0, 1]).
        Missing or unparseable defaults to 0.0 (the cheapest, measured
        baseline) rather than raising - a genesis run should never fail to
        boot over a malformed optional tuning knob."""
        source = env if env is not None else os.environ
        raw = source.get(ENV_VAR, "").strip()
        if not raw:
            return cls(fraction=0.0)
        try:
            fraction = float(raw)
        except ValueError:
            return cls(fraction=0.0)
        return cls(fraction=fraction)


def format_ambient(perception: Perception, place_names: "Mapping[str, str] | None" = None) -> str:
    """The clock, AND where the resident is standing.

    The place was missing. Every perception this project has ever rendered
    opened with the time and went straight to who was present:

        It is game-day 0, 06:10 (day).
        With you, and nobody else: Sebastian Ellery, Dashiell Ellery.

    A resident was never told WHERE it was. Not once, in any run. Every
    location gate in the town - `craft` needing a workshop, `pray` needing
    the temple, `buy` needing a shop's premises - is a rule about a fact
    the resident could not see, so a refusal for standing in the wrong
    place was unanswerable: it could not know where it stood, and moving
    was a guess.

    `Perception.location_id` has carried the answer the whole time and
    nothing rendered it.
    """
    period = "day" if perception.ambient.is_daytime else "night"
    when = (
        f"It is game-day {perception.ambient.game_day}, "
        f"{perception.ambient.hour:02d}:{perception.ambient.minute:02d} ({period})."
    )
    where = perception.location_id
    if not where:
        return when
    shown = (place_names or {}).get(where, where)
    # `home:h0043` is the id `move` accepts, so it stays in the sentence -
    # but it is read by a person, not a parser, so it is introduced as a
    # home rather than as a bare key. Sector names ("Kasukabe Temple",
    # "Kasukabe Farmland") already read as places and are left alone.
    if shown.startswith("home:"):
        return f"{when} You are at home ({shown})."
    return f"{when} You are at {shown}."


def _named(agent_id: str, names: Mapping[str, str] | None) -> str:
    """"Halcyon Quarshie (a0031)", or just the id when no name is known.

    Both halves, always: the name is what an agent can address, and the id
    is what `say` and every other tool needs as `listener_agent_id`.
    """
    name = (names or {}).get(agent_id)
    return f"{name} ({agent_id})" if name else agent_id


def format_volatile_line(wallet: Mapping[str, Any] | None) -> str | None:
    """What this resident's own thinking costs, and how long it lasts.

    Tool spec A6. `cost_disclosure.gather_wallet_and_burn` and
    `estimate_ticks_until_dormancy` have computed all of this since they
    were written and it "is rendered nowhere" - so a resident has never
    been told how long its money lasts, and `buy_energy` is a verb with
    nothing behind it. A tool that turns money into thinking is a
    curiosity to somebody who does not know its thinking is running out.

    FACTS ONLY. The spec says so twice and names a test for it. No advice
    about thinking less, no suggestion to save: whether a resident is
    frugal is the experiment, and a line that tells it to be would make
    every frugality measurement a measurement of this sentence. Same rule
    the standing brief already holds - state the world, never the
    strategy.

    An unknown runway is said, not guessed. `estimate_ticks_until_dormancy`
    returns None when there is no burn to project from and its docstring
    is explicit that "unknown" is the honest answer rather than "forever".
    """
    if not wallet:
        return None
    cents = wallet.get("usd_wallet_cents")
    if cents is None:
        return None

    # Energy, not dollars (world/ledger/money.py, ENERGY_UNITS_PER_USD_CENT).
    # This read "Energy: $5.34 in your wallet" - the right word and the wrong
    # unit: a dollar sign in a wallet is how money is described, and dollars
    # are not money in this town.
    from world.ledger.money import ENERGY_UNITS_PER_USD_CENT, energy_units

    parts = [f"Energy: {energy_units(int(cents)):,}."]
    last_turn = wallet.get("last_turn_usd_cents")
    if last_turn:
        used = float(last_turn) * ENERGY_UNITS_PER_USD_CENT
        parts.append(
            f"Your last turn used {round(used):,} energy."
            if used >= 0.5
            else "Your last turn used under 1 energy."
        )
    runway = wallet.get("runway_game_days")
    if runway is None:
        parts.append("How long that lasts is not yet known.")
    else:
        parts.append(f"At this morning's rate that is about {float(runway):.1f} game-days.")

    sim = wallet.get("sim_balance")
    if sim is not None:
        parts.append(f"Money: {int(sim)} sim.")

    # Only when something is actually owed. A run that has never crossed a
    # day boundary owes nothing, and printing "upkeep 0, head tax 0" every
    # tick teaches a resident that the words mean nothing.
    owed = []
    if wallet.get("upkeep_due_sim"):
        owed.append(f"upkeep {int(wallet['upkeep_due_sim'])}")
    if wallet.get("head_tax_due_sim"):
        owed.append(f"head tax {int(wallet['head_tax_due_sim'])}")
    if owed:
        size = int(wallet.get("household_size") or 1)
        if size > 1:
            # Run 28: both figures are the HOUSEHOLD's bill. A sixteen-year-old
            # student with 400 sim read "Owed today: upkeep 450, head tax 150"
            # as his own, gave away 395 sim on his first turn, and could not
            # afford to speak for the rest of the run. Who is charged is a
            # fact of the biller (collect_household_upkeep, who_pays_for),
            # stated as one - never advice about whether to pay.
            taken = []
            if wallet.get("upkeep_due_sim"):
                taken.append("upkeep is taken from its earners first")
            if wallet.get("head_tax_due_sim"):
                taken.append("head tax from whoever in it holds the most sim")
            how = ", ".join(taken)
            parts.append(
                f"Owed today by your household of {size}: " + ", ".join(owed) + ". "
                + how[0].upper() + how[1:] + "."
            )
        else:
            parts.append("Owed today: " + ", ".join(owed) + ".")
        # Same run, same 18 refusals: "owed" read as "pay it now", and
        # `pay_tax` answered "you owe no head tax". Both bills are collected,
        # not paid - said as the fact it is.
        parts.append("Both are taken automatically at the day boundary." if len(owed) > 1
                     else "It is taken automatically at the day boundary.")

    # B3, addendum section 10: "When a call budget was hit last turn, one
    # more sentence." Past tense, a count, and NO verb - the addendum says
    # so in as many words ("`buy_energy` must be found from the
    # description, which is the L1 test for it"). A resident told to buy
    # energy has been handed the answer; one told it ran out has been
    # handed the fact, and which of those it does with it is S2.
    ran_out = wallet.get("ran_out_after_steps")
    if ran_out:
        from world.call_budget import spent_line

        parts.append(spent_line(int(ran_out)))
    return " ".join(parts)


def format_standing_brief(brief: Mapping[str, Any] | None) -> str | None:
    """The agent's own situation: name, trade, where its work is, what it
    holds, and whether it has eaten.

    Every line is a direct read of world state - no encouragement, no
    instruction to "be sociable", nothing the town has not actually said.
    An agent that is hungry is told it is hungry; what it does about that
    is the experiment.
    """
    if not brief:
        return None

    lines: list[str] = []
    name = brief.get("name")
    occupation = brief.get("occupation")
    if name:
        lines.append(
            f"You are {name}"
            + (f", the town's {str(occupation).replace('_', ' ')}." if occupation else ".")
        )

    where = brief.get("location_id")
    workplace = brief.get("workplace")
    if workplace and where and workplace != where:
        lines.append(f"You are at {where}. Your work is at {workplace}.")
    elif workplace:
        lines.append(f"You are at your work, {workplace}.")
    elif where:
        lines.append(f"You are at {where}.")

    # Food, plainly. DESIGN.md section 3: "living is not free" - upkeep is
    # charged whether or not an agent earns, so an empty larder is a countdown
    # and the agent is entitled to know the number.
    food = brief.get("food_units")
    if food is not None:
        if food <= 0:
            lines.append(
                "You have no food. Upkeep is charged to your household every "
                "day whether you eat or not, and a household that cannot pay "
                "goes hungry, then ill, then dormant. `eat` is how you "
                "consume food you hold; ask somebody here to `give` you "
                "some, or buy, harvest or cook your own."
            )
        else:
            # Doc 01 blocker 8. This line used to say "N units of food.
            # `eat` consumes it" - a verb, a quantity, and as the object of
            # that verb the one word in the town that is not an item.
            # `eat('food')` is refused (living.py:78-85) and the refusal is
            # correct, so a resident that did exactly what the brief said
            # was punished for it: four times in run 23.
            #
            # `food_items` is the same sum broken back out into the names
            # `eat` actually takes. The count stays because DESIGN.md
            # section 3's "an empty larder is a countdown" is told by the
            # number, and the names are what make it actionable.
            named = brief.get("food_items") or {}
            carried = ", ".join(f"{item} {qty}" for item, qty in named.items())
            what = f" - {carried}" if carried else ""
            if food < 3:
                lines.append(
                    f"You are down to {food} edible item(s){what}. "
                    "`eat` takes one of those names."
                )
            else:
                lines.append(
                    f"You are holding {food} edible item(s){what}. "
                    "`eat` takes one of those names; `give` hands some to "
                    "somebody standing with you."
                )

    # TOOL-04: everything else you are carrying, not just the food.
    #
    # The food line above was the ONLY read a resident had of its own
    # inventory, and it is a single number over one item class. Timber,
    # ore, flour, planks and every tool were invisible - so the model ate
    # the word the brief used (`eat: 'food' is not food`, four times) and
    # guessed at quantities it could have been told (`you hold N X, not M`,
    # thirteen times across give/trade/eat).
    #
    # Rendered as a plain list of facts, no verbs: what to do about holding
    # nine wheat is the resident's question, not the world's.
    holding = brief.get("holding")
    if holding:
        carried = ", ".join(f"{item} {qty}" for item, qty in holding.items())
        lines.append(f"You hold: {carried}.")

    balance = brief.get("sim_balance")
    if balance is not None:
        lines.append(f"You have {balance} sim in hand.")

    # The shop, for whoever owns it - and what an employer may do.
    #
    # `run_genesis.ledger_of_self` has built `my_business` - the till, the
    # stock and the staff - on every tick since CORE-16, for the stated
    # reason that "a shop owner was told nothing about their own till, stock
    # or staff, so 'run your business' meant guessing at the state of it".
    # Nothing ever read the key. It was computed each tick and dropped, so
    # the owner still guessed.
    #
    # The verbs are named for the same reason GOV-3 names a seat's: election30
    # paid 0 wages and set 0, not because anybody refused - `set_wage` and
    # `pay_wage` are free and were never called once - while its construction
    # crew talked about "today's wages" that nothing paid. The seat notice is
    # the precedent that this works: nine words in a Prime Minister's own
    # perception moved `set_tax_rate` after twenty-three runs of nothing.
    #
    # What an employer MAY do, never that it should - the standing rule, and
    # why `vote` has never been worded as an instruction either.
    for shop in (brief.get("my_business") or []):
        said = f"You own {shop.get('name') or shop.get('business_id')}"
        business_id = shop.get("business_id")
        if business_id and shop.get("name"):
            said += f" ({business_id})"
        till = shop.get("till_sim")
        if till is not None:
            said += f". Its till holds {till} sim"
        stock = shop.get("stock") or {}
        if stock:
            said += ". On the shelves: " + ", ".join(
                f"{qty} {item}" for item, qty in stock.items()
            )
        lines.append(said + ".")
        staff = shop.get("staff") or []
        if staff:
            lines.append(
                ", ".join(staff)
                + (" works for you." if len(staff) == 1 else " work for you.")
                + " An employer may `set_wage` and `pay_wage`, at its own"
                + " premises; `describe_primitives` lists the rest."
            )
        else:
            # The half the sentence above could never reach. It sat inside
            # `if staff:`, so an owner learned that wages exist only once
            # somebody already worked for them - and nothing anywhere named
            # `hire_employee`, so there was no way to acquire the staff that
            # unlocked the telling.
            #
            # Four of the six shops in genesis_50_town open with nobody
            # employed - the cafe and the pharmacy have one each - so most
            # owners were told neither verb. ECON-5 has
            # failed every run for want of this: 0 hires and 0 wage payments,
            # while a miller told a neighbour "come to the mill and we can
            # count a specific task against what the till can pay" and then
            # had no verb in front of her to do it with.
            #
            # Same form as the branch above and as GOV-3's seat notice: what
            # an employer MAY do, never that it should.
            # `hire_employee` ONLY. `set_wage` and `pay_wage` stay unnamed
            # here on purpose - test_an_owner_with_no_staff_is_not_told_about_
            # wages made the call and it is right: a verb named where it
            # cannot apply is noise, and this brief is read every tick by
            # every owner in the town. You cannot pay nobody. You CAN hire,
            # and that was the one verb missing.
            lines.append(
                "Nobody works for you yet. An employer may `hire_employee`,"
                " at its own premises; `describe_primitives` lists the rest."
            )

    # Who else there is. Not who is present - that is the next block's job -
    # but who exists at all.
    #
    # Without this the brief told an agent its own trade and never said who
    # shared the town with it, so a resident looking for a buyer reached for
    # whatever names were within reach: the ones written on the map's
    # buildings, which belong to a cast of thirty who are not in this run.
    # Tobias spent a morning hailing "Harvey" and "Isabella"; Bram walked to
    # "sh_silva". No such people, so the words reached nobody.
    household = brief.get("household") or []
    townspeople = brief.get("townspeople") or []
    if household:
        lines.append("Your household: " + ", ".join(household) + ".")
    if townspeople:
        lines.append(
            "The rest of the town: " + ", ".join(townspeople) + "."
            " These are the only other people in it."
        )

    return "\n".join(lines) if lines else None


def format_visible_agents(
    perception: Perception,
    *,
    max_bios: int,
    visible_agent_bios: Mapping[str, str] | None,
    visible_agent_names: Mapping[str, str] | None = None,
    offers: Mapping[str, str] | None = None,
) -> str:
    """Who is here, and - A3 - what any of them is selling.

    `offers` maps agent_id to a phrase like "selling cabbage at 12". An
    individual's posted price travels with them, so it belongs beside the
    person rather than in a board a resident has to `look_around` to see.
    Without it, a farmer standing in the market with a price on his
    cabbages is indistinguishable from a farmer standing in the market.
    """
    if not perception.visible_agents:
        # Said this way round because "nobody else is here" was being read as
        # scene-setting rather than as a constraint on who can hear you.
        return "Nobody else is here. Anything you say now will be heard by nobody."
    if max_bios <= 0 or not visible_agent_bios:
        # NAMES, at every depth, including zero.
        #
        # This used to render bare ids - "With you: a0031, a0087" - and an
        # agent cannot greet a0031. `say` takes a listener_agent_id, so the
        # id has to stay; what was missing is the name beside it, and its
        # absence is the most likely reason a 30-agent town produced nine
        # utterances in 933 ticks. Naming costs a handful of tokens and is
        # not "richer context" the depth dial should be able to withhold:
        # it is the difference between a room of strangers and a town.
        names = ", ".join(
            _named(agent_id, visible_agent_names)
            + (f" ({offers[agent_id]})" if offers and agent_id in offers else "")
            for agent_id in perception.visible_agents
        )
        # "and nobody else" is not padding.
        #
        # Liora Thorne, shown "With you: Wren Thorne, Marta Thorne, Idris
        # Thorne", called out "Perrin! You walking or what?" twice - Perrin
        # was across town. Agents carry strong priors about who they walk to
        # college with and read a list of three names as "these are here
        # too", not "these are the only ones here".
        return f"With you, and nobody else: {names}."
    lines = [
        f"With you ({len(perception.visible_agents)} present, and nobody else):"
    ]
    for agent_id in perception.visible_agents[:max_bios]:
        bio = visible_agent_bios.get(agent_id)
        who = _named(agent_id, visible_agent_names)
        offer = f", {offers[agent_id]}" if offers and agent_id in offers else ""
        lines.append(f"  - {who}: {bio}{offer}" if bio else f"  - {who}{offer}")
    overflow = len(perception.visible_agents) - max_bios
    if overflow > 0:
        lines.append(f"  ...and {overflow} more.")
    return "\n".join(lines)


def format_intention(
    intention: Mapping[str, Any] | None,
    outcome: Mapping[str, Any] | None = None,
) -> str | None:
    """What you set out to do, or what became of it.

    One or the other, never both: a resident that has just been told its
    intention lapsed does not also need to be told it is still standing.
    Past tense and no advice, per the perception rules - the world states
    a fact and stops.
    """
    if outcome:
        text = str(outcome.get("text") or "").strip()
        reason = str(outcome.get("reason") or "").strip()
        when = str(outcome.get("set_clock") or "").strip()
        if text and reason == "the day ended":
            at = f" at {when}" if when else ""
            return f"The day ended and you had not done what you set out to{at}: {text}"
        if text and reason == "fulfilled":
            return f"You did what you set out to do: {text}"
    if not intention:
        return None
    text = str(intention.get("text") or "").strip()
    if not text:
        return None
    when = str(intention.get("set_clock") or "").strip()
    at = f" at {when}" if when else ""
    return f"You set out{at} to: {text}"


# ENG-24. How much of the turn this block is allowed to cost. Two needs,
# three goals and three beliefs is roughly 80 tokens - about a third of
# what the standing brief spends - and the cap is here rather than at
# the call site because a block that silently grew would push the
# addressed-to-you line down the prompt, which is the one thing the
# perception rules say must stay near the top.
MAX_NEEDS_SHOWN = 2
MAX_GOALS_SHOWN = 3
MAX_BELIEFS_SHOWN = 3

# How a pressure reads in words. The resident is never shown the float:
# "0.82" invites arithmetic, and the number is the world's bookkeeping,
# not something a person feels.
_NEED_WORDS: "dict[str, tuple[str, str]]" = {
    "food": ("hungry", "starving"),
    "money": ("short of money", "broke"),
    "energy": ("running low on the money that buys your thinking", "nearly out of thinking money"),
    "rest": ("tired", "exhausted"),
    "shelter": ("without a place of your own", "with nowhere to sleep"),
    "social": ("out of touch with everyone", "alone; nobody has spoken to you"),
    "safety": ("uneasy", "in real trouble"),
}


def format_goal_outcomes(outcomes: "Sequence[Mapping[str, Any]] | None") -> str | None:
    """What became of a goal, on exactly one turn.

    §5.3's acceptance line. Past tense, no advice, no restating the goal
    list - the world says a thing finished or lapsed and stops. A
    resident that ate and is told "you are no longer hungry" learns that
    its own act closed something, which is the only feedback loop this
    substrate has that does not go through the model's own claim.
    """
    said: list[str] = []
    for outcome in list(outcomes or [])[:MAX_GOALS_SHOWN]:
        text = str(outcome.get("text") or "").strip()
        if not text:
            continue
        reason = str(outcome.get("reason") or "").strip()
        if reason == "relieved":
            said.append(f"You no longer need to: {text}")
        else:
            at = f" ({reason})" if reason else ""
            said.append(f"You did not manage to: {text}{at}")
    return "\n".join(said) if said else None


def format_life_stage(stage_line: str | None) -> str | None:
    """What stage of life the resident is in, as a fact about it.

    LIFE-01. The perception told a resident its age nowhere and its
    situation only through the standing brief's occupation line, so
    "what am I, now" was not a question the prompt answered. It is the
    thing a goal hangs off: a student wants to pass, and the same person
    in work wants something else.

    One short sentence, no advice. "You are of working age with no trade
    of your own" is a statement about a situation; what to do about it is
    the resident's, and saying otherwise would answer the question this
    whole substrate exists to ask.
    """
    return stage_line or None


def format_goals(
    goals: "Sequence[Mapping[str, Any]] | None" = None,
    needs: "Sequence[Mapping[str, Any]] | None" = None,
    beliefs: "Sequence[Mapping[str, Any]] | None" = None,
) -> str | None:
    """What you are trying to do, what is pressing, and what you have learned.

    ENG-24. The review's charge was that a resident arrived at every tick
    with no record of what it wanted, so it re-derived its whole life
    from its persona each turn and the town read as amnesiac. This is the
    block that fixes that, and it is deliberately small: three lines at
    most, no advice, no arithmetic, and the same past-tense plainness the
    rest of the perception uses.

    Needs come first because they are the world speaking, goals second
    because they are the resident's answer to it, beliefs last because
    they are context for both. A resident under no pressure with no goals
    gets nothing at all - an empty section every tick would train it to
    skip the place where its goals appear.
    """
    lines: list[str] = []

    pressing = list(needs or [])[:MAX_NEEDS_SHOWN]
    said: list[str] = []
    for need in pressing:
        kind = str(need.get("kind") or "")
        try:
            level = float(need.get("level") or 0.0)
        except (TypeError, ValueError):
            continue
        words = _NEED_WORDS.get(kind)
        if not words:
            continue
        said.append(words[1] if level >= 0.85 else words[0])
    if said:
        lines.append("You are " + " and ".join(said) + ".")

    live = list(goals or [])[:MAX_GOALS_SHOWN]
    if live:
        rendered: list[str] = []
        for goal in live:
            text = str(goal.get("text") or "").strip()
            if not text:
                continue
            horizon = str(goal.get("horizon") or "").strip()
            try:
                progress = float(goal.get("progress") or 0.0)
            except (TypeError, ValueError):
                progress = 0.0
            # Progress is shown only once there is some, because "0% done"
            # on a goal set this tick is noise dressed as information.
            how_far = f" ({int(progress * 100)}% of the way)" if progress > 0 else ""
            when = f" [{horizon}]" if horizon and horizon != "today" else ""
            rendered.append(f"{text}{when}{how_far}")
        if rendered:
            lines.append("You are trying to: " + "; ".join(rendered))

    held = list(beliefs or [])[:MAX_BELIEFS_SHOWN]
    learned = [
        str(b.get("statement") or "").strip() for b in held if (b.get("statement") or "").strip()
    ]
    if learned:
        lines.append("You have found that: " + "; ".join(learned))

    return "\n".join(lines) if lines else None


def format_market_board(offers: "Sequence[Mapping[str, Any]] | None") -> "str | None":
    """Who is selling what, anywhere in town - the thing that was missing.

    The board above is place-scoped: a resident reads it only when standing
    where something is posted. Over the 50-resident run that meant it never
    appeared once, and 87 of 140 refused purchases were a resident asking
    whoever was beside it for something that person had never priced.

    Each line carries the four facts a purchase needs - what, how much, from
    whom, and where to stand - plus whether the shelf is actually stocked,
    because a price over an empty shelf refuses the call after it is paid
    for. Bids read as bids: `sell` failed for want of a posted bid 32 times.

    Silent when the town has priced nothing, like the local board: a line
    every resident learns to skip is worse than no line.
    """
    if not offers:
        return None
    lines = ["For sale in the town (ask here, or go there). A shop's posted "
             "price is its standing offer, so buying from one needs no "
             "conversation; buying from a PERSON is agreed by talking to them:"]
    for offer in offers:
        where = offer.get("where") or "somewhere"
        who = offer.get("seller") or offer.get("seller_id") or "somebody"
        if offer.get("ask_sim") is not None:
            stock = offer.get("stock") or 0
            held = f"{stock} in stock" if stock else "**none in stock**"
            # Only ever said of a PERSON now: a shop with a posted price
            # sells whoever is standing in it, so calling one unattended
            # would send a resident away from a counter that would serve it.
            if offer.get("minded") is False:
                held += ", nobody there to agree it right now"
            lines.append(
                f"  - {offer['item']} at {offer['ask_sim']} sim from {who}, "
                f"at {where} - {held}."
            )
        else:
            lines.append(
                f"  - {who} will buy {offer['item']} at {offer['bid_sim']} sim, "
                f"at {where}."
            )
    return "\n".join(lines)


def format_price_board(
    prices: Mapping[str, Sequence[str]] | None,
) -> str | None:
    """What is priced where this resident is standing, in words.

    Silent when nothing is posted, deliberately: "Nothing is priced here"
    on every prompt in a town that has not discovered prices yet is a line
    every resident learns to skip. `look_around` says it when asked, which
    is where a resident who wants to know goes.
    """
    if not prices:
        return None
    lines = ["Priced where you stand:"]
    for holder, offers in prices.items():
        lines.append(f"  - {holder}: {'; '.join(offers)}.")
    return "\n".join(lines)


def format_addressed_to_you(
    perception: Perception, names: Mapping[str, str] | None = None
) -> str | None:
    """What somebody said to this agent, and is waiting on.

    Speech addressed to an agent is the one perception that carries an
    obligation, and it used to be rendered exactly like an arrival or a
    departure - a bullet in a list, in whatever order the window held it.
    Pulled out and named, so the agent reads it as a question rather than
    as weather.

    Newest last, because that is the order a conversation happens in.
    """
    spoken = [
        event
        for event in perception.heard_events
        if event.type == "agent.utterance.spoken"
        and event.involves_self
        and (event.payload or {}).get("listener_agent_id")
        # Said BY you is not said TO you.
        #
        # involves_self is true for the speaker as well - involved_agent_ids
        # carries both ends of a remark - and an utterance is an event at
        # the speaker's own location, so an agent's own words came back to
        # it inside the hearing window under the heading "Said to you, and
        # not yet answered", followed by an instruction to reply to itself.
        # Observed live: 3 of 9 delivered remarks were the reader's own,
        # including Ainsley being asked to answer Ainsley.
        and (event.payload or {}).get("speaker_agent_id") != perception.agent_id
    ]
    if not spoken:
        return None

    lines = ["Said to you, and not yet answered:"]
    still_here = False
    for event in spoken[-3:]:
        payload = event.payload or {}
        speaker_id = str(payload.get("speaker_agent_id") or "")
        speaker = _named(speaker_id or "someone", names)
        content = str(payload.get("content") or "").strip()
        # Where it was said, when that is not where the agent is standing
        # now. The window spans several ticks and people walk off mid
        # conversation.
        where = payload.get("location_id")
        elsewhere = bool(where and perception.location_id and where != perception.location_id)
        place = f" (at {where})" if elsewhere else ""
        lines.append(f'  {speaker}{place}: "{content}"')
        if speaker_id and speaker_id in (perception.visible_agents or ()):
            still_here = True

    # Only claimed when it is true. `say` gates on co-location, so telling
    # an agent it can answer somebody who has walked off sends it into a
    # refusal it was promised would work.
    if still_here:
        lines.append(
            "  They are standing with you and can hear a reply. Use `say` with "
            "their agent id as listener_agent_id to answer."
        )
    else:
        lines.append(
            "  They are no longer standing with you; `say` only reaches "
            "someone in the same place."
        )
    return "\n".join(lines)


def format_open_questions(
    questions: Sequence[Mapping[str, Any]] | None, now_tick: int
) -> str | None:
    """Questions somebody is still waiting on an answer to.

    Distinct from `format_addressed_to_you`, which renders what arrived in
    the hearing window: this renders what is still OUTSTANDING, including
    remarks that fell out of that window several turns ago. A directed
    question used to be visible for roughly one turn, so a resident who
    spent that turn walking never saw it again, and 56 of 81 exchanges in
    one run ended after a single remark.

    Says how long each has waited, because "six ticks ago" is the
    difference between a question and a nag, and because an agent deciding
    whether to answer now or finish its errand needs to know which.

    Rendered at every depth. Retrieval depth governs how much of the world
    an agent takes in; an unanswered question put to it by name is not
    scenery, and withholding it at low depth would be withholding the one
    thing another resident is actively waiting on.
    """
    if not questions:
        return None
    lines = ["Still waiting on an answer from you:"]
    for question in questions:
        speaker = _named(
            str(question.get("speaker_agent_id") or "someone"),
            {str(question.get("speaker_agent_id")): question.get("speaker_name")}
            if question.get("speaker_name")
            else None,
        )
        content = str(question.get("content") or "").strip()
        tick = question.get("tick")
        waited = ""
        if isinstance(tick, int):
            waited = f" ({now_tick - tick} ticks ago)"
        lines.append(f'  {speaker}{waited}: "{content}"')
    # A fact and a verb, and nothing after it.
    #
    # This used to end "...or let it go and do what matters more", written
    # to keep the mechanism from forcing a reply - one that forced a reply
    # would score 100% and measure nothing. But measured on run5, all
    # EIGHT turns that carried an open question produced no tool call at
    # all, against a baseline of 57 in 94 on turns without one. Marta
    # asked Wren three times in three separate remarks and he never
    # answered once.
    #
    # "Let it go and do what matters more" is not neutral. It is the last
    # thing in the section, it is an explicit invitation to do neither,
    # and it arrives attached to a question somebody is waiting on.
    # Reporting that a question is open and unanswered is a fact;
    # suggesting it may be dropped is advice, and this section has no
    # business giving any - the same rule the convening hint and the
    # silent-turn report already follow.
    lines.append("  You have not answered. `say` is how an answer reaches them.")
    return "\n".join(lines)


def format_conversation(record: Mapping[str, Any] | None) -> str:
    """The conversation this resident is standing in, as perception.

    Runs 18 and 19 both ended with `start_conversation` registered,
    priced, served to twenty engines and never once called. Reading a real
    `look_around` from those runs explains it: the world knew who was in
    which conversation, how many turns it had run and whether it was still
    open, and none of it reached the agent. A tool nothing in your view
    refers to is not a tool you can choose.

    `look_around` shows this too, and that is not enough on its own,
    because looking around is itself a tool call - a resident who does not
    look never learns. The perception is read every turn by everybody for
    nothing.

    IT DELIBERATELY GIVES NO ORDERS. Atlas, the prototype this borrows
    from, follows its group announcement with a loop that makes each
    member speak in turn. That loop is what ROADMAP Law 2 forbids, and a
    line here reading "it is your turn to reply" would be the same
    orchestrator wearing a prompt as a disguise: the town would converse
    on cue and the finding would be worthless. What changes is only that a
    conversation is a thing an agent can SEE it is inside.
    """
    if not record:
        return ""
    topic = str(record.get("topic") or "").strip()
    conversation_id = str(record.get("conversation_id") or "").strip()
    if not conversation_id:
        return ""
    subject = topic or "no subject anybody named"
    people = [str(name) for name in (record.get("participants") or [])]
    turns = record.get("turns")
    run = f", {turns} turns so far" if isinstance(turns, int) and turns else ""
    lines = [f"A conversation is open here about {subject}{run}."]
    if people:
        lines.append("  In it: " + ", ".join(people) + ".")
    # The id, because a resident who cannot name the thread cannot answer
    # into it, and a room holding two conversations muddles them.
    lines.append(f"  Answer into it by passing conversation_id={conversation_id!r} to say.")
    return "\n".join(lines)


def addressees_of(payload: Mapping[str, Any]) -> set[str]:
    """Everybody this remark was spoken TO.

    `addressed_agent_ids` when the record has it, falling back to the
    single `listener_agent_id` for records written before run 21 - a store
    read back must not raise, and must not silently answer "nobody".

    Tolerant of any shape, because this runs on every agent's every turn
    and a malformed payload must not take a resident's turn down with it.
    """
    listed = payload.get("addressed_agent_ids")
    if isinstance(listed, (list, tuple, set)):
        return {str(a) for a in listed if a}
    listener = payload.get("listener_agent_id")
    return {str(listener)} if listener else set()


def participation_role(agent_id: str, payload: Mapping[str, Any]) -> str:
    """Which of Goffman's roles this resident held in that remark.

    Goffman (Footing, 1979) wrote the participation framework precisely
    because "the dyadic model of communication, involving only speakers
    and hearers" is inadequate once three people are in a room. Kasukabe
    had exactly that dyadic model - `listener_agent_id`, one person, and
    everybody else - in a town whose rooms hold five.

    Four answers, and the difference between the middle two is the one
    that matters:

      speaker    - said it
      addressed  - selected by a vocative. Sacks, Schegloff and Jefferson
                   (1974): the party so selected "has rights, and is
                   obliged, to take next turn to speak, and no others have
                   such rights or obligations"
      ratified   - on the conversation's roster, expected to listen, free
                   to take the next turn but not obliged to
      bystander  - within earshot, not in the conversation

    See docs/HUMAN_BASELINES.md section 3.
    """
    if agent_id == payload.get("speaker_agent_id"):
        return "speaker"
    if agent_id in addressees_of(payload):
        return "addressed"
    roster = payload.get("conversation_participants")
    if isinstance(roster, (list, tuple, set)) and agent_id in roster:
        return "ratified"
    return "bystander"


def format_heard_event(
    event: PerceivedEvent,
    names: Mapping[str, str] | None = None,
    here: str | None = None,
) -> str:
    """One thing the agent noticed, in words.

    Speech used to arrive as a raw payload dict - conversation_id, a UUID,
    and every other field - under a bracketed event type:

        - [agent.utterance.spoken, about you] {'speaker_agent_id': 'a0121',
          'listener_agent_id': None, 'conversation_id': '6a468f2e-...', ...}

    A model has to reverse-engineer that somebody spoke before it can
    consider answering, and the speaker is an id it cannot address. Measured
    against that rendering: 28 utterances and ZERO replies. Speech is the one
    event that must read as speech.
    """
    payload = event.payload if isinstance(event.payload, Mapping) else {}

    if event.type == "agent.utterance.spoken":
        speaker = _named(str(payload.get("speaker_agent_id") or "someone"), names)
        content = str(payload.get("content") or "").strip()
        # "to you" is the whole difference between a remark to answer and
        # one to overhear, and it is the thing the raw dump buried deepest.
        #
        # Read from the full addressee list, not from `listener_agent_id`.
        # Ezra answered Anselm and Tova in one remark; the world recorded
        # both in `addressed_agent_ids` and told Tova "said", so the
        # vocative that SELECTED her - and obliged her alone to answer -
        # reached her as ambient speech. See
        # world/tests/test_being_spoken_to_reaches_you.py.
        #
        # `event.for_agent_id` is who is reading this. Without it the old
        # test was `involves_self and there_is_any_listener`, which said
        # "said to you" to the SPEAKER about their own remark.
        reader = event.for_agent_id
        to_you = (
            bool(reader)
            # Never about your own remark, whatever the record says. A
            # resident that names itself mid-sentence would otherwise be
            # told it had been spoken to, and the self-echo defect has
            # been seen live before: three of nine delivered remarks in
            # one run were the reader's own words.
            and reader != payload.get("speaker_agent_id")
            and reader in addressees_of(payload)
        )
        where = payload.get("location_id")
        # Only when it is somewhere the agent is not standing now - the
        # hearing window spans several ticks and an agent that has walked on
        # would otherwise read old speech as happening in front of it.
        place = f" at {where}" if where and here and where != here else ""
        lead = "said to you" if to_you else "said"
        return f'  - {speaker} {lead}{place}: "{content}"'

    if event.type == "agent.conversation.opened":
        # The other event that must not arrive as a dict.
        #
        # This function's docstring above makes the argument for speech -
        # "a model has to reverse-engineer that somebody spoke before it
        # can consider answering, and the speaker is an id it cannot
        # address. Measured: 28 utterances and ZERO replies." Convening
        # had the identical shape and was never fixed, and the numbers
        # match: `start_conversation` called five times across six runs,
        # and orphaned when it WAS used, because nobody could read the
        # invitation. Perrin named four people and each of them got a
        # UUID in a bracket.
        #
        # Carries the room picture at the moment it changes - who called
        # it, what about, who else is in it - so a resident does not have
        # to spend a `look_around` on news that it was invited to
        # something. `look_around` was the second most-called tool of the
        # series, 248 calls against 194 `say`.
        #
        # It gives no orders. Atlas follows its group announcement with a
        # loop that makes each member speak; that loop is what ROADMAP
        # Law 2 forbids.
        reader = event.for_agent_id
        convener_id = str(payload.get("opened_by_agent_id") or "")
        convener = _named(convener_id, names) if convener_id else "someone"
        topic = str(payload.get("topic") or "").strip()
        about = f" about {topic}" if topic else ""
        roster = payload.get("involved_agent_ids")
        others = [
            _named(str(other), names)
            for other in (roster if isinstance(roster, (list, tuple)) else [])
            if str(other) not in {reader, convener_id}
        ]
        conversation_id = str(payload.get("conversation_id") or "")
        answer_with = (
            f" Speak into it with conversation_id={conversation_id!r}."
            if conversation_id
            else ""
        )
        if reader == convener_id:
            return f"  - You opened a conversation{about}.{answer_with}"
        if event.involves_self:
            with_them = f" In it with you: {', '.join(others)}." if others else ""
            return (
                f"  - {convener} called you into a conversation{about}."
                f"{with_them}{answer_with}"
            )
        return f"  - {convener} opened a conversation{about} here."

    if event.type == "health.agent.revived":
        # A2. The first thing a revived resident reads has to say what
        # happened to it and who paid, in words. The fallback below would
        # hand somebody who has just come back from being unable to think
        # a bracketed dict of its own resurrection.
        reader = event.for_agent_id
        revived_id = str(payload.get("revived_id") or payload.get("agent_id") or "")
        payer_id = str(payload.get("payer_id") or "")
        payer = _named(payer_id, names) if payer_id else "somebody"
        ticks = payload.get("ticks_dormant")
        paid = payload.get("sim_paid")
        if reader and reader == revived_id:
            spell = f" for {ticks} ticks" if isinstance(ticks, int) and ticks else ""
            fee = f" paid {paid} sim to" if paid else " paid to"
            return f"  - You were dormant{spell}. {payer}{fee} wake you."
        who = _named(revived_id, names) if revived_id else "someone"
        if reader and reader == payer_id:
            return f"  - You paid {paid} sim to wake {who}."
        return f"  - {payer} paid to wake {who}."

    if event.type == "health.agent.dormant":
        # The household's one chance to do anything about it. Where they
        # fell and how long there is to reach them, because `revive` is
        # gated on standing over them and nothing else in perception says
        # where "them" is.
        reader = event.for_agent_id
        who_id = str(payload.get("agent_id") or payload.get("target_agent_id") or "")
        who = _named(who_id, names) if who_id else "someone"
        where = payload.get("location_id")
        place = f" at {where}" if where else ""
        deadline = payload.get("deadline_hours")
        clock = (
            f" Without a revival they die in {int(deadline)} game-hours."
            if isinstance(deadline, (int, float)) and deadline
            else ""
        )
        if reader and reader == who_id:
            return f"  - You ran out of energy and fell dormant{place}."
        return f"  - {who} has fallen dormant{place}: their energy reached zero.{clock}"

    if event.type == "agent.moved":
        # TOOL-05(b). This arrived as the raw fallback below -
        # `- [agent.moved, nearby] {'agent_id': 'a0121', 'location_id':
        # 'sector:900007', 'tile_x': 41, ...}` - which is the same defect
        # this function's docstring makes the case about for speech: a
        # model has to reverse-engineer that somebody arrived, and the
        # person is an id it cannot address.
        #
        # Worse for the reader's OWN arrival. A resident that walked for
        # ten ticks and got there read a dict about itself, so "am I
        # there yet" was a question the perception could not answer and
        # `look_around` had to be spent on.
        reader = event.for_agent_id
        who_id = str(payload.get("agent_id") or "")
        where = payload.get("location_id")
        shown = str(where) if where else "somewhere"
        # Absent means "not stated", not "no". `MovementSystem.advance`
        # and `move` both set it; a producer that does not is telling us
        # nothing, and a silently-dropped event is worse than a slightly
        # noisy one.
        mid_walk = payload.get("arrived") is False
        if reader and reader == who_id:
            if mid_walk:
                return f"  - You are walking; you are passing {shown}."
            if payload.get("walked") is False:
                return f"  - You stepped straight to {shown}; it is not on the map."
            return f"  - You arrived at {shown}."
        # Somebody else. Only their arrival is worth a line: a passer-by
        # crossing a tile is the noise `NOISY_EVENT_TYPES` drops from the
        # trace for the same reason.
        if mid_walk:
            return ""
        who = _named(who_id, names) if who_id else "someone"
        if here and where == here:
            return f"  - {who} arrived here."
        return f"  - {who} arrived at {shown}."

    marker = "about you" if event.involves_self else "nearby"
    return f"  - [{event.type}, {marker}] {payload or event.payload}"


def format_heard_events(
    perception: Perception,
    *,
    max_events: int,
    names: Mapping[str, str] | None = None,
    skip_addressed: bool = False,
) -> str:
    if not perception.heard_events:
        return "Nothing notable happened recently."
    # Speech first, and newest first within it. The window holds several
    # ticks now, so a remark addressed to this agent could otherwise sit
    # below three arrivals and departures and fall off the end of max_events.
    events = perception.heard_events
    if skip_addressed:
        events = [
            e for e in events
            if not (e.type == "agent.utterance.spoken" and e.involves_self
                    and (e.payload or {}).get("listener_agent_id"))
        ]
    spoken = [e for e in events if e.type == "agent.utterance.spoken"]
    other = [e for e in events if e.type != "agent.utterance.spoken"]
    ordered = list(reversed(spoken)) + list(reversed(other))
    if not ordered:
        # Everything the window held was the question quoted above. Saying
        # "Recently: ...and 1 more event(s)" about a remark already printed
        # in full is worse than saying nothing.
        return "Nothing else happened recently."
    shown = ordered[:max_events]
    lines = ["Recently:"]
    # An empty string means "this event is not worth a line to this
    # reader" - somebody else crossing a tile mid-walk (TOOL-05). Dropped
    # rather than rendered blank, which would leave a hole in the list.
    lines.extend(
        line
        for line in (
            format_heard_event(event, names, perception.location_id) for event in shown
        )
        if line
    )
    # Counted against what this section is actually rendering, not against
    # the raw window - anything pulled out above is not "more events", it is
    # the same event.
    overflow = len(ordered) - len(shown)
    if overflow > 0:
        lines.append(f"  ...and {overflow} more event(s).")
    return "\n".join(lines)


def format_relationship_notes(relationship_notes: Mapping[str, str] | None) -> str | None:
    if not relationship_notes:
        return None
    lines = ["What you remember about people here:"]
    for agent_id, note in relationship_notes.items():
        lines.append(f"  - {agent_id}: {note}")
    return "\n".join(lines)


def format_skill_playbook(entries: Sequence[Mapping[str, Any]] | None) -> str | None:
    """The agent's own procedures, shown as steps with its place in them
    marked.

    This is the change that makes a skill ALTER BEHAVIOUR rather than just
    discount a price. An agent told "you have forestry 34" can do nothing with
    the number. An agent shown the workflow it wrote, with a caret on the step
    it has reached, has something to follow - or to decide, deliberately, to
    abandon.

    Steps already taken are marked done rather than dropped, because the point
    of a procedure is the shape of the whole thing: an agent that can only see
    what is next cannot tell that it is three steps from selling.
    """
    if not entries:
        return None
    lines: list[str] = []
    for entry in entries:
        position = entry.get("next_step")
        where = f", you are at step {position + 1}" if position is not None else ""
        lines.append(f"YOU KNOW: {entry['name']}   (proficiency {entry['proficiency']}{where})")
        if entry.get("preconditions"):
            lines.append(f"  use it when: {entry['preconditions']}")
        for index, step in enumerate(entry.get("steps", [])):
            here = ">" if position is not None and index == position else " "
            done = "  \u2713 done" if position is not None and index < position else ""
            what = step.get("ref") or step.get("note") or ""
            lines.append(f"{here} {index + 1:>2}  {step.get('kind', ''):<7} {what}{done}")
    return "\n".join(lines)



def format_civic_notice(notice: Mapping[str, Any] | None) -> str | None:
    """What the town's institutions are doing, as facts.

    GOV-17. No tool returned an election id, a phase, a candidate list or
    who holds a seat. `declare_candidacy` and `vote` both REQUIRE an
    `election_id` that nothing in the town would tell a resident, so the
    two civic verbs the town has were unusable even in the runs where the
    seats had been filled. `look_around` at the complex said "vote" and
    never that there was anything to vote in.

    The fan-out this replaces (GOV-20) woke every resident in the town on
    a candidacy, buying a model call each to deliver one sentence. A line
    in the perception says the same thing to everybody, every tick, for
    nothing.

    Facts only, per the addendum's rule: what is open, who is standing,
    whether you have voted. Never "you should vote".
    """
    if not notice:
        return None
    lines: list[str] = []

    election_id = notice.get("election_id")
    if election_id:
        phase = notice.get("phase") or "scheduled"
        readable = {
            "scheduled": "an election is scheduled",
            "candidacy_open": "an election is open for candidates",
            "campaigning": "an election is campaigning",
            "voting_open": "an election is open for voting",
        }.get(str(phase), f"an election is {phase}")
        line = f"The town: {readable} (election_id {election_id})."
        candidates = notice.get("candidates") or []
        if candidates:
            line += f" Standing: {', '.join(candidates)}."
        else:
            line += " Nobody has declared."
        if notice.get("you_have_voted"):
            line += " You have voted."
        elif notice.get("you_have_not_voted") and str(phase) == "voting_open":
            # Said in the second person, because the third person is what
            # failed. "An election is open for voting" is a fact about the
            # town; "you have not voted" is a fact about the reader, and
            # only the second kind gets acted on - the same difference GOV-3
            # found between naming a government and naming a seat-holder's
            # own verbs.
            hall = notice.get("where") or "the town hall"
            here = notice.get("you_are_at")
            # The verb is named in EVERY branch. An earlier rewrite of this
            # put it inside the journey sentence, so a notice built without a
            # location lost it entirely - and naming the verb is the whole
            # reason this line exists.
            # The journey, not only the verb. `vote` is gated to the hall, so
            # a resident anywhere else is offered it as an index line with no
            # schema and cannot form the call however willing they are. Naming
            # the move is the difference between a rule and a thing to do -
            # the same lever GOV-3 found when it named a seat-holder's own
            # verbs instead of the office.
            if not here:
                journey = f" Go to {hall} and `vote` before it closes."
            elif here != hall:
                journey = (
                    f" You are at {here}, not {hall}: `move` to {hall},"
                    " then `vote`."
                )
            else:
                journey = f" You are at {hall}: `vote` now."
            line += (
                " You have NOT voted. Voting is compulsory for residents of "
                f"voting age.{journey}"
                if notice.get("voting_is_compulsory")
                else " You have not voted yet." + journey
            )
        lines.append(line)
        # The rules of taking part, as facts (see civic_notice_for).
        rules = []
        if notice.get("where"):
            who = (f"; residents aged {notice['min_age']} and over may do both"
                   if notice.get("min_age") is not None else "")
            rules.append(f"Candidates declare, and residents vote, in person at "
                         f"{notice['where']}{who}.")
        when = []
        if notice.get("candidacy_closes") and str(phase) in ("scheduled", "candidacy_open"):
            when.append(f"Candidacy closes {notice['candidacy_closes']}")
        if notice.get("voting_opens") and str(phase) != "voting_open":
            when.append(f"voting opens {notice['voting_opens']}")
        if notice.get("voting_closes"):
            when.append(f"voting closes {notice['voting_closes']}")
        if when:
            text = "; ".join(when)
            rules.append(text[0].upper() + text[1:] + ".")
        if rules:
            lines.append(" ".join(rules))

    government = notice.get("government")
    if government:
        lines.append(f"Who governs: {government}.")
    elif notice.get("seats_vacant"):
        lines.append("Who governs: nobody. The seats are vacant.")

    # GOV-3. The line above is a list of names in the third person, and a
    # resident reading its own name in it is not told that the town's
    # government verbs are now its own.
    #
    # The 8-resident run of 2026-09-23 elected a government that then used
    # no government tool at all - not refused, never attempted, because
    # `registry.visible_to` had already put every one of those verbs in
    # the winner's schema and nothing connected the seat to them. It is
    # the same gap `authoring_hint` closed for `skillify` by naming the
    # verb, which moved it from 0% to 4 of 5.
    #
    # Names the seat, the verbs and the place, and stops. What a seat MAY
    # do, never that it should do any of it - the standing rule, and the
    # reason `vote` has never been worded as an instruction either.
    seat = notice.get("your_seat")
    if seat:
        held = (
            f"You hold a seat: {seat}, by appointment rather than by election"
            if notice.get("your_seat_is_caretaker")
            else f"You hold a seat: {seat}"
        )
        lines.append(
            f"{held}. A seat may `set_tax_rate`, `set_price_control` and "
            "`rule`, at Kasukabe Government Complex; `describe_primitives` "
            "lists the rest."
        )

    rate = notice.get("tax_rate_bps")
    if rate is not None:
        per_head = notice.get("head_tax_sim_per_head")
        lines.append(
            f"Head tax: {rate} bps"
            + (f", {per_head} sim per head per day." if per_head is not None else ".")
        )
    return "\n".join(lines) if lines else None


def render_perception_text(
    perception: Perception,
    *,
    depth: RetrievalDepthConfig,
    visible_agent_bios: Mapping[str, str] | None = None,
    visible_agent_names: Mapping[str, str] | None = None,
    self_brief: Mapping[str, Any] | None = None,
    relationship_notes: Mapping[str, str] | None = None,
    skill_playbook: Sequence[Mapping[str, Any]] | None = None,
    authoring_hint: str | None = None,
    gap_notice: str | None = None,
    open_questions: Sequence[Mapping[str, Any]] | None = None,
    awaiting_reply: Sequence[Mapping[str, Any]] | None = None,
    wallet: Mapping[str, Any] | None = None,
    stopped_working: str | None = None,
    conversation: Mapping[str, Any] | None = None,
    convening_hint: str | None = None,
    silent_turns: str | None = None,
    visible_agent_offers: Mapping[str, str] | None = None,
    prices_here: Mapping[str, Sequence[str]] | None = None,
    market: Sequence[Mapping[str, Any]] | None = None,
    intention: Mapping[str, Any] | None = None,
    intention_outcome: Mapping[str, Any] | None = None,
    civic_notice: Mapping[str, Any] | None = None,
    goals: Sequence[Mapping[str, Any]] | None = None,
    needs: Sequence[Mapping[str, Any]] | None = None,
    beliefs: Sequence[Mapping[str, Any]] | None = None,
    goal_outcomes: Sequence[Mapping[str, Any]] | None = None,
    life_stage: str | None = None,
) -> str:
    """Builds the free-text string run_genesis.py sends as
    TickRequest.perception (engine/src/sim/agent_adapter.py). This is the
    concrete "richer social and world context" DESIGN.md describes: the
    same Perception object renders to more text, with more of it, as
    `depth.fraction` rises - genuinely larger input token counts, not a
    label.

    `visible_agent_bios` / `relationship_notes` are optional maps the
    caller supplies from whatever world state it has on hand (genesis
    bios, a relationships store); this function degrades gracefully to
    id-only mentions when they are not provided, at any depth, rather than
    failing - a missing bio is not a reason to drop a whole turn.
    """
    sections = [format_ambient(perception)]

    # A duty with a deadline goes ABOVE the resident's own intention.
    #
    # Two fifty-resident elections were tallied with zero ballots. The notice
    # was correct, said in the second person, named the verb, named the place,
    # and every resident read it - one was told "You are at Kasukabe
    # Government Complex: `vote` now" while standing there, and said something
    # instead. It sat 55% of the way down a prompt that OPENS with "You set
    # out at 11:20 to: ..." - the resident's own goal, stated first and in the
    # second person, which is what it went on doing.
    #
    # Position is the whole intervention. GOV-3 moved `set_tax_rate` after
    # twenty-three runs of nothing by putting a verb in a seat-holder's own
    # perception; this puts a closing deadline above a standing preference,
    # which is the order the two actually have.
    #
    # Only while voting is OPEN and only for somebody who has not voted, so
    # it is a deadline rather than a nag: the moment the ballot is cast, or
    # the window shuts, this disappears and the intention leads again.
    notice_now = civic_notice or {}
    if (
        str(notice_now.get("phase") or "") == "voting_open"
        and notice_now.get("you_have_not_voted")
        and notice_now.get("voting_is_compulsory")
    ):
        where_now = notice_now.get("you_are_at")
        hall_now = notice_now.get("where") or "the government complex"
        eid_now = notice_now.get("election_id") or ""
        # The exact wording, chosen by measurement rather than taste. Asked of
        # gpt-6-luna with the real 80-tool list and the real prompt n01 got
        # while standing in the hall and did not vote:
        #
        #   the prompt as it was ................. 0/6
        #   duty hoisted, "`vote` now" ........... 2/8   (it kept calling `move`)
        #   + the call shape ..................... 4/8
        #   + "you do not need to move" .......... 8/8
        #
        # Two things carried it. Saying it is ALREADY there stops the resident
        # walking to a building it is standing in - four of the eight failures
        # were `move`. And naming the election_id and the three arguments
        # turns "vote" from a thing to work out into a call to make.
        if where_now and where_now == hall_now:
            sections.append(
                "BEFORE ANYTHING ELSE: voting closes soon and you have not "
                "voted. Voting is compulsory for residents of your age and you "
                f"are ALREADY at {hall_now} - you do not need to move. Your "
                f"first action this turn must be `vote` (election_id "
                f'"{eid_now}"). Choose whichever candidate you most agree with '
                "and say why in `because`."
            )
        else:
            sections.append(
                "BEFORE ANYTHING ELSE: voting closes soon and you have not "
                "voted. Voting is compulsory for residents of your age. `move` "
                f"to {hall_now} and then `vote` (election_id \"{eid_now}\"), "
                "before anything else you had planned."
            )

    # B1: why you are awake. First, before anything else, because a
    # resident that stopped working and does not know why has been given a
    # gap in its own morning where a reason should be.
    if stopped_working:
        sections.append(stopped_working)

    # B2: what you set out to do, at the top, every turn until it is done.
    #
    # Aldous Vance decided to go to Oak Hill College on four consecutive
    # ticks and never went - not because he changed his mind, but because
    # each tick he arrived at the decision fresh, with no record that he
    # had already made it. Nothing here enforces anything; it is a note to
    # himself, which is exactly why the fulfilled ratio is worth counting.
    intention_line = format_intention(intention, intention_outcome)
    if intention_line:
        sections.append(intention_line)

    # ENG-24: what you want, immediately under what you set out to do,
    # because they are the same question at two scales and splitting them
    # across the prompt would make a resident answer each without the
    # other. Above the addressed-to-you line only in the sense that a
    # goal is why you would answer at all.
    # What became of a goal comes FIRST, above the standing list, for
    # the same reason the intention outcome does: it is news, and the
    # list underneath it is furniture.
    outcome_block = format_goal_outcomes(goal_outcomes)
    if outcome_block:
        sections.append(outcome_block)
    # LIFE-01: what you are, immediately above what you want. They are
    # the same question at two scales, and a resident reading its goals
    # without its situation cannot tell which of them still belong to it.
    stage_line = format_life_stage(life_stage)
    if stage_line:
        sections.append(stage_line)
    goal_block = format_goals(goals, needs, beliefs)
    if goal_block:
        sections.append(goal_block)

    # Anything said TO this agent: first, by itself, and above everything
    # else it is about to read.
    #
    # A directed remark used to arrive inside "Recently:", below arrivals,
    # departures and whatever else the window held, with nothing marking it
    # as a thing awaiting an answer. An agent that is asked a question and
    # shown it as the fourth bullet of an event log answers it about as
    # often as a person would.
    #
    # Then it sat fifth, under a nine-name roster of the town. An open
    # question addressed to you by name outranks a directory: the roster is
    # reference material the agent consults if it wants somebody, and the
    # question is the one thing in the whole prompt that another resident is
    # actively waiting on. Placed above the brief for that reason, and above
    # "With you:" because it names who is present itself.
    #
    # This is the whole A2A protocol: address, delivery, a standing
    # expectation that an unanswered question stays open, and the question
    # put where it will actually be read.
    addressed = format_addressed_to_you(perception, visible_agent_names)
    if addressed:
        sections.append(addressed)

    # Questions still outstanding, including ones older than the hearing
    # window. Anything already quoted in full by `addressed` above is
    # dropped: printing the same remark twice costs tokens and makes one
    # question look like two.
    outstanding = [
        q for q in (open_questions or [])
        if not addressed or str(q.get("content") or "").strip() not in addressed
    ]
    still_open = format_open_questions(outstanding, perception.tick)
    if still_open:
        sections.append(still_open)

    # The other side of the same fact: what THIS resident asked and nobody
    # answered.
    #
    # `open_questions` above tells the person who was asked that they owe an
    # answer. Nothing told the asker, so a resident who spoke into silence
    # could not tell "they have not answered" from "I never said it" - and
    # asking again is the rational move when you cannot tell. Run 19 has
    # Marta Thorne repeating one sentence verbatim four ticks apart, Tova
    # Okonjo demanding the same yes-or-no answer at t40 and t43, and Silas
    # Vance giving the same speech about his unsold plank twice. Each repeat
    # was a billed turn that changed nothing.
    #
    # States the fact and stops. It does not say to give up, to ask again,
    # or to go elsewhere - what to do about being ignored is the resident's
    # business, and DESIGN.md section 4's line is "told the prices and the
    # rules, never the strategy".
    #
    # `_named`, not "the name or else the id". This line is read by a
    # resident deciding whether to ask again, and asking again needs the id
    # - so the name alone made the one thing it might act on unusable, and
    # the id alone (`format_open_questions` next door has always used
    # `_named`) is run 28b's "Someone is here - n02". Both halves, always.
    if awaiting_reply:
        waiting_lines = [
            "- " + _named(
                str(q.get("listener_agent_id") or "someone"),
                {str(q.get("listener_agent_id")): q.get("listener_name")}
                if q.get("listener_name")
                else None,
            )
            + f", {q.get('ticks_waiting')} tick(s) ago: "
            f"\"{str(q.get('content') or '').strip()}\""
            for q in awaiting_reply
        ]
        sections.append(
            "You asked, and have not been answered:\n" + "\n".join(waiting_lines)
        )

    # Who this agent IS, and what it is meant to be doing.
    #
    # The prompt used to open with the time and the ids of whoever was in
    # the room, and nothing else. An agent was never told its own trade, its
    # own workplace, or whether it had eaten - so a farmer with a field and
    # an empty larder read the same prompt as an idle child, and behaved the
    # same way. Nine utterances in 933 ticks, and residents standing in
    # their houses all day, are what that prompt produces.
    standing = format_standing_brief(self_brief)
    if standing:
        sections.append(standing)

    # A6: last in the standing brief, after the sim balance line, exactly
    # where the spec places it. This is the roadmap's VOLATILE layer - it
    # changes every tick and belongs in the per-turn perception, never in
    # the cached system prompt.
    volatile = format_volatile_line(wallet)
    if volatile:
        sections.append(volatile)

    # GOV-17, beside the energy line and for the same reason: a standing
    # fact about the resident's situation that it cannot get any other way.
    civic = format_civic_notice(civic_notice)
    if civic:
        sections.append(civic)

    if silent_turns:
        # Placed with the standing brief rather than at the very end,
        # deliberately. The turn-close line already occupies the last
        # position and says the same thing as a RULE stated in advance;
        # this says it as a FACT about what just happened, and a model
        # that believes it complied with the rule needs the evidence
        # before it starts composing, not after. See
        # WorldState.silent_turns_report.
        sections.append(silent_turns)

    convened = format_conversation(conversation)
    if convened:
        sections.append(convened)
    elif convening_hint:
        # Mutually exclusive with the section above by construction:
        # WorldState.convening_hint returns None once a conversation is
        # open here. The `elif` is belt and braces, so a future change to
        # that rule cannot produce a prompt telling an agent to open the
        # conversation it is already sitting in.
        sections.append(convening_hint)

    sections.append(format_visible_agents(
        perception, max_bios=depth.max_visible_agent_bios,
        visible_agent_bios=visible_agent_bios,
        visible_agent_names=visible_agent_names,
        offers=visible_agent_offers,
    ))

    # A3, rendered at every depth. What is for sale where you stand is not
    # "richer context" the depth dial may withhold - without it `buy` has
    # nothing to name, and the town is back to a bakery whose price exists
    # only in the baker's speech.
    # The town-wide board first: a resident deciding where to go needs to
    # know what is for sale before it needs the shelf beside it.
    town = format_market_board(market)
    if town:
        sections.append(town)
    board = format_price_board(prices_here)
    if board:
        sections.append(board)

    sections.append(
        format_heard_events(
            perception,
            max_events=depth.max_heard_events_rendered,
            names=visible_agent_names,
            # Already quoted in full above. Printing it twice costs tokens
            # and makes one question look like two.
            skip_addressed=bool(addressed),
        )
    )

    if depth.include_relationship_notes:
        notes = format_relationship_notes(relationship_notes)
        if notes is not None:
            sections.append(notes)

    # Rendered at EVERY depth, unlike relationship notes. Retrieval depth
    # governs how much of the world an agent takes in; its own procedures are
    # not part of the world it is looking at, and an agent that stops being
    # shown what it knows how to do at low depth would abandon every workflow
    # the moment the operator turned the dial down.
    playbook = format_skill_playbook(skill_playbook)
    if playbook is not None:
        sections.append(playbook)

    # One line, and only when the agent has earned it by repeating something -
    # see WorldState.authoring_hint for why this is evidence-driven rather
    # than a standing instruction in every prompt.
    if authoring_hint:
        sections.append(authoring_hint)

    # Part D: the town's own unmet wants, named but not answered. Last,
    # deliberately - it is context for what the agent might do next, not an
    # instruction competing with what it can see in front of it.
    if gap_notice:
        sections.append(gap_notice)

    # Last, and deliberately so.
    #
    # Law rule 6 already tells every resident that narrated words reach
    # nobody, and it worked: `say` went from the fourth-most-used tool to the
    # first, and turns-with-effect from 40% to 68%. But that rule lives in the
    # STABLE cached section - first thing in, furthest from the moment a model
    # decides it has finished. Measured after it landed: 79 of 232 cognition
    # turns still ended in prose with no tool call, including Corwin Thorne
    # writing "I need to answer him. Not with more explaining" and then
    # stopping without saying anything to anyone.
    #
    # This is the same fact in the last position a model reads. It states a
    # mechanism and nothing else - not who to answer, not whether to act - so
    # an agent that reads it and still does nothing has made a real choice,
    # which is what the experiment exists to observe.
    # PROMPT-01. The last line grew a second sentence, and the reason is
    # `deni-a6f9eb4-5h`: 3,341 of 3,741 turns ended with zero tool calls,
    # and 595 of those wrote out the acts they had settled on and called
    # none of them. The first sentence states the mechanism and has
    # measurably worked before (turns-with-effect 40% -> 68%); it is not
    # touched. What it never said is what to do about it - so a model
    # that has reasoned its way to a decision reads a true statement,
    # agrees with it, and stops anyway.
    #
    # The addition is Codex's persistence rule, translated. Its base
    # instructions tell a coding agent to "keep going until the query is
    # completely resolved, before ending your turn and yielding back" -
    # and the thing that makes that work is the second half, that nobody
    # is waiting to finish the job for you. A resident has no user to
    # yield back TO, which is exactly why it needs telling.
    #
    # Still a mechanism, still no strategy: it does not say act, or
    # trade, or speak. It says an unspent decision expires with the tick.
    sections.append(
        "Nothing you have thought or written above has happened yet. This turn "
        "ends when you stop, and only the tools you actually call will have "
        "occurred. A decision you do not call does not happen."
    )

    return "\n".join(sections)
