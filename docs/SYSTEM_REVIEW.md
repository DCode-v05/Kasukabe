# Kasukabe: how a resident works, and what changed since run 19

*Written 2026-09-03 against `Real` at `1b4242d`, after run 23. Every
mechanism below was checked against the code or a run's trace before being
described here; where something is built but unproven on a live run, it
says so.*

---

## Part 1 — How an agent actually works

### 1.1 One resident is one operating-system process

A run boots `world.run_genesis`, which reads a genesis file (twenty
residents in `genesis_20.json`, fifty in `genesis_50_town.json`) and starts
**one engine process per resident**. Each gets its own directory under
`/tmp/kasukabe-agents/<agent_id>` holding four things:

| File | What it is |
|---|---|
| `persona.txt` | who this resident is, written at boot |
| `brain.sqlite` | its own memory: working memory, learning signals, knowledge |
| `session_log.jsonl` | every turn it has taken, with the tools it called |
| `memory_stream.jsonl` | observations, reflections and plans it wrote down |

Nothing is shared. Twenty residents are twenty SQLite files and twenty HTTP
servers, and the world talks to each over `POST /tick`. Measured cost is
about 27–65 MB per engine depending on how far the run has got, which is
what limits this machine to roughly twenty of them.

### 1.2 What a resident is told about itself

`persona.txt` is written once, at boot, and is two documents joined:

1. **The persona** (`build_persona_prompt`) — this resident's internal
   tension, what it values, what it will not compromise, its fears and
   ambitions, how it behaves under scarcity, with strangers, with family,
   what it is good at, its blind spot, how it talks, its daily rhythm, its
   attitudes to money, risk, authority and religion, one secret it has told
   nobody, and **nine opinions about specific named neighbours** — each a
   shared fact both parties know plus this resident's own private spin on
   it.

2. **The law of the town** (`build_soul_seed`) — the physics, stated as
   rules and never as advice: thinking costs real money billed to your own
   wallet; you can see your balance; nobody is guaranteed a living; if you
   cannot fund your thinking you go dormant and then die; sim-currency buys
   thinking at a fixed rate; **words you merely think reach nobody, only a
   tool call reaches the world**; and this is survival of the fittest, so
   what becomes of the town is for its residents to determine.

The order matters and is load-bearing. The law goes last, so the prompt
ends on *"only a tool call reaches the world"* rather than on *"internally,
you are generous but proud"*. Reversing it in the L1 harness cost eight of
thirty-seven passing scenarios and took `trade` from 80% reach to 0%.

### 1.3 What a resident is told each tick

A tick is ten game-minutes. Every third tick a resident is woken, and it
receives a freshly rendered perception — the VOLATILE layer, never cached:

- the time and whether it is daylight
- where it is standing, and who else is there
- **why it stopped working**, if a continuing action ended
- what it heard, and what was said *to it* and not yet answered
- **what it asked that nobody has answered**
- the conversation it is standing in, if any, with its id
- its trade, workplace, food, sim balance and neighbours
- its own written procedures, with its place in them marked
- **the energy line**: wallet in dollars, what the last turn cost, how many
  game-days that lasts at the current rate
- and a closing line: *nothing you have thought or written above has
  happened yet; only the tools you actually call will have occurred*

### 1.4 What it can do

**76 tools**, 65 public and 11 requiring a government seat, 27 gated on
being in a particular place. By cost category: 9 labour, 8 trade, 18
social, 1 travel, 40 fee.

They fall into groups: moving and looking; speaking and convening; working
(harvest, mine, fell timber, craft, cook); trading (`trade` person-to-
person, `buy`/`sell` at a shop, `transfer`, `post_price`); living (eat,
give, rest, `buy_energy`); employing (`hire`, `set_wage`, `pay_wage`);
authoring (`skillify`, `toolify`, `revise_*`, and conveyance by licence,
sale or teaching); civic (vote, petition, register, complain, `arrest`);
and remembering (`recall_memories`, `search_memory`, `check_debts`).

Every call is priced in sim-currency, and the price falls as proficiency
rises. Every call is dispatched through one path that checks the actor is
alive, the parameters validate, and the resident is standing where the tool
requires — and records the invocation whatever the namespace, so research
sees what in-world secrecy hides.

### 1.5 The turn

The resident's engine receives the perception, adds it to its thread, and
calls the model with the 76 tool schemas. The model may call tools, read
the results, and call more — an agentic loop, now bounded by a per-tick
call budget derived from the wallet. Each tool call goes over HTTP to the
world, which dispatches it, mutates state, emits events, and answers.
A refusal comes back as text the resident reads *and*, since this round, as
a durable `correction` signal in its own store.

When the resident stops, the engine compacts the exchange with a second
billed call, and the turn ends.

### 1.6 What happens to what it did

Every tool call emits an event. The world's recorder writes speech,
transactions, memories, skills and invented tools into the trace store,
counting every rejected write and halting the run after three consecutive
failures — because a run whose record is broken is producing nothing worth
paying for.

