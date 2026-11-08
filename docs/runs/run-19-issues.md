# Run 19, read line by line

Every finding below came from reading `docs/runs/run-19-report.md` and its
trace store by hand and asking, of each behaviour, *why did it do that*.
Report line numbers are given so each claim can be checked against the
document it came from. Code line numbers are as of the fix commit.

The run: 20 residents, `--opening homes`, $4.06 spent, 810 model calls,
tick 59, 43 utterances, 0 dropped records.

**How to read the citations.** `L176` means line 176 of
`docs/runs/run-19-report.md` as it stands in this commit - open that file
and go to the line. Every number below was checked against the file rather
than counted by eye; an earlier draft of this document had 26 of 36 of them
wrong, because they were counted inside excerpts instead of the whole file.
Regenerating the report will move them.

---

## Issue 1 — Not one coin moved between two residents, all run

**Severity: highest. This is the economy not existing.**

**Evidence.** The trace store holds 564 transactions: 379 `inference_burn`
and 185 wallet↔pool transfers. Agent-to-agent transfers: **0**.

Now read what the town *said* it was doing:

| Report line | Who | What they said |
|---|---|---|
| L176, L178 | Corwin Thorne | "Three of them, milled proper… **Thirty-six sim for all three.** Twelve each." |
| L180 | Nell Holloway | "Corwin. Thirty-six for three planks. **Fair price. I'll take them.**" |
| L206 | Corwin Thorne | "The planks come with me, **unsold** lesson in inventory." |
| L114 | Silas Vance | "**Paying the household** what I hold. 2,538 sim." |
| L206 | Corwin Thorne | "I'll **pay my share** of upkeep from what I have—1,708 sim" |

A price was agreed, accepted out loud, and the seller walked home with the
goods. Two residents narrated paying their households. **None of it
happened**: no `trade`, no `transfer`, no `sell`, no `buy` was called by
anyone in the entire run.

**Why.** `trade` exists, is ungated, and does exactly this — I checked the
live registry. So does `transfer`. The residents never reached them, and
the log says why in their own words:

| Report line | Who | What they believed |
|---|---|---|
| L160 | Silas Vance | "**No business registered.** Walking blind. Need to sell or trade." |
| L172 | Hester Vance | "**register yourself first**—thirty sim-currency at the government store" |
| L180 | Nell Holloway | "You **registered at the government store**, or we doing this between us?" |
| L188 | Silas Vance | "How do I buy here? **No business registered**, but purpose says buy and sell." |

The verb a seller reaches for is `sell`. `sell`'s description said, in
full: *"Sell an item to a business at its shop."* A resident holding three
planks reads that, concludes it needs a shop, and spends the rest of the
run trying to register a business. `trade`'s description never said a shop
was **not** required — it opened "Give goods to someone standing with
you", which reads like a gift.

`give` already solved this with one clause: *"Unlike `sell`, no business
and no payment is involved."* `trade` had no such line.

**Fix.** `app/world/tools/primitives/commerce.py:369` and `:379` — both
descriptions now name each other.

* `trade` → "Sell something to a **PERSON** rather than to a shop… **No
  business, no premises and no registration are needed** — that is `sell`,
  which is for selling into a business at its shop."
* `sell` → "…To sell to a person instead — anyone beside you, no shop and
  no registration required — **use `trade`**."

Tests: `app/world/tools/tests/test_run19_findings.py`, first two cases.

---

## Issue 2 — The shopkeeper and the customer stood in the same shop and could not hear each other

**Severity: high. This is why every shop in every run has been empty.**

**Evidence.** From the resident table (L229–248):

* Delia Okonjo, pharmacist, is at **`loc_shop_willows`** (L244)
* Silas Vance is at **`The Willows Market and Pharmacy`** (L240)
* Junia Vance, grocery clerk, is at **`business:b_grocery`** (L241)

I checked `satisfies_gate('loc_shop_willows', 'The Willows Market and
Pharmacy')` → **False**. They are two rooms to the world and one shop to
any human reading it.

