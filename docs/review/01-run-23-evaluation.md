# Run 23, evaluated against "a real day in a real town"

*Written 2026-09-03 against `docs/runs/run-23-report.md` (20 residents,
$10 cap, tick 36 to 83) and the code on `Real` at `5400e94`. Every count
below was tallied from the report's own transcript and turn tables.*

## Verdict

Run 23 is not yet a day in a town. It is twenty minds spending 48 real
minutes in one game morning, in which nothing could ever come due. The
talk is good: the model holds a price book, repays a debt carried through a
dozen runs, argues about a boundary marker with forty years of backstory.
The physics under the talk keep tripping the residents, and the survival
pressure the design is built on never fires. Almost all of it is mechanics
that exist and are wired or tuned wrong, not model behaviour.

## What kept it from being a real day

### 1. Time never bit

Tick 83 is 13:50 on game-day 0. Upkeep, head tax, sleep, learning
consolidation, dormancy and death all hang on the day boundary at tick 144.
At the measured pace (about 63 s of wall clock per tick, $12.92 per hour)
that boundary is 2.5 hours and roughly $32 away, so a $10 run can never
reach it. Nobody paid to exist, nobody slept, every wallet ended between
$5.25 and $5.85 of $6. "Stay alive" was a sentence in the prompt, not a
force.

### 2. Two geographies

Recipes gate on `mill`, `sawmill`, `kitchen`; the businesses live at
`business:b_mill` and homes have no kitchen. Places carry two or three
names each (`temple` and `Kasukabe Temple`, `government_complex` and
`Kasukabe Government Complex`, `farmland` and `farmland:wheat`, `b_bakery`
and `business:b_bakery`). The baker could not post a bread price because she
used the prefixed id, so 20 loaves sat in stock while 17 people spent 98
turns in her shop asking for bread.

| Tool | Succeeded | Refused |
|---|--:|--:|
| craft | 5 | 18 |
| buy | 1 | 9 |
| trade | 4 | 7 |
| give | 1 | 6 |
| eat | 9 | 10 |
| cook | 0 | 2 |
| move | 195 | 1 |
| say | 223 | 1 |
| look_around | 217 | 0 |

### 3. Walking is invisible and self-cancelling

`move` is an instant jump when the resident has no known tile (anyone who
started at a `home:*` placeholder) and a multi-tick walk otherwise. The
resident is not told which it got. During a walk, `look_around` still
reports the origin, so residents concluded "my move didn't register" and
re-issued it, or called `rest` to wait for arrival, which cancels the
journey. That happened 12 times. Ainsley Holloway was "stuck at the
quarry" for 25 ticks this way.

### 4. Thinking leaked into the world's silence

152 of 349 sessions were one turn with no tool call: the model wrote a
paragraph of deliberation as its reply and the town heard nothing. Marta
Thorne spent five consecutive sessions deciding while Delia and Bram
waited. Rosalind Vance's nine sessions emitted Kimi's native tool-call
markup (`<|tool_call_argument_begin|>` and friends) as plain text,
unparsed, so she answered the flour question seven times into a void. The
one question that stalled the town's bread supply was answered, and never
delivered. Eighteen turns hit the 1,536 output-token cap mid-thought.

### 5. Speech outlives presence

51 of 224 `say` calls had absent addressees and 11 reached nobody.
Unanswered questions stay in the prompt as "still waiting, 34 ticks ago"
long after the asker walked off, so residents keep answering people who
left.

### 6. Debts lived only in prose

All 20 `check_debts` calls returned empty. Bram Holloway paid the 400-sim
debt, then Ainsley Holloway paid it again five minutes later, both with
`repaid_sim: 0`. Marta Thorne doubled her money without selling a loaf.

### 7. Only thinking costs anything

`mine` costs 0 sim and `move` 1. Sim balances ran 168 to 5,713 against 90
sim a day of upkeep, so there was no scarcity in money either. Meanwhile
learning-centre calls of 55K to 108K tokens ran mid-morning at up to $0.04
each, which is why the preflight flags prompt size.

### 8. One tick holds a whole day of doing

Sessions run to 12 tool calls, so Perrin Holloway walked to the grocery,
the bakery and the cafe inside one ten-minute tick. And 231 food units
existed while all 20 residents went hungry, because perception says "you
have no food" without listing household storage or shop stock.

## What to change, in order of leverage

1. **Make obligations accrue hourly.** Charge upkeep and head tax every 6
   ticks at one twenty-fourth of the daily rate, and put a sleep window
   inside the run. Cut genesis sim to 3 to 5 days of runway. Reserve the
   pre-halt drain as a share of the cap.
2. **One canonical id per place, aliases resolved server-side.** Strip
   prefixes, match case-insensitively, and have every refusal name the id
   that would have worked. Gate craft on "a business of kind mill you are
   standing in", and give every home and the bakery a kitchen.
3. **Always walk.** Seed every resident on a real tile so the jump fallback
   never fires. Return the arrival tick, make `rest` not cancel a walk,
   show "on the road to X, N ticks left" in perception, and wake the
   resident on arrival.
4. **Make silence cost and speech land.** Parse Kimi's text-form tool
   calls at the broker or force a tool choice. Report a turn that ends in
   prose as "you stood silent; nothing you wrote was heard". Expire asks
   when either party leaves the place, and make `say` warn before charging
   when the addressee is absent.
5. **Put debts and stock in the ledger.** Seed genesis obligations into a
   debt store, add a lend/IOU tool, and let a transfer against a debt
   settle it. Perception must list own inventory, household storage,
   business stock and posted prices at the current place; item names
   should fuzzy-match.
6. **One world-changing action per tick.** Sense tools stay free and
   capped; output cap around 600 tokens; compaction tied to the wallet;
   learning centre only in sleep and never over a 100K context.
7. **Reconcile the instrument.** Person-posted prices do not emit the
   event the report reads, so the operator note says 12 prices and the live
   report says none. The 120-second dispatch limit discarded 10 paid turns.

Items 1 through 4 alone would have turned this morning into a day where
bread got baked, people got paid, and by nightfall somebody could not
afford to think. The rest of this document set (`00-README.md`) is about
the much larger distance from that day to a town that runs and grows on
its own.
