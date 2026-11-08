# What this town has actually been proven to do

*Produced by `python -m proof` on branch `evals/proving-the-town`, against
`Real` at `ba74bab`. 236 claims judged: **177 proven, 59 unproven, 0 failed**,
in 23.4 seconds. Every row cites the evidence it was decided from.
Nothing here was decided by reading source text.*

*Four areas: `reach` (what the recorded corpus proves was ever used),
`agility` (what the real frozen parser makes of another model's stream),
`stories` (what the real town does when a verb is actually called), and
`replay` (whether the calls that once worked would still be accepted).
Every finding on this branch has been fixed and the baseline re-locked;
they are kept below with their evidence rather than deleted, because a fix
whose symptom is forgotten gets reintroduced.*

## The rule

> **Nothing is green that never ran.**

Three verdicts, and the middle one is the point:

| | meaning |
|---|---|
| **proven** | it happened, and here is the row it happened in |
| **unproven** | no evidence either way — never scored as a pass |
| **failed** | it was exercised and did not hold |

`unproven` is not a softer `failed`. It is the instrument declining to
score something it has no evidence about. This project has been bitten
repeatedly by the opposite habit: the head-tax gate compared households
*visited* against households that *exist* and passed in all 23 runs
against a tax of 0 bps that took nothing from anybody.

## Why a second instrument at all

The suite is 2,054 tests and green. The dominant defect class here is not
"the code is wrong" — it is **"the code is never reached"**, and no unit
test can see that, because every unit is correct.

---

## 1. Model agility — 5 found, 5 fixed

The town is about to be driven by a different, more capable model. Every
behavioural fix on record was measured on Kimi K2.5.

`proof/agility.py` runs the **real chain** — `ScriptedBrain` → lanes →
service → `translate.py` → `kimi_tool_calls.py` → a FastAPI app on a real
port → herald's transport → **herald's frozen parser** — and sends it
streams a different provider would plausibly produce. Only the brain is
fake; the trace is a real SQLite file on disk.

The bar is deliberately low: **the town may lose the turn, it may not lose
the truth.** A hazard that raises loudly passes. Five did not, and all
five are now fixed — three in the frozen engine (the stop map, twice, and
the interleaved-block parse) and two in the seam.

### 1.1 A missing `contentBlockStart` loses the call *and* misreports the turn

Both tool calls vanished, nothing raised, and the turn then reported
`TOOL_USE` — it claims to have used tools it did not deliver.

`herald/provider.py:653-657` sets `current_tool_id` only on that event;
`:674`'s `if current_tool_id:` discards the call without it. No exception,
no log, no trace row. The engine is safe today **only because the broker
is generous**: `translate.py:781-808` synthesises the starts Bedrock omits
for *text*. Nothing puts back a missing start for a *tool* block.

### 1.2 A tool call written as prose is billed and changes nothing

A markup style nobody parses produces a turn that costs money, produces
text, and ends `END_TURN`.

This is INFRA-07 with the serial numbers filed off. `kimi_tool_calls.py`
recovers *Kimi's* spelling — the next model will not use Kimi's spelling.

### Both are now **counted**, in the seam

`engine/src/sim/a_turn_that_tried_to_act.py`, a fourth wrapper in
`pinned_boot`'s chain, counts them:

```
+ the seam stays quiet on a healthy turn
    a well-formed stream tripped neither counter (0, 0)
+ a turn claiming tools it never sent is counted        → 1
+ a call written in an unknown markup is counted        → 1
```

The clean control matters as much as the two hazards: a counter that fired
on every turn would "catch" both and mean nothing.

It deliberately does **not** recover either call. In the first the tool's
name never crossed the wire; in the second, guessing at an unknown markup
is how you call the wrong verb with right-looking arguments — the line
`kimi_tool_calls.py` already draws. Control flow is untouched: `thread.py`
already ends the turn on `not turn_tool_calls`, so behaviour was never
wrong. **The record was.** A turn that tried and failed looked exactly
like a resident that chose not to act, and every instrument downstream —
the acting-turn rate, the silent-turn report, the whole question of
whether residents are doing anything — read it as the second.

### And the record no longer lies

`tool_use` with no tool call is not a stop reason, it is a contradiction.
The seam now rewrites it to `end_turn`, so the trace says what happened
instead of claiming tools that were never delivered. Behaviour is
untouched — `thread.py` breaks on `stop_reason != TOOL_USE or not
turn_tool_calls`, and the second clause was already true. Only the reason
written down changes, from a claim to the truth.

**Both rows now pass**, and they pass against the bar this suite set for
itself rather than a lowered one:

> The town may lose the turn. It may not lose the truth.

The call is still lost and still unrecoverable — the tool's name never
crossed the wire. What the suite now requires is that the loss is
*written down*, and it is. The `SILENT LOSS` verdict still exists and
still fires if nothing in the chain notices; the clean control still
proves the counters discriminate. `model-agility` is **17 proven, 0
failed**.

One change of measurement, stated plainly because it would otherwise look
like moving the goalposts: the hazard matrix now runs through the **seam
wrapper chain `pinned_boot` actually builds**, where before it ran against
the bare parser. Testing the unwrapped chain measured something nobody
deploys.

### 1.3 and 1.4 A refusal arrived as an ordinary finished turn — **FIXED**

`guardrail_intervened` → `END_TURN`. `content_filtered` → `END_TURN`.

`STOP_MAP` (`provider.py:573-577` and `:617-621`) mapped three reasons and
flattened everything else. `StopReason.REFUSAL` is declared at
`types.py:274` and was produced by neither map — a member of the enum no
code path could reach. The broker already translates a guardrail stop to
`"refusal"` (`translate.py:52-53`), so **the signal arrived at that line
intact and was flattened there.**

Kimi rarely refuses. GPT-class models refuse often. On a swap, every
refused turn would have been billed, produced nothing, and been recorded
as ordinary — and no instrument downstream could have told.

**Fixed** in `ea243ad` (`engine(law1):`), one key added to both maps.
Both rows now read `the loop was told StopReason.REFUSAL`. Behaviour is
unchanged — `thread.py` branches on `!= TOOL_USE`, so a refusal ends the
turn exactly as before; only the recorded reason became true. Engine
tests: 144 passed. Recorded as entry 1 in
`docs/decisions/LAW-1-MANIFEST.md`, revertable in isolation.

> `max_tokens` arrives correctly as `MAX_TOKENS`. That is how we know the
> probe discriminates rather than failing everything it touches.

### 1.6 Two interleaved tool blocks became one — **FIXED**

`stream_with_tools` held the open tool call in three scalars and read
`event["index"]` **nowhere**. Anthropic and Bedrock both emit content
blocks one at a time, so every start was followed by its own deltas and
its own stop, and the bug never fired.

A provider that interleaves two tool blocks concatenated both argument
strings into one buffer. The usual outcome is `JSONDecodeError` — loud,
and survivable. **The outcome that matters is the other one:** if the two
fragments happen to concatenate into *valid* JSON, the turn calls a real
verb with arguments nobody wrote and nothing notices. That is TRADE-01's
shape, which cost this project 6,424 calls.

Fixed in `6c81f92` (`engine(law1):`, manifest entry 2) — keyed by
content-block index, each block in its own slot:

```
before   two tool blocks interleaved → the turn failed loudly (JSONDecodeError)
after    two tool blocks interleaved → survived intact: 2 calls
```

Sequential streams are byte-identical. A **tripwire caught this fork** —
`test_a_half_written_tool_call_does_not_kill_a_turn.py` asserts the parse
line verbatim so a fork cannot happen quietly. It was updated, not
silenced, and now also checks the parse is still *unguarded*, since
`keep_the_turn_on_a_half_written_call` is still the only thing between a
truncated argument and a dead turn. 189 engine tests pass.

### What survived untouched

Truncated tool JSON still raises `JSONDecodeError` — loud, therefore
survivable, and the seam turns it into a lost turn rather than a lost run.
Colliding `toolUse` ids, a friendly `contentBlockStart` for text, and
arguments of the wrong type all passed through intact. A stream cut before
`messageStop` lost one call of two and said so.

---

## 1.5 A seat that is filled *can* govern

The reachability audit (§2) proves no government verb has ever been
called. It cannot say whether they would have worked. `proof/stories.py`
seats a government the way a scenario seats incumbents, stands the Prime
Minister in the complex, and asks:

```
+ a seat can actually change the tax rate
    set_tax_rate(500) succeeded; the charter now reads 500 bps
```

**So GOV-3 is entirely a reaching problem.** The eighteen verbs are
sound, the ACL flips the instant the seat is held, and the fix belongs in
what a seat-holder is *told* — not in the mechanism. Worth knowing before
anybody spends a run on it.

One thing found on the way, recorded `unproven` rather than `failed`: a
resident with no seat who calls a government verb is answered
`ToolNotFoundError` — **the same answer a typo gets**. A seat-holder who
mistypes cannot tell "I may not do this" from "I spelled it wrong".

---

## 2. Verb reach — 59 unproven, 0 failed

Searched: 520 recorded session files and 44 real trace databases.
`app/data/kasukabe.db` is **excluded** — its manifests carry `mock_mode=1`
and it is where the test suite writes.

### Reached for and never once succeeded — `unproven`, and here is why

| verb | |
|---|---|
| `pay_tax` | 10 calls, 0 successes |
| `pray` | 3 calls, 0 successes |
| `buy_energy`, `withdraw_item`, `work_site` | 1 call each, 0 successes |

These were `failed` in the first version of this report. **That was wrong,
and correcting it is the most useful thing in this section.** Every one of
the eighteen refusals was read. Not one is a defect:

| verb | what the town actually answered |
|---|---|
| `pay_tax` ×5 | *"you owe no head tax"* — the resident owed nothing |
| `pay_tax` ×4 | called with **no arguments**: *"amount_sim: Field required"* |
| `pay_tax` ×1 | `world server unreachable` |
| `pray` ×3 | *"requires location 'temple', actor is at 'home:h24'"* |
| `buy_energy` | *"you hold 685 sim and tried to spend 10000. Buy 685 or less"* |
| `work_site` | called with no arguments: *"building_id: Field required"* |
| `withdraw_item` | *"no storage with id 'field\_storage'"* — an invented id |

A verb that refuses correctly has **held** its contract. Scoring it as a
defect is the same overclaim this package exists to prevent, pointed the
other way — so the instrument now declines to score it.

Two things this does show, and neither is a handler bug:

**The refusals are good.** They name the real parameter, the actual
balance, the required place. That is `registry.validate_params` and the
location gate working exactly as designed.

**The residents are misinformed, repeatedly.** Three prayers from a
kitchen; an energy purchase for fifteen times the wallet; two verbs called
with none of their required arguments; a storage id nobody has. That is
WIRE-03's shape — *"the one parameter that decides whether the call
succeeds was invisible"* — and it is a question about what the served tool
description shows, not about the code behind it.

Every one of these was a paid call that changed nothing.

### Never once called

**All eighteen GOVERNMENT verbs.** `appoint_police`, `approve_patent`,
`convene`, `deny_subpoena`, `file_subpoena`, `grant_plot`,
`grant_subpoena`, `issue_builder_license`, `issue_business_permit`,
`petition`, `reject_patent`, `revoke_patent`, `rule`,
`set_business_licensing`, `set_price_control`, `set_tax_rate`,
`table_amendment`, `vote_amendment`.

This is GOV-3, and the audit shows it is not one bad run — it is the
whole recorded history. They were **served and never reached for**:
`registry.visible_to` gates the namespace on `is_sitting` alone, so every
one was in a seated winner's schema wherever they stood, and the refusal
tables carry not one government entry.

**`vote`.** Residents declared candidacy 10 times in the corpus. The
corpus holds **no ballot at all**.

**`toolify`, `do_in_order`, `define_tool`.** Authoring is served in full
and never used — confirming the finding across a wider corpus than the
five runs it was originally measured on.

Also never called: `hire_employee`, `pay_wage`, `set_wage`,
`fire_employee`, `marry`, `steal`, `arrest`, `build_structure`,
`teach_tool`, `license_tool`, `sell_tool`, and 28 others.

---

## 3. The instrument audits itself too

**`election8` and `teambox50` left no per-turn session files.** The two
most recent runs — including the only one that ever seated a government —
produced a report, a log and a video, and nothing that can be re-read
turn by turn. Their traces (`app/data/election-at-dawn-8.db`) are
gitignored and absent from this checkout.

So every `unproven` above means *"not in the corpus this instrument can
read"*, never *"never happened"*. Saying so is the difference between an
instrument and a claim — and it is also a real finding: **the project
cannot re-audit its own most important run.**

---

## 3.5 Replay — the contract moved under the model's feet

`proof/replay.py` takes every call the 520 session files record as having
**succeeded**, deduplicates by argument *names*, and pushes each through
the registry's real `validate_params` — synonym folding, stray-parameter
refusal and all. World state is deliberately not replayed: those runs had
other towns, so re-executing `move` would fail for reasons that are not
regressions. The contract is what is checked.

**54 of 72 distinct call shapes still validate. 18 do not**, out of 8,482
recorded successful calls.

The largest by far:

```
! sell(business_id, item, quantity, unit_price_sim)
    succeeded 57 times in the corpus and is refused today:
    sell: invalid params: buyer_id: Field required;
    sell takes: buyer_id, item, quantity
```

Others include `post_price(… business_id … quantity)`,
`stock_shelf(… price_sim …)`, `quote_action_cost(params …)` (now
`call_params`), and `rest(conversation_id)`.

**Not every one of these is a defect, and the report does not pretend
otherwise.** Some of the drift is deliberate and good: TRADE-01 turned
`post_price`'s *silent drop* of `business_id` into an explicit refusal, so
a corpus call that "succeeded" by doing the wrong thing correctly fails to
validate now. That is the fix working.

What every row does prove is that **a real model, in a real run, wrote
these argument names and the world accepted them.**
`param_repair.SYNONYMS` exists for exactly this — "72 entries, every one
observed in a real session log" — and these are the spellings it does not
yet fold. A model still reaching for the old name pays for the call and
gets nothing.

Six further shapes (`recall_memories`, `recall_session_log`) are recorded
as `unproven`, not failed: the seam registers those straight onto the
thread, so the world registry never served them and their absence here is
correct.

---

## 4. What to fix, and where

| # | Fix | Lands in |
|---|---|---|
| 1.1 | Track the open tool call by `event["index"]`, not scalars; treat a delta for an unopened block as an opened one | `herald/provider.py` — **frozen**, needs a Law 1 manifest entry, or a seam wrapper alongside `half_written_tool_call.py` |
| 1.2 | Count turns that produced tool-call-shaped text and no call; surface as a run-report line | seam + `run_report.py` |
| 1.3/1.4 | Extend `STOP_MAP` so `refusal` is reachable | `herald/provider.py` — **frozen**, same treatment |
| 2 | `pay_tax` was called 4 times with no arguments — check what its served description shows about `amount_sim` | `world/tools/registry.py` `_index_row` |
| 3.5 | Fold the 18 drifted argument spellings a real model actually used — `sell`'s is 57 calls — or confirm each tightening was deliberate | `world/tools/param_repair.py` |
| 3 | Write session files for every run; stop relying on a gitignored trace | `run_genesis.py` |

Law 1 is deliberately overridden on this branch. Every frozen-engine edit
lands as its own `engine(law1):` commit with an entry in
`docs/decisions/LAW-1-MANIFEST.md` diffed against base `95e8788`, so each
one stays revertable and upstreamable.

---

## Running it

```
cd app
python -m proof                     # the gate
python -m proof --show failed       # just the failures
python -m proof --skip agility      # fast loop (agility boots a server per hazard)
python -m proof --update-baseline   # accept current state as the lock
```

The gate is a **ratchet, not a target**: nothing that was proven may stop
being proven, and nothing may newly fail. Demanding all-green would be red
on the day it was written, and a gate that is red on day one only teaches
a team to bypass it.

| area | what it asks | time |
|---|---|---|
| `reach` | has this verb ever been used? | 3.8s |
| `agility` | what does the frozen parser make of another model's stream? | 8.9s |
| `stories` | what does the real town do when a verb is called? | 10.2s |
| `replay` | would the calls that once worked still be accepted? | 0.5s |
| | **total** | **23.4s** |

### Where the time went, and the guess that was wrong

An earlier version of this section said `stories` was slow **because each
one boots a real 314-resident town**, and recommended pooling the boot.
That was asserted without measuring it, and it was wrong — the same habit
this whole package exists to break, committed inside the package.

Measured:

```
boot       0.4s
teardown  23.1s
```

The boot is nothing. **The teardown was the entire cost**, and the stories
were paying it fifteen times — 347 of the suite's 487 seconds. Pooling the
boot would have saved about seventeen.

Inside the teardown:

```
20.6s  capture_final_reading
 2.4s  archive_engine_homes
 0.0s  collect_wire_health
```

`capture_final_reading` asks the world everything a report will want while
it can still answer — eight routes including `/agents` for every resident
and `/world/utterances?limit=5000` — over an in-process ASGI transport. It
is not slow because anything is broken. It is eight serialisations of a
large world, and it exists because the 50-agent run ended with a report
saying *"The world reported no roster"* about fifty residents. For a paid
run that is cheap insurance bought once.

A story needs none of it: it holds the world object, asserts against the
live ledger and charter, and reads its evidence off the trace on disk. So
`proof/town.py` lifts those two run-only steps out of its teardown and
lets every other part of `runner.shutdown()` run exactly as production
runs it. Teardown went **23.1s → 0.1s**.
