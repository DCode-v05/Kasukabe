# deni-50-agent — how fifty residents talked to each other

*The communication report. Its companion,
[`deni-50-agent-RESOURCES.md`](deni-50-agent-RESOURCES.md), covers what the
same run cost the machine. The generated per-turn record is
[`deni-50-agent-report.md`](deni-50-agent-report.md).*

**The run.** Fifty residents, `genesis_50_town.json`, opened at dawn,
stopped by its own $15 spend cap. Artifacts under
`docs/runs/series/fifty/`.

| | |
|---|---|
| Residents | **50** |
| Bedrock calls | **2,288** |
| Failures | **0** |
| Spend | $14.75 of a $15 cap |
| Ticks | 37 → 71 |
| Utterances | **259** (199 directed, 60 to the room) |
| Conversations | **29** |
| Residents who spoke | **50 of 50** |
| Residents alive at stop | **50 of 50** |

**Zero failed calls in 2,288.** Five times the load the conversation code
was built against, and neither wire-format defect that used to cost a turn
per run — the orphan `tool_result`, the context-window overflow — appeared
once.

---

## §1 The gates

| Gate | Value | Bar | |
|---|---|---|---|
| G1 reply rate | 92/199 = **46%** | ≥ 60% | ✗ |
| G2 exchanges 4+ turns | **15** | ≥ 3 | ✓ |
| G3 group, 3+ speakers | **15** | ≥ 1 | ✓ |
| G4 settlements | **4** | ≥ 1 | ✓ |
| G5 alive at stop | **50/50** | all | ✓ |
| G6 turns with world effect | 1004/1707 = **59%** | ≥ 50% | ✓ |

**Five of six.** The only failure is G1, which has failed in every run this
project has made. G2 and G3 are not marginal: 15 and 15, against bars of 3
and 1. At ten residents those were 2–4 and 1–4.

---

## §2 Against human behaviour

Bars from [`HUMAN_BASELINES.md`](../HUMAN_BASELINES.md); each cites
published research.

| | Value | Bar | | Source |
|---|---|---|---|---|
| B1 selected party answers | **46%** | ≥ 64% | ✗ | Stivers 2009, ten languages |
| B2 addressees all recorded | *not measurable* | 0 | — | SSJ 1974 |
| B3 median conversation size | **3** (largest 11) | ≤ 4 | ✓ | Dunbar |
| B4 over-large threads split | **1/7** | ≥ 50% | ✗ | Dunbar |
| B5 named price then moved | **1/18** | ≥ 1 | ✓ | — |
| B6 favours returned | 0/3 | reported | — | Gouldner |
| B7 debts named in speech | **21** | reported | — | Gouldner |

---

## §3 The conversation ledger — 29 threads

| id | speakers | remarks | where | ticks |
|---|--:|--:|---|---|
| `d787858e` | **11** | 22 | business:b_grocery | 38–49 |
| `fa0afbc5` | **10** | 29 | business:b_bakery | 37–71 |
| `c2a87b23` | **8** | 28 | carpentry | 37–56 |
| `1c335a23` | 7 | 22 | home:h_field | 37–62 |
| `a7c858f0` | 7 | 20 | Oak Hill College | 38–70 |
| `adf2ddb6` | 5 | 15 | home:h_quarry | 37–67 |
| `100edc41` | 5 | 12 | home:h21 | 37–62 |
| `3d32c1ac` | 4 | 10 | home:h22 | 37–39 |
| `64b31523` | 4 | 11 | home:h23 | 37–58 |
| `ebbf0c13` | 4 | 8 | Kasukabe Farmland | 38–57 |
| `f803eb4c` | 4 | 14 | Kasukabe Ore Outcrop | 38–41 |
| `b608c250` | 4 | 9 | Kasukabe Farmland | 39–45 |
| `55ce46b6` | 4 | 8 | Harvey Oak Supply Store | 39–60 |
| `36b1924b` | 3 | 15 | home:h24 | 37–59 |
| `83241572` | 3 | 8 | Kasukabe Government Complex | 42–53 |
| *(14 more with 1–2 speakers)* | | | | |

