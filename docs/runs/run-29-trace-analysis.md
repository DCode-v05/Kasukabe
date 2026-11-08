# Run 29 — what the fixes did, read from the trace

*14 September 2026. 20 residents, ticks 36–77 (6.8 game-hours, game day 0),
$20.06 spent against a $20 cap, 2,717 successful calls, 2,323 acts, 567
utterances, 0 failed calls. The canonical report is `run-29-report.md`; this is
the verdict on the four things that had never met a live run.*

---

## The headline

**Turns that changed the world went from 51% to 94%.**

| | Run 28 | Run 28b | **Run 29** |
|---|--:|--:|--:|
| **G6 — turns with a world effect** | 65% | 51% | **94%** (332 of 354) |
| Calls that copied the turn record | — | 43% of first calls after a record | **0.1%** (2 of 2,349) |
| Calls ending with no tool call | 18% | 23% | **12%** |
| Acts per resident turn | ~1 | ~1 | **7.15** |
| Residents who acted | — | — | **20 of 20** |

That is the records-in-the-world's-voice change (`36341e4`), and it is the
largest single behavioural movement this project has measured. The offline
replay predicted 19→29 tool calls in 30; the live run went further, because
every turn benefits rather than only the first call after a record.

**The eval predicted the other half too.** L1 measured `toolify` and
`do_in_order` at 0% reach and warned they would not be used. They were not:
**0 tools invented, 0 programs run**, in a town that authored five skills.
A prediction that holds is worth as much as a fix.

---

## What the residents authored

**Five skills, by three residents — and one of them stands on another.**

| Skill | Author | Tick | Steps |
|---|---|--:|---|
| `harvest_wheat_run` | Kem Okonjo (n17) | 50 | move → harvest → move → check *find_buyers* |
| `saw_planks_from_timber` | Silas Vance (n12) | 67 | check *timber_count* → craft |
| **`saw_through_timber_stack`** | Silas Vance (n12) | 69 | check *timber_count* → **skill `saw_planks_from_timber`** |
| `Timber Felling` | Corwin Thorne (n09) | 72 | fell_timber → check *timber quality* |
| `timber felling` | Tobias Holloway (n03) | 76 | check *look_around* → fell_timber |

Silas wrote a skill, then two ticks later wrote a second skill **whose step runs
the first one**. That is composition of authored work by a resident, unprompted
— the first in this project — and it is the shape the operator asked for, in
the skill half rather than the tool half.

And two of them were *walked*, not just filed: `saw_planks_from_timber` and
`saw_through_timber_stack` reached proficiency 37 over 8 completions each,
`harvest_wheat_run` 29 over 5. A skill nobody walks stays at the 1 its author
was seeded with, so those numbers are work done through the procedure.

The two `timber felling` skills are the same job written twice by two people,
in the same hour, differing only in step order and capitalisation — a duplicate
the town has no way to notice, and the first real evidence for a "somebody
already wrote this" surface.

**Nothing diffused.** No lesson, no licence, no sale: every artifact is held by
its author, so T5 is still 1 by construction.

---

## The economy

**Residents ate.** 20 meals in under seven game-hours, against run 28's *"you
have no food"* in 89% of perceptions while a bakery shelf held twenty bread.
ECON-4 — the rung the whole programme was blocked on — moved on its first live
test after the naming and shop-redirect fixes.

**Prices discovered, and moved.** 15 posts, first at tick 42:

- bread: 12 (bakery) → 18 (grocery) → 15 (person, twice)
- wheat: 15 (grocery) → 11 ask / 8 bid (n17) → 10 ask / 7 bid (n17) → 12/8 (n02)
- copper tools 250, iron tools 300, flour 60, potato 12, carrot 9, cabbage 8

Two residents posted **two-sided quotes** (ask and bid), and n17 moved its wheat
price down twice within six ticks. That is price response (ECON-14) appearing
without anybody being told prices could move.

**12 trades, 12 buys, 12 transfers, 18 shelf stockings, 6 gifts.** Against
this: **no wages** (`hire_employee` grants access to a business's tools and pays
nobody until somebody sets a wage — 0 set), **no credit**, **no registrations**.

**No day boundary.** The run stopped at tick 77 of 144, so nothing was charged
for upkeep or head tax, and ECON-11's "the state takes" stays untested. Nobody
bought energy either, so the dollar wallet only went down.

**One complaint was filed** — the first in the project's history — and one
candidacy declared. Neither reached a ruling or a ballot: the ballot opens at
tick 288.

---

## The bill, and the two knobs

**$20.06, and for the first time the recorded price is the price AWS charges.**
`BEDROCK_SERVICE_TIER=flex` was in force all run, recorded in the manifest, with
**zero downgrade warnings** — Bedrock served flex and said so.

**Flex is not slower here. It is faster.**

| | Run 28b (Standard) | Run 29 (Flex) |
|---|--:|--:|
| Latency p50 | 2,917 ms | **2,691 ms** |
| Latency p90 | 9,030 ms | **6,235 ms** |
| Calls sampled | 1,140 | 2,717 |

