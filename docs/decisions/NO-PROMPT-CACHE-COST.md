# Running without prompt caching: where the money goes, and what cuts it

*Written 2026-09-10 after run 28's complete trace analysis and run 28b. Some levers
below are built; the rest are operator decisions, and this file is where they get
made.*

---

## The constraint

The residents think with `moonshotai.kimi-k2.5` on Bedrock (ap-south-1). **Prompt
caching is not available for this model.** The broker confirms it on every call:
`cache_read_input_tokens` and `cache_creation_input_tokens` are hard-coded to 0 in
`app/broker/translate.py`, because Bedrock's Converse `cachePoint` is honoured only
for models that support it, and Kimi K2.5 is not one of them.

So **every call pays full price for every token it re-reads.** A resident's turn is
up to 12 model calls, and each one re-sends the whole prefix: system prompt, tool
definitions, recent history, the perception, and every tool result earlier in the
same turn.

## Where the tokens go

| Measure | Run 28 | Run 28b |
|---|--:|--:|
| Input share of spend | **95%** ($9.54 of $10.04) | **96%** |
| Resident-call input tokens, median | 19,092 | 27,338 |
| …p90 | 26,851 | 30,790 |
| Output tokens, median | 136 | — |

What one resident call carries:

| Part | Size | Notes |
|---|--:|---|
| Tool definitions | **43,499 chars ≈ 10,900 tokens** → now 38,559 ≈ 9,600 | one `genesis_20` resident; 23–24 full schemas + 56–57 one-line rows |
| System prompt | 8,227 chars ≈ 2,000 tokens | persona, soul seed, standing brief |
| Stored history | 7 messages, median 6,407 chars (p90 12,233) | `KASUKABE_HISTORY_MESSAGES`, default 24 |
| Perception | median 2,823 chars ≈ 700 tokens | flat through a run |
| Tool results earlier in the turn | grows: 16,216 → 20,750 tokens across a limit turn | `look_around` median 1,507 chars (281 calls in 28b), `list_places` 2,000, `describe_primitives` 1,935, `find_people` 1,173 |

The bill is **calls × prefix**. Without caching both multiply fully, so the levers
are of two kinds: make fewer calls, and make each call re-read less.

---

## The levers, and what is done

Savings per $10 run are estimated from run 28b's 1,245 calls at $0.36 per million
input tokens. They are estimates, labelled as such; the next run measures them.

### Fewer calls per decision

| Lever | Status | Evidence | Estimated saving |
|---|---|---|--:|
| **A rest while walking is free, and says time does not pass inside a turn** | **Built** — `repeated_calls.py`, `primitives/living.py` | 226 of 329 rests in run 28 were mid-walk; limit turns were 45% of turn spend | up to ~$1.5 if the waits stop |
| **Name, not number** — person parameters accept names; a `buy` from a shopkeeper in the shop reaches the shop | **Built** — `world/tools/names.py`, `primitives/commerce.py` | 11% of calls naming someone by id failed (5% by name); each failure is a paid call and usually a retry | small per run, large per failed transaction |
| **`do_in_order`** — several acts, one call, nothing filed | **Built**, never used live | 0 programs in runs 26–28b | depends on adoption |
| **Stop the turn record being copied instead of acted on** | **Built** — `engine/src/sim/resident_voice.py`: a stored record reaches the model inside the world's message, not the resident's; the frozen engine's `request_plan` nudge (a tool served in 0 of 885 calls) is cut. Storage unchanged. `KASUKABE_RECORDS_IN_WORLD_VOICE=0` reverts, and is recorded in the manifest | Replay of 30 real run-28b first calls through Kimi, full 24-message histories rebuilt from the engine homes (`engine/tests/eval_records_in_world_voice.py`): live 17 called a tool / 11 copied a record; replayed as recorded 19 / 8; replayed in the world's voice **29 / 0** | the largest remaining waste: in run 28b 101 of 235 such calls copied, 111 called no tool |

### Less re-read per call

