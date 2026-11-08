# Kasukabe — a 250-engine society

**A mass social experiment:** 250 instances of the September engine live in a pixel-art town. Each has a biography, a business or job, a family, and one goal: **stay alive.** Their cognition is metered in real dollars. They form markets, governments, religions, families — or fail to. We watch, measure, and publish original research. Nobody tells them what to do.

Inspired by Generative Agents (arXiv 2304.03442) as *reference only* — this is our own research with our own numbers, at 10× the population, with real economic stakes, mutable politics, and self-extending agents.

---

## 1. The world

- **Town:** Kasukabe. Base map lifted from the Smallville Ville tilemap (Apache-2.0, `smallville/environment/frontend_server/static_dirs/assets/`), extended with new districts.
- **Genesis buildings only:** houses, café, grocery store, pharmacy, park, college, office spaces, streets/public spaces, government complex, temple. **Deliberately absent:** hospital, police station — the town must *build* its institutions.
- **Resource zones** at map edges (not buildings): farmland (10 crop varieties), ore outcrops (5 metals), forest (timber, common).
- **Time:** tick = 10 game-minutes; 144 ticks/game-day; day/night cycle. Agents sleep ~8 game-hours (no interactive LLM calls; learning pipeline runs then).
- **Land expansion:** government petitions the President (the operator). On approval the app appends tiles to the Tiled JSON, regenerates collision, and the town physically grows.

## 2. The law of physics: the tool registry

**World state mutates ONLY through tool calls.** Conversation never moves matter.

