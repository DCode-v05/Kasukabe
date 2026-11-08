# Dynamic Chat — Consolidated Report across `final1`–`final6`

**Subject.** The *dynamic group conversation* capability of Kasukabe — the
feature specified in [`docs/specs/group-conversation-spec.md`](../../specs/group-conversation-spec.md)
("Spec — dynamic group conversation"), where a conversation is a first-class
record that residents can convene, join, address into, and close. This report
consolidates the six "final" runs held under
`docs/runs/series/{final1..final6}/` and reads them specifically for what they
show about live, multi-party chat between the ten residents.

**Sources analysed.** For each run: `gates.txt` (the authoritative grade,
produced by `app/loop_report.py` off the completed trace store), `runner.log`
(world lifecycle), `broker.log` (Bedrock gateway), and `sessions/n01..n10.jsonl`
(one line per agent *wake/turn*: its perception `input`, inner `output`, and the
`actions` — tool calls — it actually took).

**A note on authority.** Two kinds of number appear below and are kept apart on
purpose:

- **Official gate numbers** come straight from each run's `gates.txt`. These are
  the graded results and are treated as authoritative.
- **Session-log reconstructions** are computed here directly from the
  `sessions/*.jsonl` `world:say` / `world:start_conversation` actions. They are
  labelled *(reconstructed)* everywhere they appear and use looser, explicitly
  stated definitions. Where a reconstruction differs from a gate, the gate wins;
  the difference itself is often the interesting finding.

---

## 1. Executive summary

1. **Chat happens, and it is good.** Every run that produced data holds real
   multi-party conversation — 3–6 distinct speakers in one place, naturalistic,
   with genuine social and economic stakes (a hidden 400-sim household debt, a
   moved boundary marker, grain and timber trade, a forestry apprenticeship).
   The group gate **G3 passes in every graded run.** The residents are alive at
   the end in every run: **G5 = 10/10, everywhere.**

2. **The gate that fails is the same one every time: G1, reply rate.** No run
   clears the 60% bar (best is `final5` at 52%). This is not a plumbing failure —
   remarks are delivered (near-zero "nobody heard" among directed remarks). It is
   a *follow-through* failure: residents keep talking in a thread but frequently
   do not answer the specific question put to them. This matches
   [`SERIES.md`](../SERIES.md) §4's finding that G1 has stopped measuring delivery
   and now measures whether residents *choose* to answer.

3. **The dynamic-convening tool works but is barely used, and inconsistently
   honoured.** `world:start_conversation` — the deliberate "convene a named group
   chat" verb that is the whole point of the spec — is called **0–2 times per
   run** (5 times total across all six runs). When residents *do* convene, the
   town sometimes pours into the named conversation (a success:
   `final2`, `final5`-forest) and sometimes ignores it and keeps talking in the
   room's *implicit* conversation, orphaning the convened one at 1 remark
   (`final4`). Convening is an available power the residents mostly don't reach
   for, and the model does not yet reliably route speech into a conversation once
   convened.

4. **Two of the six runs are not usable results.**
   - **`final6` FAILED** — no `sessions/` directory, an empty `broker.log`, no
     `gates.txt`. It is not gradeable and contributes no chat data.
   - **`final5` is DISPUTED** — its own directory contains a `VOID.txt`
     ("recorded 0 calls … not a result", citing Build Spec v2 §10.1) that
     contradicts its `gates.txt` (299 calls, fully graded). It also ran through a
     severe broker retry storm (180 throttle + 510 error log lines, vs ≤7
     throttles and 0 errors in every other run). Per the project's own
     discipline it should be excluded from the headline; its session content is
     nonetheless real (75 turns, 26 utterances) and is used qualitatively.

   That leaves **four clean graded runs — `final1`, `final2`, `final3`,
   `final4`** — as the load-bearing evidence.

---

## 2. The gates, side by side

Bars (from [`SERIES.md`](../SERIES.md) §1): **G1** reply rate to directed remarks
≥ 60%; **G2** exchanges reaching 4+ alternating turns ≥ 3; **G3** ≥ 3 distinct
speakers in one place/window, at least once; **G4** a price agreed in speech then
moved by a tool call ≥ 1; **G5** agents alive at stop 10/10; **G6** cognition
turns producing a world effect ≥ 50%.

