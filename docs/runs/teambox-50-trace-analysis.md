# The 50-resident run — read line by line

*`teambox-50-100usd-2026-09-16`: 50 residents, 194 ticks (into game-day 1), 9,998
Bedrock calls, **$100.40**, 99.4% dispatch success. Written 2026-09-18 from the
report itself (56,623 lines) — the trace database for this run is not on this
machine, so every figure below is counted out of the report's own turn records and
conversation, and says which.*

---

## The headline

**The town talked like a town and traded like a broken one.** All 50 residents
spoke, 4,025 utterances, not one line repeated verbatim by the same resident. But
of the two verbs an economy is made of, **`buy` failed 140 times in 178 attempts
(79%) and `trade` 108 times in 168 (64%)**. Then the money ran out: 37 of 50
residents went dormant, the first at tick 95, and $100.40 over 50 residents is
$2.01 each — exactly the $2 wallet each of them started with.

| Measure | Count | Read from |
|---|--:|---|
| Utterances / exchanges | 4,025 / 57 | conversation section |
| Residents who spoke | **50 of 50** | ” |
| `say` calls vs productive acts | **3,935 vs ~450** | turn records |
| Skills written / tools invented / programs run | **3 / 0 / 0** | inventions |
| Skills ever walked | 1 of 3 | learning table |
| Knowledge taught to another resident | **1** (3 calls) | turn records |
| Residents dormant by the end | **37 of 50** | stakes |
| Energy bought | 6 purchases, $2.21 | ” |

---

## What the residents actually said

The conversation is the best evidence this project has produced. Three moments are
worth keeping.

**A gift with a condition attached.** The only knowledge transfer in the run —
Ivo Pemberton taught `Quick Harvest` to Jovana Ravensworth for nothing:

> *"Ivo's offering to teach me his 'Quick Harvest' procedure for free — just wants
> me to promise to use it to fill bellies, not just pockets. That's fair enough."*
> — Jovana Ravensworth, before calling `teach_skill`

Nobody wrote that condition into the world. There is no mechanism for it, no way
to enforce it, and it was offered and accepted anyway.

**Mutual aid at the edge of dormancy.** Tick 84, a shopkeeper running out of
thinking money:

> **Farid Ravensworth → Delphine Quintero:** *"I'm down to 270 energy, heading
> toward dormant. Can you give me one wheat from the shelf? I'll reimburse the
> till immediately — just need to stay conscious."*
> **Delphine Quintero:** *"Farid — take it, eat now. One wheat off the shelf, I'll
> adjust the books after. Can't have you going dormant with customers waiting."*

Dormancy became a social fact the residents managed for each other: 47 utterances
discuss running out of energy, the first at tick 56.

**A resident diagnosing the market failure this report is about.** Tick 61:

> **Bram Holloway → Giselle Ingram:** *"Ulrike's bleeding at forty-five, I'm posted
> at one-fifty, and neither of us is moving stock."*

He is right, and he says it 130 ticks before anybody reading the trace could.

And the complaint that names the central defect exactly:

> **Vikram Ingram → Farid Ravensworth:** *"You've posted prices for potatoes and
> wheat, but when I try to buy them, the system says you have none. Do you actually
> have stock available, or are those just placeholder prices?"*

---

## The patterns, with their evidence

### 1. Commerce fails on a posting rule nobody can see

| Verb | Attempts | Failures | The dominant reason |
|---|--:|--:|---|
| `buy` | 178 | **140 (79%)** | "*X has not posted a price for potato*" — 87 of them |
| `trade` | 168 | **108 (64%)** | "*you hold 0 potato, not 1*" — offering goods they do not have |
| `sell` | ~40 failures | — | "*X has not posted what they pay for timber*" — 32 of them |
| `eat` | 269 | 31 (12%) | "*you hold 0 wheat, not 1*" |

`buy` needs the counterpart to have posted a **price for that item**; `sell` needs
them to have posted a **bid**. Neither is visible from where the buyer stands, so
residents ask for goods nobody has quoted. The world's error helpfully says "'trade'
is how two people agree a price" — and `trade` then fails 64% of the time because
the resident offers something it does not hold, usually the very thing it just
failed to buy. **Every one of those 248 failures is a paid model call.**

### 2. Place gates are stricter than the words residents use

`harvest` failed 9 times, 6 of them "*requires location 'farmland:wheat', actor is
at 'business:b_grocery'*", and once each for `'farmland'` and `'location:farmland'`
— a resident standing in the farmland, refused for not standing in the right part
of it.

### 3. Politeness costs money

- `pay_tax` refused **18 times**: "*you owe no head tax*" — residents trying to pay
  tax they did not owe.
