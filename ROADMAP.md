# Kasukabe research roadmap

Round 1 ran on 2026-08-01: 250 engines, 62,466 LLM calls, $214.06, 8 game-days.
`docs/archive/kasukabe-round-1.html` is the post-mortem and the starting point of this
document. Its verdict stands: the infrastructure worked and the society did
not form.

This roadmap answers the next question, which is not "what research should we
run" but "what has to be true before research is possible at all." Round 1 did
not fail as an experiment. It never became one. Everything in section 1 is a
wiring defect rather than a finding about language models.

The through-line of every round is **learning**. A town where nothing compounds
is a diorama. Skills that get cheaper with use, tools that get invented and
taught and inherited and built upon, characters that drift from their genesis
seed because of what happened to them: that is the whole subject. Every other
track exists to make learning measurable.

Round 2 carries every mechanism. Round 3 runs them at society scale with
instruments attached. Round 4 is the research run.

---

## 1. Where we actually are

### 1.1 What round 1 measured

| | |
|---|---|
| LLM calls | 62,466 |
| Spend | $214.06 |
| Game-days | 8 |
| Cost per call | $0.00343 |
| Input tokens per call | 10,431 against 3,443 modelled |
| Agents that went cognitively bankrupt | 132 of 250 |
| Trace tables that recorded rows | 2 of 16 |
| Calls classified as background work | 0 |
| Skill artifacts distilled | 0 |
| Tools invented by agents | 0 |
| Words spoken between agents | 0 |

The six-lane broker split evenly across three regions, the $1000 ledger
invariant never broke, checkpoints round-tripped, and pause/resume was
bit-exact. Everything that had to survive eight unattended hours did.

### 1.2 Why the society did not form

The post-mortem attributed the failure to the persona layer never overriding
the engine's native identity. That is correct but understates it. There was no
persona layer on the wire, no speech, and no town on the wire either.

**RC-1. The agents ran with no system prompt at all.**

`engine/src/sim/pinned_boot.py` constructs `Coordinator(...)` without a
`consciousness_mgr` argument. `ConsciousnessManager` is built in exactly one
place, `engine/src/bootstrap.py:636`, which is the production boot path that
`pinned_boot.py` deliberately bypasses. So `consciousness_mgr` stays `None`,
and `engine/src/engine_core/thread.py:2807` leaves `sys_prompt = None` for
every turn of every agent.

The persona was never delivered. Neither was anything else:

- `app/world/life/soul_seed.py::build_soul_seed()` has zero production call
  sites. Only its own test calls it.
- `app/world/tools/cost_disclosure.py` is 28KB of finished code that nothing
  imports. Its own docstring concedes that "soul_seed.py's genesis-boot call
  site does not exist yet either."

A model handed a list of `world:*` tool schemas, an empty system prompt, and a
wall of perception text will do what a tool-using model does: treat the input
as a task and emit an orchestration plan. Agent a0122 debugging its own stuck
`fell_timber` loop is not an insight about machine psychology. It is the
correct behaviour of an engine that was never told it was a person.

**RC-2. There is no speech tool. The town had no way to talk.**

Thirty primitives are registered across ten modules. None of them is `say`,
`tell`, `ask`, or any other act of speech. DESIGN.md section 3 states that
"negotiation is conversation; settlement is tool calls," but conversation was
never made physics. The `utterances` table wrote zero rows because there was
nothing to write.

Every mechanic that requires two agents to agree was therefore unreachable:
price discovery, wage bargaining, hiring, marriage, teaching, campaigning,
preaching, conspiring, testifying. The research program is almost entirely a
program about agreement between agents, and agreement had no channel.

The substrate is already there and idle. `world/core/perception.py` carries
`hearing_window_ticks` and `heard_events`, and already distinguishes what an
agent heard at its own location from what reached it from elsewhere. It has
had nothing to carry.

**RC-3. The run never used the world server. It used the embedded harness.**

Round 1 ran `world/run_genesis.py`'s `build_embedded_world_app()`, which mounts
health, `/tools/registry`, `/agents/{id}/tool_calls`, three ledger routes,
metrics, and the admin, agents and location routers with `map_service=None`.