Learning consolidates on the wake-to-asleep edge, and now also on a waking
interval, so a run that never reaches night still consolidates. Refusals,
being corrections, are what the pipeline has to learn from.

### 1.7 Where it can be watched

The viewer at `/township-v2.html` renders the map as the page, with a
drawer over it holding **Group chats** — every conversation with its full
roster, place, whether it is open, and its lines — plus Talk, Workshop,
Council, People, Money, Health and preflight.

---

## Part 2 — What changed since run 19

Thirty-eight commits. Run 19's issue list found eleven defects by reading
the report line by line; everything since is that list, the two assessment
documents, and the tool and evaluation specs.

### 2.1 The economy exists now

Run 19 recorded a price agreed aloud — *"Thirty-six sim for all three."
"Fair price. I'll take them."* — and **zero money moved all run**. Zero
transactions between two residents, in any run to that point.

The cause was one sentence: `sell` said *"Sell an item to a business at its
shop"*, so a resident holding planks concluded it needed to register a
business, and `trade` — ungated, exactly right — never said a shop was not
required. Both descriptions now name each other.

Since then, and new in this round: posted prices, wages that can go unpaid,
a head tax, `buy_energy` closing the survival loop, `check_debts`,
`revive`, and tool conveyance. **Run 23: 12 posted prices, 11 trades, 10
buys, 7 agent-to-agent transfers, and a 400-sim debt carried unspoken
through a dozen runs finally paid.**

### 2.2 The town can be found and heard

Residents were walking into rooms that did not exist — `loc_shop_willows`
appears nowhere in the codebase; Delia Okonjo invented it and spent a run
alone in it while a customer stood in the same shop under its real name
asking how to buy something. Destination folding now resolves a place named
by its one distinctive word, business premises are listed as places, and
shops no longer share a display name.

Speech was recorded against a single listener — Goffman's dyadic model in a
town of five-person rooms. Every addressee a remark names is now recorded,
in the world and in the trace, so being answered inside a remark addressed
to somebody else discharges the question. A resident is also told what it
asked that nobody answered, which is why residents used to repeat
themselves word for word.

### 2.3 Work spans time

`keep_doing` commits a resident to one job until a stop condition. The
scheduler stops waking it between repetitions, and each repetition runs
through the same dispatch at the same cost, earning the same proficiency —
so a farmer's afternoon is twenty repetitions and one model call, and a
mastered trade becomes cheap in dollars rather than only in sim. Being
named wakes a worker; ambient talk does not.

### 2.4 The world corrects

Every refusal used to be read once by the resident and by nothing else.
They are now written as `correction` signals into the same store the
learning centre batches from. **Run 23: 70 corrections across all twenty
residents.**

### 2.5 The record stopped lying

Four things were reporting success over work that had not happened, and
each is now honest:

- 125 records silently dropped in run 16 because one resident's row was
  rejected on a foreign key — now counted, and three consecutive failures
  halt the run.
- Utterances dropped for want of a thread id — now minted and kept.
- `"20 of 20 residents consolidated"` printed 235ms after *"every further
  call would be refused"* — now reports what was **encoded**, and run 23
  correctly printed **0 of 20**.
- The report described a threading method it had stopped using.

### 2.6 Evaluation exists

There was none. Now:

- **L1 verb reach** — 37 scenarios against the real model. It caught
  `buy_energy` at 0% reach *before* it shipped into a run, and reproduced
  `skillify`'s three-run zero in seconds for pennies, which finally
  separates "the run was too short" from "the description does not reach
  the situation".
- **L3 judges** — reply quality, persona adherence, energy awareness.
- **L4** — a `no_learning` arm that actually ablates.
- **§4 task label** on every call, without which C1 cannot be grouped.
- **§6 report sections** — prices, stakes, wages.
- A scripted control bakery to compare against.

### 2.7 The prompts stopped being an assistant's

The engine's seven assistant-shaped tools were removed from a resident's
surface. The compaction summariser asked residents to report as
`[user]`/`[engine]`, and they complied — one recorded that *"user placed me
at h_quarry"*, another invented a pantry that did not exist and reasoned
from it. That prompt is rewritten on its way to the model.

---

## Part 3 — What is still not true

- **41 of 76 tools were never called in run 23.** That is the honest
  denominator behind "the town does more".
- **`buy_energy`, `set_wage`, `pay_wage` fail their own L1 scenarios** at
  0–20% reach. Description work, and the harness says so cheaply.
- **No run has reached a night**, so the learning centre has never
  consolidated a full day, no soul has drifted from its genesis persona,
  and none of the four compounding quantities has a second data point.
- **The pre-halt learning drain has never fired** — the governor trips
  inside the same tick that crosses the reserve.
- **One `toolify` call in twenty-three runs.** Authoring happened once.
- **Nothing here is proven at fifty residents.** The cast exists and is
  wired; this machine fits about twenty engines.
