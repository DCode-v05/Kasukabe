# Run 28 — complete trace analysis

Run `1789033222-b5f17957` · app `3b07faa` · `world/genesis_20.json` · 20 residents ·
`moonshotai.kimi-k2.5` on Bedrock ap-south-1 · $10.00 cap · booted 10 Sep 2026
15:10:22 IST, stopped 15:34:33 · ticks 37 → 69, **06:10 → 11:30 of game-day 0**.

The run report (`run-28-report.md`) tells what happened. This document takes the
run apart: every model call, every tool, every sim, every conversation, and every
resident, read back from durable stores after the run ended.

## Sources

| Store | What was read | Rows |
|---|---|--:|
| Trace `data/kasukabe-run28-20260910.db` | `llm_calls` | 1,535 |
| | `tool_invocations` | 1,204 |
| | `transactions` | 2,050 |
| | `events` | 5,751 |
| | `utterances` | 236 |
| | `memories` | 1,597 |
| | `skills` · `eval_metrics` · `agents` · `businesses` | 103 · 7 · 20 · 4 |
| Session logs `/tmp/kasukabe-agents-run28/*/session_log.jsonl` | one record per resident turn: perception, output, every action with status and result, exit reason | 261 turns · 1,319 actions |
| Checkpoint `data/kasukabe-checkpoints-run28/checkpoint_0000000069.json.gz` | ledger, inventories, loans, price board, goals, intentions, beliefs, needs | — |
| Brains `/tmp/kasukabe-agents-run28/*/brain.sqlite` | knowledge, episodes, people, learning signals | 20 |
| Logs `app/logs/run28.log` · `broker-run28.log` | drain, engine errors, link health | — |

*Counting note.* The session logs record 42 turns ending at the 12-call limit
out of 261. The trace, grouping model calls by resident and tick, counts 46 of
264. Both are given where they are used.

---

## 1. Findings in brief

1. **Re-reading context is 95% of the bill.** 26.49M input tokens cost $9.54;
   0.28M output tokens cost $0.51. A resident's prompt grew **73%** over 32
   ticks — 15,154 → 26,193 tokens median — while the perception inside it stayed
   near 2,800 characters.
2. **42 turns ran to the 12-call limit and cost 45% of turn spend.** Their most
   common act was `rest`, and **226 of 329 rests (69%) were made mid-walk**, where
   a rest changes nothing until the turn ends.
3. **The market did not work.** `buy` 0 of 9, `sell` 1 of 5, `trade` 1 of 4,
   `post_price` 3 of 8. Meanwhile **"You have no food" was in 89% of
   perceptions** and the bakery shelf ended the run holding 20 bread.
4. **Goods were made and not sold.** 52 timber, 35 wheat, 11 flour, 7 planks, 6
   bread, 3 furniture produced; Corwin ends with 14 unsold planks, Idris with 8
   furniture and 20 timber.
5. **Every sim is accounted for.** Start + ins − outs equals the checkpoint
   balance for all 20 residents, to the unit.
6. **91 turns called no tool**, and **46% of outputs copy the world's "RECORD
   OF THE TURN" block** instead of acting.
7. **The memory stream is 95% tool echoes** — 1,250 of 1,315 observations.
8. **The town talked constantly and answered quickly**: 236 lines in 19
   conversations, a median reply one tick later — yet unanswered speech sat in
   82% of perceptions.
9. **Learning ran only in the last 90 seconds.** Two residents consolidated
   nothing; four hold 26 or more unprocessed signals.
10. **The model sometimes left English**: one spoken line switched to Chinese
    mid-sentence, and one output degenerated into mixed scripts.

---

## 2. The run at a glance

| | |
|---|---|
| Wall clock | 24 min 11 s (first call 15:10:30, last 15:34:33) |
| Game time | 06:10 → 11:30, day 0 — no day boundary, no upkeep or head tax charged |
| Residents | 20 alive · 0 dormant · 0 dead · 20 acted |
| Model calls | 1,535 · 1,464 succeeded · 71 refused by the cap in the final minute |
| Spend | $10.04 at the broker · 996¢ burned in the ledger (whole cents per call) |
| Turns | 261 in the session logs · 42 at the 12-call limit · 91 with no action |
| Actions | 1,319 · 64 refused (5%) · 41 distinct tools (34 in the trace, which records only calls that reached the world) |
| Trades · sales at a posted price · transfers | 2 · 1 · 9 |
| Utterances · conversations | 236 · 19 |
| Memories · knowledge rows | 1,597 · 232 |
| Engine errors | 4 `/tick` 500s, all the spend cap mid-turn · 0 broker exceptions · 0 lost link calls |

---

## 3. Model calls

### 3.1 By purpose

| Purpose | Calls | OK | Cost | Input tokens | Output tokens | p50 | p90 | How they ended |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| Resident turn | 1,237 | 1,225 | $9.106 | 24,182,545 | 222,430 | 2.4 s | 8.6 s | tool_use 1,006 · end_turn 192 · max_tokens 27 · cap 12 |
| Post-turn summariser | 120 | 120 | $0.488 | 1,291,370 | 9,571 | 1.9 s | 2.9 s | end_turn 120 |
| Working-memory summariser | 130 | 80 | $0.394 | 972,447 | 31,017 | 9.6 s | 19.4 s | end_turn 59 · **max_tokens 21** · cap 50 |
| Other | 28 | 20 | $0.031 | 28,409 | 11,473 | 8.6 s | 15.0 s | end_turn 17 · max_tokens 3 · cap 8 |
| Learning | 20 | 19 | $0.018 | 19,016 | 6,404 | 8.4 s | 15.1 s | end_turn 19 · cap 1 |
| **Total** | **1,535** | **1,464** | **$10.037** | **26,493,787** | **280,895** | | | |

### 3.2 What a dollar bought

Kimi K2.5 is priced at $0.36 per million input tokens and $1.80 per million
output. **Input: $9.54 (95%). Output: $0.51 (5%).** A resident call reads a
median 19,092 tokens (p90 26,851) to write 136 (p90 370). The bill is the
context, not the thinking.

### 3.3 What a resident's prompt holds

From the stored request bodies of the 1,225 successful resident calls:

| Part | Median |
|---|--:|
| System prompt (persona, soul seed, standing brief) | 8,308 chars |
| Messages (history) | 7 messages, 7,755 chars |
| Tool definitions | 82 |
| Perception (from the session logs) | 2,823 chars (p90 3,737, max 4,777) |

The trace stores tool definitions as names only in 894 of the 1,225 bodies
(`broker/trace.py` capped bodies), so their size per call cannot be measured from
the trace. The payload served at boot measured about 39,000 characters.

### 3.4 The prompt grew through the run

| Ticks | Resident-call input tokens (median) |
|---|--:|
| 37–45 (06:10–07:30) | 15,154 |
| 60–68 (10:00–11:20) | 26,193 |

+73% in 32 ticks. The perception stayed near 2,800 characters throughout, so
the growth is in what surrounds it — history and summaries. *(Inference: the
trace cannot split input tokens by prompt part.)*