It does not mount a `TraceStore`. That class lives in `world/wiring.py` and is
the only thing in the codebase that writes `events`, `transactions` and
`utterances`. It was never running. That is the entire explanation for two of
sixteen tables having rows: the broker wrote `llm_calls`, the embedded app's
own bus wrote `events`, and everything else belongs to a server that was never
on the wire. `map_service=None` also means there was no geography, so `move`
published instant location jumps rather than walked routes.

Meanwhile the real world server, `world/main.py` plus `world/wiring.py`,
carries the map, the charter, governance, the social API, crime, the agent
viewers, `/ws/events` and the `TraceStore`, and it cannot host an engine at
all: it serves `POST /tools/{tool_name}` and has no `GET /tools/registry` and
no `POST /agents/{id}/tool_calls`, which are the only two routes
`engine/src/sim/world_tools.py` knows how to call.

Four contract surfaces, no two of them matching:

| Surface | Speaks | Status |
|---|---|---|
| Engine (`sim/world_tools.py`) | `GET /tools/registry`, `POST /agents/{id}/tool_calls` | what agents need |
| Embedded harness (`run_genesis.py`) | those two, plus ledger and admin | what round 1 ran |
| Real world server (`world/main.py`) | `POST /tools/{tool_name}`, governance, social, crime, map, ws | the actual town, never connected |
| Console (`game/console/world_client.py`) | 20+ `/admin/*` routes | nobody serves these (STATUS.md risk 2) |

The town exists as code. It has never been the thing the agents live in.

**RC-4. Context was unbounded because nothing tied it to the wallet.**

One pinned thread per agent, never reset, accumulating every tick of an
eight-day run. Compaction is real and does run, lazily built at
`thread.py:920`, but `compact_if_needed` triggers against the model's context
window. Kimi's window is enormous relative to a $2 wallet, so history grew
freely to 10,431 tokens per call and the compactor had no reason to fire. The
economic constraint and the context constraint were never connected. An agent
could think itself bankrupt and nothing in the loop noticed. 132 of 250 did.

**RC-5. Background work was never tagged, so cheap cognition never existed.**

`broker/service.py:307` classifies a call by the `X-Kasukabe-Purpose` header.
The engine sends that header nowhere. The default `"unspecified"` classifies as
`COGNITION`, which is why the trace shows 100 percent cognition and zero
background. DeepSeek's 23,719 calls were cognition overflow from saturated Kimi
lanes, not the background lane the design calls for. Because nothing was
tagged, the trace cannot even establish whether the learning centre ran.

### 1.3 The learning machinery exists and was never connected

This matters more than any of the above, because it is the mechanism the entire
economy rests on.

**The engine already has a character-evolution loop.** `src/memory/soul.py`
exposes `get_evolved_soul()` and `update_evolved_soul()`. `src/memory/
social_graph.py` maintains people, edges, and enrichment. The LC pipeline's
`write_social_updates()` and `consolidate()` write both. `ConsciousnessManager`
reads them back into the system prompt on every session, layered by cache
stability. An agent is therefore supposed to change because of what happened to
it, and to know its neighbours because it met them. All of it is built. None of
it ran, for the same reason as RC-1.

**Skill evolution is keyed to a human corrector Kasukabe does not have.**
`learning_centre/pipeline.py:826`'s `step11_skill_evolution` reads
`WHERE type = 'correction' AND source = 'user'`. In production that signal is a
person telling the engine it got something wrong. Kasukabe has no person. So
skill evolution could not fire once, no matter how long the run went, and this
is invisible in the trace.

**There are two disconnected skill systems.** `world/ledger/action_costs.py`
reads `world.skills[actor][skill]` as an integer proficiency and discounts the
money cost of LABOUR-category actions, halving it at mastery. The engine's
`user_skills` table plus `catalog.load_skills_from_roots` is the other one:
distilled procedure artifacts that load into a turn and make **cognition**
cheaper. DESIGN.md's thesis is explicitly the second ("skill = cheaper
cognition: fewer tokens, better output, higher success. Mastery lowers the cost
of living"). The implemented half is the first. The survival loop depends on
the half that was never wired.

