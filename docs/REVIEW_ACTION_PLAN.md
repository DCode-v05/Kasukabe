# The review, and the plan to act on it

*Written 2026-09-03 after reading all nineteen documents in `docs/review/`
(5,529 lines) end to end. This is what they say and what I propose to do
about it, in the order I would do it. The review is a read-only audit; no
code was changed by it, and none has been changed by this document either.*

---

## Part 1 — What the review says

### The verdict in one line

**The residents are better than the world they live in.** The talk is
good; the physics under the talk keep tripping the residents, and the
survival pressure the whole design rests on has never once fired.

### Four causes explain almost everything

Nineteen documents and roughly 250 numbered findings reduce to four
mechanical causes. This is the single most useful thing in the set,
because it means the work is not 250 items long.

**1. Two composition roots.** There are two world servers. Every launcher
uses `run_genesis --world-mode embedded`; the map service, crime engine,
charter event sink, trace event binding and research viewers are all wired
into the *other* one (`world/main.py`). So map growth is a logged no-op,
politics events go to a void, the crime engine is never constructed, and
the research viewers 404 — in every run ever made.

**2. Engines built as libraries with no tick driver.** Elections, crime,
construction completion, zone regeneration, pensions, inheritance and the
learning drain each have a documented intended caller that nobody wrote.
They pass their tests. They have never run.

**3. Silent defaults and dead branches.** A head-tax rate of zero that
passes its own gate. Checkpoint snapshots written to an in-memory database
that logs success. A perception branch whose inputs are never produced. A
pacing interval nothing reads. A memory system constructed with no stores.

**4. Tests that assert source text.** The exact paths that are broken are
covered by tests that grep the code for a function name. They cannot fail.

### The eight things that stopped run 23 being a day

From doc 01, tallied from run 23's own transcript:

| # | What happened | Why |
|---|---|---|
| 1 | **Time never bit.** Tick 83 = 13:50 on day 0. Upkeep, tax, sleep, learning, dormancy all hang on tick 144 — 2.5 hours and ~$32 away. Every wallet ended at $5.25–5.85 of $6. | Day boundary unreachable at this pace and price |
| 2 | **Two geographies.** `craft` 5 succeeded / 18 refused; `buy` 1/9; `cook` 0/2. The baker could not post a bread price, so 20 loaves sat unsold while 17 people spent 98 turns asking for bread. | Five place-id vocabularies, no resolver |
| 3 | **Walking is invisible.** `move` is sometimes an instant jump, sometimes a multi-tick walk, and the resident is not told which. `look_around` reports the origin mid-walk, so residents re-issued moves or called `rest`, which cancels the journey. 12 times. | No journey state in perception |
| 4 | **Thinking leaked into silence.** 152 of 349 sessions were one turn with no tool call. Rosalind Vance emitted Kimi's native tool-call markup as plain text, unparsed, nine sessions running — she answered the flour question seven times into a void. | Kimi text-form tool calls parsed by nothing |
| 5 | **Speech outlives presence.** 51 of 224 `say` calls had absent addressees; unanswered questions persist for 34+ ticks after the asker left. | No expiry on presence |
| 6 | **Debts lived only in prose.** All 20 `check_debts` calls returned empty. Bram paid the 400-sim debt, then Ainsley paid it again, both with `repaid_sim: 0`. Marta doubled her money without selling a loaf. | No obligation ledger |
| 7 | **Only thinking costs anything.** `mine` costs 0 sim, `move` 1. Balances ran 168–5,713 against 90/day upkeep. | No scarcity in sim |
| 8 | **One tick holds a whole day.** Sessions run to 12 tool calls; 231 food units existed while all 20 residents went hungry, because perception never lists household storage or shop stock. | No per-tick action budget; no holdings in perception |

### The five findings that cost the most

1. **The learning centre cannot write a single row** on the sim path — the stores are never constructed. Nothing consolidates, no soul has ever moved, and batches re-send the whole history at 55K–108K tokens.
2. **Every model call is emergency-compacted.** The day-0 perception is pinned into every prompt for the life of the run; tool results are gutted.
3. **Nobody can build anything.** No plot, no licence, no completion caller — and a finished smithy would satisfy no gate.
4. **The genesis election is never tallied**, so every government tool has been uncallable in all 23 runs.
5. **A theft case is filed against the victim.** The justice pipeline convicts the robbed — and the built one is never constructed on the running host.

### What is genuinely sound

The dispatcher (one path, lock, snapshot, charge-before-handler with
refund, refusal log, event publish). The ledger's $1000 invariant. The
broker's metering, purpose routing and stream repair. Speech, addressees,
open questions, group conversations. Persona delivery, with the
law-of-the-town ordering measured. The charter, crime, life, map and
construction packages **as libraries**. And the evaluation discipline.

**None of the fixes require touching the frozen engine core.**

---

## Part 2 — The plan

### How I propose to sequence it

The review's own roadmap (doc 09) is seven phases. I agree with its
ordering and would change one thing: **phase 0 and the cheapest half of
phase 1 should be one push**, because a single root with no memory and a
compacted prompt still cannot show anything.

Each phase below ends in a gate that is a query over a completed trace,
never a mid-run number.

### Phase A — One root, and a record that does not lie (~1 week)

The precondition. Without it every later fix lands on the wrong host or on
a test that cannot fail.

