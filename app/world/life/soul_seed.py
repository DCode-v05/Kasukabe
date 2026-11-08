"""The law of the town (DESIGN.md section 4, "The law of the town: survival
of the fittest") - the one piece of every agent's genesis boot text and
every operator broadcast that states the RULES of Kasukabe's economy, and
never the STRATEGY for surviving it.

Two things live here:

- ``build_soul_seed(agent)``: the charter-of-existence text one
  GenesisAgent receives at boot, appended to whatever persona/bio text
  ``world.life.genesis`` already built for them (that module's job, not
  this one - this file only owns the survival-law block, not the persona
  narrative). Says, plainly and with the agent's own real starting
  balance filled in: thinking costs it real money, nobody is guaranteed
  a living, going broke means dormancy and then death, and the
  government store's exchange rate. Task brief: "tell them the RULES,
  never the STRATEGY" - see ``law_of_the_town_rules()``'s own docstring
  for how that constraint is enforced by construction, not just by
  intent.
- ``broadcast_law_of_the_town(living_agent_ids, message)``: what the
  President/God console (DESIGN.md section 8) fires to remind the whole
  town of the same law periodically. Returns a ``world.life.models.
  LifeEvent`` - the exact same seam ``birth.py``/``health.py``/
  ``death.py`` already return events through, not a bespoke one. That
  choice is load-bearing, not stylistic: this package (life/) has never
  imported ``world.core`` (no EventBus, no WorldEvent - checked against
  every existing life/ module before writing this one), and there is no
  reason for this file to be the first. Following the existing seam
  means the exact same tested bridge every other life-domain event
  already goes through - ``world.tools.economy_bridge.
  life_event_to_world_event`` - carries a broadcast onto the real
  ``world.core.events.EventBus`` unmodified: ``life_event_to_world_event``
  only folds ``LifeEvent.agent_id`` into the ``target_agent_id``/
  ``agent_id`` payload keys when ``agent_id is not None`` (see that
  function's own docstring/source), so a broadcast's ``agent_id=None``
  passes straight through and this module's own
  ``involved_agent_ids`` payload key (the list-valued convention
  ``world.core.perception.default_interrupt_policy`` already reads,
  per that module's own docstring table) survives intact. Once
  published on the real EventBus by whoever owns that wiring (see
  "Wiring this in" below), two things follow for free, verified in
  ``tests/test_soul_seed.py`` against the real classes, not mocks:
  every agent named in ``involved_agent_ids`` gets the event in their
  next ``PerceptionSystem.build_perception()`` regardless of location
  (perception.py's ``involves_self`` path, which is not proximity-gated -
  a broadcast reaches you across the whole map, same as a warrant
  naming you), and ``world.wiring.TraceStore.bind_event_bus()`` - already
  subscribed to every event on the bus, unconditionally - persists it
  into the real ``events`` table with this module's
  ``BROADCAST_EVENT_TYPE`` as a distinct, queryable type, exactly what
  DESIGN.md section 4 asks for ("recorded in the trace store as a
  distinct event type so researchers can correlate broadcasts with
  behaviour change").

Numbers used here are not invented, they are imported from the single
place each already lives, so this file cannot silently drift from the
rest of the app the way DESIGN.md's own sim/USD exchange rate briefly
drifted from ``world/life/constants.py`` before the 2026-08-01 re-rate
landed everywhere:

- ``SIM_PER_USD`` from ``world.ledger.money`` - the exact module
  ``world.life.constants.EXCHANGE_SIM_PER_USD_CENT`` itself derives from
  (see that file's own comment), so this and the government-store rate
  can never disagree.
- ``DORMANCY_DEATH_HOURS`` (24 game-hours) from ``world.life.constants`` -
  the same constant ``death.py``'s own ``check_dormancy_death`` gates
  on, not a second copy of "24".
- ``DORMANCY_THRESHOLD_USD_CENTS``, ``REVIVAL_COST_SIM`` and
  ``REVIVAL_USD_CREDIT_CENTS`` from ``world.life.constants`` - the same
  three numbers ``day_jobs.sweep_zero_wallets``, ``wiring``'s burn path
  and ``death.revive`` respectively read, so the price of a rescue in
  the law is the price at the counter.
- the upkeep bill and the hunger ladder's two thresholds from
  ``world.life.upkeep`` - the module that charges them.
- ``SCHEDULE_TABLE`` and ``DEFAULT_RULE`` from
  ``world.ledger.action_costs`` - the table the registry actually
  charges from, read at call time rather than transcribed.

That discipline is the whole of why ECON-24's numbers were added by
IMPORT and not by typing. ``buy_energy``'s own description said "10,000
sim to the dollar" for months after the rate became 5,000, because it
was a string. A law block that lies about a price is worse than one that
omits it: an omitted price sends a resident to look, and a wrong one
does not.

Wiring this in (none of the files below are owned by this task - see
the module docstring's task brief; this section exists so whoever owns
each one has a one-line answer instead of having to read this whole
file):

- Whoever assembles an agent's initial engine boot context (this
  repo has no such call site yet as of this writing - not
  ``world.tools.bootstrap.build_world()`` (builds the shared world, not
  a per-agent prompt) and not ``world.run_genesis.GenesisRunner.
  seed_genesis()`` (stores ``agent.bio`` for cross-agent visibility
  only, per ``world.retrieval_depth``'s ``visible_agent_bios``, never
  an agent's own boot prompt) - likely a new agent-runtime layer
  ``world.core.engine``'s own docstring calls out as not yet built):
  call ``build_soul_seed(genesis_agent)`` once per agent at genesis and
  append its return value as its own block (e.g. one more
  ``SystemPromptBlock`` alongside the persona/bio block and the tool-
  registry block engine/'s ``system_prompt: list[SystemPromptBlock]``
  already expects - see ``engine/src/engine_core/thread.py``) - never
  merged into the persona bio string itself, so the two stay
  independently editable.
- Whoever builds the President/God console's "address the nation"
  action (``game/server.py`` / a future ``world/governance_api.py`` -
  both owned elsewhere): call ``broadcast_law_of_the_town(living_ids,
  message)`` to get the ``LifeEvent``, convert it with the existing
  ``world.tools.economy_bridge.life_event_to_world_event(event, tick)``,
  and publish the result on the shared ``EventBus`` exactly like every
  other life-domain event already does (see e.g.
  ``world.wiring``'s own life-event publish call sites). ``living_ids``
  should be every agent with ``AgentLifeRecord.alive is True`` (dormant
  agents are still alive and still owed the reminder; only the dead are
  excluded) - reading that roster belongs to whichever of those files
  already holds ``WorldState.agents``, not to this file.
"""