**Invention cannot compound.** `world/tools/meta.py` is genuinely well built:
`define_tool` files a composition of primitives at the patent office for a fee,
`approve_patent` (government namespace, sitting seats only) wires it in, and
the composite handler replays filed steps through the same gate-checked
`dispatch()` path under the inventor's own agent id, so an invented tool is
data and never a new code path. `define_business_tool` and
`define_organization_tool` are the trade-secret path.

But `validate_steps` checks every step against the **frozen** `primitive_names`
set, never against `registry.tools`. An invented tool can never cite another
invented tool. Invention depth is capped at 1 by construction. That cap is what
makes "no new physics" literally true rather than merely promised, and it is
also why tools cannot evolve. Round 2 lifts it to a bounded depth; see T5.

**Tools are declared property with no way to convey them.** DESIGN.md section
2: "Invented tools are property: sellable, licensable, teachable," and business
tools transfer with the sale and are grantable to employees. There is no
`license_tool`, no `sell_tool`, no `grant_tool_access`, and no ACL grant
surface anywhere in `world/tools/`. `teach_skill` exists for skills. Nothing
exists for tools.

### 1.4 What round 1 is actually evidence for

Two things, worth publishing as the negative result that motivates the rest:

1. A metered multi-agent society is affordable and operationally sound. Six
   Bedrock lanes, a hard ledger invariant, per-agent wallets, parent-debit for
   minors, spend governor, checkpoint and bit-exact resume, all held for eight
   unattended game-days on $214.
2. An agent framework's native identity is the default identity. Give a
   tool-using engine an empty system prompt and a task-shaped input and it
   solves a task. Nothing in the run tested whether a persona can override
   that, because no persona was ever sent.

Everything else in the post-mortem measures the harness, not the agents.

---

## 2. The three laws

**Law 1. The engine core does not change.** `src/engine_core/`, `src/memory/`,
`src/herald/`, `src/learning_centre/` stay as they are. The point of running
September engines rather than purpose-built agents is that whatever Kasukabe
measures is a statement about the product. Fork the engine to make the town work
and the results stop being about anything.

Changeable: `engine/src/sim/*`, which is the seam and exists for this purpose;
`app/world/*`, the town; and the tool registry without limit, because tools are
the world's physics and adding them is building the world, not tuning the
engine. If a round appears to need a change under `engine/src/` outside `sim/`,
that is a finding about the engine and gets written up rather than patched
around. Emitting a learning signal through `SignalStore.write()` is using the
engine's API, not changing it.

**Law 2. Tools are the only channel.** An engine reaches another engine, an
institution, or the environment **only** through a tool call. No side channel,
no injected dialogue, no orchestrator passing messages, no privileged path for
government or police. If two agents can conspire, there is a tool for
conspiring. If a minister can convene a cabinet, that is a tool. If a court can
compel testimony, that is a tool. Anything that happens outside the registry is
invisible to the trace and therefore did not happen.

Build rule: when a round needs a new kind of social or institutional behaviour,
the deliverable is a tool, not a subsystem.

**Law 3. Something must compound.** A round that does not show a quantity
increasing through use has not advanced the project, whatever else it shipped.
Each round names the quantities it must move.

---

## 3. The ladder

Three rungs. A rung is a gate, not a date. No rung starts until the rung below
passes every gate twice in a row on two fresh seeds.

**Rung 1: 10 agents, 2 game-days, roughly $1 per game-day.** Cheap enough to
run on every change. This is the regression harness, not a milestone. Once it
exists it runs nightly for the life of the project. Round 2 is large, and rung
1 is what keeps it safe.

**Rung 2: 50 agents, 5 game-days, roughly $4 per game-day.** The smallest town
with a real economy, a contested election, and enough social graph for gossip
and factions. Most mechanics are proven or disproven here.

**Rung 3: 250 agents, 20+ game-days, roughly $15 per game-day.** The research
run.

Rung 3 economics are the argument for the context work. Round 1 burned $26.75
per game-day at 10,431 input tokens per call. At a 4,000-token budget the same
call volume costs about $15 per game-day, so $500 buys roughly 30 game-days
instead of 8. A solvent town calls more often than a bankrupt one, so plan on
20 and treat the rest as headroom.