1. **Fold the two world servers into one.** Recommended direction: the
   runner absorbs `build_world_server`'s wiring — map service, crime
   engine, charter sink, trace event binding, social and viewer routers.
   One day's work, then decide whether `main.py` survives as a fixture.
2. **One day-job module and one tick-job module**, called from the root:
   upkeep, head tax, wages, pensions, inheritance, intention expiry,
   obligation accrual; election phases and tally, construction completion,
   zone regeneration.
3. **Charge at real day boundaries**, not the first tick.
4. **Fix the record**: checkpoint to the real database, `tick` on every
   call row, charter events on the bus, world events recorded, a run
   manifest.
5. **Replace every source-string test on the live path** with a
   behavioural one, and add a "has a production caller" test per engine.

**Gate:** a mock run crosses tick 144 on one root; every day job fires
once at the boundary; `politics.election.scheduled` and
`economy.upkeep.charged` are in the trace *on disk*; the suite is green on
a host without map assets by **skipping, not failing**.

### Phase B — A day that is affordable and remembered (~2 weeks)

6. **Wire the memory stores** in the seam — episodic, knowledge, social,
   soul — pass them to `MemorySystem` and `ConsciousnessManager`, wire the
   pipeline's invalidation. This is the single highest-value fix in the
   set: it is why no compounding has ever had a chance.
7. **Stop per-call emergency compaction**; bound history in the seam.
8. **Cut tool schemas to ≤4K on the wire** — location-and-role reduced,
   with a one-line index for the rest.
9. **Parse Kimi's text-form tool calls** at the broker.
10. **Real pacing, asynchronous turns, night bursted, dormant skipped**,
    and the pre-halt drain reserved as a *share* of the cap — the fixed
    ten cents never fires, as run 23 showed.

**Gate:** two game-days at ≤$0.15 per resident-day; p95 input ≤8K; every
resident has an episode and a soul diff after night one; no compaction
marker on the wire; no paid turn discarded.

### Phase C — A world that can be lived in (~3 weeks)

11. **One canonical place id per place**, resolved server-side, with every
    refusal naming the id that would have worked.
12. **Workshops belong to businesses**; give every home and the bakery a
    kitchen. This alone unblocks `craft` and `cook`.
13. **Always walk** — seed every resident on a real tile, return the
    arrival tick, stop `rest` cancelling a journey, show "on the road to
    X, N ticks left".
14. **Shelf and till** so an owner can stock and draw; **holdings in
    perception** so a resident can see what it has.
15. **The obligation ledger** — seeded genesis debts, a lend/IOU tool, and
    transfers that settle against a debt.
16. **Re-rate the currencies** so energy is buyable from honest work.

**Gate:** run 23's refusal classes (`no business with id`, `location:mill`,
`you hold N not M`) are empty in a replay; someone buys energy from
earnings; a seeded debt settles once.

### Phase D — Institutions that run (~3 weeks)

17. Election tick job and tally; caretaker government so seats are never
    vacant for a whole run.
18. Case polarity fixed (the victim is not the accused) and one justice
    pipeline, not two.
19. Amendment tools with a veto window; a treasury that spends.

**Gate:** a full election with a transfer of power; the treasury's day
shows salaries and pensions paid.

### Phase E — The town builds (~4 weeks)

20. Plots, permits, builder licences with issuing tools; the construction
    site as a continuable place; completion into a **gated** place; tenders
    with escrow; one map on the production host, drawn in the viewer.

**Gate:** a mill the town needed is tendered, built by hired hands under
`keep_doing`, paid by milestone, walked to, and used.

### Phase F — Market physics, ideology, and the instrument (parallel)

21. Quantity on posts, stock in view, spoilage, a clearing venue.
22. The policy engine: an ideology change alters the mechanics.
23. The research instrument: raw payloads, engine-mind checkpoints,
    nightly exports, research routers on the running host, replay.

---

## Part 3 — What I would do first, if you want one thing

**Phase A item 1 and Phase B item 6, together.** One root, and the memory
stores constructed. They are perhaps three days of work between them, they
are both in the seam and the world (never the frozen core), and until both
are done every other measurement in this project is taken through a
distorting lens: half the mechanisms are absent from the host that runs,
and nothing any resident learns survives the turn it happened in.

---

## Part 4 — The decisions only you can make

The review lists fourteen. These four block the most:

| # | Decision | Why it blocks |
|---|---|---|
| 1 | **One root**: runner absorbs the server's wiring, or `main.py` becomes the host | Everything in Phase A |
| 2 | **`SIM_PER_USD`**: 2,000 with a burn target, or 10,000 with prices scaled | Whether energy is buyable at all |
| 3 | **The President**: a veto window with auto-approval, or a hard gate | Whether an unattended run can ever grow |
| 4 | **History**: bounded by the seam, or the engine's compaction with a raised window | The prompt-size fix in Phase B |

Ten more are in doc 09, each attached to the spec it blocks.

---

## Part 5 — Two things to hold on to

**The review's own limit, stated plainly.** The map assets are gitignored
and absent from that checkout, so 117 of the app suite's failures and skips
are environmental, and **the reviewers never ran the town**. Their evidence
is the code, the tests, and run 23's trace — which is strong for "this has
no caller" and weaker for "this would not work".

**What is not broken.** The hard parts — one dispatch path, a structurally
enforced money invariant, a metering broker, speech that lands, personas
that reach the wire — are done and sound. What is missing is almost
entirely *wiring* and *drivers*, which is the cheapest kind of missing.
