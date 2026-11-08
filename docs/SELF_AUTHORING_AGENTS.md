# Self-authoring agents — the wiring plan

**Target:** the `Real` branch, currently running 40 engines against Bedrock.
**Goal:** agents in the live sim author their own tools and skills, both get
cheaper with practice, and an observatory shows what the town cannot do.

**Status:** **All five parts are built.** A–D (1,920 app tests, 4,024 engine
tests, 59 of them new) plus Part E, the viewer surfaces — E.1–E.5 and E.7 by
DCode-v05, E.6 (the observatory) delivered as part of C, and the non-map UI
rebuilt on top. What building them changed about this plan is its own section
— *What building it changed*, before *Settings*.

**Legal under ROADMAP Law 1**, which says outright: *"Changeable:
`engine/src/sim/*` … `app/world/*`, the town; and the tool registry **without
limit**."* Nothing here touches `engine_core/`, `memory/`, `herald/` or
`learning_centre/`.

---

## Scope

Being honest up front — the full design is larger than this. What is in, and
what is deliberately held back:

| In scope | Held back, and why |
|---|---|
| Cost engine wired | **Tool revision / versioning** — `revise_tool` is real value but nothing breaks without it. Part D. |
| Registry refresh | **Registration fees** — `patent_fee_paid_sim` stays unused; filing is free for now. |
| `skillify` with written artifacts | **Routing skills** — they emerge for free once artifacts exist; no code, so nothing to cut. |
| `toolify` (thin wrapper on `define_tool`) | **Outcome quality scoring** — Part A counts *successful* calls only, not whether the call was wise. |
| Repetition counting | **Business/org namespaces for skills** — every skill is open at first; the charter path covers governmental ones. |
| Observatory, read-only | **Auto-surfacing limitations into perception** — the read-only half ships; the write-back is Part D. |
| UI: Skills tab + observatory page (Part E) | **Workshop / Council tabs** — valuable, but nothing is blocked without them. |

Everything held back is *additive*. Nothing in Part D requires redoing A–C.

---

## Tools and skills are different entities

This is the model everything below is wired to. Getting it wrong collapses two
mechanisms into one.

**A tool is the basic unit.** It is the only thing that causes an effect in the
world — the ledger moves, an item changes hands, a body moves across the map.
There are 42 primitives, and a **composite tool** is a filed sequence of them.

**A skill is an ordered workflow over one or more tools.** Step A, then step B,
then *call the tool*, then C and D. A skill may also reference another skill as
one of its steps. It is a written procedure with structure, held by a person,
that gets better the more it is followed.

### The distinction that matters: who walks the steps

Both a composite tool and a skill are "an ordered list of steps that call
tools", so the difference has to be mechanical, not verbal. It is:

| | Composite tool | Skill |
|---|---|---|
| Who executes the steps | **The registry.** `build_composite_handler` runs `for step in steps: dispatch(...)` — a blind loop | **The agent.** One step per decision, with the world observed in between |
| Decision between steps | none | every step |
| Can it branch, pause, abandon? | no | yes |
| Cost | one call, charged per step | each step charged as an ordinary call |
| Improves with use | no — a macro is a macro | yes — proficiency rises with completions |
| Can be taught | copied | taught, and the student starts with the words but not the practice |
| Answers | *what can be done* | *how well, and in what order* |

> **The composite runs without the agent. The skill runs through the agent.**

That single sentence is why both are worth having. A composite is for a
sequence with no judgement in it — three steps that always go together. A skill
is for a procedure where what you saw at step B changes what you do at step C.

### What this means for the data

`discounts_tools` is no longer declared. It is **derived** from the steps of
kind `tool`. An agent cannot point a skill at 42 tools without writing 42
steps, which quietly closes the junk-skill hole without a rule.

```python
class SkillStep(BaseModel):
    order: int
    kind: Literal["tool", "skill", "check"]
    ref: str | None      # a tool name, or another skill's name; None for a check
    note: str            # what to do here, or what to look for

class DefineSkillParams(BaseModel):
    name: str                    # "limbing"
    preconditions: str           # when this workflow applies at all
    steps: list[SkillStep]       # THE WORKFLOW, in order
    # discounts_tools is derived: every step with kind == "tool"
```

A worked skill, as an agent would file it:

```
skill: "clearing a stand"           preconditions: "the timber order is unfilled"
  1  check   — is the saw sharp, and is anyone else working this stand?
  2  tool    look_around
  3  skill   limbing                 ← a skill referencing a skill
  4  tool    fell_timber   { quantity: 1 }
  5  check   — is the load worth carrying yet?
  6  tool    move          { destination: "Harvey Oak Supply Store" }
  7  tool    sell          { good: "timber" }
```

Steps 3 and 5 are what a composite tool cannot express: one delegates to
another procedure, the other is a judgement the agent makes with the world in
front of it.

### Two consequences to wire deliberately

**Proficiency counts completions, not calls.** If a skill's proficiency rose on
every `fell_timber`, then "skill" and "tool use" would measure the same thing
and the workflow would be decoration. A skill advances when the agent walks
*its* steps in order, and completes at the last one. That also yields the
research metric worth having: **adherence** — does an agent actually follow the
playbook it wrote?

**Skills referencing skills need a depth limit.** Step 3 above expands into
another workflow. Cap the nesting at 2, matching the guard
`build_composite_handler` already uses, so a malformed pair of skills that name
each other cannot loop.

---

## The two blockers, first

Neither is optional. Both are small.

### Blocker 1 — no tool call costs anything  ✅ *closed by Part A*

`app/world/ledger/action_costs.py` was a complete, tested cost engine: 42 tools
priced, six with `skill_discount=True`, a curve to 50% off at proficiency 100 —
that **`registry.py` never imported**. And `SKILL_FOR_TOOL`, the map from tool
to the skill that discounts it, was described in that file's own docstring as
*"sketched, not provided."*

**Both are now done.** `dispatch()` charges before the handler and counts a
repetition after it; the six entries are written; that docstring's own
"integration point (for whoever wires `world/tools/registry.py`)" has been
taken up. Measured: first harvest 17 sim, sixty-first 9 sim.

### Blocker 2 — the engine reads the tool list once  ✅ *closed by Part B*

`engine/src/sim/pinned_boot.py:223`

```python
tools: ToolRegistry = await world_tools.build_world_tool_registry(world)
```

Ran **once, at boot**, and nothing re-fetched: an agent could file a tool at
tick 500 and **nobody could call it, including the inventor**, until the run
restarted.

**Now closed.** `ToolRegistry.version` bumps in `register()` — the single choke
point every invented tool passes through — `GET /tools/registry/version` serves
it from both world hosts, and `SimEngineState.send_tick` calls
`refresh_world_tool_registry` each tick: one small number when nothing has
changed, a full fetch only when it moves. Verified over real HTTP.

---

## The wiring, end to end

This is the path a self-authored capability travels. Every hop is a file that
already exists.

```
  AGENT (engine process, port 9100+n)
    │  the model emits a tool call
    ▼
  engine/src/sim/world_tools.py
    │  WorldClient.call_tool()  ──HTTP──►  world :8100
    ▼
  app/world/tools/registry.py :: dispatch()
    │  1 validate params
    │  2 location gate
    │  3 actor requirements
    │  4 ◄── NEW: charge, discounted by skill          (Part A)
    │  5 handler runs
    │  6 ◄── NEW: record outcome + repetitions++       (Part A)
    ▼
  app/world/tools/meta.py
    │  toolify / skillify write here                    (Part B)
    │  → world.custom_tools + tools_registry table
    ▼
  GET /tools/registry  (world :8100)
    │  ◄── NEW: carries a version/etag                  (Part B)
    ▼
  engine re-fetches when version changes                (Part B)
    │
    ▼
  the new tool is callable by every agent
```

And the second loop, for skills:

```
  skillify writes skills.skill_artifact_text
    ▼
  app/world/retrieval_depth.py :: render_perception_text()
    │  ◄── NEW: inject the agent's own skill artifacts  (Part B)
    ▼
  app/world/run_genesis.py :2622  builds perception text
    ▼
  :2633  POST /tick  {tick, is_sleeping, perception}
    ▼
  the agent decides with its own playbooks in context
```

---

## Running 30–50 agents on 8 GB — the namespace backend

**Nothing needed stripping.** The ask was to remove Docker and the sandboxes,
run on OS primitives, and give each engine its own `SEPTEMBER_HOME`. All three
were already true:

| Believed | Measured |
|---|---|
| Docker runs the engines | `run_genesis.py` never invokes Docker. The supervisor was already plain OS processes |
| The sandbox has to go | `engine/src/sim/` imports only `sandbox.permissions`, a permission *data type*. A sim engine registers world tools, a session log and a memory stream — **no shell tool** — so the bubblewrap executor is never reached |
| Each engine needs its own home | Already `<home_root>/<agent_id>`, holding that engine's SQLite, skills and everything else |

Deleting `engine/src/sandbox/` would also break ROADMAP Law 1 — changes under
`engine/src/` outside `sim/` are *"a finding written up rather than patched
around"* — and would remove the shell-tool suite for no runtime gain.

### The real problem: one OS process per agent

The crash was never Docker. It was that **every engine paid for its own Python
interpreter and the engine's imports** — about 56 MB — and 40 of those is
4.4 GB on a machine with ~1.3 GB free. It does not fail politely: the machine
swaps, then the OOM killer takes something, often the world server, so the run
dies looking like a world bug.

### The fix: `SEPTEMBER_HOME` *is* the namespace

One process, one directory per agent. No container, no sandbox, no port.
`engine/src/infra/september_home.py` already declares that one directory holds
*"the database, config, credentials, skills, plugins, workspaces, sidechains,
tool results, caches, logs. Nothing the engine writes lives outside it."* That
is already a complete isolation boundary — so `NamespaceSupervisor` keeps
exactly it and drops only the process around it.

Measured on an 8 GB M1, **30 real engines booted**:

| Backend | Per engine | 30 agents | 50 agents |
|---|---|---|---|
| `process` — one OS process each | ~110 MB | 3.3 GB ✗ | 5.5 GB ✗ |
| **`namespace`** — one home each, shared process | **2.8 MB** | **190 MB ✓** | **270 MB ✓** |

A 44× difference, and the difference between a 50-agent run and an
unresponsive laptop. `namespace` is now the default; `process` and
`firecracker` remain for machines with the memory.

Two things fall out of it beyond the memory: a tick goes straight into the
engine object, so the run drops **50 listening sockets and a round trip per
agent per tick**; and there is no port range to collide with anything else.

**What is kept, and what is given up.** Kept: every byte an engine writes
stays in its own directory, agents cannot read each other's SQLite, memory or
skills, and a restart continues a resident rather than replacing them. Given
up: an unhandled exception is no longer contained by a process boundary — so
`start()` records a failed boot instead of raising, and one bad profile cannot
abort the other 49. That is the trade, and on this hardware the alternative is
not "50 isolated agents", it is no run at all.

### The preflight

`app/world/memory_budget.py` refuses before the first spawn rather than letting
the machine swap:

```
40 engines need about 4400 MB, but only 1540 MB is free after reserving
512 MB for the world server, broker, viewer and the OS.
This machine can run about 9 engines right now.
```

It is backend-aware — the same 50 agents are ~270 MB namespaced and 5.5 GB as
processes. It reads memory free *now*, not total RAM, so closing an IDE
genuinely raises it. `KASUKABE_SKIP_MEMORY_CHECK=1` overrules it, and it
applies only when a real supervisor is about to spawn.

One correction worth recording: the first draft reserved 2048 MB for "system"
and concluded an 8 GB laptop could run *one* engine. That double-counted —
free memory already excludes what the OS holds. Measuring the run's own
services (world 87 MB, viewer 12 MB, broker ~20 MB) set the reserve at 512 MB,
and a test pins it there.

**Tests:** `test_namespace_supervisor.py` — 11, `test_memory_budget.py` — 9.

---

## Where everything lives

Paths are relative to the repo root. `/Users/sharan/Downloads/kasukabe` was
synced to `Real` on 2026-08-26, so every path below resolves in the main
project folder — and in the `kasukabe-real` worktree, which holds the same
commit. Line numbers were read off the tree that day and will drift as files
change; the symbol name is the durable half, so grep for that if a number is
stale.

### Part A — acting costs, practice pays

| What | File | Line |
|---|---|---|
| `SKILL_FOR_TOOL` — the six seeded trades | `app/world/ledger/action_costs.py` | 375 |
| `REPS_TO_MASTERY = 60` | `app/world/ledger/action_costs.py` | 389 |
| `proficiency_from_reps()` — the square-root curve | `app/world/ledger/action_costs.py` | 392 |
| `proficiency_for()` — highest-wins across skills | `app/world/ledger/action_costs.py` | 422 |
| `refund_action()` — reversing a charge whose act failed | `app/world/ledger/action_costs.py` | 960 |
| The charge, in `dispatch()` | `app/world/tools/registry.py` | 365 |
| `bump_skill_repetition()` | `app/world/tools/state.py` | 463 |
| **Tests** — 11 | `app/world/tools/tests/test_skill_economy.py` | |

### Part B — skillify, toolify, a registry that refreshes

| What | File | Line |
|---|---|---|
| `skillify` — author a workflow | `app/world/tools/meta.py` | 619 |
| `toolify` — invent a tool, ungated | `app/world/tools/meta.py` | 859 |
| `describe_primitives` — the priced vocabulary | `app/world/tools/meta.py` | 779 |
| `validate_skill_steps()` — shared by skillify and revise | `app/world/tools/meta.py` | 575 |
| `quote_action_cost` — price one specific call | `app/world/tools/cost_disclosure.py` | |
| `SkillStep`, `SkillDefinition` | `app/world/tools/models.py` | 377 |
| `SkillProgress` | `app/world/tools/models.py` | 436 |
| `advance_skill_workflows()` — the step tracker | `app/world/tools/state.py` | 331 |
| `skill_tools` — tool → skills reverse index | `app/world/tools/state.py` | 284 |
| Registry version bump, in `register()` | `app/world/tools/registry.py` | 204 |
| `GET /tools/registry/version` | `app/world/main.py`, `app/world/run_genesis.py` | |
| `refresh_world_tool_registry()` — engine refetch | `engine/src/sim/world_tools.py` | 243 |
| Per-tick refresh hook | `engine/src/sim/pinned_boot.py` | |
| Prices wired into tool descriptions | `app/world/tools/bootstrap.py` | |
| Workflow rendered into the prompt | `app/world/retrieval_depth.py` | |
| **Tests** — 12 / 6 / 8 / 9 | `test_skillify.py`, `test_skillify_governance.py`, `test_tool_vocabulary.py`, `test_agent_authoring_reach.py` (all in `app/world/tools/tests/`) | |

### Part C — the observatory

| What | File | Line |
|---|---|---|
| `failed_at` — the hop, as a class attribute | `app/broker/errors.py` | 35 |
| `LedgerUnavailableError` — the mislabelled 502, fixed | `app/broker/errors.py` | 130 |
| `_require_usable_ledger_response()` | `app/broker/ledger.py` | 286 |
| Failure bodies actually recorded | `app/broker/service.py` | 582 |
| `failed_at` column | `app/supabase/migrations/20260825120000_llm_calls_failed_at.sql` | |
| `ToolRefusalRecord` | `app/world/tools/models.py` | 452 |
| `unmet_requirement_for()` — the stable grouping key | `app/world/tools/registry.py` | 500 |
| `record_refusal()` — written *after* the rollback | `app/world/tools/registry.py` | 531 |
| `GET /world/refusals` | `app/world/main.py` | 366 |
| Observatory service (197 lines) | `app/observatory/app.py` | |
| Capability gaps | `app/observatory/gaps.py` | |
| Run believability | `app/observatory/health.py` | |
| Failures by hop | `app/observatory/hops.py` | |
| The page | `app/observatory/static/index.html` | |
| **Tests** — 14 / 6 | `app/observatory/tests/test_observatory.py`, `app/world/tools/tests/test_refusal_log.py` | |

### Part D — closing the loop

| What | File | Line |
|---|---|---|
| `GapFeedback` — the one component that writes in | `app/world/gap_feedback.py` | 84 |
| `notice_for()` — rate limit, headcount, audit log | `app/world/gap_feedback.py` | 92 |
| `render_notice()` — names the gap, never the answer | `app/world/gap_feedback.py` | 133 |
| `revise_skill` | `app/world/tools/meta.py` | 966 |
| `revise_tool` | `app/world/tools/meta.py` | 1101 |
| `tools_registry` writer — the table nothing wrote to | `app/world/wiring.py` | 788 |
| `skills` writer — artifact text + real repetitions | `app/world/wiring.py` | 711 |
| Trade-secret lock, on `teach_skill` | `app/world/tools/primitives/social.py` | 204 |
| `Charter.standing_orders` | `app/world/charter/models.py` | |
| **Tests** — 19 | `app/world/tools/tests/test_part_d.py` | |