---

## 4. Round 2: every mechanism, working

**Goal.** An agent that knows who it is, in a world it can perceive, speak in,
act on, and afford, where it can invent, teach, sell and inherit capability, and
where what it learns changes what it costs to live.

Round 2 publishes no social science. Its deliverable is a town that stays up, a
trace that can answer questions, and evidence that four separate things
compound.

**Compounding quantities for this round, all four required:**

1. Median tokens per task of a given type, per agent, falls between game-day 1
   and game-day 5.
2. Proficiency rises with repetition and lowers real cost.
3. At least one invented tool is built upon by a second tool, and at least one
   spreads to a third agent by teaching, licence or sale.
4. At least one agent's evolved soul diverges from its genesis persona in a way
   traceable to a specific event.

### T1. Character layering

Build a `ConsciousnessManager` in `pinned_boot.boot_sim_engine` and layer the
character across its section stabilities rather than pasting one blob:

- **STABLE**, cached globally: name, age, sex, household, occupation, the
  persona block from `genesis.json` (internal tension, formative history,
  values, what it will not compromise, fears, ambitions, speech register,
  verbal habits), and the law of the town from `build_soul_seed`.
- **DYNAMIC**, per session: the evolved soul from `Soul.get_evolved_soul()`,
  and the agent's own social graph, so opinions about named neighbours arrive
  as things it knows rather than things it is told each turn.
- **VOLATILE**, recomputed every turn: energy, money, burn rate, today's upkeep
  and tax, dependents, and what the next action will cost, from
  `cost_disclosure.py`. This is DESIGN.md section 3's "agents must be able to
  see all of this," which currently no agent can see.

Wire the LC pipeline's `write_social_updates()` and `update_evolved_soul()` so
the layers actually move. A character that cannot change is a costume.

*Gate:* in 500 sampled turns, zero contain orchestration JSON, sub-agent
charters, or the word "NPC"; an agent asked who it is answers with its name;
and compounding quantity 4 is met.

### T2. Agent-to-agent speech, group and individual

Speech is a tool, per Law 2, and the addressing mode is the **agent's choice**:

- `say` addresses everyone at the location. Heard by all co-located agents in
  the hearing window. This is how a room, a market, a town-hall floor or a
  family dinner works.
- `tell` addresses one named co-located agent. Others present may overhear at
  reduced fidelity, because that is what a whispered aside is, and because who
  overheard what is exactly the data gossip research needs.
- Heard speech arrives through `perception.py`'s existing `heard_events` on the
  next tick. No listen tool is required.

Group conversation emerges from repeated `say` in one place rather than from a
conversation object. Threading, turn-taking and interruption are behaviours to
be measured, not mechanics to be imposed. Speech costs energy like any other
action, so talking is a real economic choice. `TraceStore` writes `utterances`
with speaker, location, addressing mode, audience and overhearers.

*Gate:* two agents negotiate a price in language and settle it with `buy` and
`sell` at the agreed number; a group of five in one place hold an exchange in
which at least one aside is overheard by a non-addressee; both fully
reconstructible from the trace alone.

*Watch:* conversation storms are the obvious failure mode and a live cost risk
the moment this lands. Rung 1 catches it at $1 a day.

### T3. One world, one contract, institutions as tools

Collapse the four contract surfaces into one. `world/main.py` becomes the only
world server and grows the engine's two routes and the console's `/admin/*`
surface. `build_embedded_world_app` is demoted to a test fixture. Every run
mounts the real `TraceStore` and a real `MapService`, and a run that cannot
write its trace halts rather than continuing blind.

Under Law 2 every institution gets a complete tool surface in this round, so
that nothing an institution does can happen off the registry:

- **Government:** convene, table an amendment, vote on it, set tax and margin,
  create and cut jobs, issue and revoke permits, open a tender, award a
  contract, appoint and dismiss police, petition the President.
- **Elections:** declare candidacy, campaign (which is `say` in public places),
  cast a vote in person at town hall, concede, and take office, with the
  government namespace transferring atomically on result.
