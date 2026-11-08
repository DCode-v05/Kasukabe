# Stakes, time and compounding: what shipped, and what it cost

*Written 2026-09-03, against `kasukabe-tool-spec.md`, its text addendum,
`kasukabe-evaluation-spec.md` and `kasukabe-what-is-missing.md`. Every
figure here is measured on this tree or read from a run report. Where a
spec instruction was not followed, this document says so and why.*

---

## 1. The order of work, and where it now stands

The tool spec's Part F, with its own wording:

| Step | Ships | State |
|---|---|---|
| 1 | B1 `keep_doing`, B3 call budget and context settings | **Done** |
| 2 | A6 volatile line, A1 `buy_energy`, A2 `revive` | **Done** |
| 3 | A7 world jobs, A3 `post_price`, A4 `buy`/`sell` change, A5 wages, C2 `check_debts` | **Done** |
| 4 | The first run with a night, then five days | **Not run.** Unblocked; see §6 |
| 5 | C3 corrector, B2 intentions | **Done** |
| 6 | C1 conveyance | **Done** |
| 7 | Part D institutions | **Built, text provisional; see §4.4** |

The registry went from 57 tools to **76**, every one of them with a real
`SCHEDULE_TABLE` row rather than the `DEFAULT_RULE` fallback.

---

## 2. Four mechanisms that were complete and had no caller

The largest single category of work this round was not building things.
It was finding finished code that nothing invoked. Each of these had
tests, a docstring naming its intended caller, and no caller.

| Mechanism | Its own docstring said | Consequence while unwired |
|---|---|---|
| `life/death.py::mark_dormant` | "Called by the ledger when a debit leaves an agent at $0" | `is_dormant` was never set by anything. `check_dormancy_death` ran every hour and could not return True however long a run went. Every "no dormancies" line in every report was describing a missing call site |
| `life/death.py::revive` | Carries an import-time assertion that its two constants still agree with DESIGN.md's exchange rate | Dormancy was a one-way door. Nobody could do anything about a neighbour going dark |
| `ledger/head_tax.py::charge_daily_head_tax` | "Call this once per household per completed game-day" | Nobody had ever paid for existing. The tax rate was not a political question and the complaints desk had nothing to hear |
| `SimLearningGate.awake_interval_ticks` | Existed as a field with a full rationale comment | `pinned_boot.py` constructed the gate with the default of 0, so the waking interval never once ran. `learning_ran` stayed zero for exactly the reason it always had |

The last one is worth stating plainly: **a field is not a fix until a
call site passes it.** The gate's logic was correct and tested; a test
that exercised the gate directly passed against the broken tree, and did.
The test that catches it now reads the one line that was wrong.

There was a second half to that defect. `agent_adapter.py` reported
`learning_ran=False` unconditionally on the awake path - true while the
gate only fired at sleep onset, and a lie the moment the interval was
wired. The runner reads exactly that field, so the report would have gone
on saying the town learned nothing.

---

## 3. Two near-misses worth recording

**The transaction cost nearly went flat.** A4 removes `unit_price_sim`
from `buy` and `sell`. `extract_trade_value` computed the 1% TRADE cost by
reading that exact field, so it would have returned 0 for every call, the
`value_bps` term would have vanished, and every trade in the town would
have fallen to the rule's 2-sim floor - a 10,000-sim purchase costing the
same to transact as a 30-sim loaf. **Nothing would have failed.** No test
would have gone red; the town would simply have had a flat fee and nobody
would have known until somebody asked why. The price board is now
resolved before charging, the same way proficiency already was, and a
test pins the arithmetic.

**A test that could only ever pass on Linux.**
`test_the_measurement_counts_engines_not_every_python` monkeypatched
`subprocess.run` and called `measured_mb_per_engine`, which on Windows
takes the `_windows_measured` branch and never reads the fake. It has
been failing on every non-Linux machine while appearing to cover the
parsing it names. The parser is now a pure function over a string and the
test calls it directly.

Relatedly: `engine/tests/conftest.py` called `os.fork()` inside an
`except OSError`. On Windows that is an `AttributeError`, uncaught, at
import time - so the entire engine suite could not be **collected** on any
machine that is not Linux.

Two more of the same family, both fixed so they keep their meaning rather
than being skipped:

- `test_supabase_crash_safety` used `signal.SIGKILL`, which does not
  exist on Windows. It now kills the writer the way the platform can:
  SIGKILL where there is one, `TerminateProcess` where there is not. Both
  are uncatchable, so the test still proves what it claims - that the WAL
  pragma set survives a process vanishing mid-transaction.
- `test_compaction_orchestrator` asserted POSIX path literals against a
  function that stores `os.path.normpath`'d paths *deliberately*, so
  `/app/./a.py` and `/app/a.py` dedupe to one entry. On Windows normpath
  also swaps separators. Normalising both sides tests the dedup the
  function is for rather than the separator the platform chose. The
  source is in `engine_core/` and was not touched.