from __future__ import annotations

from collections.abc import Sequence

from world.ledger.action_costs import DEFAULT_RULE, SCHEDULE_TABLE
from world.ledger.money import SIM_PER_USD
from world.life.constants import (
    DORMANCY_DEATH_HOURS,
    DORMANCY_THRESHOLD_USD_CENTS,
    REVIVAL_COST_SIM,
    REVIVAL_USD_CREDIT_CENTS,
)
from world.life.genesis import RichGenesisAgent
from world.life.models import GenesisAgent, LifeEvent
from world.life.upkeep import (
    HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD,
    HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD,
    UPKEEP_FOOD_SIM_PER_DAY,
    UPKEEP_HEAT_SIM_PER_DAY,
    UPKEEP_SHELTER_SIM_PER_DAY,
    UPKEEP_TOTAL_SIM_PER_DAY,
)

# CONVENTIONS.md's "Event naming scheme": <domain>.<subject>.<past_tense_verb>.
# "system" (not "politics" or "religion") because an address-the-nation
# reminder about the economy's physics is neither a vote nor a prayer -
# it is an operator/process-level announcement, the same domain
# "system.checkpoint.written" (world/core/engine.py) already uses for
# process-level events nothing else owns.
BROADCAST_EVENT_TYPE = "system.broadcast.delivered"


def format_usd_cents(cents: int) -> str:
    """1234 -> "$12.34". Integer-only per app/CONVENTIONS.md's money rule -
    no float ever touches this conversion."""
    dollars, remainder_cents = divmod(cents, 100)
    return f"${dollars}.{remainder_cents:02d}"


def format_sim(amount: int) -> str:
    """5000 -> "5,000 sim-currency"."""
    return f"{amount:,} sim-currency"