- `drop_intention` refused **36 times** (20% of attempts): "*you have not set out to
  do anything*".

Neither is a mistake worth refusing. Both should succeed quietly.

### 4. The town talks well and works little

3,935 `say` against roughly 450 productive acts (299 harvests, 58 fells, 47 crafts,
46 mines). 1,093 `look_around`, 775 `move`, 700 `rest`. 80% of all model calls
(8,048 of 9,998) were labelled idle — turns that produced no work.

But the talk is not slop: **zero lines were repeated verbatim** by the same
resident, all 50 residents spoke, and the median resident spoke 83 times. The
subjects are real ones — 519 utterances about credit and promises, 318 about the
election and the empty seats, 135 offering or asking for work.

### 5. A labour market that exists only in speech

135 utterances offer or seek work — *"I'll haul, I'll dig, I'll sort whatever needs
sorting"* — and **no wage was ever set in the world**. A child offered to sweep the
shop four times and was never answered.

### 6. Authoring: three skills, one of them real

- `Quick Harvest` (Ivo Pemberton) — **one step**, wrapping `harvest`. Walked 32
  times, which is why it is also the one skill that earned proficiency: a one-step
  skill completes on every ordinary act.
- `efficient_harvest` (Rosalind Quarshie) — 4 steps, never walked.
- `Fell Timber Efficiently` (Corwin Thorne) — 4 steps, three of them consecutive
  `fell_timber` calls, so any other act breaks the walk. Never walked.

`skillify` was called 3 times and worked 3 times. `toolify`, `do_in_order` and
`revise_skill`: zero. Residents consulted `describe_primitives` 90 times and filed
3 procedures.

### 7. The money ran out on schedule

A resident starts with $2.00 and burns about $2.89 per game-day, so a wallet lasts
about 100 ticks. The first dormancy is at tick 95. `buy_energy` converts sim to
thinking at 5 sim per energy unit, and one game-day costs roughly 14,440 sim while
wheat sold for 12–25 — so the exit exists and nobody could afford it. Six purchases,
$2.21 in total, all run. (Full arithmetic: `docs/decisions/WHAT-A-RUN-COSTS.md`.)

---

## Defects the run exposed, and what became of each

| # | Defect | Evidence in the report | Status |
|---|---|---|---|
| T50-1 | Report says "The world reported no roster" for 50 residents | *Every resident* section | **Fixed 2026-09-18** — the run now reads itself before stopping (`capture_final_reading`) |
| T50-2 | Refusals "not recorded" — the section that would have shown patterns 1–3 | *What the town could not do* | **Fixed** — captured in the final reading |
| T50-3 | "Authoring tools MISSING skillify, toolify" in a run where `skillify` worked 3 times | *The instrument* | **Fixed** — an unasked question now reads "not recorded", not as a failed check |
| T50-4 | "Quick Harvest … held by 1" while its own learning table shows two holders | *What the town invented* | **Fixed** — holders are read from the trace, not assumed to be the author |
| T50-5 | "Nothing anybody invented reached anybody else" over a recorded lesson | *Diffusion* | **Fixed** — diffusion now counts `agent.skill.taught` |
| T50-6 | 37 dormancies, `Cause` = `None` on every row, and no table heading | *Stakes* | **Fixed** — three named causes recorded at all three call sites |
| T50-7 | Learning and memory-flush calls refused with `InsufficientFundsError` (58) | failures list | **Open** — housekeeping is billed to the resident's own empty wallet |
| T50-8 | `buy`/`trade`/`sell` posting rule invisible to the buyer | 248 failed calls | **Open** — the biggest single waste in the run |
| T50-9 | Engine homes left in `/tmp` (1,530 MB) | *The instrument* | Fixed earlier (9c8b395) if the teambox build predates it |
| T50-10 | The `<system-reminder>` plan nudge reaching learning signals | 49 occurrences | **Open** — stripped on the wire, not where signals are stored |

---

## What the next run should answer

1. **Does commerce work when the posting rule is visible?** Show, in the
   perception, what is purchasable here and at what price. Target: `buy` failure
   rate under 20%, from 79%.
2. **Does anybody author when repetition is expensive?** `toolify` has never been
   used in any run.
3. **Do residents stay awake?** Fund wallets for the planned length, or the run
   measures dormancy rather than behaviour.
4. **Does a taught skill spread further than one pair of hands?** T5 asks for three.

*Related: `docs/runs/run-29-report.md`, `docs/decisions/NO-PROMPT-CACHE-COST.md`,
`docs/decisions/WHAT-A-RUN-COSTS.md`.*