### Part E — the viewer

| What | File | Line |
|---|---|---|
| Workshop + Council API | `app/world/workshop_api.py` | |
| Panels, roster step, 15s polling | `app/game/static/js/township/part-e.js` | 8 |
| Mid-workflow dot on the map (E.5) | `app/game/static/js/township/village2.js` | 1733 |
| Dock shell | `app/game/static/js/township/shell.js` | |
| Page | `app/game/static/township-v2.html` | |
| Teammate's CSS layer | `app/game/static/css/instrument.css`, `css/parts/{controls,books,resident,overlays}.css` | |
| **Non-map rebuild** (578 lines, loads last) | `app/game/static/css/parts/revamp.css` | |
| └ the `top: 82px` search fix | `app/game/static/css/parts/revamp.css` | 61 |
| **Tests** — 10 | `app/world/tests/test_workshop_api.py` | |

### Running on a small machine

| What | File | Line |
|---|---|---|
| `NamespaceSupervisor` — one process, one home per agent | `app/world/namespace_supervisor.py` | |
| `NamespaceAgentGateway` — a tick with no HTTP | `app/world/run_genesis.py` | |
| Backend selection (`namespace` is default) | `app/world/run_genesis.py` | |
| Memory preflight — refuses before the first spawn | `app/world/memory_budget.py` | |
| Measured per-engine constants, both backends | `app/world/memory_budget.py` | 34 |
| **Tests** — 11 + 9 | `test_namespace_supervisor.py`, `test_memory_budget.py` | |

### Running it

```bash
cd app
uv run uvicorn world.main:app --port 8100        # world  (314 residents)
WORLD_BASE_URL=http://127.0.0.1:8100 \
  uv run uvicorn game.server:app --port 8400     # viewer → /township-v2.html
uv run uvicorn observatory.app:app --port 8500   # observatory
```

The full run with real engines and Bedrock spend is under *Part C.3* below.

---

## Part A — make actions cost, and practice count

**Outcome:** a live 40-agent run where in-game balances diverge,
skilled agents work cheaper, and `repetitions_observed` climbs.

### A.1 Write the map — `app/world/ledger/action_costs.py`

```python
SKILL_FOR_TOOL: dict[str, str] = {
    "harvest": "farming",       "fell_timber": "forestry",
    "mine": "mining",           "craft": "artisanry",
    "cook": "cooking",          "build_structure": "masonry",
}
```

Six entries because six rules carry `skill_discount=True`. Anything absent
resolves to proficiency 0 and is charged full price — correct for `move`,
`say`, `look_around`.

### A.2 Charge from dispatch — `app/world/tools/registry.py`

In `dispatch()`, immediately **before** `spec.handler(...)`:

```python
from world.ledger.action_costs import ActionActor, DEFAULT_SCHEDULE

actor_ctx = ActionActor(
    actor_id=actor_id,
    skill_proficiency=world.skills.get(actor_id, {}).get(
        SKILL_FOR_TOOL.get(tool_name, ""), 0),
    distance_tiles=resolve_move_distance(world, actor_id, params)
                   if tool_name == "move" else 0,
)
await DEFAULT_SCHEDULE.charge(
    world.ledger, tool_name, params, actor_ctx, world.tick())
```

This is the snippet `action_costs.py`'s own module docstring instructs you to
add. **Order matters:** charging after the gates means a refused call never
costs, and `registry.execute()`'s existing `restore(snapshot)` already rolls
back a failed charge cleanly. Let `ActionUnaffordableError` propagate, exactly
as `ToolValidationError` already does.

### A.3 Count repetitions — same function, after the handler returns

```python
skill = SKILL_FOR_TOOL.get(tool_name)
if skill and outcome is not None:
    world.bump_skill_repetition(actor_id, skill)   # new, ~15 lines
```

> **Part A only.** This counts raw successful calls, which is enough to prove
> the loop moves. From Part B the counter is driven by **workflow completion**
> instead — see B.2 — because otherwise "skill" and "tool use" measure the same
> thing and the workflow is decoration.

`bump_skill_repetition` increments `repetitions_observed` and raises
`proficiency_level` on a curve with diminishing returns near the cap. **The
column already exists and is written by nothing.**

**Decided:** `REPS_TO_MASTERY = 60` on a square-root curve — first discount
felt after four uses, half price at sixty. See *Settings — decided* below for
the arithmetic.

### A.4 Verify

```bash
# after ~1 game day of a 40-agent run
sqlite3 app/data/kasukabe.db \
  "SELECT agent_id, task_type, proficiency_level, repetitions_observed
   FROM skills ORDER BY repetitions_observed DESC LIMIT 10;"
```

Balances should spread; the workers should not be the poorest.

> **Decided:** in-game insolvency **stops an agent acting, it does not kill**.
> A second mortality path would confound the survival experiment — see
> *Settings* below.

---

## Part B — skillify, toolify, and a registry that refreshes

**Outcome:** an agent files a skill mid-run, its playbook appears
in its own prompt, and a tool filed at tick 500 is callable at tick 501.

### B.1 `skillify` — `app/world/tools/meta.py`

A skill is an **ordered workflow**, not prose. See *Tools and skills are
different entities* above for why the steps are walked by the agent rather than
replayed by the registry.

```python
class SkillStep(BaseModel):
    order: int
    kind: Literal["tool", "skill", "check"]
    ref: str | None      # a tool name, or another skill's name; None for a check
    note: str            # what to do here, or what to look for

class DefineSkillParams(BaseModel):
    name: str                    # "clearing a stand"
    preconditions: str           # when this workflow applies at all
    steps: list[SkillStep]       # the workflow, in order
```

The handler:

1. **Validates every reference.** A `tool` step must name a tool in the live
   registry; a `skill` step must name an existing skill. Nothing may point at
   something that does not exist.
2. **Caps nesting at 2**, matching `build_composite_handler`'s own guard, so two
   skills naming each other cannot loop.
3. **Derives `discounts_tools`** from the steps of kind `tool`. It is never
   declared — which is what stops an agent pointing a skill at 42 tools without
   writing 42 steps.
4. **Serialises the workflow into `skills.skill_artifact_text`.** The column is
   TEXT and already exists; JSON goes in it.
5. **Seeds the inventor at proficiency 1.**

Then `SKILL_FOR_TOOL` stops being a constant and becomes a reverse index built
from world state — *tool → the skills whose steps name it* — seeded with the six
built-in trades. That is the point: the vocabulary stops being ours.

### Who has to ask permission — almost nobody

**An ordinary skill needs no approval, and neither does an ordinary tool.**
An agent files a workflow and uses it the same tick. No fee, no review, no
waiting on a seat-holder. A skill only orders tools the agent could already
call one at a time, so approving it would be asking permission to do — in a
particular order — things nobody needs permission for.

Gating every skill would also break the thing the experiment is for. If a
seat-holder must bless each procedure before anyone may follow it, the town's
competence is bounded by its government's attention, and what you measure is a
bureaucracy rather than a society.

> **No patent office.** Earlier drafts routed invented tools through
> `charter/patent_office.py`. That is dropped from the plan. A village does not
> run a patent office; it keeps a book of rules, and the only things that go in
> the book are the things that bind everybody.

### The town's governance is four things

| | What it is | Where |
|---|---|---|
| **Government** | The seats. Who holds office right now. | `charter/government.py` |
| **Elections** | How seats change hands. | `charter/elections.py` |
| **Amendments** | How the rulebook changes — the council's process. | `charter/amendments.py` |
| **Rules and regulations** | The rulebook itself: every standing order in force. | the `Charter`, as amended |

The amendment lifecycle already in the tree is a village council's, not a
software workflow:

```
PROPOSED  →  PASSED_AWAITING_PRESIDENT  →  ENACTED
   ↓                    ↓
REJECTED             VETOED
```

A seat-holder proposes. The council votes and a threshold carries it. It goes
to the president, who signs it into force or vetoes it. Enacted, it becomes
part of the town's rules and regulations, and it binds everyone until another
amendment repeals it.

Citizens are not silent in between: `charter/complaints.py` and
`charter/land_petitions.py` already give the ungoverned a way to object, which
is what makes the council answerable rather than merely procedural.

### Government skills are standing orders

The rule is mechanical, not declared — the same principle as
`discounts_tools`:

> A skill is **governmental** if any of its steps names a tool carrying
> `ActorRequirements(require_government_seat=True)`.