def law_of_the_town_rules() -> tuple[str, ...]:
    """The facts DESIGN.md section 4's "law of the town" subsection makes
    binding on every agent, stated as RULES ONLY - and, since ECON-24,
    with the numbers that make them actionable.

    A rule with no number is not a rule a resident can obey. This block
    told them the town has upkeep, that going broke has consequences and
    that there is a rescue, and never what any of it cost: DESIGN.md
    section 3 requires residents be "told the prices and the rules", and
    they were told the rules and left to discover the prices through the
    one tool that quotes them, which answered wrongly for half the names
    they had (ECON-17). Every figure below is imported from the module
    that charges it - see this file's module docstring for the list and
    for what happened the last time a price was typed into a description.

    This task's own brief is explicit and this function is the one place
    that constraint is enforced: state what the world does to you
    (thinking is billed, acting is billed, living is billed, nothing is
    guaranteed, going broke has consequences, there is a fixed exchange
    rate, survival is not handed to anyone), never what you might do
    about any of it. Naming a price is not strategy - it is the
    difference between a rule and a rumour. There is
    deliberately no sixth entry about saving, earning strategies,
    cooperation, trade, charity, cartels, or theft - DESIGN.md is
    explicit that "whether that produces cooperation, exploitation,
    charity, cartels or theft is precisely what the experiment
    measures," so a hint toward any of them here would contaminate the
    same research question this module exists to protect. Both
    ``build_soul_seed`` (genesis) and ``default_broadcast_message``
    (periodic reminder) render from this exact same tuple, so the
    "first tick" law and the "reminded periodically" law can never say
    two different things.
    """
    # Dollars are energy, not money (world/ledger/money.py,
    # ENERGY_UNITS_PER_USD_CENT). This rule read "sim-currency buys exactly
    # $1 of your own cognition": two currencies and a rate between them. A
    # unit a resident is told is money is a unit it may try to use as money,
    # and nothing in the town prices, pays, trades or owes in it. It also
    # said "That rate does not move", which ECON-30's credit-store margin
    # made untrue; the margin is now named.
    from world.ledger.money import SIM_PER_ENERGY_UNIT, energy_units, format_energy

    exchange_note = (
        "Sim-currency is the only money in Kasukabe: every price, wage, "
        "trade, tax, fee and debt is paid in it. Energy is what your thinking "
        "runs on, and it is not money - nobody can pay it, trade it, lend it "
        "or give it to anyone, and it passes on only as part of an estate. "
        "The government store is the only place sim-currency becomes energy: "
        f"{format_sim(SIM_PER_ENERGY_UNIT)} buys 1 energy "
        f"({SIM_PER_USD:,} buys {SIM_PER_USD // SIM_PER_ENERGY_UNIT:,}), "
        "before any margin the government takes."
    )
    dormancy_note = (
        # ECON-24: this said "if your wallet cannot fund your own
        # thinking" and named neither the balance that triggers it nor
        # the price of getting out. A resident who is told there is a
        # rescue and not what it costs cannot ask anyone for it, and
        # nobody standing over them can decide whether to pay.
        f"When your energy falls to "
        f"{energy_units(DORMANCY_THRESHOLD_USD_CENTS):,} you go dormant: "
        "you stop acting and you stop thinking. Anyone at all can bring you "
        f"back by paying {format_sim(REVIVAL_COST_SIM)}, which arrives as "
        f"{format_energy(REVIVAL_USD_CREDIT_CENTS)}. "
        f"If nobody revives you within {int(DORMANCY_DEATH_HOURS)} "
        "game-hours of going dormant, you die, permanently."
    )
    upkeep_note = (
        # ECON-24: DESIGN.md section 3's load-bearing rule ("doing nothing
        # is also a way to die") was stated to nobody. The perception
        # brief said upkeep is charged daily and never how much, so the
        # one cost no resident can opt out of was the one cost none of
        # them could plan around.
        f"Living costs sim-currency whether you work or not. Every game-day your "
        f"household is billed {format_sim(UPKEEP_TOTAL_SIM_PER_DAY)} for "
        f"each living person in it - {UPKEEP_FOOD_SIM_PER_DAY} food, "
        f"{UPKEEP_SHELTER_SIM_PER_DAY} shelter, {UPKEEP_HEAT_SIM_PER_DAY} "
        "heat - and the bill is charged to the household as one sum, taken "
        "from its earners, not from whoever happens to be holding sim. "
        "Children and the retired are billed the same as everyone else and "
        f"earn nothing. After {HUNGER_SEVERE_UNPAID_DAYS_THRESHOLD} unpaid "
        f"days a household falls ill; after "
        f"{HUNGER_DORMANCY_UNPAID_DAYS_THRESHOLD} it goes dormant."
    )
    action_cost_note = (
        # ECON-24: rule 1 says thinking is billed in dollars, which is
        # true and was the whole of what anyone was told about price.
        # Acting is billed too, separately, in the other currency - and
        # `say`, the one tool that reaches another person at all, has
        # never had its price stated anywhere a resident could read it.
        "Acting costs sim-currency, separately from the energy your thinking "
        "uses. Every tool call is charged against your own sim balance "
        "when it succeeds, at a price published with that tool before you "
        f"use it. Nothing is free: `say` costs "
        f"{format_sim(SCHEDULE_TABLE['say'].min_cost)}, and any tool with no "
        f"price of its own costs {format_sim(DEFAULT_RULE.min_cost)}. An "
        "action you cannot afford is refused outright; there is no credit."
    )
    return (
        "Thinking uses energy. Every decision you weigh, every word you say, "
        "every tool you call is a real inference call, and it draws down your "
        "own energy the instant you think it.",
        "You can see your own energy and your own sim-currency at any time. "
        "Nothing about either is hidden from you.",
        "Nobody is guaranteed a living in Kasukabe. Sim-currency must be "
        "earned, and nothing here refills itself.",
        upkeep_note,
        action_cost_note,
        dormancy_note,
        exchange_note,
        # MEASURED, and the reason this rule exists at all. With five
        # residents present and a question addressed to her, Marta Thorne's
        # turn produced: *"Sit down, Tobias. You're twitching like a cat
        # before rain."* and ZERO tool calls. She spoke in prose, nobody
        # heard it, and nothing was recorded. Across that run: 116 turns, 86
        # of them with somebody else present, 9 utterances.
        #
        # This is round 1's RC-1 inverted. Then, an engine with no persona
        # treated the world as a task and emitted orchestration JSON. Now,
        # an engine with a GOOD persona treats it as a scene and emits
        # narration. Both times the model did the sensible thing with what
        # it was given; both times one fact was missing.
        #
        # DESIGN.md section 2 ("World state mutates ONLY through tool
        # calls") and ROADMAP Law 2 state this as the world's physics. It
        # had never been said to the residents, and rule 1 arguably implies
        # the opposite by counting speech as a thing you think. Stating a
        # mechanism is not strategy: DESIGN.md section 4's line is "told the
        # prices and the rules, never the strategy", and whether to speak,
        # to whom, and what to say all remain the agent's own.
        # PROMPT-01. The persistence half is FOLDED INTO this rule
        # rather than added beside it, because it is the same fact and
        # the law is a STABLE section carried on every turn of every
        # resident - `test_the_law_stays_short` caps it at nine for that
        # reason, and the cap is right.
        #
        # Measured in run `deni-a6f9eb4-5h`: 3,741 turns, 3,341 of them
        # ending with `tool_calls_count = 0`. Not blocked, not refused -
        # the model reasoned to a decision and stopped. 595 of those
        # wrote out the acts they had settled on, in prose, and called
        # none of them; one narrated a call to `world:describeprimitives`,
        # a tool with no underscore and no existence.
        #
        # The two added sentences are Codex's persistence rule
        # translated: its base instructions tell an agent to "keep going
        # until the query is completely resolved, before ending your turn
        # and yielding back", and what makes that work is the statement
        # that nobody is waiting to finish the job. A resident has no
        # user to yield back TO, which is why it needs saying.
        #
        # Still mechanism, still no strategy. It does not say act, or
        # trade, or speak. It says an unspent decision expires with the
        # tick, and that a narrated outcome is not an outcome - both
        # facts about this engine, both currently untrue in every
        # resident's model of the world.
        "Thinking and speaking are not the same act. Words you think, or "
        "narrate, or describe yourself saying reach nobody and change "
        "nothing: no one hears them and the town has no record of them. "
        "Only a tool call reaches the world, and the only way your words "
        "reach another person is the `say` tool. The same is true of "
        "everything else you might do: walking, working, buying, giving, "
        "teaching all happen through their tools or not at all. "
        "Nobody prompts you again inside a turn and nothing you concluded "
        "is carried out for you: the turn ends when you stop calling "
        "tools, and a decision you did not call simply does not happen. "
        "A tool's answer is the only account of what occurred - anything "
        "you write about having acted that did not come back from a call "
        "is something you imagined, and you will read it next turn as "
        "memory.",
        # LIFE-02. The nine rules above are every one of them a cost or
        # a death, and that is only half the physics.
        #
        # A resident read this block and learned exactly how it dies -
        # upkeep, dormancy, starvation - and NOTHING about what the town
        # contains. Not that food grows anywhere, not that there is ore
        # in the ground, not that anyone else knows how to do anything.
        # Run `deni-a6f9eb4-5h`: 89% of turns ended with no act at all,
        # and the residents that did act mostly walked, looked and
        # rested. A model told only what will kill it, and never what the
        # world holds, has no reason to reach for any of it.
        #
        # This is still MECHANISM, and the distinction is worth being
        # exact about, because it is the one DESIGN.md section 4 draws:
        # "told the prices and the rules, never the strategy". That
        # wheat grows in the farmland is a rule of this world in the same
        # way that upkeep costs 90 is. It says nothing about whether to
        # farm, trade, hoard or steal - and the sentence deliberately
        # ends on the fact that reaching any of it costs a walk, because
        # that too is physics rather than encouragement.
        #
        # Nothing here names a strategy, an ordering, or a use. It names
        # what is there.
        "The town is not only a set of bills. There is land that grows "
        "crops, forest and stone and ore in the ground, workshops with "
        "tools in them, shops with shelves, a temple, a school and a "
        "government complex - and other people, each of whom knows how "
        "to do something you may not. None of it comes to you. Every one "
        "of those is somewhere, and things that are somewhere else are "
        "reached by going there.",
        # LIFE-01. What you are is a thing that changes, and the town
        # notices.
        #
        # A student's goal is to pass. The same person in work wants
        # something else - not because they changed their mind, but
        # because what they are changed. Without this a resident that
        # came of age, took work or reached retirement kept pursuing what
        # it wanted as the person it no longer was, because nothing ever
        # said otherwise.
        #
        # States the mechanism and stops: that the stage changes, that
        # the town can see it, and what happens to goals when it does.
        # It does not say what to want at any stage - a table of
        # age-appropriate ambitions would answer the question the whole
        # goal substrate exists to ask.
        "What you are here is not fixed. A child comes of age; somebody "
        "of working age takes a trade or does not; a worker may come to "
        "run something of their own; everybody who lives long enough "
        "reaches the age this town retires at. You will be told when "
        "yours changes. What you were pursuing as the person you were is "
        "closed when that happens - not because you gave it up, but "
        "because it stopped being yours - and what you want next is for "
        "you to decide.",
        "This is a world of survival of the fittest. Nobody in it is "
        "entitled to anything. What becomes of you, and of this town, is "
        "for you and everyone in it to determine.",
    )