### 3.5 Spend by minute

| Clock | Calls | $ | Running $ | | Clock | Calls | $ | Running $ |
|---|--:|--:|--:|---|---|--:|--:|--:|
| 15:10 | 53 | 0.27 | 0.26 | | 15:23 | 56 | 0.42 | 5.18 |
| 15:11 | 33 | 0.17 | 0.43 | | 15:24 | 68 | 0.53 | 5.71 |
| 15:12 | 72 | 0.36 | 0.79 | | 15:25 | 16 | 0.12 | 5.83 |
| 15:13 | 45 | 0.23 | 1.01 | | 15:26 | 51 | 0.39 | 6.21 |
| 15:14 | 67 | 0.37 | 1.38 | | 15:27 | 55 | 0.47 | 6.68 |
| 15:15 | 61 | 0.40 | 1.78 | | 15:28 | 75 | 0.65 | 7.32 |
| 15:16 | 93 | 0.54 | 2.33 | | 15:29 | 51 | 0.47 | 7.79 |
| 15:17 | 59 | 0.35 | 2.68 | | 15:30 | 60 | 0.58 | 8.37 |
| 15:18 | 91 | 0.60 | 3.28 | | 15:31 | 44 | 0.42 | 8.78 |
| 15:19 | 32 | 0.24 | 3.52 | | 15:32 | 50 | 0.51 | 9.29 |
| 15:20 | 40 | 0.25 | 3.77 | | 15:33 | 94 | 0.37 | 9.66 |
| 15:21 | 76 | 0.52 | 4.29 | | 15:34 | 129 | 0.38 | 10.04 |
| 15:22 | 64 | 0.47 | 4.76 | | | | | |

Calls per minute stayed flat while dollars per minute rose from about $0.3 to
$0.6 — the same number of calls, each reading more.

### 3.6 Pace

- A resident call: p50 2.4 s, p90 8.6 s, max 69 s.
- A resident turn, first call to last: median 10 s, p90 40 s, max 102 s.
- A tick: median 40 s, set by its slowest turn (median 37 s). About 8 residents
  were due per tick.
- No rate-limit waiting at all; no Bedrock timeouts; no broker exceptions.

### 3.7 Truncation and refusals

- **51 calls stopped at `max_tokens` (768 output tokens)**: 27 resident calls,
  **21 of 80 working-memory summaries (26%)**, 3 other.
- **71 calls refused by the spend cap**, all at 15:34: 12 resident turns, 50
  working-memory summaries, 8 other, 1 learning. Four engines answered `/tick`
  with a 500 that names the cause: *"process-wide spend cap of $10.00 has been
  reached ($10.04 spent) - dispatch refused"*.

---

## 4. Turns

### 4.1 Two kinds of turn

| | Ran to the 12-call limit | Finished on its own |
|---|--:|--:|
| Turns | 42 | 219 |
| Model calls per turn | 12.0 | 3.2 |
| Actions per turn | 16.0 | 2.9 |
| Refused actions | 22 (3%) | 42 (7%) |
| Turns with no action | 0 | 91 |
| Median cost (trace: 46 limit turns of 264) | **8.42¢** | 1.47¢ |
| Share of all turn spend | **45%** | 55% |
| Most-called tools | `rest` 214 · move 123 · look_around 85 · say 83 · harvest 23 · craft 23 | say 148 · rest 115 · move 109 · look_around 76 · fell_timber 24 |

Context grows inside a limit turn from 16,216 to 20,750 input tokens (median,
first call to last), so the last calls of a long turn are its dearest.

### 4.2 Waiting for a walk

| | |
|---|--:|
| `rest` calls | 329 |
| …made while still walking | **226 (69%)** |
| Turns with 5+ rests | 27 |
| …where every rest returned an identical result | **25** |
| Most rests in one turn | 43 |
| `rest → rest → rest` in limit turns | 118 |
| Walk length (`move` estimate) | median 4 ticks · p90 7 · max 9 |
| `move` calls re-issuing a walk already under way | 21 |
| Turns that began mid-walk | 62 of 261 |

Time passes only between turns. A rest inside a turn buys nothing, and still
costs a model call and 1 sim — 328 sim across the run, a tenth of all action
fees. Bram Holloway, in one turn on the way to the ore outcrop:

> "Still walking. 4 ticks to go. Need to rest until I arrive, then work. …
> Still walking, still not there. The road's longer than I counted. I'll rest
> once more and hope the outcrop comes into view. … Something's wrong with my
> count."

Each rest answered: *"You paused. You are still walking to Kasukabe Ore
Outcrop; pass stop_walking to give it up."* — `rest_handler` in
`app/world/tools/primitives/living.py`.

### 4.3 Repeating the same call

**376 identical calls** (same tool, same parameters, same turn) in 87 turns:
`rest` 240, `look_around` 66, `move` 25, `harvest` 19, `fell_timber` 19, `craft`
3, `mine` 2, `post_price` 1. Runs of the same tool three or more times in a row:
`rest` 53, `say` 6, `harvest` 5, `fell_timber` 4, `craft` 4, `mine` 1.

Genuine repeated work — the shape `do_in_order`'s `repeat` folds — is the 43
identical `harvest`, `fell_timber`, `craft` and `mine` calls. The rest are waits.

### 4.4 Turns that did not act