The consequence is in the transcript. Silas, standing in the pharmacy:

> L188 · t40 — "Nell. Need food. **How do I buy here?** … You know prices?
> Sell meals?"

The pharmacist was standing in her own shop and heard nothing. Rosalind
asked "Seen Marta? Or anyone needing flour?" (L190) into the same void.
Corwin walked in and left again: "Thought this was a market, but it's
pharmacy. **Wrong turn.**" (L192)

Three residents spoke in a shop whose owner was in it. `/world/utterances`
records three utterances at `The Willows Market and Pharmacy` and none at
`loc_shop_willows`.

**Why.** The town has three id vocabularies for the same ground — the map
name, `business:<id>` from `business_location_id()`, and shop ids like
`loc_shop_willows` from the map's building data. `app/world/tools/
place_aliases.py` exists precisely to reconcile vocabularies and already
does it for the government complex and the farm sectors, but has no entry
for shops or businesses. Its own docstring names this exact failure mode:
*"A resident who walks somewhere that does not exist stands alone in a
place of its own making: nobody can see it, nobody can speak to it."*

**FIXED.** Two holes, both closed:

* `app/world/tools/primitives/movement.py` - `canonical_destination` gained
  two rules. A place named by its ONE distinctive word now folds onto it
  ("willows" belongs to exactly one room in the town, so `loc_shop_willows`
  can only mean the pharmacy); and where several places share every word
  the agent used, the shortest name wins, so `l_college` reaches "Oak Hill
  College" rather than "Dorm for Oak Hill College". Both keep the module's
  existing discipline: one candidate or nothing, and an unmatched name is
  still returned untouched.
* `app/world/tools/primitives/places.py` - business premises are now listed
  by `list_places` under the name people call them by. A shop nobody can be
  told about is a shop nobody can walk into, and Junia Vance spent
  fifty-nine ticks in one.

Tests: `test_nobody_stands_in_a_room_they_invented.py`, eleven cases,
including the end-to-end one - two residents who spell the shop differently
now hear each other.

---

## Issue 3 — Two residents were told their work is in a room the map does not have

**Evidence.** `WORKPLACE_BY_OCCUPATION` named `"Kasukabe Grocery"` and
`"Kasukabe Construction Co."`. Neither is in the 47 places `list_places`
offers. Junia Vance, the grocery clerk, spent the whole run at
`business:b_grocery` (report L241) — a room no customer can walk into,
because `move` will not route anyone to a name the map does not draw.

Her standing brief told her, every turn, that her work was somewhere she
could not go.

**Fix.** `app/world/run_genesis.py:216-224` — pointed at rooms that exist:
grocery work → `Harvey Oak Supply Store`, construction → `carpentry`. A
test now walks every workplace in that table and fails if any is not a
place an agent can reach: `test_run19_findings.py::
test_every_workplace_a_brief_names_is_somewhere_an_agent_can_walk`.

---

## Issue 4 — A fifth of the money was spent teaching residents they are an assistant

**Severity: high, and it corrupts memory rather than merely wasting money.**

**Evidence.** Half the rows in every resident's turn table are not turns.
They are compaction calls, and the report shows their prompt (L258, L292,
and ~80 more):

> "Summarize this exchange in under 100 tokens total. Format: **[user]**
> what they said/asked (50 tok max) **[engine]** what you did/responded…"

Measured in the trace store: **165 of 810 calls (20%), $0.43 (10% of
spend), 1.1 million input tokens (11%).**

What the model wrote back, into each resident's own memory:

| Report line | Resident | The memory it stored |
|---|---|---|
| L292 | Tobias Holloway | "[user] At 06:00, **user placed me at h_quarry**…" |
| L283 | Ainsley Holloway | "[engine] Assessed pantry stocks (**3 grain, 2 meat, berries for ~2 days**). Decided household cannot sustain five…" |
| L260 | Bram Holloway | "[user] Silas said forge was cold… [engine] Said 'I hear you. Thanks'…" |

The first has a resident believing a **user** put it in its own home. The
second is a pantry that **does not exist** — the whole town held 12 units
of food that morning (L222: cabbage 1, flour 10, wheat 1) and Ainsley held
none. The summariser was asked what an *engine* did for a *user*, and
answered in the genre it was asked for, inventing the props that genre
needs. The next turn reads that summary back as memory and reasons from
it. Ainsley then made **0 tool calls in 12 turns** (L271).

**Why.** The prompt is built inline at
`engine/src/engine_core/thread.py:4241-4246`, which is **frozen** under
ROADMAP Law 1 — and correctly so; it is right for the product that engine
was written for.

**Fix.** `engine/src/sim/resident_voice.py` (new) rewrites that one prompt
on its way to the model, and `engine/src/sim/pinned_boot.py:324` wraps the
primary adapter with it. This is the seam `tool_surface.py` already uses to
take the same engine's seven assistant-shaped tools off a resident's
registry — frozen code untouched, the resident's surface corrected after
the fact. The replacement keeps the frozen prompt's budget (100 tokens) and
focus (facts, decisions, people, actions, no pleasantries), drops the two
roles that do not exist in a town, and adds the line run 19 proves is
needed: *do not write down things that did not happen*.

Tests: `engine/tests/test_resident_voice.py`, six cases, including one that
fails if the wrapper is ever imported but not wired to the live adapter.

---

## Issue 5 — Nothing was authored, and the report said the machinery was fine

**Evidence.** L76 and L78: *"Nothing was authored… An agent is prompted to write a
procedure down only after repeating one job several times, and nobody
crossed that line."* The repetition table (L84–96) shows the town's
maximum: **2 completions** (Silas artisanry, Rosalind farming, Corwin
artisanry and forestry).

So in a $4 run, no resident gets past two repetitions of anything, and the
authoring nudge is gated above that. The gate is not wrong, but it is
**unreachable at this run length** — which makes "nothing was authored" a
statement about the budget, not about the residents.

**FIXED, without lowering the bar.** Lowering three to two would have been
gaming the eval. The real defect was WHAT was counted: a procedure is a
sequence, and the hint only ever looked for one tool used again and again.
Corwin Thorne harvested and cooked, twice over - a two-step job repeated,
exactly what `skillify` exists to write down - and no single tool reached
three, so nothing was said.

`app/world/tools/state.py` now has a second rule beside the first: a PAIR
of consecutive work actions, repeated twice, fires the same hint naming
both steps. The single-tool rule keeps its threshold of three. Travel and
idle actions stay excluded from both, so "walk, look, walk, look" is still
not a trade.

Tests: `test_authoring_hint_is_reachable.py`, five cases.

---

## Issue 6 — Residents repeat themselves verbatim because nothing tells them they were heard

**Evidence.**

* Marta Thorne says the same sentence twice, four ticks apart, once
  lower-cased and once capitalised - both rendered on report L208 (t53
  and t56): *"Corwin. Sit down
  and tell me what you mean by 'learning how to feed myself the hard way.'
  You've been wandering?"*
* Tova Okonjo demands the same yes-or-no answer at t40 (L142) and again at
  t43 (L146): *"Are you holding anything? Yes or no."* Father Anselm never
  answers either.
* Silas Vance delivers substantially the same speech about his unsold plank
  at t40 (L110) and t41 (L196).

**Why.** `say` returns `heard_by`, so a speaker learns who was present —
but nothing tells it whether the person it addressed has since *spoken*.
The `open_questions` mechanism carries an unanswered question forward to
the person who was asked (visible working at L297), but there is no
converse signal to the asker that their question is still outstanding
versus answered. So the rational move is to ask again.

**FIXED.** `WorldState.awaiting_reply` is the other side of
`open_questions`: what THIS resident asked that nobody has answered. It is
rendered into perception by `app/world/retrieval_depth.py` and wired at
`app/world/run_genesis.py`, so a resident who spoke into silence can now
tell "they have not answered" from "I never said it".

It states the fact and stops - no advice to give up, ask again or go
elsewhere. Only directed remarks count, so "Morning, all" is not a debt
anyone owes, and the notice clears the moment they reply.

---

## Issue 7 — One `say` call, three different people addressed inside it

**Evidence.** L132, Ezra Okonjo, one utterance addressed to Father Anselm
(`listener_agent_id` = Anselm), whose body contains three separate
speeches: one to Anselm ("You want temple bread, walk to temple"), one to
Tova ("**Girl.** Two sim for walking, you said?"), and one closing remark
to the room.

The record says Ezra spoke to Anselm. Two thirds of what he said was to
somebody else, and Tova — who was standing there and had asked the
question he was answering — is not recorded as its addressee.

**FIXED, and it needed no behavioural change at all.** Neither splitting
utterances nor teaching one-remark-per-call: the record simply had to hold
what was already true. `social.everyone_addressed` returns every
co-located resident in vocative position, `say` records them as
`addressed_agent_ids`, and both `open_questions` and `awaiting_reply` read
that list rather than the single parameter. Ezra answering Tova inside a
remark addressed to Anselm now discharges her question, which it always
should have.

The vocative test is shared with `addressee_from_content` rather than
copied, so the singular answer and the full list can never drift.

---

## Issue 8 — "What the town could not do" showed one row, and the town could not do a great deal

**Evidence.** L212–216 lists exactly one obstruction: `location:sawmill`,
1 agent, 1 attempt. Meanwhile the transcript has residents unable to sell
(Issue 1), unable to find a buyer, unable to buy food, and walking into a
shop whose keeper cannot hear them (Issue 2).

**Why.** That table is built from *refusals* — tool calls the world
rejected. Everything in Issues 1 and 2 is a failure to ever call a tool.
The table is accurate and reads as reassuring, which is worse than
inaccurate: a run where nobody tries anything scores a nearly empty
"could not do" table.

**FIXED with an exact measure, not a fuzzy one.** No language matching: a
new "What nobody reached for" section lists every tool that exists, needs
no particular place, was visible to all twenty residents, and was called
zero times. `trade` is exactly that, and would have been the first row.
Gated tools are excluded on purpose - `vote` goes uncalled whenever there
is no election, and thirty such rows would bury the one that matters.

`app/world/run_report.py`: `untouched_tools` and `tool_call_counts`.

---

## Issue 9 — The report described a threading method it had stopped using

**Evidence.** L100: *"43 utterances in 9 exchanges. **Grouped by place and
participants**, so a reply sits under what it answered."*

That sentence was true before `threads()` was changed to group by the
world's own `conversation_id`. It was not updated. The reader's only
account of how the exchanges were assembled was wrong — and it was **my
own change** that made it wrong.

**Fix.** `app/world/run_report.py:664` now describes what the function
does, and counts it: how many exchanges came from a real conversation id
and how many fell back to the heuristic.

---

## Issue 10 — 48% of spend bought turns that ended without an action

**Evidence.** By `stop_reason` in the trace store:

| stop_reason | calls | spend |
|---|--:|--:|
| `end_turn` | 387 | **$1.95** |
| `tool_use` | 296 | $1.97 |
| `max_tokens` | 10 | $0.14 |

And per resident (L271, L288): Ainsley Holloway — **12 turns, 0 tool
calls, $0.0352**. Tobias Holloway — **12 turns, 0 tool calls, $0.0411**.

Two residents thought for twelve turns each and touched the world zero
times. The report's headline says **"Dispatch success 100.0%"** (L70),
which is true of the calls that were made and silent about the turns that
made none.

The `max_tokens` row is its own small waste: 10 calls, $0.14, truncated
mid-output.

**FIXED.** The run table now carries two more rows beside dispatch
success: **Residents who acted** (how many touched the world at all, and
the percentage) and **Never touched the world**, naming them. Ainsley and
Tobias would have been named in run 19.

`app/world/run_report.py`: `acting_turn_rate`. Together with issue 4's
prompt fix and the response-token budget from issue 11, this is the third
lever on the same 48%.

---

## What I changed, in one table

| # | Issue | File and line | Change |
|---|---|---|---|
| 1 | No peer-to-peer trade | `app/world/tools/primitives/commerce.py:369,379` | `sell` and `trade` now name each other; `trade` states no shop or registration is needed |
| 3 | Unreachable workplaces | `app/world/run_genesis.py:216-224` | grocery → `Harvey Oak Supply Store`, construction → `carpentry`; new test walks the whole table |
| 4 | `[user]`/`[engine]` summariser | `engine/src/sim/resident_voice.py` (new), `engine/src/sim/pinned_boot.py:324` | Rewrites the frozen prompt into a resident's voice; adds "do not write down things that did not happen" |
| 9 | Report described the wrong method | `app/world/run_report.py:664` | Sentence now matches `threads()`, and counts real vs fallback threads |
| 11 | Engine suite would not collect | `engine/src/sim/config.py`, `engine/tests/test_sim_response_budget.py` | Implemented the missing `SIM_MAX_RESPONSE_TOKENS` (1536) and exported `MAX_TOKENS`; fixed the fixture's wrong field |

| 2 | Invented rooms, split shops | `movement.py` `canonical_destination`, `places.py` `known_places` | Distinctive-word and shortest-name folding; business premises listed as places |
| 5 | Authoring nudge unreachable | `state.py` `_repeated_work_pair` | A repeated PAIR of work actions fires the hint; the single-tool threshold is unchanged |
| 6 | Residents repeat themselves | `state.py` `awaiting_reply`, `retrieval_depth.py`, `run_genesis.py` | The asker is told which of its questions went unanswered |
| 7 | Multi-addressee remarks | `social.py` `everyone_addressed`, `bootstrap.py`, `state.py` | Every addressee recorded; both question ledgers read the list |
| 8 | Obstruction table too kind | `run_report.py` `untouched_tools` | New "What nobody reached for" section, exact, no language matching |
| 10 | Headline hid actionless turns | `run_report.py` `acting_turn_rate` | Run table names residents who never touched the world |

**All eleven are now fixed.** Nothing on this list is outstanding.

---

## Issue 11 — The engine's whole test suite had stopped collecting

Found while running the suite after the Deni merge, not in the report — but
it belongs here, because it is why several of the findings above went
unnoticed.

**Evidence.** `engine/tests/test_sim_response_budget.py` (added in
`c81e624`) imports `SIM_MAX_RESPONSE_TOKENS` from `src.sim.config`. That
name has never existed in that file — `git log -S` finds no commit that
ever added it. A collection error in one module aborts the whole run:

    ImportError: cannot import name 'SIM_MAX_RESPONSE_TOKENS'
    Interrupted: 1 error during collection

So **no engine test has run since that commit**. The same file's `profile()`
helper also passes `september_home=` to `SimProfile`, which takes
`home_root` — a second error behind the first.

**Why it matters beyond the red.** That test documents a change that never
landed, and run 19 is the evidence for it: the engine default of 4096
tokens per response is ~16,000 characters, and residents were writing
essays instead of acting. Run 19's numbers — 387 calls ending with no tool
call at $1.95, and 10 calls truncated at the ceiling — are the same
finding, one iteration later.

**Fix.**
* `engine/src/sim/config.py` — `SIM_MAX_RESPONSE_TOKENS = 1536` added with
  the measurement behind it, and `sim_env()` now exports `MAX_TOKENS`, so
  the budget reaches a live engine instead of being a constant nobody
  reads. Per model call, so a turn needing eleven tool calls still gets
  eleven calls to make them.
* `engine/tests/test_sim_response_budget.py` — fixture corrected to the
  real `SimProfile` shape.

Engine tests collect and pass again.