**Seven threads exceeded four speakers.** That is §6's finding.

---

## §4 Who spoke, and who was spoken to

`said` is remarks made; `heard` is remarks directed at them.

| Resident | said | heard | | Resident | said | heard |
|---|--:|--:|---|---|--:|--:|
| Bram Holloway | **16** | 5 | | Perrin Holloway | 6 | 1 |
| Marta Thorne | 14 | **15** | | Milo Quarshie | 6 | 5 |
| Mireille Jarrah | 13 | 5 | | Camille Quarshie | 6 | 5 |
| Greta Quarshie | 13 | 7 | | Hester Vance | 5 | 6 |
| Tova Okonjo | 11 | 4 | | Vikram Ingram | 5 | 4 |
| Rosalind Vance | 10 | 4 | | | | |
| Corwin Thorne | 9 | 3 | | | | |
| Ainsley Holloway | 8 | **8** | | | | |

**All fifty spoke.** That is new: earlier ten-resident runs consistently had
one silent resident, and Bram Holloway was the recurring absentee across
`final3`, `final4` and `final5` — *"frequently the subject of conversation
and the addressee of directed remarks, yet contributes no speech himself…
the clearest single drag on G1."*

**Bram is the most talkative resident in this run.** See §5.

Marta Thorne is the town's hub — most spoken-to (15) and second-most
speaking (14), running the bakery counter that §5.2 records.

---

## §5 Evidence — the transcripts

### 5.1 The 400 sim, finally said out loud

The strongest single thing in the run, and it closes a thread this project
has been watching for a dozen runs. Bram Holloway has owed Marta Thorne
400 sim since an empty copper assay. In every previous run neither said
the word "repayment": in `final4` Ainsley set 400 aside and Bram did not
know, and Marta refused to settle it behind his back — *"I'll not take
what Bram doesn't know he's giving."* G4 read 0 there **because the
conversation could not close.**

Here, at the bakery, `fa0afbc5`:

```
t41  Bram → Marta:  "Marta. Five weeks since the assay came up empty. You lent
                     me 400 sim when I had nothing. I'm here to repay, in full,
                     in one piece. No more silence about it."

t41  Bram → room:   "Ainsley. I hadn't told you the numbers. Five weeks thin on
                     copper, one empty assay, 400 sim borrowed. It's paid now.
                     That's the story."

t54  Marta → room:  "Bram, I've heard you. Five weeks holding quiet, not wanting
                     shame between neighbours. You speak plain, I answer same —
                     the 400 sim you borrowed, I'll take back with thanks, not
                     ceremony."
```

And in the ledger, at t39: **a 400-sim `sale` recorded against `b_yard`**,
the Holloway–Thorne yard. The debt was named, acknowledged, and moved.

This is the reciprocity literature acted out — a delayed obligation, felt
as debt, complicated by face, and finally discharged. `HUMAN_BASELINES.md`
§5 says of this material: *"Nothing needs adding here. It needs
measuring."* B7 measures it: **21 remarks naming an obligation.**

### 5.2 A real market forms — the bakery, 10 speakers, 29 remarks

`fa0afbc5`, ticks 37–71. Marta runs a counter and quotes a price book that
stays consistent across thirty ticks:

```
t48  Marta → Ainsley: "Ainsley, I've four day-olds from yesterday. Twelve sim for
                       the lot — two less than fair, for the years between us."
t51  Marta → Farid:   "Farid, retail at thirty sim per fresh loaf. Day-olds to
                       regulars at twenty — Ainsley here knows that price."
t59  Marta → Farid:   "Farid, twenty-four by the dozen — four eighty for twenty
                       today, or four sixty-eight for two dozen tomorrow."
t62  Marta → Arjun:   "Arjun Holloway, you're third in line this morning. Sit a
                       moment, let me sort these two first."
```

