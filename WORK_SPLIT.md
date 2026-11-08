# Splitting Round 2 — two tracks, one merge, no conflicts

**What this is:** now that the plan (DESIGN.md + COGNITION_DELIVERY.md + AGENT_LIFE_FRAMEWORK.md, published as "The 250") is approved, this is the complete, item-for-item work split between the two of you — every buildable piece of that document accounted for, sized roughly equal, and drawn along real file boundaries so both branches merge into `main` without fighting over the same lines.

**Revised from the first pass:** checking the actual code turned up something that changes the scope — `hire`, `fire`, and `transfer_business` (sale-or-inheritance handoff) are **already real, working tool calls** in `world/tools/primitives/civic.py`, and government policy/treasury plumbing already exists in `world/charter/government.py`. AGENT_LIFE_FRAMEWORK.md §23 ("go from exists to actually runs") reads like new mechanics are needed; they mostly aren't — what's actually missing is **visibility**: nothing renders profit/loss, a treasury balance, or an election's consequences anywhere yet. That moved a chunk of assumed "new mechanic" work into the dashboard track instead, which is reflected below.

---

## The split, in one line each

- **Track A — Sharan — "the base."** What one agent knows, says, does, and owns: persona delivery + speech (COGNITION_DELIVERY.md), the session log and its context-recovery tool, the life-cycle transition at 18, and property/land ownership.
- **Track B — Denistan — "the town, watched and kept alive."** The fault-tolerant watchdog, and every visibility surface AGENT_LIFE_FRAMEWORK.md asks for — family survivability, skills, business, and government — plus the demographic research module.

Both tracks are almost entirely file-disjoint by design. There is exactly **one** file both of you touch, in two different functions — flagged explicitly in section 3.

---

## 1. Track A — Sharan: the base

| # | File | What goes there | Source |
|---|---|---|---|
| A1 | `engine/src/sim/pinned_boot.py` | Wire `ConsciousnessManager` into the `Coordinator(...)` call (RC-1) — read `persona.txt` from `profile.september_home`, register it as a STABLE section before the frozen engine's generic identity can fire | COGNITION_DELIVERY.md §1 |
| A2 | `app/world/life/soul_seed.py` | New `build_persona_prompt(agent)` — renders the persona-depth fields (bio, tension, values, opinions about named neighbours) that `build_soul_seed()` doesn't already cover | COGNITION_DELIVERY.md §1 |
| A3 | `app/world/run_genesis.py` | Two additive changes inside `start_agents()`: (a) `seed_genesis()` parses `RichGenesis` instead of `Genesis` so persona data survives; (b) before each engine spawns, write `<home_root>/<agent_id>/persona.txt` | COGNITION_DELIVERY.md §1 |
| A4 | `app/world/tools/primitives/social.py` | New `say` tool (RC-2) — `SayParams`, `say_handler`, registered in `build_specs()` | COGNITION_DELIVERY.md §2 |
| A5 | `app/world/wiring.py` | `TraceStore.record_utterance_if_new()` — the missing branch that actually writes to the `utterances` table, called from `bind_event_bus()`'s existing handler | COGNITION_DELIVERY.md §2 |
| A6 | `app/world/ledger/action_costs.py` | One new `SCHEDULE_TABLE["say"]` row — SOCIAL category, flat cost | COGNITION_DELIVERY.md §2 |
| A7 | `engine/src/sim/session_log.py` **(new file)** | The 4-field JSON log (input / reasoning / actions / done status) written once per turn, plus the tool an agent calls to read a recent slice of its own log back into context | The 250 §32 |
| A8 | `app/world/life/household.py` (extend) | Life-cycle transition: when a minor crosses `MINOR_AGE_LIMIT_YEARS`, the parent-billing relationship for their own cognition ends, and the agent's next tick actually decides — stay and contribute, move out alone, or pair off — instead of nothing happening | The 250 §26 |
| A9 | `app/world/tools/primitives/property.py` **(new file)** | 1×1 tile ownership (government vs. private assets), the auction tool for expanded land, and the will/inheritance-override mechanic — same shape as `commerce.py`'s `transfer` | The 250 §31 |

**Why this is "the base":** A1–A6 are the exact same edit set already proven to work end-to-end earlier in this project's own history (tested locally against a fake broker, then reverted pending this approval), so this half of the track has the least unknown risk. A7 hooks into the same boot function you're already touching in A1. A8 and A9 are both genuinely new mechanics — nothing in the codebase handles a life-cycle transition or tile-level ownership today — and both are self-contained additions with no dependency on Track B.

## 2. Track B — Denistan: the town, watched and kept alive