All figures below are copied from each run's `gates.txt`.

| run | calls | ok | spend | utts | **G1** reply | **G2** deep | **G3** group | **G4** settle | **G5** alive | **G6** effect | verdict |
|---|--:|--:|--:|--:|:--|:--|:--|:--|:-:|:--|:--|
| **final1** | 414 | 407 | $2.53 | 61 | 16/50 = **32%** ✗ | **4** ✓ | 4 ✓ | 1 ✓ | 10/10 ✓ | 305/359 = **85%** ✓ | graded ✓ |
| **final2** | 457 | 450 | $2.53 | 39 | 13/30 = **43%** ✗ | **2** ✗ | 2 ✓ | 2 ✓ | 10/10 ✓ | 201/329 = **61%** ✓ | graded ✓ |
| **final3** | 424 | 424 | $2.37 | 54 | 9/28 = **32%** ✗ | **2** ✗ | 3 ✓ | 2 ✓ | 10/10 ✓ | 282/355 = **79%** ✓ | graded ✓ |
| **final4** | 239 | 239 | $1.09 | 34 | 11/30 = **37%** ✗ | **3** ✓ | 3 ✓ | **0** ✗ | 10/10 ✓ | 122/182 = **67%** ✓ | graded ✓ |
| **final5** | 299 | 299 | $1.48 | 31 | 13/25 = **52%** ✗ | **3** ✓ | 3 ✓ | **0** ✗ | 10/10 ✓ | 144/224 = **64%** ✓ | **DISPUTED** (VOID) |
| **final6** | — | — | — | — | — | — | — | — | — | — | **FAILED** (no data) |

**Reading the table.**

- **G1 (reply rate) — fails in all six.** 32%, 43%, 32%, 37%, 52%. This is the
  single consistent failure of the whole dynamic-chat effort. Even the best run
  answers barely half of directed remarks; the bar is 60%.
- **G3 (group) and G5 (alive) — pass everywhere.** The town reliably forms 3+
  speaker gatherings and nobody dies. The *structure* of group chat is solved.
- **G2 (depth) — split 3 pass / 2 fail.** `final1`, `final4`, `final5` reach ≥3
  deep exchanges; `final2` and `final3` reach only 2. Depth is fragile and run-to-run
  variable.
- **G4 (settlement) — split 3 pass / 2 fail.** `final1`–`final3` each land at
  least one spoken price that a tool call then honours; `final4` and `final5`
  land none. A conversation that agrees a price but never executes the transfer
  is a chat that didn't finish its business.
- **G6 (world effect) — passes everywhere,** 61–85%, comfortably over the 50%
  bar. Talk is not idle: turns move the world.

> **Comparability caveat.** These runs did not all run equally long or under
> equal conditions. `final1`/`final2` ran to the spend cap ($2.53 each, ticks 50
> and 71). `final4` spent only $1.09 and its `runner.log` shows a ~3-hour stall
> (05:22→08:20) before a `ReadError`. `final5` ran on a throttled broker. G1
> denominators differ (25–50 directed remarks), so small absolute swings move the
> percentage a lot. Treat the *pattern* (G1 always short, G3/G5 always clear) as
> the signal, not the exact ordering.

---

## 3. Run-by-run

### 3.1 `final1` — the strongest clean run

- **Completed** to tick 50, stopped cleanly by the spend cap ($2.53 vs a $2.50
  cap; `runner.log`: "the broker has halted dispatch … the run is stopping").
  Final checkpoint written at tick 50.
- **Best-in-set on depth and effect:** G2 = 4 (only run to clear it comfortably),
  G6 = 85% (highest), G3 = 4 groups. **G4 = 1** settlement: *"Ainsley Holloway
  said 400, paid at tick 40."*
- **Weakest on G1:** 32% — 16 of 50 directed remarks answered.
- Largest gathering: **5 speakers at `home:h_quarry`, ticks 37–46** — the whole
  Holloway household converging on Bram's copper and the debt.
- Convening (`world:start_conversation`): **never called.** All conversation
  formed implicitly from the room-and-window mechanism.