| Lever | Status | Evidence | Estimated saving |
|---|---|---|--:|
| **Strip class docstrings from served schemas** | **Built** — `registry._without_class_docstrings` | `toolify` 5,172 → 2,837 chars, `do_in_order` 4,412 → 2,077 | −1,235 tokens/call ≈ **$0.55** |
| **Spend the full-schema budget on verbs the town uses** | **Built** — `registry.COMMON_VERBS` | `arrest`, `come_of_age`, `ask_for_a_child` out; `trade`, `transfer`, `give`, `buy`, `sell` in | ~0 tokens; fewer refusals |
| **Memory keeps what changed, not every wait and look** | **Built** — `engine/src/sim/memory_stream.py` | 95% of observation memories were echoes | indirect: smaller retrieval |
| **Old perceptions are not re-sent in full** — an older perception reaches the model as its first line only (when and where); records and the current perception stay whole | **Built 2026-09-17** — `engine/src/sim/resident_voice.py`, `KASUKABE_OLD_PERCEPTIONS_IN_FULL=1` reverts, recorded in the manifest | Run 29: median resident call grew 14,110 → 23,759 input tokens across the run (1.68×, N10's bar is 1.25×); late in the run 61% of the world's message (median 20,747 chars) was superseded perceptions. Replay of 30 real late first calls through Kimi on Flex: as recorded 26/30 called a tool at 20,514 input tokens; compacted **30/30 at 15,558**; 0 copied records either way | **~5,000 tokens per late call (−24%)** ≈ $3.5 on a run-29-sized $20 run, and the growth that N10 measures stops |
| History window 24 → 12 messages | **Decision** — `KASUKABE_HISTORY_MESSAGES`, now recorded in the manifest | run 28b's median call was 8,200 tokens larger than run 28's, as histories filled | ~3,000 tokens/call ≈ $1.3 (estimate) |
| `toolify` and `skillify` served as one-line rows | **Decision** | `toolify` filed once in four runs, never called; `do_in_order` already carries the step shape | ~1,150 tokens/call ≈ $0.50 |
| Full-schema budget 24 → 16 | **Decision** — `KASUKABE_FULL_SCHEMA_BUDGET` | the tail of the budget is rarely-called verbs | measure before deciding |
| Cap `look_around`'s place list (40 → nearest ~12, with `list_places` for the rest) | **Proposed** | 1,507 chars median per look, re-read by every later call in the turn | ~$0.2 |

### Not levers

- **A cheaper model.** The allowlist's other model, `deepseek.v3.2`, costs $0.37 per
  million input tokens against Kimi's $0.36. There is no cheaper input on offer.
- **A model with caching.** A cache-capable model on Bedrock would change these
  economics entirely, and it would change *who the residents are*. The model is a
  fixed variable of the experiment, so that is a research decision, not a cost fix.
- **Caching at the broker.** A broker can deduplicate identical requests, but no two
  resident calls are identical, and the provider bills what it receives.

---

## The tier the prices assumed, and nobody asked for

**Built 2026-09-14.** This file's whole price table is the FLEX table — Kimi at
$0.36 per million input tokens — and until now no request ever set Bedrock's
`serviceTier`, so AWS served every call at **Standard**: $0.72 per million in
ap-south-1, where every run has gone. Every cost figure this project has
published is therefore about half the real charge:

| Run | Recorded | At Standard list prices |
|---|--:|--:|
| 27 | $5.00 | ≈ $8.98 |
| 28 | $10.04 | ≈ $18.31 |
| 28b | $10.00 | ≈ $18.13 |

`BEDROCK_SERVICE_TIER` (default `flex`) now sets it, `default` sends no field
at all so a model that has never seen the parameter is unaffected, an unknown
tier is refused at boot rather than forty minutes into a run, and the broker
warns when Bedrock serves a tier other than the one asked for — a silent
downgrade would put the project straight back to paying Standard while pricing
Flex. The tier is recorded in the run manifest.

**Verified against the real API, 2026-09-14** (ap-south-1, through the
inference profile):

```
asked 'flex'   :  519 ms | serviceTier reported: {'type': 'flex'}
asked 'default':  951 ms | serviceTier reported: ABSENT
asked 'flex'   :  560 ms | serviceTier reported: {'type': 'flex'}
asked 'default':  480 ms | serviceTier reported: ABSENT
```

Bedrock confirms the tier it served, so the downgrade detector has something
real to read. No latency penalty was visible on these calls; AWS documents Flex
as "for workloads that can handle longer processing times", so p90 latency is
worth watching over a full run rather than four calls.

---

## Decision

**Built and in force from run 29:** free waiting, name resolution, the shopkeeper-to-shop
purchase, stripped schema docstrings, used-verbs ranking, echo-free memory.

**To decide before run 29** (operator):

| Decision | Options | Recommendation |
|---|---|---|
| History window | 24 (today) · 12 | **12** for run 29, set explicitly and recorded; compare G6 and N11 against run 28 |
| `toolify` / `skillify` in full | keep · one-line rows | keep for run 29; revisit if `do_in_order` gets used |
| Full-schema budget | 24 · 16 | 24 for run 29; measure the tail's refusals first |

**By:** — **On:** —

*Fill this in before run 29 boots. A configuration nobody wrote down is a bill nobody
can explain.*