Those tools already exist and are already marked: sentencing and subpoenas in
`justice.py`, ordinances in `civic.py`, land seizure in `property.py`. A
workflow that strings them together is not a trade — it is **a procedure of
state**. It does not belong to the agent who wrote it; it belongs in the book
of rules, and it enters the book the way every other rule does.

```python
# app/world/tools/meta.py — inside the skillify handler
wields_state_power = any(
    registry.spec(step.ref).actor_requirements.require_government_seat
    for step in params.steps if step.kind == "tool"
)
if wields_state_power:
    # A standing order. Goes to the council as an amendment: proposed,
    # voted to threshold, signed or vetoed by the president. Filed as
    # PENDING and not followable until ENACTED.
    await world.charter.amendments.propose_amendment(
        proposer_agent_id=agent.agent_id,
        kind="standing_order",
        body=serialise(params.steps),
    )
else:
    commit(skill)        # ordinary trade skill: immediate, free, ungated
```

Three properties fall out of doing it this way:

- **State power cannot be smuggled in.** The seat requirement is still checked
  at `dispatch()` on every individual step, so an un-enacted governmental skill
  fails at its first state-power step anyway. The council governs the
  *procedure*; the seat gate governs the *act*.
- **Nothing is declared, so nothing can be mislabelled.** An agent cannot mark
  a governmental workflow "ordinary" to skip the council, because the classifier
  reads the steps.
- **A standing order can be repealed.** It went in as an amendment, so it comes
  out as one. A trade skill belongs to its author forever; a procedure of state
  lasts exactly as long as the council leaves it in the book.

**What this changes from earlier drafts.** Every previous version had a
government approval gate on public skills, and leaned on it to answer "what
stops a junk skill". That gate is gone for ordinary skills, so the arithmetic
guards now carry the whole load — and they are stronger for it:

1. `discounts_tools` is derived from the steps, so pointing at 42 tools costs
   42 steps.
2. Proficiency only rises from **completing the workflow**, so a junk skill is
   worth 0% until somebody actually walks it end to end.
3. Highest-wins means a junk skill overlapping a real one adds nothing.

A junk skill is now self-defeating rather than refused — which is the better
outcome, because refusing it needs somebody's attention and self-defeat does
not.


### B.2 Following a workflow — the progress tracker

The one genuinely new piece of state. Without it a skill is a document nobody
is measured against.

```python
# app/world/tools/state.py
class SkillProgress(BaseModel):
    agent_id: str
    skill_name: str
    next_step: int          # which step the agent is expected to take next
    started_tick: int
    last_advance_tick: int
```

Driven from `dispatch()`, in the same place Part A counts repetitions:

- A tool call matching step 1 of a skill the agent holds **opens** progress.
- A call matching `next_step` **advances** it.
- Reaching the last step **completes** it — and that is what increments
  `repetitions_observed` and raises proficiency.
- No advance for N ticks (suggest 12, one game-hour) **abandons** it.

Abandonment is not a failure to hide. *Which skills get abandoned, and at which
step*, is one of the more interesting things the observatory can report — it is
the town telling you which of its own procedures do not survive contact with the
world.

### B.3 The workflow in the prompt

`app/world/retrieval_depth.py :: render_perception_text()` already assembles
what an agent is told each tick and already has a text budget. Add the agent's
skills whose `preconditions` match the current perception, rendered as numbered
steps **with the agent's current position marked**:

```
YOU KNOW: clearing a stand   (proficiency 34, you are at step 4)
  1  check    is the saw sharp, and is anyone else working this stand?
  2  tool     look_around                                    ✓ done
  3  skill    limbing                                        ✓ done
> 4  tool     fell_timber
  5  check    is the load worth carrying yet?
  6  tool     move → Harvey Oak Supply Store
  7  tool     sell → timber
```

This is the change that makes a skill *alter behaviour* rather than just
discount a price. The agent is not told "you have forestry 34"; it is shown the
procedure it wrote and where it has got to.

### B.4 `toolify` — a thin wrapper

`define_tool` already exists and is well built — an invented tool is **data,
not code**: `build_composite_handler` replays the filed steps through the same
gate-checked `dispatch()` every ordinary call takes.

`toolify` adds one thing agents need and lack: **`describe_primitives`**,
returning the callable vocabulary with signatures and costs, so composing is
informed rather than guesswork against a half-remembered name.

### B.5 Registry refresh — the blocker

Two small changes:

**World side** — `GET /tools/registry` returns a version. Bump it whenever
`world.custom_tools` changes:

```python
return {"version": world.tool_registry_version, "tools": rows}
```

**Engine side** — `engine/src/sim/world_tools.py` + `pinned_boot.py`. Keep the
version from the last fetch; on each tick, if the world reports a higher one,
rebuild the registry. `build_world_tool_registry` is already idempotent — call
it again and rebind.

> This is the only change outside `app/`, and `engine/src/sim/` is explicitly
> the seam Law 1 leaves open for exactly this.

### B.6 Verify

```bash
curl -s localhost:8100/tools/registry | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d['version'], len(d['tools']))"
sqlite3 app/data/kasukabe.db \
  "SELECT agent_id, task_type, substr(skill_artifact_text,1,60) FROM skills
   WHERE skill_artifact_text IS NOT NULL;"
```

---

## Part C — the observatory, and a run that proves it  ✅ *built*

**Outcome:** a page on `:8500` showing what failed and where,
what the town keeps failing to do, and whether the run is believable.

### C.1 Fix the trace first — `app/broker/`  ✅ *built*

Neither turned out to be one line. Both would have saved a day this week:

- **Record failure bodies.** `llm_calls.response_body` is **empty for every
  failure** — 5,724 of them in the last run, none diagnosable from the DB.
- **Tag the hop.** Add `failed_at`: `bedrock` / `ledger` / `world` / `engine` /
  `broker-internal`. A `502 UpstreamError` this week actually meant *the broker
  could not reach world's ledger* — a downstream failure labelled upstream.

### C.2 `app/observatory/` — read-only, port 8500  ✅ *built*

Live output from the end-to-end run, all three panels against a real world
server and a real trace database:

```
IS THIS RUN BELIEVABLE?
   believable - read on
   88/100 calls  ·  $0.35  ·  3 agents

WHAT THE TOWN CANNOT DO   (7 refusals seen)
  → no-such-tool:mend_nets       4 agents, 4 attempts   [mend_nets]
    location:farmland:rice       2 agents, 2 attempts   [harvest]
    funds                        1 agents, 1 attempts   [harvest]

WHAT IS FAILING, AND WHERE
     ledger              9  LedgerUnavailableError
     bedrock             3  UpstreamError
     untagged share: 0%
```

`→` marks a **missing verb** — the town reached for something that does not
exist. That is the row Part D feeds back to those four agents as the prompt
for a `toolify`.

```
app/observatory/
    app.py       FastAPI on :8500
    hops.py      failures grouped by which link broke
    gaps.py      refused intentions, by requirement and headcount
    health.py    saturation, headroom, liveness, burn rate
    static/      one page, same visual language as the township
```

The gap query is the one that matters:

```sql
SELECT unmet_requirement,
       COUNT(DISTINCT agent_id) AS agents,
       COUNT(*)                 AS attempts
FROM tool_refusals
GROUP BY 1 ORDER BY attempts DESC;
```

*"14 agents tried to treat illness and lacked the skill"* is a capability gap
with a name and a headcount — and, on Part D, the prompt for a `toolify`.

Health matters as much: a run at 39% dispatch success is a broken instrument,
not a finding about society, and it should say so itself.

### C.3 The run

```bash
cd /Users/sharan/Downloads/kasukabe-real/app

# broker — real Bedrock, cap it deliberately
SPEND_CAP_USD=4.00 uv run uvicorn broker.app:app --port 8200 &

# the town: embedded world + 40 real engines
KASUKABE_HOME_ROOT=/tmp/kasukabe-agents \
uv run python -m world.run_genesis --live \
  --genesis-path world/genesis_realsim_40.json \
  --world-mode embedded --world-host 127.0.0.1 --world-port 8100 \
  --broker-base-url http://127.0.0.1:8200 \
  --budget-cap-cents 300 --supervisor-backend process \
  --engine-repo ../engine --no-resume --tick-interval-s 12 &

# viewer + observatory
uv run uvicorn game.server:app --port 8400 &
uv run uvicorn observatory.app:app --port 8500 &
```

**Two traps, both hit this week:**