- *(Reconstructed)* 61 `world:say`; the richest thread was an un-convened 23-remark,
  5-speaker exchange at `home:h_quarry`.

### 3.2 `final2` — most talk-time, most "thinking without acting"

- **Completed** longest, to tick 71, again stopped by the spend cap.
- **Convening actually worked here.** Marta Thorne called
  `world:start_conversation` twice (topics "Morning grain and the day's bread"
  and "Bread baked and hungry bellies"). The first became the run's spine: an
  **18-remark, 5-speaker** thread (see §5.1) that grew its roster automatically as
  Idris, Ainsley, Corwin and Nell spoke into the room.
- **But G2 still failed (2)** and G1 was 43%. Long conversation ≠ deep
  *alternation*; much of it is parallel monologue.
- **G4 = 2** settlements. G6 = 61% (lowest of the passing runs).
- *(Reconstructed) engagement texture:* of 124 agent wakes, **94 (76%) produced no
  tool call at all** — the agent thought and wrote inner monologue but touched
  nothing. (This is a per-*wake* count and is not in tension with G6's 61%, which
  is computed over a finer per-cognition-call unit of 329; a single acting wake
  contains many effecting calls.) The high idle-wake rate partly reflects the
  longer run including quiet stretches.

### 3.3 `final3` — good breadth, shallow and unanswered

- **Ended on a fault:** `runner.log` tail shows repeated `n10` timeouts then
  `tick dispatch failed for n10: ReadError`. All 424 recorded calls succeeded, so
  it is graded, but it did not stop cleanly.
- **G1 tied-lowest at 32%** (9/28) and **G2 failed (2)** — the shallowest,
  least-answered clean run despite 54 utterances and 6-speaker gatherings.
- **G3 = 3, G4 = 2, G6 = 79%** all healthy. Settlement: *"Ainsley said 50, paid at
  tick 38."*
- Convening: **never called.**
- Notable *(reconstructed)*: a 20-remark, **6-speaker** breakfast thread at
  `home:h_field` — wide but shallow, exactly the profile that clears G3 and fails
  G1/G2.

### 3.4 `final4` — convening orphaned, and no deal closed

- **Stalled then failed:** normal ticking until 05:22, then a ~3-hour gap, then
  `ReadError` on n01/n03/n07/n10. Cheapest run ($1.09), fewest calls (239).