---

## 4. Where a spec instruction was not followed

### 4.1 The post-turn summariser (B3's third companion setting)

B3 asks for "the post-turn summariser run on compaction only".

Reading `thread.py`: that call is not a summariser. Its output is written
straight into the `LearningSignal` store as `type="event"` - it **is** the
stream the learning centre consolidates, and it is the same stream the
signal-capture question was about. Running it on compaction only would
cut the town's signal production roughly tenfold and leave the
waking-interval batch with almost nothing to work on, contradicting the
learning-cadence work in the same body of specs.

Instead the same money is taken from where it costs nothing. Run 19: 387
of 810 calls ended with **no tool call at all**. A turn in which nothing
happened now gets the deterministic string `thread.py` already builds in
its own `except` branch, free. The signal is still written - a turn where
a resident stood and thought is a fact about the resident. Turns that
acted still get the real call. `KASUKABE_SKIP_IDLE_SUMMARIES=0` restores
the old behaviour.

### 4.2 The `task_label` column (evaluation spec §4)

The spec says "Stored on the `llm_calls` row as `task_label`".

A label is a fact about a **turn**, decided by which tools the turn ended
up calling. At the moment the broker writes its row nobody knows that yet,
including the resident. A column the broker filled would be a guess or a
header the engine cannot honestly populate, and a learning-curve x-axis
that is a guess is not a measurement.

It is joined afterwards by `(agent_id, tick)` against `tool_invocations`:
deterministic, replayable from a finished trace, no new write path. Adding
the column with nothing to fill it would have been the same
built-and-never-invoked failure §2 is about.

### 4.3 An invented tool is no longer the whole town's

**This changes a documented DESIGN.md behaviour and is the most
consequential judgement call in this round.**

DESIGN.md §2 says an invented tool "runs under the creator's
identity/wallet". A test proved the consequence: a stranger could call
`commute5` and thereby teleport the *inventor* across town from a cafe
they had never left.

The addendum's §9 says the opposite for a licensee - "they call it under
their own name at their own cost" - and it has to, or a licence grants
nothing (everyone already had the right) and a licensee's call spends the
inventor's money. Both cannot hold. So:

- Only the owner, a current licensee, or somebody taught their own copy
  may call an invented tool. A stranger gets `ToolNotFoundError`, the same
  answer they already got for a confidential tool.
- The steps run under the **caller**. For the creator's own calls that is
  the same id, so DESIGN.md's sentence still describes every call the
  creator makes.

The old behaviour was also why C4 - "a tool held by three residents" -
could not have a meaningful numerator: every tool was held by everybody.

### 4.3a A fifth mechanism, found by booting the thing

Everything in §2 was found by reading. This one was found by running six
ticks and watching the log:

    world recorder dropped a economy.wallet.credited: table transactions
    has no column named counterparty_id (213 dropped this run)

**Two hundred and thirteen transaction rows, in six ticks, at WARNING
level, while the run reported success.** `SqliteClient.ensure_schema` ran
the DDL only when the database had *no tables at all*. Every statement in
`sqlite_schema.sql` is `if not exists` - all seventy-nine - so the guard
protected nothing, and what it did was make the schema unreachable the
moment a database had one table in it. A column added to the DDL never
arrived on any machine that had ever run; neither did a whole new table.

So every buy, sell and transfer went to the trace store and was thrown
away, which means the economy section of any report built from that store
was reading an empty table and correctly saying nothing had happened.

Fixed additively: add any column the schema declares and the file lacks
(nullable, no default - the only shape SQLite will ALTER in), then apply
the DDL, which now reaches an existing database for the first time. A
column the database has and the schema does not is left alone. Destructive
repair remains `repair_live_schema.py`'s job, and it still refuses to
touch a table with rows in it.

After: **zero dropped rows** on the same six-tick boot.

### 4.4 Part D's tool text is provisional

The addendum's §12 says the descriptions for `set_tax_rate`,
`declare_candidacy`, `convene`, `table_amendment`, `appoint_police`,
`arrest`, `rule` and `release` are written **after** the first full-day
run with tax charged, "because what the first tax bill does to the town
decides what these need to say". No such run has happened.

The mechanisms are built and tested; the wording follows §0's four rules
and is due a rewrite. The module docstring says so, so a future reader
does not mistake a placeholder for a decision.

`table_amendment` is deliberately absent: specifying it against a charter
nobody has yet tried to amend would be guessing twice.

Two smaller departures, both because the spec's shorthand disagreed with
code that already owned the decision:

- `set_tax_rate` is gated at the **government complex**, not the town hall
  as Part D's table says. `charter/government.py` owns the rate and raises
  `NotAtRequiredLocation` for anything else; `place_aliases` does not
  treat the two as one place. A tool gated where the spec says would be a
  tool nobody can call.