Not a controlled comparison — different run, different load — but it is the
opposite of the risk the plan flagged, and worth repeating before anyone treats
Flex as a latency cost.

**History 12 cut the prefix by a fifth.** Median resident-call input fell from
27,338 tokens (run 28b, window 24) to **21,429** (p90 30,790 → 25,295) while the
town was markedly more active. Input was still **96% of spend** — 53.4M input
tokens against 467k output.

**Prompt caching: still nothing, and now measured per call.** Every response
reported `cache_read_input_tokens` and `cache_creation_input_tokens` as absent
or zero, across all 2,717 calls. Kimi K2.5 on Bedrock has no cache, so every
one of those 53.4M input tokens was paid for at full price.

**What a game-day costs now: about $70** ($2.94 per game-hour) — higher than run
28b's $18, because this town did four times the work per hour. Acts per dollar:
116. Acts per model call: 0.85, and one model call still buys one act, because
no programs ran.

---

## What this moves on the checklist

| Item | Before | After run 29 |
|---|---|---|
| **C-A / G6** — act at all | held narrowly, 51% | **held, 94%** |
| **ECON-1** — a resident posts a price | held | held, with two-sided quotes |
| **ECON-4** — nobody holding sim starves | not held | **moved**: 20 meals, no starvation reports |
| **ECON-14** — prices respond | not reached | **first evidence**: n17 cut wheat twice |
| **C4 / Q3** — residents invent | 1 filing in five runs | **5 skills, 3 authors, one composed on another** |
| **ECON-8 / C-G** — capability compounds | not reached | **still not reached** in tools; the skill half moved |
| **ECON-5** — labour | not reached | not reached: 0 wages |
| **ECON-7** — knowledge worth money | not reached | not reached: 0 taught, 0 licensed |
| **GOV-1/2** — an election | blocked by run length | still blocked: tick 77 of 288 |

---

## What run 30 should answer

1. **Reach for `toolify` and `do_in_order` is still 0%**, exactly as L1 said.
   The next lever is the perception's own evidence — telling a resident what
   repetition has cost it in decisions — measured at 20 samples before it ships.
2. **Two residents wrote the same skill an hour apart.** Nothing tells an
   author that the job already has a procedure. That is a cheap surface and a
   real duplicate-work finding.
3. **A day boundary.** Upkeep, head tax and arrears have never been charged in a
   run that also traded this much; at ~$70 a game-day that is a $70 question.
4. **Diffusion.** Five skills exist and none moved. `teach_skill` works
   (verified 2026-09-14) and has never been called.

---

## Defects the run exposed, and what became of them

Found by reading this run's trace and report after it stopped, 2026-09-15.

| Defect | Cause | Status |
|---|---|---|
| **The morning's learning was thrown away.** All 14 end-of-run `auto:learning` calls were refused; "0 of 20 resident(s) consolidated". | The launch passed `--budget-cap-cents 2000` against a $20.00 broker cap. CORE-25's halt reserve was applied only when the runner derived its cap from `SPEND_CAP_USD`, so the explicit flag left the broker no room; it halted first and refused the drain. | **Fixed** — `cap_with_room_for_the_drain` lowers an explicit cap to the broker's cap less the reserve when `SPEND_CAP_USD` is known. |
| **The warning for that did not print.** | "The broker had already halted dispatch" was keyed to the runner's governor, which never tripped: the broker's counter got there first. | **Fixed** — `dispatch_had_halted` also asks the broker (`/admin/spend`). |
| **Every model call labelled "idle"** in the C1 section — 2,717 of 2,717. | The label join read only the live `/world/tool_invocations` endpoint, on the stale grounds that invocations had no table. A final report is written after the world stops, so it joined against nothing. | **Fixed** — falls back to the trace's `tool_invocations` table. Re-run on this trace: harvest 226 calls, fell_timber 135, transfer 116, buy 113, craft 98, trade 66. |
| **A skill built on a skill discounted nothing.** Silas Vance's `saw_through_timber_stack` runs `saw_planks_from_timber` and was filed as "discounts nothing". | `WorldState.skill_tools` — what dispatch reads to price a call — counted only a skill's direct `tool` steps, so anyone taught the composite paid full price for the work it performs. | **Fixed** — `skill` steps contribute their followable sub-skill's tools, in dispatch and in what is filed. |
| **The engine homes were deleted.** 20 session logs and `brain.sqlite` files, 135 MB, gone from `/tmp` within a day. | The default home root is `/tmp/kasukabe-agents` and nothing archived it at stop (INFRA-09's open half). | **Fixed** — `archive_engine_homes` packs them into the checkpoint directory at shutdown. This run's are unrecoverable. |
| `toolify` / `do_in_order` at 0 uses; the same skill written twice an hour apart. | Reach, and an exact-match name check. | **Fixed by DCode-v05 in `032b40f`** — the nudge now states what repetition cost in decisions, and names are matched on case and spacing. |
| Kimi's single lane peaked at 113 calls a minute against a 100 RPM limit. | — | **Not a defect**: 0 of 2,717 calls waited on the rate limit. |