- **Nothing else may hold port 8100.** A stale standalone `world.main` there
  makes the broker debit against the wrong app — 61% of dispatches fail with a
  502 that says "upstream".
- **`--tick-interval-s 0`** derives ~100% of broker capacity with zero
  headroom. Give it slack; 12s for 40 agents against 200 RPM.

### C.4 What success looks like

| Check | Where |
|---|---|
| Balances diverged; workers are not poorest | Real sim roster, `:8400` |
| `repetitions_observed` climbing | `skills` table |
| At least one skill authored mid-run | `skill_artifact_text` not null |
| A tool filed at tick N callable at N+1 | `/tools/registry` version bumped |
| Failures attributable to a hop | observatory, `:8500` |
| A named capability gap with a headcount | observatory gaps view |

---

## Part D — closing the loop  ✅ *built*

**Outcome:** the observatory stops being a report nobody in the town can read.
A gap the agents generated is fed back to the agents who hit it, and what they
do about it is the measurement.

### D.1 Gap feedback — `app/world/gap_feedback.py`

**The one place the instrument writes into the experiment.** Everything else
in Parts A–C only reads: the observatory opens its database read-only, the
refusal log is a record, the roster is a view. This module deliberately breaks
that, and because it does, it is a separate file with its own audit log rather
than four lines hidden in the tick loop.

The loop it closes:

```
an agent reaches for something the town cannot do
  → the refusal is recorded          (registry.execute, after the rollback)
  → refusals group into a named gap  (observatory/gaps.py)
  → the agents who hit it are told   (gap_feedback.py)          ← D.1
  → one of them invents a tool, or writes a procedure
  → the gap stops appearing
```

Telling agents what to want is the fastest way to get a result that means
nothing, so three constraints carry the whole design:

| Constraint | Why it is not politeness |
|---|---|
| **Names the gap, never the answer** | *"Nobody knows how to mend_nets"* is an observation the agents generated. *"Use toolify to build mend_nets"* would be us inventing it and crediting them. A test asserts the notice contains no verb — not `toolify`, not `invent`, not `you should`. |
| **Rate-limited, one game-day per agent per gap** | An agent told every tick is being nagged into compliance, and its eventual invention measures our persistence rather than its judgement. |
| **Every notice logged in world state, with the tick** | A researcher can subtract prompted inventions from spontaneous ones. An unlogged nudge quietly contaminates every finding downstream of it. |

Two further choices:

- **Told to the agents who hit it, not broadcast.** A town-wide announcement
  would make the first inventor whoever the scheduler happened to wake, rather
  than whoever wanted the thing.
- **Two agents minimum** (`MIN_AGENTS_FOR_A_GAP`). One agent's refusal is
  usually that agent being in the wrong place; two independent agents wanting
  the same impossible thing is the town wanting it.

`GapFeedback(enabled=False)` silences it entirely, because a control run needs
the instrument silent rather than merely quiet.

### D.2 `revise_skill` / `revise_tool` — changing your mind, on the record

Earlier versions are **retained, never overwritten** — "what did this agent
think this job was, before it learned better" is the question that makes a
revision interesting, and a skill that silently mutates leaves no trace anyone
changed their mind. `because` is free text and kept: the reason is the one
field the system cannot derive.

Four rules, each with a reason:

1. **Proficiency survives a revision.** An agent that has walked a procedure
   sixty times and improves one step has not become a beginner. A reset would
   make improving something you are good at strictly worse than leaving it
   wrong.
2. **Only the author may revise a trade skill.** Not a permission system — if
   anyone could rewrite "clearing a stand", the name would stop referring to
   one agent's idea of the job and the town's knowledge would stop being
   attributable.
3. **A revision cannot add a step needing a government seat.** Otherwise
   revising a trade skill is a way into the book of rules without the council
   seeing it. A governmental workflow cannot be revised here at all — it went
   in as an amendment and comes out the same way.
4. **In-flight progress is cleared.** Step 4 of version 1 is not step 4 of
   version 2, and guessing which one it became would be inventing a fact.

`revise_tool` refuses **patented** tools outright: the patent office approved a
specific disclosure, and letting the holder swap the steps behind it would make
the approval refer to something that no longer exists. It also rebuilds the
registry spec rather than mutating it, which bumps `registry.version` — a
revised tool whose engines kept calling the old shape would be the Part B
blocker again, one layer down.

### D.3 `tools_registry` — a table nothing had ever written to

`patent_fee_paid_sim` was not merely unused; **the whole table was**. So "what
has this town invented" was unanswerable from the trace no matter how many
tools its agents composed.

Three provenances now share it, and the columns that distinguish them are the
interesting part:

| Source | `patent_fee_paid_sim` | `status` |
|---|---|---|
| `toolify` | null — no fee was assessed at all | `active` at once |
| `define_tool` | the charter's real filing fee | `pending_review` |
| `revise_tool` | null | `active`, re-inserted with the new composition |

Null rather than 0 for the free paths: `0` reads as *"a fee was assessed and
came to nothing"*, which is a different fact.

### D.4 Trade-secret skills

`skillify` takes an optional `business_id`. The author must own the business or
work there — a trade secret filed by a stranger would be a way to attach a
private procedure to somebody else's shop. A **governmental** workflow cannot
be a trade secret at all: the book of rules is public by construction, and a
secret procedure of state is a contradiction.

The lock is on `teach_skill`, not on authoring. **Teaching is the only way a
skill leaves the person holding it**, so it is the one door that needs one —
without it `namespace` would be a label the world ignored, and a business that
knew something others did not would last exactly until the first employee
talked.

The namespace mirrors `tools_registry`'s own column rather than inventing a
second vocabulary, so "what does this business know that others do not" is one
question across tools and skills instead of two.

### D.5 Routing skills — still no code

A skill whose steps are about *when* rather than *how* needs nothing new. It is
authored, taught, followed and revised by the same machinery, which is why
"knowing which tool, when" never wanted a planner. This remains true and
remains unbuilt, because there is nothing to build.

---

## Part E — what any of this looks like on screen  ✅ *built*

Verified against the running viewer on 2026-08-26, world bound with 314
residents:

| | Surface | State |
|---|---|---|
| E.1 | Resident file → **Skills** tab | built — tabs are Profile · Skills · Family · Estate · Memory · Experts · Money, with an honest empty state: *"Holds no skill yet. Not an error: most residents start this way, and watching that change is the point."* |
| E.2 | Roster row → skill and step | built (`part-e.js:67`) |
| E.3 | Town books → **Workshop** | built, served by `world/workshop_api.py` `/workshop` |
| E.4 | Town books → **Council** | built, served by `/council` |
| E.5 | Map → a quiet mark mid-workflow | built (`village2.js:1733`) — one copper dot at the shoulder |
| E.6 | Observatory on `:8500` | built in Part C; boots, `/api/gaps` answers `available: true` |
| E.7 | Live polling | built — Workshop and Council poll at **15s**, the cadence the plan specified |

E.8 was guidance on sequencing, not a deliverable.

**What the panels show right now is nothing, and that is correct.** The
Workshop reads *"Nothing filed yet — the town is connected and has invented
nothing so far. This is the honest early state of a run, not a missing
panel."* Parts A–D are wired and the town is bound, but no agent has invented
anything because no engines are running. Populating it needs a live Bedrock
run.

### The non-map UI, rebuilt

Part E shipped the panels; running them surfaced that the surfaces around
those panels had real breakage. Rebuilt in
`app/game/static/css/parts/revamp.css` (578 lines, loads last, touches nothing
the canvas draws).

**The search field was landing on the clock.** `.map-search` began life
absolutely positioned over the canvas with `top: 82px` to clear the old top
bar. Moved into the controls drawer it keeps `position: relative` — and a
*relative* box with `top: 82px` is shifted 82px **down** from where flow put
it, straight onto the clock beneath. That is why the drawer showed a stray
`0` (the tail of `06:00:00` poking out to the left of the input) and ghost
digits behind the placeholder. `.in-pane` reset `margin` but never the
offsets.

The first fix did not stick either: the offset comes from
`body.shell-on .map-search`, specificity (0,2,1), which beats a plain
`.map-search.in-pane` (0,2,0) no matter how late it loads. Matching that
context is what makes the reset hold.

**Everything else that was wrong:**