Nothing here requires Track A's work to exist first — every view below can be built and tested against data the ledger already produces from ordinary trades, hires, and elections today.

| # | File | What goes there | Source |
|---|---|---|---|
| B1 | `infra/firecracker/supervisor.py` | New watchdog: polls (or subscribes to) `UnitState`/`last_health_check_at` on every managed unit and calls the *already-existing* `Supervisor.restart(unit_id)` the instant one goes `FAILED` — closing the one real gap in that interface | The 250 §33 |
| B2 | `app/world/run_genesis.py` | **One line only** — see section 3 | The 250 §33 |
| B3 | `app/world/family_api.py` **(new file)** | Read-only endpoints: per-household solvency, the town-wide at-risk watchlist sorted by runway, the wealth leaderboard, family-tree data (relationship graph + each node's live status), town-wide money-flow totals — reads the trace store, mutates nothing | The 250 §19.1–§19.6 |
| B4 | `world/agents_api.py` (extend) | Business profit/loss over time, per business — the data already exists in `transactions`; this is a new query and view model, not a new ledger | The 250 §23 |
| B5 | `world/governance_api.py` (extend) | Treasury balance made visible alongside the policies it already returns, plus election-outcome traceability (which policy changes happened under which Prime Minister's term) | The 250 §23 |
| B6 | `world/agent_viewer_api.py` or a new `world/skills_api.py` | Skill view per agent (which skills, what level, self-taught vs. taught by whom), a town-wide skill-spread view, and a family skill tree | The 250 §22 |
| B7 | `game/console/app.py` + new templates/static | Every view above, rendered — status chips, the household solvency card, the at-risk watchlist table, the wealth ticker, the skill views, the business/treasury views — presented with a confidence/plain-question framing rather than bare numbers | The 250 §19.7 |
| B8 | `app/research/demographics.py` **(new file)** | Population-level measurement: behavior broken down by demographic slice (age band, sex, occupation, household shape) and a bias check against it, the same methodology the two papers in §24 use | The 250 §24 |

**Why this is "the town":** B1 is pure infra, orthogonal to what any agent is thinking. B3–B8 are all read paths over data structures (`transactions`, `events`, the charter, `agents`) that are already populated today by mechanics already in the codebase (`transfer`, `hire_employee`, `fire_employee`, elections) — none of it needs a new mutation, only a new way to look at what's already there.

## 3. The one shared file, and how to not collide on it

Both tracks touch `app/world/run_genesis.py` — but in two different functions, which is what keeps this safe:

- **Track A** (A3) edits inside `start_agents()` — writing `persona.txt` per agent, right before each engine subprocess spawns.
- **Track B** (B2) adds **exactly one new line** — a call to the watchdog from B1 — in the runner's startup path (next to where `start_embedded_world_server()` or the equivalent supervisor setup already happens), not inside `start_agents()` at all.

Because these are two different functions in the same file, git merges them automatically in the overwhelming majority of cases. To remove even the small residual risk: whoever finishes first merges to `main` first; Track B's one line goes in as a follow-up commit *after* A3 has already landed, so there's never a moment where both diffs exist unmerged against the same base at once.

No other file in either track's list appears in the other track's list.

## 4. Suggested git workflow

- Two branches: `sharan/base-persona-property` and `denistan/town-dashboard-resilience`.
- Each of you works entirely inside your own new/owned files (sections 1 and 2) until you reach the shared-file step in section 3.
- Pull `main` before starting, and again right before touching `run_genesis.py`.
- Small, frequent commits — easier for the other person to review if `run_genesis.py` needs a second look.
- Neither of you needs the other's branch to test: Track A tests against the existing mock world (world `mock_mode=False` + broker `MOCK_MODE=true`, $0 spend, per COGNITION_DELIVERY.md's own local-validation plan). Track B tests against existing ledger/trace data without any agent needing to be "alive" or talking yet.

## 5. Suggested order within each track

**Track A:** A3 (genesis loads `RichGenesis`, writes `persona.txt`) → A2 (the renderer A3 calls) → A1 (the seam that reads the file) → A4/A5/A6 (speech, all three land together) → A7 (session log, once A1's boot path is stable) → A9 (property — fully independent, can also be done first if you'd rather start there) → A8 (life-cycle transition, last — the smallest, most isolated piece).

**Track B:** B1 (watchdog — fully independent, good first task) → B4/B5/B6 (the three read-only API extensions — independent of each other, split further between sessions if useful) → B3 (the family API, new file) → B7 (console views, once B3–B6's endpoints are real) → B8 (demographic module — independent of everything else, can run in parallel the whole time) → B2 (the one shared line, last).

---

*September · internal research · plan, approved — implementation split, revised for full coverage, 2026-08-20*