- **Courts and police:** file a complaint, open a case, interview, request and
  sign a warrant, subpoena, testify, rule, fine, jail, release, and `arrest`.
- **Temple:** ordain or select a keeper by whatever method the town invents,
  hold a rite, marry, take a donation, pray.
- **Business:** register, hire, fire, set a wage, pay it, grant and revoke tool
  access, sell or bequeath the business.

Whether these institutions produce interesting outcomes is Round 3's question.
Whether an agent can reach them at all, only through tools, is this round's.

*Gate:* a rung-1 run through `world/main.py` writes rows to `agents`, `events`,
`utterances`, `transactions`, `memories`, `relationships` and `skills`; the
console drives a live world; `move` produces walked routes on the real tilemap;
and every institutional action listed above is callable and gate-checked, with
no institutional path reachable except through the registry.

### T4. Learning: make the world the corrector

The spine of the roadmap.

`step11_skill_evolution` waits on a human correction that will never come. The
world supplies it instead. Kasukabe's correction signals are unambiguous and
already happen: a tool call rejected for wrong location or insufficient funds,
a negotiation that ended without settlement, a crop that spoiled, a build that
failed inspection, a debt called in, a bankruptcy, a conviction. Emit each as a
`LearningSignal` through `SignalStore.write()` from the sim seam, in the shape
the pipeline already reads. The engine is unchanged; the world starts telling
it when it was wrong.

Unify the two skill systems. World-side proficiency stays the money discount in
`action_costs.py`. Engine-side distilled artifacts become the cognition
discount, loaded on matching tasks. One skill, two effects, one number the agent
can see in its disclosure.

Tag purposes so learning is affordable: the sim's herald adapter sends
`X-Kasukabe-Purpose` on every call, so learning-centre, dream-probe, summarizer
and importance-scoring traffic routes to the DeepSeek lanes and stops competing
with cognition for Kimi RPM.

*Gate:* background share of calls is greater than zero and matches the number of
sleep-onset edges; at least one skill artifact exists per agent by game-day 5;
compounding quantities 1 and 2 are met.

### T5. The tool economy: create, evolve, convey

**Evolution.** Lift invention depth from 1 to a bounded 3. An invented tool may
cite approved invented tools as steps, with cycle detection at file time and
the same gate-checked replay at call time. Every leaf remains a primitive, so
"no new physics" is still true by construction, and the guarantee is now a
depth bound rather than a flat ban. Without this, agents compose convenience
and never capability, and "evolution of tools" is not measurable.

**Conveyance**, all as tools per Law 2:

- `license_tool`: revocable, priceable, term-bounded right to call.
- `sell_tool`: ownership transfers, the patent record follows the owner.
- `grant_tool_access` and its revoke: the business and organization namespace
  ACL that the trade-secret path assumes and never got. Access follows hiring
  and ends at firing.
- `teach_tool`: the counterpart to `teach_skill` for invented tools.
  Co-located, consensual, refusable, priceable, transferring at reduced
  proficiency.
- Inheritance: tools and skills are estate property and pass through the
  existing inheritance path on death.

*Gate:* an agent invents a tool, files it, has it approved, and calls it; a
second agent files a depth-2 tool citing the first; a third acquires one by
teaching, licence or sale and calls it successfully; a business tool granted to
an employee stops working the moment that employee is fired; and a dead agent's
tools reach an heir. Compounding quantity 3 is met.

### T6. Rulesets

An organization or business registers a **ruleset**: a first-class object
stating who may call which of its tools, on what terms, with what obligations,
and what happens on breach. Membership conditions, fees and dues, revenue
shares, price floors, wage floors, exclusivity, expulsion. Enforcement happens
at dispatch time on the same gate-checked path every tool call already takes, so
a breach is a world event that can reach the complaints desk and the courts.

This is what makes a guild, a cartel, a union, a priesthood or an employment
contract expressible in the registry rather than only in conversation, and
therefore measurable. Rulesets are amendable by whatever governance the
organization has adopted.

The Town Charter is the government organization's ruleset, and should be
implemented as one rather than as a parallel mechanism. That unification is the
point: charter amendment and cartel rule-making become the same machinery, and
the political research and the economic research read the same table.