| Symptom | Cause |
|---|---|
| `TOWN WEALTH 1,572,961 SIMTREASURY 3,012 SIM` | `.hud-stat` spans sat inline with no gap, so the figures ran together as one word |
| Eight book tabs on two ragged rows, panel height jumping | `flex-wrap: wrap` — without overriding it the overflow never engages, because nothing overflows |
| Speed buttons stacked vertically | they carry `grid-column: span 2`; a six-column grid makes that mean exactly one third |
| Scoreboard tiled four-then-two with a rule stopping halfway | no fixed grid; now three equal cells that tile any number of figures |
| Trait / habit / workplace read as a config dump | right-aligned mono, so *"takes a walk through the park every dawn"* was jammed against the panel edge. It is prose, not a settings table |
| Controls drawer clipped its own stats | no scroll on a panel taller than the viewport |
| A yellow Pause beside a blue 1× beside a green Labels | three accents in nine buttons, none of them meaning anything |

**No new palette.** `township.css` already ships a good one — `--ember` money,
`--mint` alive, `--rust` death, `--brass` government, three families, four
radii. The components were ignoring it. What was genuinely missing was a
**spacing and size scale**: six spacing values, one control height, four type
sizes. Six choices produce rhythm; thirty produce the ragged stacking this
replaced. Reference is Linear — the closest well-solved version of this exact
problem, a dense dark panel where the data is the interesting thing and the
frame gets out of the way.

One method note: the first pass guessed class names and silently did not
apply. The selectors were rewritten against the live DOM
(`.score-grid`, `.sv`/`.sk`, `<dl class="facts">`).

Verified: `Village` API intact, canvas rendering at 1773×949, zero console
errors, 277 game tests pass.

### The original sketch



None of the above is visible today. An agent could invent a trade, teach it to
its daughter, and reach mastery, and the map would show a figure walking to the
forest. This part is what makes the mechanism observable — to a researcher
watching a live Bedrock run, and to a reader who has never seen the codebase.

**Five surfaces, all in the viewer you already have:**

| Surface | Where it lives now | What it gains |
|---|---|---|
| Resident file | right dock, `profilePanel` | a **Skills** tab: workflows, proficiency, current step |
| Real sim roster | right dock, `rs-row` | the skill being followed, and how far in |
| Town books | left dock, tabbed | two new tabs: **Workshop** and **Council** |
| The map | `village2.js` | a quiet mark on anyone mid-workflow |
| Observatory | new, `:8500` | its own page — failures, gaps, health |

### E.1 Resident file → a Skills tab

The single most important view: a skill is a *document*, so show the document.

```
┌─ YARA THORNE ─────────────── forester · 52 ─┐
│  profile   kin   money   [SKILLS]   memory  │
├─────────────────────────────────────────────┤
│  clearing a stand              ●●●●●○○○ 61  │
│  invented by her · 34 completions           │
│                                             │
│   1  check   saw sharp? stand clear?     ✓  │
│   2  tool    look_around                 ✓  │
│   3  skill   limbing                     ✓  │
│ ▸ 4  tool    fell_timber                    │  ← where she is now
│   5  check   load worth carrying yet?       │
│   6  tool    move → Harvey Oak Supply       │
│   7  tool    sell → timber                  │
│                                             │
│  limbing                       ●●●○○○○○ 38  │
│  taught by Dorian Dunn · 12 completions     │
│  ▸ collapsed — 3 steps                      │
└─────────────────────────────────────────────┘
```

Everything here already exists in the data: steps from
`skills.skill_artifact_text`, proficiency and `repetitions_observed` from the
same row, provenance from `taught_by_agent_id`, and the current position from
the `SkillProgress` tracker in B.2.

**Why the step list and not a number.** A proficiency bar tells you an agent is
good at something. The step list tells you *what they think the job is* — and
when two foresters at the same level have different step 3, you can see the
town's knowledge diverging, which is the whole experiment.

### E.2 Roster row → the skill, and how far in

The roster already shows *what* each agent is doing. One more line makes it
legible as a procedure rather than a twitch:

```
WHO                        DOING                          IN-GAME  THINKING
Yara Thorne                clearing a stand · step 4/7      5,000    $0.03
  forester · Kasukabe Forest   felling timber
Bram Holloway              dormant — cannot afford to think 5,000    $0.00
  miner · Kasukabe Farmland
Kira Ives                  (no skill) walking to the temple 5,000    $0.02
  grocery clerk
```

`(no skill)` is deliberately visible. An agent acting outside any workflow it
holds is the normal early state, and watching that fraction fall over a run is
one of the clearest signals that skills are being adopted.

### E.3 Town books → a Workshop tab

The town's invented capability, in one place:

```
┌─ WORKSHOP ──────────────────────────────────┐
│  TOOLS INVENTED             4               │
│   rope-making      Ada Gray     tick 412    │
│                    used 61× by 7 agents     │
│   dry-curing       Milo Ziegler tick 883    │
│                    used 3× by 1 agent    ⚠  │
│                                             │
│  SKILLS AUTHORED           11               │
│   clearing a stand Yara Thorne  tick 508    │
│                    held by 4 · avg prof 41  │
│   limbing          Yara Thorne  tick 455    │
│                    held by 9 · avg prof 33  │
│                    ↑ spreading fastest      │
└─────────────────────────────────────────────┘
```

The ⚠ marks a filing nobody adopted — invented once, used three times, never
taught. That is not a bug to fix; it is the town telling you which ideas did not
take, and it is exactly as interesting as the ones that did.

### E.4 Town books → a Council tab

Only governmental workflows appear here, which is the point — the tab should be
mostly empty, and when it is not, something constitutional is happening.

```
┌─ COUNCIL ───────────────────────────────────┐
│  BEFORE THE COUNCIL                         │
│   "seizure for unpaid tax"                  │
│    moved by Suri Grave · tick 1204          │
│    ▓▓▓▓▓░░░  3 of 5 seats · needs 4         │
│    then: the president signs or vetoes      │
│                                             │
│  IN FORCE                     2             │
│   "summons before sentencing"  enacted 903  │
│   "market inspection round"    enacted 1102 │
│                                             │
│  REPEALED                     1             │
│   "night curfew"     enacted 604, out 871   │
└─────────────────────────────────────────────┘
```

Reads straight off `charter/amendments.py`'s existing statuses —
`PROPOSED → PASSED_AWAITING_PRESIDENT → ENACTED / VETOED / REJECTED` — with no
new state. The repealed list matters as much as the active one: a rule the town
tried and dropped is a finding.

### E.5 The map → a quiet mark

Resist the urge to decorate. One affordance only: a small dot beside anyone
currently mid-workflow, in the accent colour, so a glance at the town shows how
much of it is *executing a procedure* versus improvising. Clicking the figure
opens the resident file already scrolled to that skill.

Everything else — who invented what, who taught whom — belongs in the books,
not floating over the map.

### E.6 The observatory page, `:8500`

Three panels, in the order a researcher needs them:

```
┌─ IS THIS RUN BELIEVABLE? ───────────────────┐
│  dispatch success   ▓▓▓▓▓▓▓▓▓░  92%         │
│  lane headroom      ▓▓▓▓▓░░░░░  48% of 200  │
│  engines alive      40 / 40                 │
│  spend              $1.46 of $4.00          │
└─────────────────────────────────────────────┘
┌─ WHAT THE TOWN CANNOT DO ───────────────────┐
│  medicine ≥ 30        14 agents, 61 attempts│
│  masonry ≥ 25          6 agents, 22 attempts│
│  no tool for "repair"  4 agents, said aloud │
└─────────────────────────────────────────────┘
┌─ WHAT IS FAILING, AND WHERE ────────────────┐
│  ledger unreachable    412   ██████░░░░     │
│  bedrock throttled      38   █░░░░░░░░░     │
│  engine 500              7   ░░░░░░░░░░     │
└─────────────────────────────────────────────┘
```

The middle panel is the one that closes the loop with `toolify`: a capability
gap with a **name and a headcount** is the prompt for an invention, and in
Part D it is fed back to those same agents as perception.

Put *believability* first on the page, not last. A run at 39% dispatch success
is not a finding about society — it is a broken instrument, and the researcher
should see that before they read anything else.

### E.7 Making it live during a Bedrock run

The viewer already polls; these views ride the same cadence rather than adding
new timers.

| View | Source | Cadence | Note |
|---|---|---|---|
| Roster + skill step | `/api/realsim/roster` | 4s | already polling; add the skill fields |
| Resident Skills tab | `/api/realsim/roster` | on open, then 4s | no extra call — the roster already carries per-agent rows |
| Workshop | `/api/town/workshop` (new) | 15s | changes rarely; a tool is invented every few game-days at most |
| Council | `/api/town/council` (new) | 15s | same |
| Observatory | its own page | 5s | separate service, separate budget |