**91 turns called no tool** — one model call each, all in daylight. Their
output is prose dialogue that never became a `say` (*"Seems like we cross paths
often these days."*), or a copy of the world's record of the previous turn.

- **120 of 261 outputs (46%)** contain *"RECORD OF THE TURN THAT JUST ENDED"*,
  the header `render_action_record` in `engine/src/sim/session_log.py` writes into
  each resident's history. PROMPT-03 relabelled that block because models filled
  it in as a template; under Kimi they still do.
- **92 outputs (35%)** contain `</think>` — reasoning text in the reply channel.
- Output length: median 1,971 characters, p90 6,569.

### 4.5 Leaving English

- 1 of 236 utterances switched to Chinese mid-line — Rosalind Vance: *"Junia.
  You're far from the grocer's counter.你知道哪里可以买到大批的谷物吗？…"* ("Do
  you know where to buy grain in bulk?")
- 3 of 261 outputs contain Chinese; one (Wren Thorne) degenerated into mixed
  scripts: *"b mountain{ ও }ীক্ষা十三field"*.
- 2 tool calls carried Chinese in their parameters — Ezra Okonjo's intention
  *"produce goods for sale or订单"* ("orders").
- 1 of 1,597 memories; 0 knowledge rows.

---

## 5. Tools

### 5.1 Every tool used

From the session logs. *First* is the game hour of first use.

| Tool | Calls | OK | Refused | Residents | Identical repeats | First | Main refusal |
|---|--:|--:|--:|--:|--:|--:|---|
| rest | 329 | 328 | 1 | 17 | 240 | 06 | cannot afford (1 sim) |
| move | 232 | 231 | 1 | 20 | 25 | 06 | cannot afford |
| say | 231 | 230 | 1 | 20 | 0 | 06 | cannot afford |
| look_around | 161 | 161 | 0 | 20 | 66 | 06 | |
| harvest | 38 | 37 | 1 | 6 | 19 | 07 | wrong place |
| craft | 33 | 25 | 8 | 8 | 3 | 08 | missing input ("needs 1 wheat, has 0") |
| fell_timber | 31 | 26 | 5 | 6 | 19 | 10 | called from the street |
| set_intention | 25 | 24 | 1 | 12 | 0 | 07 | text too long |
| check_debts | 25 | 25 | 0 | 13 | 0 | 07 | |
| describe_place | 20 | 20 | 0 | 11 | 0 | 08 | |
| keep_doing | 19 | 17 | 2 | 6 | 0 | 06 | wrong place |
| find_people | 19 | 19 | 0 | 9 | 0 | 11 | |
| stock_shelf | 18 | 13 | 5 | 5 | 0 | 06 | missing input |
| describe_person | 14 | 14 | 0 | 9 | 0 | 06 | |
| list_places | 11 | 11 | 0 | 6 | 0 | 06 | |
| transfer | 9 | 8 | 1 | 4 | 0 | 06 | insufficient sim |
| describe_primitives | 9 | 9 | 0 | 5 | 0 | 08 | |
| **buy** | **9** | **0** | **9** | 5 | 0 | 09 | see §6 |
| post_price | 8 | 3 | 5 | 3 | 1 | 06 | no ask or bid given |
| drop_intention | 7 | 6 | 1 | 5 | 0 | 07 | nothing set |
| set_goal | 7 | 7 | 0 | 4 | 0 | 10 | |
| mine | 6 | 4 | 2 | 1 | 2 | 06 | wrong place |
| draw_from_till | 6 | 6 | 0 | 2 | 0 | 06 | |
| cook | 6 | 5 | 1 | 1 | 0 | 06 | missing input |
| retire_goal | 6 | 4 | 2 | 3 | 0 | 06 | unknown goal id |
| **sell** | **5** | **1** | **4** | 3 | 0 | 09 | see §6 |
| **trade** | **4** | **1** | **3** | 3 | 0 | 07 | see §6 |
| eat | 4 | 2 | 2 | 4 | 0 | 08 | holding none |
| recall_goals | 4 | 4 | 0 | 3 | 1 | 08 | |
| search_memory | 4 | ? | ? | 3 | 0 | 09 | **no result recorded** (§14) |
| start_conversation | 3 | 3 | 0 | 2 | 0 | 06 | |
| take_from_shelf | 3 | 1 | 2 | 2 | 0 | 07 | empty shelf · not an employee |
| give | 2 | 2 | 0 | 2 | 0 | 07 | |
| update_goal | 2 | 0 | 2 | 2 | 0 | 08 | nothing said to change |
| pay_tax | 2 | 0 | 2 | 2 | 0 | 10 | nothing owed |
| note_belief | 2 | 1 | 1 | 1 | 0 | 10 | text too long |
| pay_wage | 1 | 0 | 1 | 1 | 0 | 08 | not an employee |
| register_business | 1 | 0 | 1 | 1 | 0 | 09 | 5,000 fee, held 3,842 |
| quote_action_cost | 1 | 1 | 0 | 1 | 0 | 09 | |
| recall_memories | 1 | 1 | 0 | 1 | 0 | 06 | |
| pray | 1 | 1 | 0 | 1 | 0 | 07 | |

### 5.2 Refusals by kind

64 of 1,319 actions (5%). By tool: `buy` 9, `craft` 8, `post_price` 5,
`stock_shelf` 5, `fell_timber` 5, `sell` 4, `trade` 3. The commonest messages:
a missing input (*"needs 1 wheat, has 0"*, 11 across craft, stock_shelf,
take_from_shelf and buy); `post_price` with neither price (5); `fell_timber`
from the street (5); trading more than held (3).

### 5.3 Where they walked

`move` destinations: the bakery 24, the wheat strip 20, the forest 17, the yard
16, the grocery 16, the ore outcrop 15, the mill 15, the Willows market and
pharmacy 14, Kasukabe Farmland 11, Oak Hill College 9.

### 5.4 Never called

31 tools that need no particular place were never called — the live report's
35 less `update_goal`, `pay_wage`, `buy` and `pay_tax`, which were tried and
refused. Among them: `hire_employee`, `set_wage`, `deposit_to_till`,
`withdraw_price`, `teach_skill`, `skillify`, `toolify`, `revise_skill`,
`revise_tool`, `license_tool`, `sell_tool`, `build_structure`, `set_will`.

---

## 6. Commerce: why almost nothing sold

### 6.1 Every attempt

**`buy` — 0 of 9**

| Who | When · where | Asked | Why it failed |
|---|---|---|---|
| Marta | 06:50 bakery | 5 wheat from Junia | `buy` must be called on the street |
| Marta | 07:10 street | 5 wheat from Wren | `buy` must be called at Kasukabe Farmland |
| Marta | 07:10 street | 5 wheat from Junia | Junia had not posted a price for wheat |
| Liora | 07:30 street | 1 bread from Tobias | Tobias had not posted a price for bread |
| Junia | 08:00 wheat strip | 2 wheat from Wren | Wren had not posted a price |
| Tobias | 09:10 street | 2 bread from **Marta** | *"n07 needs 2 bread, has 0"* |
| Delia | 09:30 Government Complex | 1 bread from **Marta** | *"n07 needs 1 bread, has 0"* |
| Delia | 10:20 **bakery** | 2 bread from **Marta** | *"n07 needs 2 bread, has 0"* |
| Marta | 10:30 bakery | 10 wheat from Junia | *"n13 needs 10 wheat, has 0"* |

Three of the four bread purchases named Marta herself. Her own hands were empty;
her **shop's** shelf held bread. Nobody named `b_bakery` as the seller.

**`sell` — 1 of 5.** Wren sold Junia 2 wheat at her bid, 22 sim. Idris offered
the yard 8 furniture and 4 planks — the yard had posted no buying price for
either. Idris tried again from home — `sell` must be called on the street.
Corwin offered 14 planks to `business:sawmill`, which does not exist.

**`trade` — 1 of 4.** Bram traded Ezra 1 copper ore for 50. Marta and Junia (×2)
tried to *buy* wheat with `trade` — refused with *"you hold 0 wheat"*, because
`trade` has the caller hand over the goods. A buyer cannot open one.

**`post_price` — 3 of 8.** Five posts named an item and no price. Of the three
that landed, Junia's flour (18) and wheat (ask 14, bid 11) were posted **holding
none of either**, and the world said so.

**`eat` — 2 of 4.** Liora ate bread and Tobias ate wheat; Ainsley and Delia held
none. **`take_from_shelf` — 1 of 3**: Marta took bread from her own shelf; Junia
was refused at an empty grocery shelf and at a mill she does not work for.
**`register_business` — 0 of 1**: Delia's pharmacy costs 5,000 and she held 3,842.

### 6.2 Hunger beside full shelves

*"You have no food"* stood in **231 of 261 perceptions (89%)**. Eating was the
town's commonest topic (51 lines). Two meals were eaten all morning. At 11:30:

| Holder | What was on hand |
|---|---|
| Thorne's Bakery `b_bakery` | bread 20 · flour 20 |
| Kasukabe Grocery `b_grocery` | apple 20 · carrot 20 · potato 16 · milk 10 · bread 8 · flour 6 |
| Vance Mill `b_mill` | wheat 30 · flour 27 · malt 2 |

### 6.3 What was made, and where it ended up

Produced (from successful `harvest`, `fell_timber`, `mine`, `craft`, `cook`
results): **timber 52 · wheat 35 · flour 11 · planks 7 · bread 6 · copper ore 4 ·
furniture 3 · barley 2 · malt 2 · copper ingot 1 · copper wire 1**.

| Resident | Made | Holding at 11:30 |
|---|---|---|
| Idris Thorne | timber 24 · planks 4 · furniture 3 | furniture 8 · timber 20 · planks 4 |
| Marta Thorne | wheat 21 · bread 6 · flour 5 | — |
| Corwin Thorne | timber 12 · planks 1 | **planks 14** |
| Tobias Holloway | timber 7 · wheat 3 | timber 7 · wheat 2 |
| Silas Vance | timber 5 · planks 1 | planks 5 |
| Bram Holloway | copper ore 4 | copper ore 3 |
| Junia Vance | wheat 4 · flour 3 | bread 12 (bought) |
| Rosalind Vance | wheat 3 · flour 3 · barley 2 · malt 2 | — |
| Aldous Vance | timber 3 | timber 1 |
| Wren Thorne | wheat 2 | wheat 4 |
| Kem Okonjo | wheat 2 | wheat 2 |
| Nell Holloway | timber 1 · planks 1 | planks 1 |
| Ezra Okonjo | copper ingot 1 · copper wire 1 | copper wire 1 |

### 6.4 Prices and the three completed deals

Prices posted: Junia flour ask 18 (t44), Junia wheat ask 14 / bid 11 (t46),
Marta bread ask 15 (t51) — all three still on the board at the end.

| Tick | Deal | Sim |
|--:|---|--:|
| 45 | Bram → Ezra, 1 copper ore (`trade`) | 50 |
| 56 | Wren → Junia, 2 wheat at Junia's posted bid (`sell`) | 22 |
| 68 | Marta → Junia, 12 bread (`trade`) | 144 |

---

## 7. Money: every sim accounted for

### 7.1 Transactions

| Currency | Type | Reason | Rows | Amount |
|---|---|---|--:|--:|
| sim | fee | action_cost | 1,013 | 3,409 |
| usd | burn | inference_burn | 983 | 996¢ |
| sim | credit | refund | 36 | 514 |
| sim | transfer | transfer | 9 | 1,480 |
| sim | transfer | till | 6 | 2,621 |
| sim | trade | trade | 2 | 194 |
| sim | sale | sale | 1 | 22 |

### 7.2 Reconciliation

Start (from `genesis_20`) + everything in − everything out, against the
tick-69 checkpoint. **The residual is 0 for every resident.**

| Resident | Start | In | Out | End | Fees | Other movements |
|---|--:|--:|--:|--:|--:|---|
| Bram Holloway | 3,200 | 645 | 606 | 3,239 | 191 | transfers in 595, out 415 · trade in 50 |
| Ainsley Holloway | 4,600 | 400 | 237 | 4,763 | 27 | transfer in 400, out 210 |
| Tobias Holloway | 1,200 | 2 | 260 | 942 | 260 | refund 2 |
| Perrin Holloway | 400 | 27 | 423 | **4** | 28 | transfer out 395, in 25 · refund 2 |
| Nell Holloway | 2,600 | 0 | 39 | 2,561 | 39 | |
| Wren Thorne | 3,800 | 22 | 62 | 3,760 | 62 | sale in 22 |
| Marta Thorne | 5,200 | 2,963 | 1,195 | **6,968** | **745** | till in 2,620 · refunds 199 · trade in 144 · wages out 450 |
| Idris Thorne | 2,900 | 6 | 365 | 2,541 | 365 | refunds 6 |
| Corwin Thorne | 1,800 | 2 | 290 | 1,512 | 290 | refund 2 |
| Liora Thorne | 500 | 2 | 79 | 423 | 79 | refund 2 |
| Rosalind Vance | 4,100 | 0 | 163 | 3,937 | 163 | |
| Silas Vance | 2,600 | 6 | 141 | 2,465 | 141 | refund 6 |
| Junia Vance | 700 | 289 | 579 | 410 | 413 | refunds 288 · till 1 · sale out 22 · trade out 144 |
| Aldous Vance | 200 | 0 | 120 | **80** | 120 | |
| Ezra Okonjo | 3,400 | 0 | 117 | 3,283 | 67 | trade out 50 |
| Delia Okonjo | 3,900 | 5 | 198 | 3,707 | 198 | refunds 5 |
| Kem Okonjo | 1,500 | 2 | 76 | 1,426 | 76 | refund 2 |
| Tova Okonjo | 300 | 450 | 39 | **711** | 39 | wages in 450 |
| Father Anselm | 1,800 | 0 | 50 | 1,750 | 50 | |
| Hester Vance | 2,200 | 0 | 66 | 2,134 | 56 | 10 to the treasury |

### 7.3 Where fees went

All 1,013 action fees (3,409 sim) went to the treasury. Less 514 refunded,
plus Hester's 10: **2,905 sim, exactly the treasury's closing balance.** `rest`
alone accounts for 328 sim of the fees.

### 7.4 Refunds

A refund is the reversing leg of a charge whose action then failed
(`refund_action`, `app/world/ledger/action_costs.py`). 36 refunds, 514 sim:
Junia 11 (288 sim) and Marta 13 (199) — the two residents with the most refused
actions (12 and 18). The largest single refund was 174 sim to Junia at t46. The
trace does not store which tool each refund reversed (§14).

### 7.5 Energy burned, per resident

In USD cents (×10 for energy): Marta 97 · Delia 89 · Tobias 84 · Rosalind 72 ·
Junia 61 · Corwin 55 · Liora 55 · Kem 53 · Bram 52 · Idris 42 · Aldous 38 ·
Silas 37 · Ezra 37 · Tova 35 · Nell 33 · Anselm 33 · Wren 32 · Perrin 31 ·
Ainsley 30 · Hester 30. **Total 996¢.** Everyone started with 600¢; the lowest
balance at the end was Marta's 503¢. Energy never constrained anyone.

### 7.6 Tills, loans, wages

- **Tills.** Marta drew 2,620 sim from the bakery in five draws (620, 200, 500,
  800, 500): 8,000 → 5,380. Junia drew 1 from the grocery (3,999). The mill
  (5,000) and the yard (6,000) were untouched.
- **Loans at the end.** Bram owes Marta 400 and Rosalind 260; nothing repaid.
- **Wages.** No wage promise exists. Marta paid Tova 150 three times with
  `transfer` after `pay_wage` was refused.

---

## 8. Social life

### 8.1 Totals

236 lines in 19 conversations — median 7 lines, longest 39, three of a single
line. A line is a median 30 words (p90 59, longest 201). Of 191 lines addressed
to someone, **125 (65%) were answered by an addressee within 6 ticks**; the
median reply came **one tick** later (p90 9).

### 8.2 Every conversation

| Ticks | Where | Lines | Speakers | Opening line |
|---|---|--:|---|---|
| 37–49 | home:h_forge | 6 | Tova 3 · Delia · Kem · Ezra | Delia: "I'm off to the pharmacy. Anyone need anything before I go?…" |
| 37–49 | home:h_quarry | 19 | Perrin 5 · Nell 5 · Ainsley 4 · Bram 3 · Tobias 2 | Perrin: "I have four hundred sim. Our bill today is six hundred…" |
| 37–39 | home:h_field | 5 | Marta 2 · Corwin 2 · Wren | Marta: "Wren, love, what's the wheat looking like?…" |
| 37–63 | the grocery | 13 | Rosalind 6 · Anselm 2 · Junia 2 · Kem 2 · Marta | Anselm: "Good morning. I am seeking Hester Vance…" |
| 38 | home:h_mill | 7 | Hester 5 · Aldous 2 | Hester: "Rosalind. I'm needing food before I go out." |
| 38 | the bakery | 1 | Tova | "Marta. I just came of age this morning—officially contributing now…" |
| 38–68 | the wheat strip | 25 | Junia 7 · Wren 5 · Kem 5 · Ainsley 4 · Rosalind 2 · Marta 2 | Rosalind: "Junia. You're far from the grocer's counter.你知道…" |
| 39–56 | the yard | 17 | Ezra 5 · Silas 4 · Nell 3 · Rosalind 2 · Corwin 2 · Idris | Ezra: "Vance. You carry those on foot, or you waiting for Bram's cart?" |
| 40–43 | the bakery | 13 | Liora 5 · Marta 4 · Anselm 2 · Rosalind 2 | Anselm: "Good morning, Marta. Tova. I am looking for Hester Vance…" |
| 41–68 | the street | **39** | 14 speakers, Tobias 7 · Idris 6 | Tobias: "Corwin—right, so I wanted to ask. The forestry trade…" |
| 41–68 | the bakery | 34 | Marta 11 · Tova 9 · Kem 8 · Liora 2 · Junia 2 · Bram · Delia | Kem: "Marta—whatever's warm from this morning, I'll take it. Paying proper…" |
| 46–64 | the forest | 24 | Tobias 15 · Aldous 3 · Corwin 3 · Idris 3 | Aldous: "Corwin. I'm here to learn, not to watch…" |
| 51–57 | Government Complex | 7 | Delia 4 · Anselm 2 · Silas | Silas: "Father. I'm looking for timber today…" |
| 56–67 | the ore outcrop | 6 | Ezra 2 · Corwin 2 · Bram 2 | Ezra: "Color's off, Bram… This last batch—running thin." |
| 62–68 | home:h_quarry | 3 | Liora 2 · Perrin | Liora: "Perrin. The 400 sim you told me about—I've kept it…" |
| 62 | the mill | 1 | Kem | "Mill's empty, but I'm not. Two bushels of wheat…" |
| 63–65 | the temple | 7 | Delia 4 · Hester 3 | Hester: "Delia. You said you had my tincture ready…" |
| 66 | sawmill | 1 | Corwin | "Silas. I've got fourteen planks, rough-cut this morning…" |
| 67–68 | the wheat strip | 8 | Delia 4 · Wren 3 · Kem | Delia: "Have you seen Wren this morning? I need willow bark…" |

### 8.3 The threads the town followed

- **The Holloway household bill.** Perrin gives away 395 of 400; Bram passes 400
  to Ainsley and gets 200 back; Nell: *"Household's square… You've eleven sim and
  a day's work ahead."*
- **A job without a hiring tool.** Tova comes of age, keeps Marta's till until
  noon, and is paid 150 three times. Kem offers her carrying work instead.
- **An apprenticeship.** Tobias asks Corwin and Idris about the forestry trade;
  Idris: *"Aldous Vance. Your hands are welcome. Come to the forest when you're
  ready."*
- **A dying copper seam.** Ezra: *"This last batch—running thin. Darker at the
  edge."* Bram admits it.
- **A boundary dispute.** Father Anselm spends his morning seeking Hester about
  the Holloway–Thorne marker; Hester tells Delia she was standing there when it
  was set.
- **Undercutting.** Junia reports that Kem undercuts the mill's rate; Kem offers
  the mill four sim a bushel.
- **A pharmacy that cannot open.** Delia needs 5,000 to register it and seeks
  willow bark for Hester's tincture.

### 8.4 Who talked with whom

Most exchanged lines: Marta ↔ Tova 13 · Tobias ↔ Idris 12 · Delia ↔ Hester 9 ·
Delia ↔ Father Anselm 7 · Kem ↔ Tova 7 · Wren ↔ Junia 7 · Marta ↔ Junia 6 ·
Wren ↔ Marta 5 · Marta ↔ Liora 5 · Ainsley ↔ Nell 5.

| Resident | Spoke | Addressed | Answered | Left unanswered |
|---|--:|--:|--:|--:|
| Tobias Holloway | 24 | 11 | 9 | 2 |
| Marta Thorne | 23 | 14 | 13 | 1 |
| Kem Okonjo | 18 | 10 | 9 | 1 |
| Delia Okonjo | 17 | 12 | 8 | 4 |
| Junia Vance | 13 | 13 | 10 | 3 |
| Tova Okonjo | 13 | **19** | 15 | 4 |
| Rosalind Vance | 12 | 8 | 4 | 4 |
| Corwin Thorne | 12 | 11 | 9 | 2 |
| Wren Thorne | 11 | 13 | 11 | 2 |
| Nell Holloway | 10 | 8 | 7 | 1 |
| Idris Thorne | 10 | 12 | 7 | 5 |
| Liora Thorne | 10 | 9 | 5 | 4 |
| Aldous Vance | 9 | 11 | 7 | 4 |
| Father Anselm | 9 | 10 | 5 | 5 |
| Hester Vance | 9 | 7 | 5 | 2 |
| Ainsley Holloway | 8 | 10 | 8 | 2 |
| Ezra Okonjo | 8 | 6 | 4 | 2 |
| Bram Holloway | 7 | 6 | 6 | 0 |
| Silas Vance | 7 | 7 | 4 | 3 |
| Perrin Holloway | 6 | 6 | 4 | 2 |

What was said, by lines mentioning: eat 51 · wheat 35 · sim 30 · pay 20 · owe
15 · bread 13 · flour 13 · school 11 · copper 11 · timber 8 · college 7 ·
apprentice 6 · hungry 5 · help 3. Where: the bakery 48 lines, the street 39, the
wheat strip 32, the forest 24, home:h_quarry 22, the yard 17.

### 8.5 The unanswered backlog

*"Said to you, and not yet answered"* was in **213 of 261 perceptions (82%)** and
*"You asked, and have not been answered"* in **210 (80%)**, built by
`format_addressed_to_you` and the open-question block in
`app/world/retrieval_depth.py`.

---

## 9. What they wanted

### 9.1 Goals

| Tick | Resident | | Goal |
|--:|---|---|---|
| 37 | Junia | set | Stock the grocery with flour and bread. Make sales to cover household upkeep and taxes. |
| 38 | Perrin | set | Earn enough to cover what I owe. *(the only goal from a need — money)* |
| 41 | Marta | set | Run the bakery through morning trade, pay Tova fairly, and get more grain… |
| 49 | Delia | set | Register pharmacy business at Government Complex… |
| 52 | Marta | retired, set | See Liora fed and off to school, then manage the bakery through midday trade |
| 60 | Junia | retired, set | Sell flour inventory to cover taxes and upkeep. Source bread from the bakery. |
| 64 | Delia | retired, set | Earn enough to register pharmacy (1,235 sim needed) while providing Hester's tincture today |
| 65 | Ainsley | set | Buy surplus wheat from Wren, sell at market, return profit to household |

Eight goals set by five residents; seven came from the persona, one from a need.

### 9.2 Intentions

24 set by 12 residents · **12 fulfilled** · 6 dropped · 5 still open at the end.
Ezra set five in one morning, each to go somewhere and check something, and
fulfilled four. Among those dropped: Junia's *"Check stock levels and post prices
for the day"*, Marta's *"Check the bakery till… then get grain to bake more
bread"*, Delia's *"Secure herb supply from Wren Thorne"*.

### 9.3 Beliefs

23 belief updates, 19 standing at the end. Every one is the world correcting a
resident about a place: *"move does not work here: could not afford"*, *"mine does
not work here: must be at ore_outcrop"*, *"buy does not work here: must be at…"*.

---

## 10. Memory and learning

### 10.1 The memory stream

1,597 memories: **1,315 observations** (importance 3.2), 165 plans (6.0), 117
reflections (8.0). **1,250 observations (95%) are raw tool echoes** —
`I did world:rest: {"location_id": …}` — written by
`engine/src/sim/memory_stream.py:284`: `rest` 328, `move` 231, `say` 230,
`look_around` 161; median 212 characters. None appears in a perception (0 of
261).

| Resident | Observations | Plans | Reflections |
|---|--:|--:|--:|
| Marta Thorne | 164 | 9 | 4 |
| Delia Okonjo | 134 | 10 | 5 |
| Tobias Holloway | 121 | 12 | 8 |
| Bram Holloway | 118 | 9 | 7 |
| Rosalind Vance | 92 | 10 | 3 |
| Junia Vance | 79 | 9 | 6 |
| Corwin Thorne | 78 | 8 | 6 |
| Liora Thorne | 67 | 11 | 4 |
| Kem Okonjo | 61 | 8 | 6 |
| Aldous Vance | 50 | 6 | 7 |
| Silas Vance | 43 | 5 | 6 |
| Ezra Okonjo | 42 | 4 | 4 |
| Idris Thorne | 40 | 7 | 6 |
| Father Anselm | 40 | 5 | 8 |
| Ainsley Holloway | 36 | 7 | 6 |
| Wren Thorne | 32 | 6 | 5 |
| Tova Okonjo | 31 | 12 | 8 |
| Hester Vance | 31 | 10 | 8 |
| Perrin Holloway | 28 | 9 | 6 |
| Nell Holloway | 28 | 8 | 4 |

### 10.2 What the learning centre consolidated

All 20 learning calls ran between 15:33:53 and 15:34:29 — the pre-halt drain.
The waking interval counts ticks a resident is *dispatched*
(`engine/src/sim/learning_gate.py`), and no resident reached 18 of its own.

| Resident | Knowledge | At 1.0 | Episodes | People | Signals pending | The episode it wrote |
|---|--:|--:|--:|--:|--:|---|
| Liora Thorne | 18 | 2 | 1 | 3 | 13 | "Walked to Holloway home and asked Perrin about the 400 sim debt and unspoken trouble" |
| Perrin Holloway | 17 | 4 | 1 | 0 | 13 | "Couldn't afford to talk, walk, or rest after giving 395 sim to the household" |
| Junia Vance | 17 | **7** | 1 | 8 | 4 | "Harvested wheat, milled flour, and told mother Kem undercuts our mill rate." |
| Tova Okonjo | 17 | 5 | 1 | 0 | 0 | "Milled Kem's wheat and stocked the flour; he still owes for it" |
| Father Anselm | 17 | 4 | 1 | 0 | 0 | "Spent the morning seeking Hester Vance to ask about the Holloway-Thorne boundary marker placement." |
| Idris Thorne | 16 | 1 | 0 | 10 | **32** | — |
| Silas Vance | 16 | 1 | 1 | 6 | 0 | "Felled timber for planks, but couldn't sell them and found no buyer" |
| Aldous Vance | 15 | 5 | 1 | 10 | 13 | "Mother avoided my question about household accounts, then tried to work timber in the street" |
| Ezra Okonjo | 14 | 2 | 1 | 5 | 0 | "Walked to the ore outcrop, confronted Bram about the failing copper seam…" |
| Bram Holloway | 13 | 3 | 1 | 0 | 0 | "Admitted to Ezra that the copper seam is dying, and mined three ore" |
| Delia Okonjo | 13 | 2 | 1 | 0 | 7 | "Found Wren Thorne at farmland and explained I needed willow bark for Hester's tincture…" |
| Hester Vance | 12 | 1 | 1 | 5 | **33** | "Asked Silas and Aldous for food, prayed, then broke news to Delia about the boundary marker" |
| Marta Thorne | 10 | 3 | 1 | 0 | 26 | "Paid Tova 150 sim for tending shop, milled our own wheat, and stocked 5 loaves" |
| Ainsley Holloway | 8 | 1 | 1 | 0 | 13 | "Spoke to Wren Thorne and Kem Okonjo at the wheat field about buying surplus and Marta's debt." |
| Wren Thorne | 8 | 1 | 1 | 0 | 26 | "Harvested wheat and sold two sheaves to Junia Vance for 22 sim" |
| Corwin Thorne | 8 | 0 | 1 | 0 | 4 | "Felled ten timber, sawed them into fourteen planks…" |
| Nell Holloway | 7 | 1 | 0 | 0 | 9 | — |
| Rosalind Vance | 6 | 0 | 0 | 0 | 13 | — |
| **Tobias Holloway** | **0** | 0 | 1 | 0 | 4 | "Got Corwin's son Idris to take me on as a forest apprentice; cut 7 timber to pay my keep." |
| **Kem Okonjo** | **0** | 0 | 1 | 0 | 12 | "Offered Tova work at the supply store to earn her 150 sim share instead of bakery wages." |

232 knowledge rows · 43 at confidence 1.0 (19%) · 0 saying "the user" · 7
recording tool trivia · 0 naming dollars.

### 10.3 Skills practised

Proficiency after a morning: Marta farming 59 (21 repetitions), artisanry 29,
cooking 29 · Corwin forestry 43 (11) · Idris forestry 37 (8), artisanry 34 (7) ·
Wren farming 32 (6) · Rosalind artisanry 29, farming 29 · Bram mining 26 · Tobias
forestry 26 · Junia farming 26 · Aldous forestry 22 · Ezra artisanry 18 · Kem
farming 18. No skill was written with `skillify`.

### 10.4 Recorded evaluations

`eval_metrics` holds seven rows for this run: programs adopted by 0 residents,
ceilings held (1.0), acts per decision undefined; character P1 orchestration
leaks 0, **P2 first-person leaks 1** (Tobias: *"The user wants me to continue
playing as Tobias Holloway"*), P3 not measured.

---

## 11. Perception

Sections present in the 261 perceptions:

| Section | Turns | Share |
|---|--:|--:|
| Energy line | 261 | 100% |
| Owed today | 261 | 100% |
| The town | 254 | 97% |
| Recently | 236 | 90% |
| **You have no food** | **231** | **89%** |
| Said to you, and not yet answered | 213 | 82% |
| You asked, and have not been answered | 210 | 80% |
| With you, and nobody else | 193 | 74% |
| You have found that (corrections) | 95 | 36% |
| You set out at (an intention) | 58 | 22% |
| You run something of your own | 56 | 21% |
| You are trying to (a goal) | 41 | 16% |
| You are out of touch | 17 | 7% |
| You stopped | 16 | 6% |
| You did what you set out to do | 12 | 5% |

Median 2,823 characters, p90 3,737, longest 4,777.

---

## 12. Every resident

| Resident | Trade · age | Calls | Spend | Turns | At limit | No action | Actions | Refused | Mid-walk rests | Identical repeats | Sim start → end | Spoke | Top tools |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| Marta Thorne | baker · 45 | 118 | $0.97 | 15 | 6 | 5 | 164 | **18** | 0 | 36 | 5,200 → 6,968 | 23 | move 31, look_around 22, harvest 22, say 21 |
| Delia Okonjo | pharmacist · 49 | 112 | $0.89 | 14 | 3 | 5 | 136 | 11 | 36 | 43 | 3,900 → 3,707 | 17 | rest 45, move 19, say 17 |
| Tobias Holloway | day labourer · 19 | 112 | $0.85 | 12 | 4 | 0 | 121 | 1 | 45 | 55 | 1,200 → 942 | 24 | rest 58, say 24, move 14 |
| Rosalind Vance | miller · 39 | 108 | $0.73 | 13 | **7** | 5 | 93 | 1 | 6 | 21 | 4,100 → 3,937 | 12 | move 31, look_around 22, say 12 |
| Liora Thorne | student · 17 | 93 | $0.56 | 14 | 2 | 2 | 67 | 1 | 14 | 19 | 500 → 423 | 10 | rest 21, look_around 10, move 10 |
| Junia Vance | grocery clerk · 20 | 90 | $0.61 | 14 | 3 | 4 | 79 | 12 | 0 | 6 | 700 → 410 | 13 | move 15, say 13, look_around 12, craft 8 |
| Kem Okonjo | trader · 24 | 85 | $0.53 | 12 | 2 | 2 | 61 | 1 | 7 | 11 | 1,500 → 1,426 | 18 | say 16, move 16, look_around 13 |
| Corwin Thorne | forester · 22 | 82 | $0.55 | 12 | 3 | 3 | 78 | 2 | 22 | 30 | 1,800 → 1,512 | 12 | rest 27, move 15, say 12, fell_timber 11 |
| Bram Holloway | miner · 44 | 72 | $0.52 | 14 | 4 | **8** | 118 | 2 | **44** | **79** | 3,200 → 3,239 | 7 | **rest 78**, move 14, say 7 |
| Idris Thorne | forester · 68 | 70 | $0.42 | 12 | 1 | 3 | 40 | 3 | 0 | 6 | 2,900 → 2,541 | 10 | move 10, say 8, craft 7, fell_timber 6 |
| Aldous Vance | student · 15 | 70 | $0.39 | 14 | 2 | 6 | 50 | 5 | 19 | 23 | 200 → 80 | 9 | rest 24, say 9, fell_timber 8 |
| Ezra Okonjo | artisan · 52 | 62 | $0.37 | 12 | 1 | 5 | 42 | 2 | 4 | 5 | 3,400 → 3,283 | 8 | move 12, say 8, rest 6, set_intention 5 |
| Silas Vance | sawyer · 41 | 61 | $0.38 | 12 | 1 | 4 | 43 | 1 | 10 | 11 | 2,600 → 2,465 | 7 | rest 14, move 7, say 7 |
| Wren Thorne | farmer · 47 | 60 | $0.33 | 13 | 1 | 4 | 32 | 0 | 6 | 5 | 3,800 → 3,760 | 11 | say 11, rest 6, keep_doing 6 |
| Perrin Holloway | student · 16 | 60 | $0.32 | 14 | 0 | 6 | 28 | 4 | 0 | 2 | 400 → **4** | 6 | say 7, look_around 4, move 4 |
| Father Anselm | office administrator · 63 | 59 | $0.33 | 12 | 0 | 6 | 41 | 1 | 10 | 14 | 1,800 → 1,750 | 9 | rest 10, look_around 9, say 9 |
| Hester Vance | artisan · 74 | 58 | $0.30 | 13 | 1 | 5 | 31 | 0 | 0 | 4 | 2,200 → 2,134 | 9 | say 9, rest 9, move 6 |
| Nell Holloway | artisan · 71 | 56 | $0.33 | 13 | 1 | 5 | 28 | 0 | 0 | 2 | 2,600 → 2,561 | 10 | say 10, look_around 5, move 5 |
| Tova Okonjo | student · 17 | 54 | $0.35 | 14 | 0 | 6 | 31 | 0 | 3 | 3 | 300 → 711 | 13 | say 13, rest 7, look_around 5 |
| Ainsley Holloway | trader · 42 | 53 | $0.30 | 12 | 0 | 7 | 36 | 3 | 0 | 1 | 4,600 → 4,763 | 8 | say 8, describe_person 5, set_intention 4 |

*Calls and spend are from the trace; turns and actions from the session logs.*

---

## 13. Tick by tick

Game time is 06:00 plus ten minutes a tick.

| Tick | Game | Calls | $ | Residents | What happened |
|--:|---|--:|--:|--:|---|
| 37 | 06:10 | 64 | 0.33 | 7 | first turns (15:10:30) · Marta draws 620 from the bakery till · **Perrin transfers 395 of 400 to Bram** |
| 38 | 06:20 | 102 | 0.50 | 13 | Ainsley gives Perrin 10, Bram gives 15 · Tova comes of age |
| 39 | 06:30 | 46 | 0.24 | 6 | |
| 40 | 06:40 | 22 | 0.12 | 5 | |
| 41 | 06:50 | 89 | 0.56 | 13 | Marta sets her bakery goal |
| 42 | 07:00 | 63 | 0.36 | 10 | |
| 43 | 07:10 | 43 | 0.27 | 9 | |
| 44 | 07:20 | 54 | 0.32 | 8 | Junia posts flour at 18, holding none — the first price |
| 45 | 07:30 | 61 | 0.41 | 8 | Bram trades Ezra 1 copper ore for 50 · Bram transfers 400 to Ainsley |
| 46 | 07:40 | 70 | 0.48 | 10 | Junia posts wheat 14/11 · Ainsley transfers 200 to Bram · Marta draws 200 · Junia refunded 174 |
| 47 | 07:50 | 33 | 0.20 | 7 | Hester pays 10 into the treasury |
| 48 | 08:00 | 38 | 0.26 | 7 | |
| 49 | 08:10 | 85 | 0.60 | 12 | Delia sets her pharmacy goal |
| 50 | 08:20 | 40 | 0.29 | 8 | |
| 51 | 08:30 | 37 | 0.28 | 7 | Marta posts bread at 15 · pays Tova 150 |
| 52 | 08:40 | 43 | 0.36 | 7 | Marta pays Tova 150 · draws 500 and 800 · resets her goal |
| 53 | 08:50 | 24 | 0.17 | 10 | |
| 54 | 09:00 | 24 | 0.18 | 7 | |
| 55 | 09:10 | 12 | 0.09 | 7 | |
| 56 | 09:20 | 34 | 0.26 | 10 | Wren sells Junia 2 wheat at her bid, 22 |
| 57 | 09:30 | 36 | 0.32 | 8 | |
| 58 | 09:40 | 18 | 0.15 | 5 | |
| 59 | 09:50 | 45 | 0.37 | 10 | |
| 60 | 10:00 | 15 | 0.14 | 6 | Junia resets her goal |
| 61 | 10:10 | 21 | 0.21 | 6 | |
| 62 | 10:20 | 41 | 0.36 | 10 | |
| 63 | 10:30 | 36 | 0.34 | 7 | Marta pays Tova a third 150 · draws 500 · refunded 110, 57, 11 |
| 64 | 10:40 | 32 | 0.33 | 8 | Delia resets her goal to 1,235 sim short |
| 65 | 10:50 | 25 | 0.21 | 10 | Ainsley sets a goal to trade wheat |
| 66 | 11:00 | 17 | 0.17 | 3 | spend within 80¢ of the cap — the pre-halt drain starts (15:33:07) |
| 67 | 11:10 | **157** | **0.88** | **20** | learning batches for all 20 · 11 answer within 60 s, 9 time out |
| 68 | 11:20 | 37 | 0.30 | 13 | Junia buys 12 bread from Marta for 144 · the broker halts at $10.01 |
| 69 | 11:30 | — | — | — | runner stops at $10.04 (15:34:33) · final checkpoint |

---

## 14. Anomalies and what the trace cannot tell

- **`search_memory`: 4 calls, no recorded result.** It is an engine-side tool
  (`engine/src/sim/memory_query.py`), and the session log attaches a result only
  when the engine emits a matching result event (`session_log.py`). Whether the
  four searches answered is unknown — itself a recording gap.
- **Kem's learning batch returned and left nothing.** His call succeeded at
  15:34:11, after the drain's 60 s timeout; he holds 0 knowledge and 12 pending
  signals. Tobias's 0 is explained — his batch was refused by the cap.
- **Refunds do not name their tool in the trace.** The memo carries it in the
  ledger; the trace row does not, so which failed action each refund reversed is
  not recoverable from the database.
- **Tool definitions are stored as names only** in 894 of 1,225 resident request
  bodies, so tool payload per call cannot be measured from the trace.
- **The ledger burned 996¢ against the broker's $10.04** — whole cents per call
  in the ledger, nano-dollars at the broker.
- **Needs levels at the end** (for example food 0.0 and energy 0.0 for Bram) are
  in the checkpoint; their scale is not established here and they are not
  interpreted.
- **Idris holds 8 furniture and made 3** this run; the difference is presumably
  his seeded stock and is not verified here.

---

## 15. Next directions

In order of what the evidence says they are worth.

1. **Make waiting free.** A `rest` while walking, or an identical repeat in one
   tick, should cost nothing and say the walk continues when the turn ends.
   `rest_handler` in `app/world/tools/primitives/living.py`, folded with the rule
   `app/world/tools/repeated_calls.py` already applies to zero-tile moves.
   *Evidence:* 226 mid-walk rests; limit turns are 45% of turn spend.
   *Measure:* mid-walk rests, limit-turn share, ticks per minute.
2. **Open the purchase path.** Three things stood between a hungry resident and a
   stocked shelf: a `buy` naming a shop's owner did not reach the shop's shelf, a
   buyer cannot open a `trade`, and a price can be posted with nothing to sell.
   `app/world/tools/primitives/` (buy, trade, post_price). *Evidence:* `buy` 0 of
   9 while shelves held 20 bread and 89% of perceptions said "You have no food".
   *Measure:* completed purchases, meals eaten.
3. **Stop the record block being imitated.** Move the past turn's record out of
   the history the model continues and into the next perception.
   `engine/src/sim/session_log.py` (not frozen). *Evidence:* 91 no-action turns;
   46% of outputs copy the block. *Measure:* no-action turns, record echoes.
4. **Cut what every call re-reads.** Input is 95% of spend and grew 73% in 32
   ticks. Bound the history and summaries a turn carries; check whether Bedrock
   prompt caching is available for Kimi; serve full tool schemas by use (`harvest`,
   `sell`, `trade`, `post_price`) rather than alphabetically (`arrest`,
   `auction_land`). `app/world/tools/registry.py`, the engine's bounded history.
   *Measure:* input tokens per call by tick.
5. **Write outcomes to memory, not echoes.** Skip `rest`, `look_around` and
   repeated moves; keep what changed. `engine/src/sim/memory_stream.py`.
   *Measure:* echo share; what learning consolidates.
6. **Age the unanswered backlog** — a design call, since the line exists so an
   obligation is felt. `app/world/retrieval_depth.py`.
7. **Give the working-memory summariser room** — 26% of its summaries were cut.
8. **Record what is missing:** results for engine-side tools, and the tool name
   on refund rows.
9. **Then run `genesis_20` again.** $10 after 1–4 shows whether the same money
   buys more of the day; $30 or a smaller per-turn budget reaches the day
   boundary. It is also `do_in_order`'s first live test — and still waiting on two
   decisions: the learning interval (per resident or world ticks), and whether
   speech costs sim.