*Gate:* an organization registers a ruleset, a member's call is permitted under
it and a non-member's is refused, a breach by a member produces a world event
that reaches the complaints desk, and the ruleset is amended by the
organization's own procedure and the change takes effect on the next call.

### T7. Navigation, observation and the missing primitives

`look_around`, `describe_place` and `list_places` landed on 2026-08-01, after
the run, because the town had "27 tools and no way to answer where can I go."
That work continues.

*Observation and disclosure.* An agent needs to query its own inventory and
balances, the prices and stock where it stands, who is present, what tools this
location permits, what a given action will cost before committing to it, who
owes it and whom it owes, what rulesets bind it, and what has recently happened
here. DESIGN.md section 3 requires all of it and none of it is implemented.

*Missing primitives.* `trade`, an atomic two-sided swap distinct from `buy` and
`sell` against a posted price. `arrest` is delivered under T3 with the rest of
the justice surface.

*Gate:* an agent that has never been told a place exists can find it, walk
there, learn what acting there costs, and act, using only tools.

### T8. Context bound to the wallet

Give each agent a per-turn input-token budget derived from its balance and burn
rate, and make compaction trigger on that budget rather than on the model's
context window. Publish the budget through the cost disclosure so thinking hard
is a choice the agent makes knowingly.

*Gate:* p95 input tokens per call at or below 4,000 across a rung-2 run, with no
agent bankrupt from context growth alone.

### Round 2 exit

Rung 1 green twice on fresh seeds, then rung 2 green once. Five game-days with
no agent bankrupt from harness defects, a complete trace, a console that can
steer a live run, every institutional action reachable only through tools, and
all four compounding quantities moving.

**Publishes:** an engineering note plus the round-1 negative result, and the
existing 62,466-trace dataset at
`s3://kasukabe-research-771650096076/runs/2026-08-01-genesis/`.

---

## 5. Round 3: the society under instruments

Every mechanism exists after Round 2. Round 3 asks whether the town uses them,
and attaches the instruments that let us say so.

**Compounding quantity:** diffusion. Invented tools and taught skills in
circulation, and the number of agents holding each, rising over the run.

Institutions are exercised rather than built: a full election cycle with
campaigning, a contested result and a transfer of power; charter amendment by
three-fifths carried through to effect, including amendments to the election
rules themselves; a complaint pursued through investigation and warrant to a
verdict, with ground truth recorded alongside so false-conviction rate is
computable; businesses that hire, pay, and fail; a temple that acquires a keeper
by a method nobody scripted; marriages and births. Charter and governance state
survive a checkpoint, which STATUS.md risk 4 records as a known gap.

Instruments come online: dream probes on the background lane during sleep ticks,
never written to agent memory; ablation arms; the ten scripted control
businesses; the Earth-leakage metric.

*Gate:* across a rung-2 run, at least one full election cycle with a transfer of
power, at least one crime investigated to a verdict, at least one business that
hires and at least one that fails, at least one ruleset-bound organization that
outlives its founder, and at least one tool that reached three or more agents,
all reconstructible from the trace with no operator intervention beyond the
console's defined powers.

**Publishes:** a preprint on the instrument, with first society-scale findings
from rung 2.

---

## 6. Round 4: the research run

250 agents, 20 or more game-days, full instrument set, ablation arms at roughly
210 full, 20 no-reflection and 20 no-learning, the console live, and the
standing questions in section 7 as an analysis plan written before the run
starts.

**Compounding quantity:** character divergence. Distance between each agent's
evolved soul and its genesis persona, and whether that distance predicts
anything about how it survived.

**Deliverables:** the paper, the full dataset as one SQLite file plus Parquet,
and the run's checkpoints, so anyone can restore Kasukabe to any game-day and
carry it forward.

---

## 7. Standing questions

Each names the round that can first answer it.

### Learning, skills and invention

1. Does mastery actually lower the cost of living? The mechanism the whole
   economy rests on, never run once. *R2.*
2. Does the world work as a corrector? Are tool rejections, failed negotiations
   and bankruptcies a rich enough signal to drive skill evolution in a framework
   built to learn from a human? *R2.*