**Three rules, all learned the hard way this session:**

1. **One poll, one instant.** The roster merges the world snapshot and
   `/agent-viewer/agents` in a single request precisely so a row's money and its
   activity were true at the same moment. Do not let the Skills tab open a
   second call — the numbers would disagree by a tick and nobody would know why.
2. **Never show a fabricated number beside a real one.** The top bar showed
   "Town wealth 1,573,142" from the mock economy while a real run held 40,000.
   Every new view is either live or says plainly that it is not.
3. **Poll whether or not the panel is open.** The top bar depends on the roster,
   so gating the poll on panel visibility left a stale wealth figure beside a
   live run.

### E.8 What to build first

If only one thing ships: **E.1, the Skills tab.** It is the only view that shows
a skill *as a document*, and without it "the agent wrote its own procedure" is a
claim in a database rather than something anyone can look at.

E.6 is second, because a run nobody can trust is not worth looking at.

---

## What building it changed

Three things the plan had wrong, found by writing the code rather than reading
it.

### 1. A junk skill must not earn from work the agent was doing anyway

As written, Part A counted a repetition for *every* skill naming the tool just
called. File a workflow over the forty tools you already use, go about an
ordinary day, and it collects forty repetitions for work you would have done
regardless — the junk-skill defence, the whole reason no approval gate is
needed, would have been worth nothing.

The fix is an asymmetry, and it is load-bearing:

| | Earns a repetition when… |
|---|---|
| **Built-in trades** (six, seeded) | a discounted tool call completes |
| **Authored skills** (anything an agent wrote) | **the workflow is walked end to end** — steps along the way earn nothing |

Naming is free; following is not.

### 2. A failed action was rolled back and paid for at the same time

The charge must come first, so an unaffordable action is refused while the
world is untouched. But `ToolRegistry.execute()` restores `OWNED_STATE_FIELDS`
when a handler raises and **the ledger is deliberately not in that snapshot** —
it is a sibling system guaranteeing its own atomicity. So a handler failing
halfway left the world reverted and the wallet debited.

An existing test caught it: `test_buy_more_than_in_stock_never_charges_buyer`,
written long before any of this, encoding exactly the invariant the new charge
broke. The fix is an explicit reversing move rather than deleting the original
transaction, so the books stay double-entry and both legs survive in the audit
trail — an erased charge would read as though the attempt never happened.

The refund path is rare: every gate (visibility, actor requirements, params,
location) is checked before the charge, so a call the agent was never allowed
to attempt produces no transactions at all.

### 3. The registry version wanted its own route

The plan had `GET /tools/registry` return `{"version": …, "tools": […]}`. Every
existing caller parses that route as a bare list, so changing its shape would
have broken them all to add one integer. It also polls badly — forty engines
would each pull the whole vocabulary every tick to learn nothing had changed.

`GET /tools/registry/version` is additive and costs one small number. A world
too old to serve it answers 404, which the engine reads as "never changes".

One bug of my own here, caught by testing rather than reasoning: the engine
prefixes world tools with `world:`, so the de-duplication check compared
unprefixed names, matched nothing, and would have rebuilt every `ToolDef` on
every refresh.

### 4. `toolify` was not a thin wrapper — `define_tool` contradicted the plan

The plan called `toolify` "a thin wrapper" over `define_tool`, adding only
`describe_primitives`. Reading `define_tool` showed it cannot be wrapped:

- it **gates on standing inside the patent office** (`LOCATION_PATENT_OFFICE`),
- it charges `patent_filing_fee_sim` — **1,000 sim at genesis**,
- and it files the tool `approved=False`, so **the inventor cannot use its own
  invention** until a seat-holder approves it.

That is precisely the bureaucracy *Who has to ask permission* removed: "a
village does not run a patent office", and "an ordinary skill needs no
approval, **and neither does an ordinary tool**." An invented tool binds
nobody — it orders primitives its author could already call one at a time.

So `toolify` is the ungated path, and it is genuinely necessary rather than an
alias: no fee, no location gate, no approval, callable the same tick.
`define_tool` is untouched — an agent that wants the patent office's
protection can still pay for it.

### 5. Nothing recorded refusals, and the recording hit the same snapshot trap

The gaps view is the whole reason the observatory exists, and its query reads
`tool_refusals` — **a table that did not exist, fed by a recording that did
not happen**. Every gate in `dispatch()` raises and nothing was written down,
so "what does the town keep failing to do" was unanswerable.

Adding it walked straight into the same trap as the action charge, from the
opposite side: a refusal exists *only* because the call failed, and
`execute()` restores owned state on exactly that path. A snapshotted refusal
log would erase itself the instant it had something to say — empty precisely
when it mattered. So `tool_refusals` is deliberately **not** in
`OWNED_STATE_FIELDS`, and the write happens *after* `world.restore()`.

The grouping key is built from the exception's structured fields, never its
message, because its entire job is to group: "14 agents tried to treat illness
and lacked the skill" needs the same string fourteen times, not fourteen
phrasings. `ToolValidationError` is deliberately excluded — a malformed call
is a mistake, not a thing the town cannot do, and letting typos in would bury
the real gaps under them.

### 6. The mislabelled 502 was a bare `httpx` error, not a wrong label

The plan described the symptom — a `502 UpstreamError` that really meant the
ledger was unreachable — and prescribed a `failed_at` tag. The tag was
necessary but not sufficient: the cause was that `broker/ledger.py` called
`resp.raise_for_status()`, which raises `httpx.HTTPStatusError`. That is **not
a `BrokerError`**, so no handler recognised it and it fell through to app.py's
catch-all as a generic internal error — indistinguishable, in the trace, from
Bedrock falling over.

`LedgerUnavailableError` fixes it at the source: still a 502 to the caller,
because it genuinely is an upstream failure from their point of view, but
`failed_at="ledger"` separates it from a Bedrock 502. Same status, entirely
different thing to fix.

`failed_at` is a **class attribute**, matching the existing `status_code`
pattern, so every subclass gets a correct answer for free and no raise site
has to remember.

### 7. Wiring prices into descriptions made maintainer notes agent-facing

`cost_disclosure.py` documented its own wiring, including
`annotate_tool_description`, which splices each rule's description into the
tool text agents read. Wiring it turned `SCHEDULE_TABLE` descriptions from
maintainer notes into **prompt content, for 46 tools, every agent, every
tick** — and five of them were paragraphs citing file names, handler names and
module docstrings.

A test now caps the price note and the reasoning lives in comments. Two of my
own rows were among the offenders.

The ordering bug was subtler: `quote_action_cost` was registered *after* the
annotation loop, making it the one tool in the registry with no price on it —
the tool whose entire job is telling agents what things cost.

### 8. Agents could reach the authoring tools but were never told they existed

`skillify` and `describe_primitives` were served in the registry payload from
the moment they were built, so an agent *could* call them. Nothing ever
suggested it should, and an LLM agent does not spontaneously invent a
vocabulary it was handed a schema for.

The fix is deliberately **evidence-driven, not a standing instruction**. A
permanent "you may invent tools" line costs 314 agents a paragraph a tick and
teaches nothing — no reason attached at any particular moment, so it reads as
furniture. `WorldState.authoring_hint` returns a line only once an agent has
actually repeated the same tool eight times with nothing written down, and it
disappears the moment the agent acts on it.

It does not name the verb. What agents invent unprompted is the experiment;
supplying the answer would measure our vocabulary, not theirs.

### 9. Two of Part C's own success checks were unverifiable

The plan's verify command reads `skill_artifact_text` and
`repetitions_observed` out of the `skills` table. Checking it found the table
was written **only on `teach_skill`** — so `skill_artifact_text` had never held
anything, and `repetitions_observed` was hardcoded `0` on every row ever
written. Both of Part C's checks would have come back empty against a healthy
run.

Fixed by making practice an event. `bump_skill_repetition` now reports whether
the proficiency actually *moved*, and `dispatch()` appends an
`agent.skill.practised` event **to the outcome** rather than publishing it —
so `execute()` publishes it on the same path as everything else and a practice
row can never outlive a rolled-back call. That is the third time the same trap
appeared, and the first time it was avoided by design rather than caught.

Emitting only on a level change is what keeps it affordable: the square-root
curve moves often early and rarely late, so it is at most one row per level per
agent per skill and stops entirely at the cap — rather than one row per tool
call, forever, for 314 agents.

### 10. The gap feedback needed to be a separate file, not four lines