- **The instructive convening case.** Perrin Holloway convened a **5-person**
  conversation (`fd8dda6a`, topic *"the household accounts and the hidden 400
  sim"*) — and it **died at a single remark.** The real conversation the family
  then held (16 remarks, 5 speakers) ran under a *different, implicit*
  conversation id (`774ba02d`). The town talked *around* the table Perrin set
  rather than *at* it (see §5.2). This is precisely the fragmentation the spec set
  out to abolish.
- **G4 = 0** — a price was discussed (Ainsley's 400 sim for Bram's debt) but the
  transfer was never executed in-run; Marta explicitly refused to settle it behind
  Bram's back. Dramatically satisfying, gate-failing.
- G1 = 37%, but **G2 = 3 (pass)** and G3 = 3 — decent depth and breadth.

### 3.5 `final5` — DISPUTED / VOID (do not count in the headline)

- **Contradiction inside its own directory:**
  - `VOID.txt`: *"final5 recorded 0 calls. A run that did not happen is not a
    result (Build Spec v2 §10.1) and is not graded."*
  - `gates.txt`: 299 calls, fully graded, **G1 = 52% (the best G1 of the set)**,
    G2 = 3, G3 = 3, G4 = 0, G5 = 10/10, G6 = 64%.

  These cannot both describe the same trace store. Following the project's stated
  discipline (§10.1, cited by the VOID itself), **final5 is excluded from the
  headline result.**
- **Broker retry storm.** Every run's `broker.log` opens with the same banner —
  *"4 of 6 lanes have no inference profile … Aggregate throughput is 200 RPM, not
  600."* — so the reduced throughput was a **constant series condition, not
  final5's fault.** What is unique to final5 is what happened under it: its log is
  **334 KB / 4,195 lines** with **180 throttle/retry** and **510 error/exception**
  lines, versus ~27 KB / ~430 lines and **≤7 throttles / 0 errors** in every other
  run (final3 and final4: zero throttles). Whatever final5 measures, it measured it
  through heavy retrying.
- **Its chat is nonetheless real and worth reading:** 75 wakes, 26 utterances, and
  the single **deepest** thread in the whole set — an 11-remark, **6-speaker**
  breakfast at `home:h_field` (§5.3). Two convenings (Liora→Perrin; Tobias→forest).

### 3.6 `final6` — FAILED, no data

- **Not a run for chat purposes.** No `sessions/` directory, `broker.log` is 0
  bytes, no `gates.txt`. `runner.log` shows preflight passing 13/14 (the one
  warning: the $2.50 cap only covers ~0.2 h), engines coming up, ticks dispatching
  (some `500`s and an `n09` timeout) — and then nothing gradeable was persisted.
- **Contributes zero conversation data** and is reported here only so the set is
  accounted for in full.

---

## 4. Cross-run synthesis — what the six runs say about dynamic chat

### 4.1 The structure of group chat is solved; the *turn-taking* is not

Across the four clean runs the pattern is stable and unambiguous:

- **G3 (group formation): 4 / 4 pass.** Getting 3–6 residents into one place,
  co-present in one conversation object, is reliable.
- **G5 (survival): 4 / 4 pass, 10/10 every time.** Chat never costs a life.
- **G1 (reply rate): 0 / 4 pass.** Getting the person who was *asked* to *answer*
  is the unsolved problem.
- **G2 (depth): 2 / 4 pass** and **G4 (settlement): 3 / 4 pass** sit in between —
  achievable, not dependable.

The town has learned to *gather and speak*. It has not learned to *take turns and
close loops*.

### 4.2 Why G1 fails — measured, not assumed

Delivery is not the bottleneck. *(Reconstructed)* across the clean runs, of all
directed remarks (those carrying an explicit `listener_agent_id`), **essentially
none went unheard** — the "Nobody was here to hear that" note appears on undirected
room-speech, not on directed remarks. This confirms [`SERIES.md`](../SERIES.md)
§3.7 (the vocative-address fix) and §4 (16 of 17 unanswered remarks reached the
listener's own prompt): the plumbing is done.

The gap is between *speaking again* and *answering*. Two definitions of "reply",
same runs:

| run | official **G1** (answered the question) | *(reconstructed)* addressee spoke again in-thread | gap |
|---|:--|:--|:--|
| final1 | 32% (16/50) | ~74% (14/19 directed) | wide |
| final2 | 43% (13/30) | ~68% (17/25) | wide |
| final3 | 32% (9/28) | ~53% (8/15) | moderate |
| final4 | 37% (11/30) | ~71% (12/17) | wide |

*(The two columns use different denominators and definitions — the reconstruction
counts "did the addressee produce any later remark in the same thread/place",
which is deliberately generous. It is shown only to locate the gap.)*

The gap **is** the finding: residents remain present and keep talking, but the
next thing they say is frequently their *own* agenda rather than an answer to the
question they were just asked. The transcripts in §5 show this directly — Marta is
asked three ways whether there's grain; she answers by issuing orders and
reassurances but not the yes/no. This is a *conversational-cooperation* problem,
not a delivery problem, and — as §4 of SERIES.md argues — any mechanism that
*forced* the answer would make G1 measure the harness instead of the town.

### 4.3 The dynamic-convening tool is under-exercised and under-honoured

This is the report's central finding for the *dynamic* part of "dynamic chat".
`world:start_conversation` is the deliberate act the spec exists to enable. Its
usage across all six runs:

| run | `start_conversation` calls | outcome |
|---|:-:|:--|
| final1 | 0 | conversations formed implicitly only |
| final2 | 2 (both Marta) | **honoured** — one became the 18-remark spine |
| final3 | 0 | implicit only |
| final4 | 1 (Perrin) | **orphaned** — died at 1 remark; town talked in a parallel implicit thread |
| final5 | 2 (Liora, Tobias) | mixed — Tobias's forest chat honoured (5 remarks); Liora's died at 1 |
| final6 | — | no data |

Two problems, both visible:

1. **Residents rarely convene.** Five deliberate convenings across ~176 utterances
   of clean-run speech. The default is still to speak into the room and hope the
   window catches an answer — the exact pre-spec behaviour §1 of the spec
   describes. The capability exists; the *disposition* to use it has not been
   instilled.
2. **A convened conversation is not reliably where the talk goes.** When Perrin
   convened the family (`final4`), the subsequent remarks attached to the room's
   implicit conversation, not to the one he named — so his convened thread shows
   1 remark and the "real" one shows 16. The spec's objective #3 ("A resident can
   speak into a *named* conversation, so two conversations in one room stay two")
   is implemented at the tool level but not *reached for* by the speakers: they
   omit the `conversation_id` and fall back to room-default routing.

Where convening *is* honoured (final2's grain conversation, final5's forestry
apprenticeship), it produces the best chat in the set — deep, on-topic, multi-party.
The mechanism is good. The behaviour around it is the gap.

### 4.4 Participation breadth — good, with one recurring absentee

*(Reconstructed)* `world:say` counts per resident:

| run | speakers active | never spoke |
|---|:--|:--|
| final1 | 9 / 10 (Ainsley 22, Corwin 12, Marta 7 …) | **Idris** |
| final2 | 10 / 10 (Ainsley 10, Marta 9 …) | — |
| final3 | 9 / 10 (Liora 14, Ainsley 8 …) | **Bram** |
| final4 | 9 / 10 (Corwin 10, Marta 5 …) | **Bram** |
| final5 | 9 / 10 (Marta 5, Corwin 4 …) | **Bram** |

Breadth is healthy — usually 9 or 10 of 10 residents speak. But **Bram Holloway
(n01), the miner, is silent in three of the four clean-and-disputed runs.** He is
frequently the *subject* of conversation (his debt, his copper, his empty seam) and
the *addressee* of directed remarks, yet contributes no speech himself. He is the
clearest single drag on G1: questions aimed at Bram cannot be answered by a
resident who never speaks. Ainsley and Marta, by contrast, are the reliable social
hubs across every run.

### 4.5 Talk is not idle (G6), but many wakes are silent

G6 passes everywhere (61–85%): when the finer per-call unit is counted, most
cognition produces a world effect. Yet *(reconstructed)* at the coarser
per-*wake* level, a large share of turns end with `actions: []` — the agent
reflected and wrote inner monologue but did nothing observable (final2: 76% of
wakes; final1: 48%; final3: 57%; final4: 53%; final5: 59%). Both are true because
they count different units. For dynamic chat the practical reading is: **a
resident often "hears" a remark, thinks about it on that wake, and acts only on a
later wake — introducing multi-tick latency into every exchange** and making tight
back-and-forth (G2) hard to sustain before the run ends or the room empties.

---

## 5. Evidence — verbatim transcripts

These are lifted directly from the `sessions/*.jsonl` `world:say` actions (content
truncated to ~200 chars). Tick numbers are game-ticks; `→Name` marks a directed
remark, `(room)` an undirected one.

### 5.1 `final2` — convening honoured: Marta's grain conversation (`10723c00`, 18 remarks, 5 speakers)

> **[CONVENE t37]** Marta Thorne opened topic *"Morning grain and the day's bread"*, roster [Marta, Wren]

```
t37 Marta →Wren:   "Wren, I've loaves to set and no grain milled yet. Have you brought wheat up from the field…?"
t38 Idris (room):  "Fire needs wood same as bread needs grain."
t38 Ainsley (room):"Good morning to you all. I smelled the bread before I crossed the threshold. Now then—Wren…"
t39 Marta →Wren:   "Wren, take Idris and Corwin and bring up the east strip… The wheat's ready—cut it and bring it up."
t40 Ainsley (room):"Marta. The household's hungry and I've got empty hands. Your rye—what's the price today?"
t42 Nell →Marta:   "Marta. I've got coin now—just sold furniture. Need bread… Name your price or I go to the market."
t44 Ainsley →Marta:"…The paper between us weighs on Bram's pride—I've set aside 400 sim for it, and he doesn't know…"
```

The roster grew from 2 to 5 by automatic join (spec objective #4) — Idris,
Ainsley, Corwin, Nell all pulled in by speaking into the room. This is the
capability working as designed.

### 5.2 `final4` — convening orphaned: Perrin sets a table nobody sits at

> **[CONVENE t37]** Perrin Holloway opened topic *"the household accounts and the hidden 400 sim"*, roster [Perrin, Ainsley, Tobias, Nell, Marta] — conversation id `fd8dda6a`

The convened thread `fd8dda6a` received **exactly one remark**:

```
t37 Perrin →Ainsley: "I've been carrying this as if it were a secret I discovered… it's just the account. It's just what we owe…"
```

Meanwhile the family's actual 16-remark conversation ran under the *implicit* id
`774ba02d`:

```
t37 Marta →Ainsley:  "Ainsley. You look well. Is Bram at the mines today?"
t38 Nell (room):     "…Perrin, that marker between our strips… it's going to stay moved until someone says it moved."
t38 Ainsley →Perrin: "Perrin. Come here. You and I need to talk about the weight of what you carry…"
t41 Ainsley (room):  "…400 sim. That's the number." 
t46 Marta (room):    "Ainsley. I'll not take what Bram doesn't know he's giving… I'll not have it settled behind his back."
```

The town held the conversation Perrin wanted — but *not in the object he
created*. That split is why G4 read 0 here: the price was named and even morally
debated, and no tool call ever moved it.

### 5.3 `final5` (disputed) — the deepest chat in the set (`013743c9`, 11 remarks, 6 speakers)

```
t39 Corwin (room):   "Wren, you got any of last season's grain still in the bin? … Idris - you planning to head up to the forest…?"
t40 Marta (room):    "Corwin, sit down… Wren—what's in the bin from last season? Enough for porridge…?"
t41 Corwin →Marta:   "Thank you, Marta. … Idris and I need to sort the south slope before the heat comes on. But yes. Sit me down."
t41 Idris (room):    "Corwin. The south slope can wait. If you've not eaten, eat."
t43 Wren (room):     "Marta. The bin's dry. I moved it all at harvest - Ainsley took the lot. I should have said."
t44 Marta →Liora:    "Liora, love. You take what you need—you're growing, I'm not…"
t58 Wren (room):     "Marta. The bin's empty. Sold the lot at harvest. You've been walking yourself weary while I stood silent."
```

Six speakers, real turn-taking, and an actual arc (the empty grain bin is
discovered and confessed). This is the ceiling of what the dynamic-chat system
produces — and it happened in the run that is formally void.

### 5.4 `final4` / `final5` — a convened chat that *did* work: the forestry apprenticeship (`9f36dfe4`, final5)

> **[CONVENE t45]** Tobias Holloway opened topic *"Forestry work and learning the trade"*, roster [Tobias, Idris, Corwin]

```
t45 Tobias (room):  "…I'm tired of work that ends when the day ends. Corwin…"
t46 Corwin (room):  "Tobias—you asked first. I know you asked first. Idris, you told me to leave it…"
t49 Idris (room):   "Tomorrow. First light. Both of you."
t53 Tobias (room):  "Tomorrow. I heard him. And I heard you—thank you."
t58 Corwin (room):  "Tomorrow. I'm grateful you heard me…"
```

A clean, closed, on-topic exchange — Tobias convenes, the two foresters resolve
who trains him, it ends in agreement. When residents both convene *and* speak into
the convened thread, the system delivers exactly the spec's promise.

---

## 6. Against the spec's objectives

Scoring [`group-conversation-spec.md`](../../specs/group-conversation-spec.md) §2
against what the runs actually exercised:

| # | Spec objective | Verdict from the runs |
|---|---|---|
| 1 | Conversation is a first-class record (id, place, tick, topic, roster, state) | **Met.** `look_around` returns live conversation objects with id/topic/turns/participants; every remark carries a `conversation_id`. |
| 2 | A resident can convene deliberately, invitees on the roster before speaking | **Met, rarely used.** `world:start_conversation` works and seeds the roster (final4: 5 names before a word) — but called only 5× in 6 runs. |
| 3 | Speak into a *named* conversation so two in one room stay two | **Implemented, not adopted.** Speakers usually omit `conversation_id` and fall back to room-default, so a convened thread gets orphaned (final4). |
| 4 | Joining stays automatic (speak in the room → join) | **Met, and strong.** final2's roster grew 2→5 by automatic join. |
| 5 | Conversation closes on quiet, never reopens | Not directly measured here (runs end before long quiet windows); no reopen anomalies seen. |
| 6 | UI shows every conversation as a live group chat | Out of scope for these logs (no UI capture in `final*`). |
| 7 | Map visible in same UI | Out of scope for these logs. |
| 8 | Two runs reported separately (talk-shaped, act-shaped) | The series exists; these six are the "final" cohort. G3/G6 show both talking and acting occur. |

The **model layer is largely built** (objectives 1, 2, 4). The **gap is
behavioural** (objective 3): residents don't consistently *use* the named-conversation
routing, and they don't *answer* (G1). Objectives 6–7 (UI) aren't evidenced by
these particular artifacts.

---

## 7. Recommendations

Ordered by leverage on the failing gate (G1) and on the dynamic-chat purpose.

1. **Make answering the asked question the salient next action.** G1 is the whole
   ballgame and it is behavioural. Without coercing a reply (which §4 rightly
   warns would make G1 measure the prompt), surface the *specific* open question
   and its asker more prominently on the addressee's next wake, and reward closing
   it. Measure the effect on G1 only on a *completed* trace (§10.1).

2. **Get residents to route speech into the conversation they were invited to.**
   Objective 3 is implemented but bypassed. When a resident is on an open
   conversation's roster, default their `say` to that `conversation_id` (or prompt
   them that they are in a named conversation) so convened threads stop being
   orphaned — fixing the `final4` split at the source.

3. **Raise the convening rate.** Five deliberate convenings in six runs is too few
   to exercise the feature. Consider prompting residents who want a multi-party
   discussion toward `start_conversation`, and log convening rate as a first-class
   dynamic-chat metric alongside G1–G6.

4. **Give Bram (and other silent subjects) a reason to speak.** A resident who is
   the subject and addressee of many remarks but never speaks is a structural G1
   drag. Investigate whether n01's prompt/soul-seed suppresses speech, or whether
   his wakes consistently choose action over reply.

5. **Reduce exchange latency to protect G2/G4.** The multi-tick "hear now, act
   later" pattern (§4.5) plus dawn-to-cap run lengths leaves little room for tight
   back-and-forth or for a spoken price to be executed before the room empties.
   Either lengthen runs past the spend cap for chat-focused measurement, or tighten
   the wake→reply loop.

6. **Housekeeping on the artifacts themselves:**
   - **Resolve `final5`.** Decide whether it is void (delete/relabel the
     `gates.txt`) or valid (delete the `VOID.txt`) — do not ship a run whose two
     files disagree. Given the throttled broker, treating it as void is the
     defensible call.
   - **Re-run `final6`.** It produced no gradeable data; the "final" cohort is
     really four clean runs, not six.
   - **Fix the broker lane profiles** (4 of 6 missing) before any run intended as
     a result, so throughput is 600 RPM and logs aren't dominated by retries.

---

## 8. Bottom line

**Dynamic chat in Kasukabe is real and often beautiful, but it does not yet pass
its own bar for cooperation.** Of six "final" runs, one failed outright
(`final6`) and one is formally void (`final5`), leaving four clean results. In
those four, the town reliably *forms* multi-party conversations (G3, G5 pass every
time) and its talk moves the world (G6 61–85%), but it **never answers enough of
what it is asked (G1 32–43%, bar 60%)**, closes a spoken deal only sometimes
(G4 3/4), and sustains deep alternation only sometimes (G2 2/4). The
*dynamic-convening* capability that defines the feature is built and, when used,
produces the best conversations in the set — yet it is reached for only five times
across all six runs and, once used, is not reliably where the speech actually
lands. The unfinished work is not plumbing; it is disposition: making residents
convene the conversations they want, speak into them, and answer the people in
front of them.

---

*Generated from `docs/runs/series/{final1..final6}/` — `gates.txt`, `runner.log`,
`broker.log`, and `sessions/n01..n10.jsonl`. Official gate figures are quoted from
each run's `gates.txt` (produced by `app/loop_report.py`); figures labelled
"(reconstructed)" are computed directly from the session-log `world:say` and
`world:start_conversation` actions and use the looser definitions stated inline.*