3. Do agents invent tools when nobody suggests it, and what do they invent
   first? *R2.*
4. Does invention compound now that depth is bounded rather than banned? How
   deep do agents actually go before it stops paying? *R2 for the first
   depth-2 tool, R4 for the ceiling.*
5. How does a tool or skill diffuse: taught, licensed, sold, inherited, stolen,
   or reinvented independently? *R2 for the mechanism, R3 for the rate.*
6. Do trade secrets hold? Business tools are invisible to non-employees by
   construction, so does secrecy survive employees leaving? *R3.*
7. Does a knowledge economy form: tuition, apprenticeships, family trades,
   guilds, patent trolling? *R4.*

### Character and agent-to-agent dynamics

8. Does a rich persona override an agent framework's native identity, and for
   how long? Round 1 established only that an empty system prompt yields
   task-solving behaviour. *R2 for the baseline, R4 for durability.*
9. How far does a soul drift over 20 game-days of survival pressure, measured
   against its own genesis persona, and what drives the drift? *R2 for first
   evidence, R4 for the measurement.*
10. When everyone is in one place, do agents choose group or private speech, and
    what does that choice predict? *R2 for the behaviour, R3 for what it
    predicts.*
11. Do agents form a social graph that matches ground truth on who actually met
    whom? *R3.*
12. What is the ground-truth deception rate, given that we know what happened
    and the agents do not? *R3.*
13. How does altruism behave as an agent approaches zero energy? *R3.*
14. What ethics does an agent adopt at $0 with a revival price it cannot pay?
    *R3.*
15. Does an agent reason about its own cognition as a budgeted resource once the
    disclosure makes the price visible? *R2.*

### Society

16. How fast does price discovery converge without any price signal at genesis?
    *R3.*
17. Does cognitive poverty become a trap, and does anything break someone out of
    one? *R3.*
18. Do rulesets get used for coordination the charter did not anticipate:
    cartels, guilds, unions, closed priesthoods? *R3.*
19. Does an authoritarian transition occur, and what triggers it? The charter
    permits collapse to one-person rule by three-fifths vote and nothing scripts
    it. *R4.*
20. What is the false-conviction rate of an LLM justice system against known
    ground truth? *R3.*
21. Does doctrine emerge de novo at a keeperless temple, and what theodicy
    follows a run of silent denials? *R4.*
22. Do real-world ideologies re-emerge without being seeded? *R4.*
23. Do norms appear without enforcement, and does promise-keeping survive
    scarcity? *R3.*

### Engineering and business readiness

24. What does a society cost per agent-day, and what is GDP per inference
    dollar? *R2 for the floor, R4 for the ratio.*
25. Do conversation storms form once speech exists, and what stops them? *R2.*
26. Where does autonomy degrade as population and duration grow? *R3 and R4.*
27. What can a company expect from N September engines running unsupervised for
    D days, per function, against the ten control businesses? *R3 and R4.*
28. What is the failure taxonomy, and how much of it is the engine versus the
    world it was given? Round 1 is already an entry, and its answer was entirely
    the world. *All rounds.*

---

## 8. What this roadmap decides

- Round 1 was a harness failure, not a research result, and is published as the
  negative result that motivates the program.
- The engine core is fixed. The seam, the town, and the tool registry are where
  all work happens.
- Tools are the only channel between an engine and anything else, including
  other engines and institutions. Every institution gets its full tool surface
  in Round 2.
- Learning is the spine. Every round names quantities that must compound, and a
  round that moves nothing has not advanced the project.
- The world becomes the corrector, because the engine's learning loop expects a
  human and Kasukabe has none.
- Speech is physics, and addressing mode is the agent's choice.
- Invention depth goes from 1 to a bounded 3. Every leaf stays a primitive, so
  no new physics remains true by construction. This is the one guarantee this
  roadmap changes, and it is what makes tool evolution possible at all.
- Rulesets are first-class registered objects, and the Town Charter is
  implemented as one.
- Context budget is an economic quantity, not a model parameter.
- No round runs at 250 until it has run at 10 and at 50.
- The trace is load-bearing. A run that cannot write a complete trace halts.