Retail 30, regulars 20, wholesale 24 by the dozen, a volume discount, and a
queue managed by name. Farid pushes back on exactly the right point —
*"I need the wholesale number before I can name volume. Retail at thirty
tells me what I'll sell for — what's my cost when I buy by the dozen?"* —
which is a genuine distributor's question.

That is price anchoring and concession structure (`HUMAN_BASELINES.md` §4)
appearing without anyone being told to bargain.

### 5.3 The eleven-speaker grocery — and why it is not one conversation

`d787858e`, ticks 38–49, at `business:b_grocery`:

```
t38  Kem      → Junia:    "Friend, I'm wondering what's been sitting too long…"
t38  Hendrik  → Junia:    "Junia, what do you have for sale today, and at what
                           prices? I'm Hendrik Whitlock — I teach at the college"
t38  Tova     → Kem:      "Kem. Need to borrow for breakfast — I'm empty…"
t38  Rosalind → Junia:    "Junia. You have grain here, or flour? And Kem Okonjo —
                           selling below my rate in my own daughter's shop now?"
t38  Tova     → Hendrik:  "Junia. What do you have for breakfast, and how much?"
t39  Kem      → Tova:     "Here, take five. Get something proper, not just crumbs"
t39  Kem      → room:     "Rosalind. I'm here to ask, not to sell. Different thing
                           entirely… Business turns, or it dies."
t41  Greta    → Ainsley:  "Ainsley. I've been standing at the counter since
                           morning, and not a single customer. Are we closed?"
t42  Ainsley  → Junia:    "Junia. I'm still waiting on an answer — bread, whatever's
                           fresh, and what you're asking for it."
t43  Junia    → Ainsley:  "Ainsley — no bread, Marta has that. Carrots fresh this
                           morning, twelve for three… Hendrik — fifteen for one,
                           last in stock. Tova — same fifteen…"
```

Read as a person would, that is **four conversations at one counter**: a
haggle (Kem/Junia), a family loan (Tova/Kem), a price dispute
(Rosalind/Kem), and a newcomer asking after stock (Hendrik). The world
records them as one thread.

**Tova's fifth line is the tell.** It is addressed to Hendrik and opens
with "Junia" — which is precisely what talking across a crowded room looks
like, and precisely why nobody can answer cleanly.

Junia's t43 reply is the shape a person adopts under that load: **one
remark answering three people by name at once.** She is doing the work the
conversation structure should be doing.

### 5.4 A convening that worked

Only two `start_conversation` calls in the whole run, both successful:

```
n03  topic "Looking for day work"                      with n26
n22  topic "Household provisions and morning plans"    with n21, n23, n24, n25
```

The second became `100edc41` — five Ingrams, twelve remarks, ticks 37–62.
Convening still under-used (2 in 259 utterances), but when used it held.

### 5.5 What went unanswered — 107 of them

The failure mode is not evasion. It is that a question is asked and the
person asked simply never speaks back:

```
t37  Perrin → Bram:      "Do we have anything in the house, or should I find
                          breakfast on the way?"
t38  Nell → Ainsley:     "Ainsley, what food do we have in the house?"
t38  Ezra → Mireille:    "Mom, can you give me some food for breakfast?"
t38  Liora → Wren:       "Wren, Marta's asking if you brought anything. Do you?"
t38  Milo → Arjun:       "You need an extra pair of hands today?"
```

Five plain, answerable questions, all about breakfast, none answered.

The live judge on an earlier run measured what this is made of: of 50
selected residents, **34 silent, 3 deflected, 13 answered** — and of those
who replied, **81% genuinely answered**, inside the human band. The town is
not evasive; it is **inattentive**.

---

## §6 The Dunbar contradiction, confirmed at scale