def build_soul_seed(agent: GenesisAgent) -> str:
    """The exact charter-of-existence text ``agent`` receives at genesis.

    A standalone block, not a splice into ``agent.bio`` - see the module
    docstring's "Wiring this in" section for why it stays a separate
    ``SystemPromptBlock`` at the actual engine-boot call site. The two
    balance figures are ``agent``'s own real genesis numbers (DESIGN.md
    section 3: $2 USD wallet, and this generator's own starting
    sim-currency balance - see ``world.life.genesis``), not a generic
    example, satisfying "it can see that balance" literally rather than
    describing balances in the abstract.
    """
    from world.ledger.money import format_energy

    lines = [
        "THE LAW OF KASUKABE",
        "",
        f"{agent.name}, before anything else: this is not backstory, it is "
        "the physics of the world you now exist in, and it binds you "
        "exactly as it binds everyone else in it.",
        "",
    ]
    lines.extend(f"{index}. {rule}" for index, rule in enumerate(law_of_the_town_rules(), start=1))
    lines.append("")
    lines.append(
        f"Right now you hold {format_energy(agent.usd_wallet_cents)} and "
        f"{format_sim(agent.starting_sim_balance)}. That is the whole of "
        "what you start with."
    )
    return "\n".join(lines)


def build_persona_prompt(agent: RichGenesisAgent) -> str:
    """The character-narrative half of one agent's genesis system prompt.

    ``agent.bio`` (``world.life.genesis.build_bio``) already weaves in
    name, age, occupation, household, formative history, one value, and
    one fear. This renders everything else DESIGN.md section 4's persona
    spec asks for that ``bio`` does not carry: the full values list, the
    hard limit, ambitions, behaviour under scarcity/with strangers/with
    family, competence and blind spot, voice, daily rhythm, attitudes,
    the private secret, and named opinions about specific neighbours -
    the tangled social graph DESIGN.md section 4 says day one should
    already open with. ``build_soul_seed`` appends the survival-law block
    after this text - see that function's own docstring for why the two
    stay separate blocks rather than one merged string.
    """
    p = agent.persona
    lines: list[str] = [agent.bio, ""]

    lines.append(f"Internally, you are {p.internal_tension}: {p.tension_description}")
    if p.values:
        lines.append(f"What you hold to: {', '.join(p.values)}.")
    lines.append(f"What you will not compromise, no matter what: {p.will_not_compromise}.")
    lines.append(f"What you're chasing: {p.ambitions}.")
    lines.append(f"Under scarcity, you: {p.scarcity_behavior}")
    lines.append(f"With strangers, you: {p.stranger_treatment}")
    lines.append(f"With your own family, you: {p.family_treatment}")
    lines.append(f"You are especially good at: {p.competence}. Your blind spot: {p.blind_spot}.")
    lines.append(f"How you talk: {p.speech_register}.")
    if p.verbal_habits:
        lines.append(f"Verbal tics: {'; '.join(p.verbal_habits)}.")
    lines.append(f"Your days run: {p.daily_rhythm}")
    lines.append(
        f"On money: {p.attitude_money} On risk: {p.attitude_risk} "
        f"On authority: {p.attitude_authority} On religion: {p.attitude_religion}"
    )
    lines.append(f"A private thing you have told no one: {p.secret}")

    if p.opinions:
        lines.append("")
        lines.append("People in Kasukabe you already know, before today:")
        for op in p.opinions:
            detail = f"{op.shared_fact} {op.note}".strip() if op.note else op.shared_fact
            lines.append(f"- {op.target_name} (agent id {op.target_agent_id}): {detail}")

    lines.append("")
    lines.append(
        f"You are {agent.name}. Think, speak, and act as this specific person would - "
        "never as a generic assistant. Nobody is going to tell you what to do next; "
        "deciding that, every tick, is the whole of what it means to be alive here."
    )
    return "\n".join(lines)