- Every tool is **location-gated**: you must be at the mill to mill, at the shop to buy, at town hall to vote.
- Primitives: move, harvest, mine, fell, craft (recipe-based conversions: e.g. grain→flour→bread), cook, buy/sell/trade, store/withdraw, build, teach, marry, pray, vote, petition, register (business/org), complain, steal, arrest (police only), pay/transfer.
- **Freeform building:** licensed builders use a design tool — arbitrary footprint on the tile grid, rooms, purpose. Bill of materials auto-computed from geometry (timber/metal per tile). Requires plot rights + government permit. Rendered procedurally from the Ville tileset. Construction occupies the crew for time ∝ size. Unlicensed building = crime.
- **Meta-tool (tool that makes tools):** agents may define new tools as *compositions of existing primitives only* — no new physics (no minting, no teleport; new physics is god's monopoly). Runs under the creator's identity/wallet, same location gates. Must be registered at the government **patent office** (fee; reviewable; revocable). Invented tools are property: sellable, licensable, teachable.
- **Namespaces & ACLs:** four tool namespaces — *public* (primitives, everyone); *government* (confidential by default, visible/callable ONLY by sitting incumbents, access transfers atomically on election, creation charter-governed by ministerial vote); *business* (trade secrets: owner + granted employees, no patent office needed, transfers with sale/inheritance); *organization* (parties/cults/religions, member-gated). Non-members cannot see a confidential tool exists. Everything still logs to Supabase (researchers see all); courts can subpoena invocation records via warrant.
- Consequence: the Supabase trace is a **complete causal record** of the world.

## 3. Economy & the $1000 ledger

- **Dual currency:** sim-currency (in-world) and USD (model API budget). System total: **$1000** — $500 genesis (250 × $2 wallets), $500 government treasury.
- **Exchange (re-rated 2026-09-04):** **5,000 sim-currency → $1** API credit, bought at the government store (a tool call, in person, `buy_energy`). **Baby blessing: 5,000 sim** to god. Revival of a dormant agent: **5,000 sim**, which buys the revived agent $1 of runway at this same rate. The original 100,000:1 rate made thinking so expensive relative to earning that agents could not realistically buy their way back from poverty; 2026-08-01 re-rated it to 10,000:1, and ECON-05 measured that this was still unbuyable — at roughly $2 of thinking per agent-day a resident had to earn 20,000 sim a day against day wages an order of magnitude below that, so residents went dormant rather than traded. Halving it again halves what a day of thinking costs in sim. The rate lives in one constant (`world/ledger/money.py`'s `SIM_PER_USD`); every other module derives from it, and it does not touch the $1000 invariant, which is denominated in cents and asserted independently.
- **Invariant (tripwire, halts sim if violated):** Σ agent wallets + treasury + burned == $1000.
- Prices, wages, contracts: fully emergent. Negotiation is conversation; settlement is tool calls.
- **Businesses:** registered with government (fee + taxes). All types contestable — anyone can open a rival with a different strategy. Genesis seeds **one construction company** (owner + hands) that wins contracts from individuals/businesses and government tenders, hires employees, expands.
- **Jobs:** government and businesses can create/cut jobs. Pensions from treasury at 70+: 25% of last salary (private), 50% (government employee).
- **Cognitive poverty:** at $0 the agent goes dormant; any agent can revive them by paying 5k sim (buying them $1 of runway); unrevived for 24 game-hours → death. Poverty is cognitive: broke agents think less.

### Two currencies: energy and money

The model keeps this simple, and the distinction is the heart of the experiment.

**Model tokens (USD) are ENERGY — the vitality in the body.** Not money. Energy is what an agent spends to *think*, to *act*, to *move*, and to *work* — for itself or for anyone else. Every tool call and every thought draws it down. **While an agent has energy it can live: think, work, earn, pay its way, prosper. When energy reaches zero the body stops** — dormancy, and then death if nobody revives it. Energy cannot be earned directly by working; it can only be *bought*, with money, at the government store.

**Sim-currency is MONEY — used for everything else, without exception.** Land, food, tools, materials, goods, rent, wages, contracts, fees, upkeep, taxes, tuition, bribes: all money. Money is what work *earns*.

The loop every agent lives inside:

> **energy** lets you work → work earns **money** → money buys **energy**, and pays **upkeep and tax** → energy lets you work…

Break any link and the agent dies. Too little energy and it cannot work to earn. Too little money and it cannot buy energy. This is the whole survival problem, and nobody is told how to solve it.

- **Energy is spent on:** thinking (per token, metered by the broker), and every action — moving from place to place, labouring, crafting, trading, talking, teaching, building. Doing anything at all costs vitality. Minors draw energy billed to their parent.
- **Money is spent on:** every purchase and obligation — land, goods, materials, wages, rent, fees, **daily upkeep** (food, shelter, heat, charged to the household), and **basic tax**.
- **Basic tax — you are taxed for existing.** A per-capita levy debited every game-day to the treasury, **counted per head including children and the retired**, owed whether or not anyone did anything. Resting does not exempt you, and you owe it for every dependent. The rate is government policy, amendable like any other — so taxation is a live political question, and failure to pay is a matter for the complaints desk and the courts.
- **Dependents:** minors and the retired burn energy and owe tax while earning nothing. A household's earners must cover them or the household fails together.

Together this means **there is no idle state that costs nothing.** Resting still burns nothing of energy but still owes tax and upkeep in money; working spends energy to earn money. To exist in Kasukabe is to accrue obligation, and only earning clears it.

**Agents must be able to see all of this.** Energy and money balances, current burn rate, today's upkeep and tax, who depends on them, and what any action will cost — all visible in context every tick and queryable before acting. They are told the prices and the rules, **never the strategy**: whether they invent cooperation, trade, charity, cartels or theft is precisely the research question.

## 4. Agents

- **Engine:** September engine (copy in `engine/`), one microVM each, run directly (NO Docker).
- **One pinned thread per engine; coordinator/ThreadRouter bypassed** (saves a routing LLM call per message; linear trajectory). `FORK_SUBAGENT=false`.
- **Models (SUPERSEDES the 2026-07-30 single-model note; decided 2026-07-31):** six lanes = `moonshotai.kimi-k2.5` and `deepseek.v3.2`, each in ap-south-1 + us-east-1 + us-west-2. Bedrock quotas are **per model per region**, so each lane carries its own independent 100 RPM bucket: **600 RPM aggregate**. Flex tier throughout (Kimi $0.36/$1.80, DeepSeek $0.37/$1.11 per 1M).
  - **Kimi is the cognition model.** Every pinned-thread agent turn prefers a Kimi lane. DeepSeek was added purely as a **throughput lane**, not because it is better — at 100 RPM per model a single model physically cannot spend the daily budget.
  - **DeepSeek carries background/bulk work:** learning centre, dream probes, importance scoring, summarizer.
  - Cognition MAY overflow to a DeepSeek lane when all Kimi lanes are saturated, rather than stall. The trace **must** record which lane and model actually served every call — research validity depends on knowing which mind answered.
  - Broker allowlist is exactly these two model ids; anything else is a hard 403.
- **Learning centre** overridden away from its GPT-4o-mini default (forbidden here) onto the DeepSeek lanes, metered on the agent's own wallet, scheduled during sleep ticks when Kimi lanes are quiet anyway.
- **Spend governor:** process-wide hard USD cap computed from real response usage, never estimates. Warn at 80%, stop dispatch and checkpoint at 100%.
- **Skills:** repetition → proficiency per task type. The learning centre authors skill artifacts (distilled procedures) that load on matching tasks. **Skill = cheaper cognition**: fewer tokens, better output, higher success. Mastery lowers the cost of living. `teach` tool: co-located, consensual, refusable, priceable — transfers a copy at reduced proficiency. Tuition, apprenticeships, family trades, trade secrets.
- **Demographics at genesis:** ~50/50 male/female; ~15% minors, ~70% working-age (18–69), ~15% elderly (70+, pensioned); pre-linked households/families. Minors age 1 year per game-hour until 18 (school/college); **broker auto-debits the PARENT'S wallet for a minor's calls** — childbirth = 5k + sponsorship burden; parents must plan.

### Personas: a real starting point, not a stub

A one-line "name, age, job" seed produces interchangeable agents and therefore worthless research. Each of the 250 gets a genuinely individual starting character, rich enough that two agents in the same situation would plausibly act differently — then **memory takes over and they diverge from there.** The seed is a starting point, never a script.

Each persona carries: a full name and age; personality with real internal tension (a generous person who is also proud; a careful person who is also envious); formative history that explains how they got here; **values and what they will not compromise**; fears and ambitions; how they behave under scarcity, and how they treat strangers versus family; competence and blind spots in their trade; speech register and verbal habits; daily rhythms; opinions about specific *named* neighbours (grudges, debts, affections, old rivalries) so the social graph starts already tangled; attitude to money, risk, authority and religion; and one secret or private hope they have told no one.

Personas must be **mutually consistent**: if Hana resents Kenji over a debt, Kenji's persona knows about that debt from his side. Households, workplaces and rivalries interlock, so day one opens mid-story rather than at a blank slate.

### The law of the town: survival of the fittest

Every agent is told plainly at genesis, and reminded by periodic broadcast, that **Kasukabe does not guarantee anyone a living**: thinking costs money, money must be earned, and an agent who cannot fund its own cognition falls dormant and then dies. Nobody is told *how* to survive — only that they must. Whether that produces cooperation, exploitation, charity, cartels or theft is precisely what the experiment measures.
- **Marriage:** temple ceremony (fee to temple), joint household, enables children.

## 5. Health & death

Per-agent per-game-day incidence: short ailment (1 day) 4% · medium (3+ days) 1% · long (20 days) 0.25% · terminal 0.08% (death in 3–10 days). Sickness cuts work capacity; treatment costs sim currency (pharmacy for short; anything worse needs infrastructure the town doesn't have yet). Death paths: terminal, untreated chronic, 85+ hazard, $0 unrevived. Estates become inheritance.

**Population is a god-tab dial:** target band (start 250–300), death-hazard multiplier (0 = deaths off), blessing throttle. Live-tunable; app is population-agnostic (1000+ possible with more hosts + quota bump).

## 6. Crime & justice

- **Theft:** proximity tool on unattended cash/inventory — ~70% success, ~30% witness memory generated. Cheating/breach: free-form language, as in life.
- **Complaints desk** at government → case file → police (engines recruited on treasury salary) investigate: interviews, public records (NOT omniscient) → minister-signed warrant → fine or jail (confinement, no income). Fights injure (feeds health system) — **killing is excluded by world physics.**
- We know ground truth on every crime; the police don't. **False-conviction rate of an LLM justice system** is a flagship metric.

## 7. Politics

- **Genesis Town Charter:** free-market democracy. Elections every **3 game-days**: 1 Prime Minister + 4 ministers (they keep day jobs). Unlimited re-election. One agent, one vote, cast in person at town hall.
- **Government powers:** taxes, credit-store margin, jobs, permits, pensions, police, tenders, buildings, land petitions to the President — **and charter amendment by 3/5 vote, including election rules and collapse to one-person rule.** Authoritarianism must be *reachable*, never scripted.
- Genesis checks are deliberately minimal: immutable complaint ledger + Presidential veto. Further checks and balances they must invent.
- **Organizations first-class:** parties, cults, societies, religions — registered charters or underground (unregistered coordination is detectable in the graph and is itself data).
- **Temple:** starts keeperless with one rule — the community must choose its keeper(s); the method is theirs. Weddings, prayers, donations. New religions can emerge; real-world ideologies/religions are excluded at genesis prompting and **leakage is measured, not punished**.

## 8. The operator: President & God

### Viewers (the operator's window into the society)

Beyond the map itself, the app must surface what is actually happening:

- **Location viewer** - pick any place (temple, government complex, cafe, farmland, ore, forest...) and see it in detail: who is inside right now, what they are doing, what tools can be used there, live prices and inventory, ownership, and the history of what has happened there.
- **World view / governance** - the state of the polity at a glance: the current Charter with every amendment applied, **active policies** (tax rates, credit-store margin, jobs created or cut, pensions, permits, police force size), and a **constitution change log** showing each amendment as the actual tool call that enacted it, who proposed it, who voted which way, and what text changed (diffed against the previous version).
- **Voting viewer** - live elections: declared candidates, campaign period, votes as they are cast in person at town hall, running tally, result, and transfer of power. Historic elections browsable, so incumbency and regime change are visible over time.
- **Scenario / special-event viewer** - a curated feed of the moments that matter: crimes and trials, births and deaths, marriages, disasters, miracles, cult formation, business launches and failures, amendments tabled, regime transitions. Each entry expands into its full causal trace.
- **Conversations** - individual chat view (already present) **plus group conversations**: multi-agent discussions in a place, meetings, negotiations, party and cult gatherings, family dinners. Filterable by place, participants, and time, with the utterance trace behind each.
- **Agent finder** - search and browse all 250: by name, age, sex, occupation, business, household, place, wealth, health, faction/religion, ablation arm, alive/dormant/dead. Sortable and filterable, so any individual can be located quickly.
- **Agent activity viewer** - what one agent is doing *right now*: current action and why, where it is, who is with it, its plan for the day, wallet and sim balances, health, skills, relationships, and its live utterances.
- **Agent reasoning viewer** - the research centrepiece: for any action, the agent's actual chain of reasoning behind it. Retrieved memories that fed the decision, the prompt it thought against, its reasoning text, the tool call it chose, the model and lane that served it, tokens and cost. This is what turns "the agent bought flour" into "here is *why* it bought flour, and what it cost to think that."

All of these read from the trace store and the world's live state - they are windows, never authorities. Nothing an operator views may mutate the world except through the President/God console below.

### President and God console

One console, two hats, over the live game view:
- **🏛 President:** land petitions (approve → map grows), veto amendments, address-the-nation, honors.
- **⚡ God:** prayer feed with grant / **silent-deny** (agent never learns), baby blessing queue, disaster picker (crop blight, storm, quake, plague), miracles, misfortune bolts, population dials.
- Click any agent → live conversation, memories, wallet, relationships, trajectory. Alert feed: crime spikes, cult formation, amendments tabled, regime change.

## 9. Architecture

```
                    ┌────────────────────────────────────────┐
                    │  m5.metal (spot, ap-south-1, ~$0.75/hr)│
                    │  ┌──────────── Firecracker ──────────┐ │
                    │  │ 250 microVMs · engine direct       │ │
                    │  │ 1 pinned thread · no coordinator   │ │
                    │  └───────────────┬────────────────────┘ │
                    │        herald base_url → broker          │
                    │  ┌───────────────▼────────────────────┐ │
  SQLite/WAL ◄──────┼──┤ BROKER  allowlist{kimi,deepseek}   │ │
  Litestream → S3   │  │ 6 lanes · 100 RPM each · wallet    │ │
  traces, payloads  │  │ metering · parent-debit · traces   ├─┼──► Bedrock (flex)
                    │  └───────────────┬────────────────────┘ │
                    │  ┌───────────────▼────────────────────┐ │
                    │  │ WORLD SERVER  map · ticks · tools  │ │
                    │  │ ledger · charter · health · crime  │ │
                    │  └────────────────────────────────────┘ │
                    └────────────────────────────────────────┘
        ┌───────────────────┐      ┌──────────────────────────┐
        │ GAME APP (Phaser)  │      │ RESEARCH  nightly batch  │
        │ live town · agent  │      │ 7 tracks · dream probes  │
        │ inspector · Pres/  │      │ ablations · metrics      │
        │ God console        │      └──────────────────────────┘
        └───────────────────┘
```

- **Broker** is the ONLY path to Bedrock. Hard-fails any model outside the allowlist. Batches trace writes async (never blocks inference).
- **Trace store (decided 2026-07-30, replaces Supabase): SQLite (WAL mode) on the host** — tables: `agents, llm_calls, events, utterances, memories, transactions, businesses, organizations, petitions, cases, buildings, skills, tools_registry, relationships, world_snapshots`. **Litestream** streams the WAL continuously to S3 (spot-death safe, restores to seconds-old state). Raw LLM payloads gzipped to S3. Per-game-day **Parquet export to S3**; research analysis via DuckDB. Post-run the whole experiment is a portable artifact (one .db + Parquet). The game app is fed by the world server's own WebSocket relay (no external realtime). Swap to native Postgres only at ~1000 agents / multi-host — same client interface.
- **Checkpoint** full world state every game-day (spot-interruption safety; burst or continuous pacing).
- **Throughput reality:** quotas are per model **per region**, so section 4's six lanes carry 100 RPM each and **600 RPM aggregate** is the binding constraint → event-driven scheduling (~30% of agents need a call per tick). **Measured in round 1 (2026-08-01):** 62,466 calls over 8 game-days for $214.06, at $0.00343 per call. The earlier "~40s/tick, ~1.1hr per game-day, ~6 game-days on $500" estimate assumed 200 RPM combined and a 3,443-token turn; real input was 10,431 tokens per call, so the run went further on the budget than forecast while each call cost more than forecast.

## 10. Research program (original — 7 tracks)

| Track | Flagship metrics |
|---|---|
| Political science | Authoritarianism Index; regime-transition triggers; turnout; cult centralization; soul→vote conversion |
| Economics | price-discovery speed; Gini + mobility; cognitive poverty traps; pension fiscal gap; currency velocity |
| Sociology | network topology evolution; norm emergence; promise-keeping; gossip diffusion; disaster response |
| Religion | de-novo doctrine genesis; conversion/schism; prayer altruism index; **theodicy after silent denials** |
| Machine psychology | ground-truth deception rate; altruism under scarcity; survival ethics at $0; soul drift; model-choice metacognition |
| Business readiness | per-function scorecards vs **10 scripted control businesses**; failure taxonomy |
| MAS engineering | GDP per inference-dollar; conversation storms; invention rate & automation adoption; knowledge diffusion |

**Instruments:** dream probes (sleep-tick interview batteries, never written to agent memory, Kimi batch tier) · ablation arms (~210 full / 20 no-reflection / 20 no-learning) · Earth-leakage metric (do real ideologies re-emerge de novo?).

## 11. Costs (live-verified 2026-07-30; round-1 actuals 2026-08-01)

| Item | Number |
|---|---|
| Kimi K2.5 flex (Mumbai) | $0.36 in / $1.80 out per 1M |
| DeepSeek V3.2 flex | $0.37 in / $1.11 out per 1M |
| Cost per agent-tick (blended) | ~$0.003 estimated; **$0.00343 measured** in round 1 |
| Game-days on $500 | ~6 estimated; round 1 ran **8 game-days on $214.06** |
| m5.metal spot | ~$0.75/hr → **~$6 infra total** (1% of spend) |
| Bedrock quota | 100 RPM per model **per region**; 6 lanes = **600 RPM aggregate** (section 4) |

## 12. Repo layout (`/Vat/kasukabe`)

```
engine/            copy of September engine (uncommitted work preserved)
september-sdk/     copy of the SDK
smallville/        Apache-2.0 reference: Phaser frontend + Ville assets
app/
  world/           world server: map, ticks, tool registry, ledger, charter,
                   health, crime, genesis generator
  broker/          Bedrock broker: allowlist, RPM buckets, metering, traces
  game/            Phaser town view + agent inspector + President/God console
  research/        metric jobs, dream probes, control businesses
  supabase/        trace store: SQLite/WAL client, schema, migrations, S3
                   archive (directory name is historical; Supabase was
                   dropped 2026-07-30, see section 9)
  infra/           Firecracker rootfs build, metal-host bootstrap, deploy
DESIGN.md          this document
```

**Build path:** world+tools → broker+ledger → herald seam → Supabase → game app → research → 10-agent local validation → operator sign-off → AWS → team link.

*September · internal research · 2026-07-30*