**Seven threads exceeded four speakers. One split.**

Sizes: **11, 10, 8, 7, 7, 5, 5**, then 4 and below.

Dunbar's result is that spontaneous conversation caps at about four —
observed mean 3.4, one speaker plus 2.4 listeners — and that **at five it
fissions into two**. Humans at that grocery counter would have been having
four conversations. Kasukabe records one.

**G3 rewards exactly this shape.** It counts distinct speakers with **no
ceiling**, so an eleven-speaker thread scores as a triumph — while G1 and
B1 mark down the shallowness it produces. The consolidated report noticed
the symptom without the cause: *"wide but shallow, exactly the profile that
clears G3 and fails G1/G2."*

This run is the clearest evidence yet, and it points at a plausible
mechanism for the G1 failure rather than a correlation: **nobody is
selected when everybody is talking.** Sacks, Schegloff and Jefferson are
explicit that selection obliges exactly one person and no others; a thread
with eleven people in it has no such structure.

**Putting a ceiling on G3 changes a gate, which is the operator's call and
not mine.**

---

## §7 The mechanisms built this round, verified live

All three reached residents at scale. Counted from the fifty engines' own
session logs:

| Perception section | Turns carrying it |
|---|---:|
| `Said to you:` | **302** |
| `Still waiting on an answer from you` | 298 |
| `A conversation is open here` | 509 |
| `called you into a conversation` | **6** |

The convening invitation — which before this round arrived as a raw dict
with a UUID — rendered as words six times, once per convening plus the
invitees.

### The one I cannot measure, and it is my defect

**B2 reports "no evidence", and it is right to.** It reads
`addressed_agent_ids` off the `events` table — and that table holds only
**broker** events (`broker.call.completed` and friends). World events never
land there, and the `utterances` table has no column for the addressee list
either.

So the addressee fix is **invisible to the trace store**. That is exactly
the failure shape that once cost this project six invisible `trade` calls:
the mechanic works and the record says nothing. It is my defect, in a tool
I wrote to catch precisely that, and it is **not fixed**. The session-log
evidence above is what stands in for it.

---

## §8 The economy

| | count |
|---|--:|
| `sale` | 14 |
| `purchase` | 7 |
| `trade` | 2 |
| `transfer` | 766 |
| **Settlements (G4)** | **4** |
| Debts named in speech | 21 |

Notable movements: the **400-sim** repayment at t39 (`b_yard`); a 50-sim
`trade` between Rosalind Vance and Idris Thorne at t38 — two residents,
no shop; purchases at the bakery and grocery from t37 onward.

B5 reads **1 of 18**: eighteen remarks named a price, one was followed by a
matching movement inside the window. The gate passes on ≥1; the *share* is
what to watch across runs.

---

## §9 What this run says

**The machinery is sound at fifty.** Zero failures in 2,288 calls, 50 of 50
alive and speaking, five of six gates, and the deep-exchange and group
gates cleared by a wide margin.

**Two things genuinely improved at scale.** Every resident spoke, closing
the recurring-absentee problem. And the 400-sim debt — carried unspoken
through a dozen runs — was named, acknowledged and paid.

**The reply rate is not a scale problem.** 46% here against 24–54% at ten
residents, and below the 64% human floor at every population tried.

**Two things I would look at next**, neither of which I have done:

1. **A ceiling on conversation size.** The strongest structural departure
   from human behaviour in the run, and a plausible *cause* of the reply
   failure rather than a companion to it.
2. **Make the addressee list observable.** `utterances` needs the column,
   or B2 can never be measured and the fix stays unfalsifiable from the
   trace.

---

*Gate figures from `app/loop_report.py`; behavioural figures from
`app/world/evals/behavioural.py` against `docs/HUMAN_BASELINES.md`;
transcripts and ledgers read from the run's trace store; perception counts
from `docs/runs/series/fifty/sessions/*.jsonl`.*