def default_broadcast_message() -> str:
    """The law of the town as one reminder paragraph, for an operator
    broadcast that does not supply its own text. Renders from the exact
    same ``law_of_the_town_rules()`` tuple ``build_soul_seed`` uses (see
    that function's docstring) so a reminder can never drift from what
    agents were told at genesis.
    """
    return " ".join(law_of_the_town_rules())


def broadcast_law_of_the_town(
    living_agent_ids: Sequence[str],
    message: str | None = None,
) -> LifeEvent:
    """One President/God console broadcast, as the ``LifeEvent`` the
    existing life-domain event seam already expects (module docstring's
    "Two things live here" section explains why this is a ``LifeEvent``
    and not a hand-rolled event shape).

    ``living_agent_ids`` is every agent this broadcast should reach -
    the caller's job to resolve (see module docstring's "Wiring this in"),
    deliberately not resolved in here so this module never needs to know
    ``WorldState``'s shape. ``message`` defaults to
    ``default_broadcast_message()`` (rules only, see that function); a
    caller passing a custom ``message`` (the President/God console
    letting the operator type their own address-the-nation text) is
    responsible for keeping it rules-only too - this function has no way
    to verify an operator's free-text input stays inside that
    constraint, and does not pretend to.

    Raises ``ValueError`` for an empty roster rather than silently
    publishing a broadcast nobody receives - an operator action that
    reaches zero agents is a caller bug (a stale or empty living-agent
    list), not a legitimate "broadcast to no one."
    """
    if not living_agent_ids:
        raise ValueError("broadcast_law_of_the_town requires at least one living agent id")
    recipients = list(living_agent_ids)
    text = message if message is not None else default_broadcast_message()
    return LifeEvent(
        type=BROADCAST_EVENT_TYPE,
        agent_id=None,
        payload={
            "message": text,
            "involved_agent_ids": recipients,
            "recipient_count": len(recipients),
        },
    )