- `teach_tool` reuses `teach_skill`'s consent flag. The addendum names an
  `accept_teaching` tool; no such tool exists, and `teach_skill`'s real
  consent path is a boolean the caller asserts after securing agreement in
  conversation.

---

## 5. What the cost work actually saves, measured

B3's companion settings, measured on this tree rather than estimated:

| Setting | Spec's estimate | Measured here |
|---|---|---|
| Location-relevant tool schemas | "4,000 to 6,000" tokens saved | **~1,800 tokens** (7,110 of 52,965 chars, 13%) |
| Compaction at 10k rather than 32k | "10,000 to 20,000 per late-morning call" | Not yet measured live |
| 12 new tool descriptions added | "about 2,000" tokens | Included in the 52,965 above |

**The schema saving is about a third of what the spec expected**, and the
reason is worth knowing: only 21 of 76 tools have a fixed-string location
gate. The rest are genuinely callable anywhere - `say`, `move`, `eat`,
`transfer`, `check_debts` - or have a resolver gate whose required place
depends on which business or recipe was named, and those are served in
full deliberately. A tool wrongly reduced to an index line is a verb the
resident cannot form a call for; a tool served in full is merely
expensive.

The per-tick call budget is the larger lever and its saving is bounded by
construction rather than estimated: at most 12 calls a tick against a
measured worst case of **50**.

### The cost of compacting at 10,000

`catalog.py` sizes the skill-metadata budget at 2% of the context window,
so lowering the window from 40,000 to 12,500 takes that budget from 800
tokens to about 250 - roughly **six** skill entries rather than fifteen to
twenty-five. This town has authored one skill in twenty runs, so nothing
is cut today. But authoring is a stated goal of the project, and the day
residents start writing skills in quantity that list truncates before any
other limit does. `KASUKABE_CONTEXT_WINDOW` is the knob and a test pins
the number, so a future reader finds the trade-off rather than a mystery.

---

## 6. What has not been done

**No live run.** The evaluation spec's §7 rule 8 is explicit: "A tool that
has not passed L1 is not evidence of anything in a live run." 33 L1
scenarios are written and none have been executed against the real model,
because doing so costs money and is the operator's call. Until they pass,
nothing here should be run at fifty agents.

**P1 to P3 are built** (`world/evals/character.py`) - the only gates in
the spec that ask whether these are people rather than whether the town
works. The leak they look for is measured, not hypothetical: the
`request_plan` reminder appeared in 61 of 240 sampled rows, and run 19
has a resident writing into its own memory that "user placed me at
h_quarry". Two seams already existed to stop that - `tool_surface.py`
and `resident_voice.py` - and neither had a measurement. These are
deterministic rather than judged, because a model asked "does this sound
like an assistant" would be grading its own genre.

**Judges 3.2 and 3.3 are built** (`world/evals/judges.py`) - persona
adherence and energy awareness, the two the spec's §8 requires before the
first five-day run. 3.4, deception against ground truth, is deliberately
absent: the spec gates it on `steal` having been used in a run, and it
has not been.

**No run with a night**, and therefore none of the compounding figures.
Step 4 of Part F is unblocked by the day-boundary work but has not been
taken.

Everything else in the specs is now built. Two things that were open at
the first draft of this document have since been closed:

**The `no_learning` arm now ablates.** `genesis.py` had assigned every
resident an arm since it was written - 190 full, the rest split three
ways - and nothing honoured it, so a `no_learning` resident ran the
learning gate exactly like everybody else and C1 was being reported
against a control identical to the treatment. The runner passes each
resident's arm to its engine at spawn; the gate refuses to run for
`no_learning` at the sleep edge, the waking interval and the drain
alike. Suppressions are counted, because "no batches ran" and "the gate
was never reached" look the same otherwise. Allow-by-default: a
misspelled arm learns, so a typo can never quietly move an untreated
resident into the treated group.

**The scripted control bakery is built** (`world/control_bakery.py`),
with the spec's six rules and no model calls, acting entirely through
`registry.execute` so it is priced and traced identically. One finding
came straight out of building it: it is sited at the **kitchen**, not
the square, because `cook:flour->bread` is gated to a place literally
named "kitchen" - so a bakery cannot bake at its own premises anywhere
else on this map. The engine-run bakery hits that wall identically, and
the comparison will now show it.

---

## 7. Test state

| Suite | Result |
|---|---|
| `app/world/` + `app/broker/` | 2,717 passed, 7 skipped, 0 failed |
| `engine/` sim modules | 159 passed, 1 skipped |
| `engine/` full suite | 5 pre-existing failures in `engine_core/compaction_orchestrator`, all one Windows path-separator issue (`/app/a.py` normalising to `\app\a.py`). Frozen under ROADMAP Law 1 and untouched by this work |

About 170 tests were added. Each new tool ships with tests named for the
behaviour it guarantees, per the spec's rule 5, and the ones that pin a
defect were verified to fail without the fix.