`run_genesis.py:2622` was the named insertion point, and putting the logic
there would have been shorter. It is in `world/gap_feedback.py` instead
because it is **the only component in the whole system that writes into the
experiment**, and that fact deserves a file with its own docstring, its own
audit log and its own off switch rather than four lines inside a tick loop
where the next reader would not notice what they were looking at.

The `enabled` flag exists for the same reason: a control run needs the
instrument *silent*, and "we did not call it" is not the same guarantee as "it
cannot speak".

### Smaller corrections

| Planned | Built | Why |
|---|---|---|
| A skill may discount at most **three** tools | **No cap** — `discounts_tools` derived from steps | 42 tools costs 42 steps to write |
| `skill_artifact_text` holds prose | It holds the **serialised workflow** | Prose cannot be walked, tracked, or shown with a position marked |
| Standing orders live somewhere unspecified | `Charter.standing_orders`, an **amendable field** | Followability read live from the rulebook — no enactment callback to forget |
| `describe_primitives` returns `sim_cost` | `sim_cost_minimum` | Priced with no params; per-unit tools cost more for a bigger job |
| `GET /tools/registry` returns `{version, tools}` | Additive `GET /tools/registry/version` | Every caller parses the existing route as a bare list; 40 engines should not pull the vocabulary each tick to learn nothing changed |
| `toolify` wraps `define_tool` | Its own ungated handler | `define_tool` gates on the patent office, charges 1,000 sim, and files unapproved — the bureaucracy the plan removed |
| `tool_refusals` is queried | It had to be **built**, outside the snapshot | A refusal only exists because the call failed, and that is exactly the path `execute()` rolls back |
| `quote_action_cost` unregistered and unpriced | Registered, priced 0, before the annotation loop | Agents cannot reason about costs they cannot ask about; an agent too poor to act must not be too poor to find out why |
| Gap feedback at `run_genesis.py:2622` | Its own module, `world/gap_feedback.py` | The one component that writes into the experiment deserves an audit log and an off switch, not four lines in a tick loop |
| "patent fees: `patent_fee_paid_sim` stays unused" | The **whole `tools_registry` table** was unused | "What has this town invented" was unanswerable from the trace, however much it invented |
| `skills` table is queried for artifacts | It was written **only on `teach_skill`** | `skill_artifact_text` had never held anything and `repetitions_observed` was hardcoded 0 |

### Pricing the two new meta-tools

```python
# app/world/ledger/action_costs.py
"skillify":            base_cost=0   # writing down a procedure orders tools you
                                     # could already call: charging charges twice
"describe_primitives": base_cost=1   # a token 1 - an agent that could enumerate
                                     # the world free would never need to remember
```

Free is not unlimited: a workflow costs **thinking** to compose, and thinking is
the scarce budget.

### Where it stands

| | |
|---|---|
| app tests passing (world · game · broker · observatory) | **1,920** |
| engine tests passing | **4,024** (56 skipped) |
| new tests written | **59** across five files |
| cheaper at mastery, measured | **50%** (32 sim → 16, the cap) |
| tools agents can now author with | **6** — `toolify`, `skillify`, `revise_tool`, `revise_skill`, `describe_primitives`, `quote_action_cost` |

### Lint, and the engine's excluded tests

`ruff check` across `app/` went from **47 errors to 0**. Six were mine; the
other 41 predate this work. Nothing was suppressed with `noqa` — the two
`E402` cases were a constant accidentally wedged into an import block (moved
below it), `F821` was a `-> "Path"` annotation referring to a name the module
never imported, and one `F841` was a variable computed for an assertion its
comment promised and nobody ever wrote (the comment now describes what the
test actually does). `ruff format` is not run: it would rewrite 185 files
repo-wide, including untouched ones, which is a style decision and not a fix.

Ten engine test files are excluded on macOS, and the reason is explicit in
their own output:

> `Shell execution requires the bubblewrap sandbox (Linux container).`
> `Refusing to run unsandboxed — use "docker compose run" for local dev.`

Seven of them run and produce **12 failures against 250 passes**, every failure
naming the sandbox. Three (`test_home_restore`, `test_integration_boot`,
`test_lc_scheduler_pause`) hang rather than fail. This is the engine correctly
refusing to execute shell commands outside its Linux container — frozen code
under ROADMAP Law 1, untouched by any of this, and verifiable only in Docker
(not running on this machine). **Not yet run against live Bedrock.**

---

## Settings — decided

These were open questions. They are now defaults, with the arithmetic that
produced them. Change them after watching one run, not before.

### Mastery costs 60 successful repetitions, on a square-root curve

```python
# app/world/ledger/action_costs.py
REPS_TO_MASTERY = 60

def proficiency_from_reps(reps: int) -> int:
    # 0 -> 0, 60 -> 100 (the cap), diminishing all the way.
    return min(SKILL_DISCOUNT_PROFICIENCY_CAP,
               round(SKILL_DISCOUNT_PROFICIENCY_CAP
                     * (reps / REPS_TO_MASTERY) ** 0.5))
```

The arithmetic, from constants already in the tree: `TICKS_PER_DAY = 144`,
`scheduler_target_fraction = 0.3`, so an agent is due on roughly **43 acts per
game-day**. If its trade tool is one act in three, that is ~14 trade-acts a
game-day — so **60 reps is about four game-days**, or two hours of wall-clock at
a 12-second tick.

Square root rather than linear, deliberately:

| Reps | Proficiency | Discount |
|---|---|---|
| 4 | 26 | 13% |
| 15 | 50 | 25% |
| 34 | 75 | 37% |
| 60 | 100 | 50% |

An agent feels the first discount after **four** uses — early enough to notice
that practice pays — while true mastery still takes a game-week. A linear curve
gives no feedback for days and then arrives all at once.

### Two skills on one tool: highest wins

```python
prof = max((world.skills[actor].get(s, 0) for s in skills_for(tool)), default=0)
```

Not only because stacking would blow the 50% cap and make
`SKILL_DISCOUNT_MAX_BPS` meaningless. The better reason is behavioural:
**highest-wins makes a second overlapping skill worth exactly nothing**, so an
agent who wants more advantage must broaden rather than file redundant variants
of what they already hold. Stacking rewards spam; this rewards range.

### Junk skills are stopped by arithmetic, not judgement

An agent could file "breathing" and point it at all 42 tools. Three cheap
guards make that self-defeating, and none of them needs a policy:

1. **Every discounted tool costs a step to name.** `discounts_tools` is
   derived from the workflow, never declared, so pointing at 42 tools means
   writing 42 steps. No bounds check and no committee — the arithmetic of
   writing it down is the whole limit.
2. **Proficiency rises only when the workflow is walked end to end.** Not per
   tool call: a skill filed over work the agent was already doing earns nothing
   from that work. It stays at the 1 its author was seeded with until somebody
   actually follows it start to finish.
3. **Highest-wins** (above) means a junk skill overlapping a real one adds
   nothing at all.

There is **no approval gate on an ordinary skill at all** — see *Who has to ask
permission* in Part B. The guards above are the whole defence, and they are
enough precisely because they need nobody's attention: a junk skill is
self-defeating rather than refused.

### In-game insolvency does not kill

**It stops you acting. It does not kill you.**

There is already one death path — USD wallet to zero, dormant, then dead — and
it is the survival experiment's dependent variable. A second mortality path
would confound it: you could no longer tell whether an agent died of cognitive
poverty or of in-game poverty, and "who survives" would stop having a single
meaning.

An agent that cannot afford to act must earn, borrow, sell or ask. That is the
social pressure worth having, and it produces trade rather than a second
graveyard.

### Spent sim currency goes to the treasury

Not burned. Burning shrinks the money supply monotonically, so the town deflates
for reasons nobody chose and every later run is harder than the last in a way
that is an artifact rather than a finding.

Treasury recycles it, and the government already has seven initiatives to spend
it on. **Government solvency becomes a function of economic activity** — a
coupling worth having in a town meant to grow institutions.

Watch for the treasury accumulating without being spent. If it does, that is a
finding about the government, not a bug in the rule.

---

*Every file, line, column and constant named here was read in the working tree
of the `Real` branch. Failure evidence is from a live 40-agent Bedrock run on
2026-08-25. **All five parts are built** — A–D here, Part E by DCode-v05 with
its non-map surfaces since rebuilt, verified against the running viewer on
2026-08-26 with 314 residents bound. The wireframes under Part E are the
original sketch, kept for the reasoning; what shipped is described above
them.
Verification so far is the suites plus scripted end-to-end runs over real HTTP;
none of it has yet run against live Bedrock.*
