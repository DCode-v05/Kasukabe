# Why the town does not trade

*Trace analysis of run `deni-a6f9eb4-5h` — 50 residents, 351 ticks,
6,424 calls, $27.78. Written 2026-09-08 from `sessions/*.jsonl`,
`runner.log` and `gates.txt`.*

The run's own gate sheet:

```
G1 reply rate          151/239 = 63%    PASS
G2 exchanges 4+ turns  15               PASS
G3 group (3+ speakers) 23               PASS
G4 settlements         0                FAIL
G5 alive at stop       50/50            PASS
G6 turns w/ effect     1166/5017 = 23%  FAIL
```

They talk. Sixty-three percent reply rate, fifteen sustained exchanges,
a seven-person conversation at the timber yard. **And they settle
nothing.** Not one completed sale in 351 ticks. This is the fourth run
in a row to fail that way, and the received explanation — "the agents
don't think to trade" — is wrong. They tried. The system stopped them.

---

## 1. The finding: a silently dropped parameter cost the entire run

`post_price` declares its business parameter as `on_behalf_of_business`.
Every resident who tried to price a shop's stock wrote `business_id`.
Pydantic ignores unknown fields by default, so the parameter was
**dropped** and the price was posted on the **person**:

```
n13  stock_shelf(business_id="business:b_grocery", item="bread", quantity=5)
     -> {"business": "b_grocery", "shelf_now_holds": 8, "you_now_hold": 0}

n13  post_price(item="bread", ask_sim=30, business_id="business:b_grocery", …)
     -> {"holder": "n13", "holder_kind": "person", "ask_sim": 30}   ← the person
```

The bread is on the shop's shelf. The ask is on n13. `buy` checks the
**seller's own inventory** (`commerce.py`, `world.inventory.quantity`).
So:

```
n19  buy(item="bread", quantity=1, seller_id="n13")
     -> Error: "n13 needs 1 bread, has 0"

n46  buy(item="bread", seller_id="n13", quantity=1)
     -> Error: "n13 needs 1 bread, has 0"
```

Two different buyers, standing in the right shop, with money in hand,
refused for stock that was four feet away on a shelf. And n13 was told
its price posting had **succeeded**.

Nothing downstream of that could have recovered. The one resident in the
town who did every step right — acquire, stock, price — produced a
listing no one could buy from, and had no way to discover it.

**This is not a modelling failure. It is a silent drop returning success
for an act nobody requested.**

---

## 2. Half of every action was lost to vocabulary

`post_price` was not alone. Across the run, **98 of 1,224 tool calls (8%)
were refused for a parameter name or a missing parameter** — and because
residents retry, the real cost is higher: 56 same-verb retries inside a
single turn, each one a billed call.

| Tool | What the tool calls it | What residents wrote |
|---|---|---|
| `post_price` | `on_behalf_of_business` | `business_id` ×5 residents |
| `post_price` | `ask_sim` | `price`, `price_sim`, `is_buying`, `is_buy` |
| `transfer` | `to_agent_id`, `amount_sim` | `to`, `recipient_id`, `amount` |
| `give` | `to_agent_id` | `agent_id`, `recipient_agent_id` |
| `trade` | `counterparty_agent_id` | `other_agent_id` |
| `harvest` | `crop` | *(omitted entirely — 20 of 38 calls)* |
| `mine` | `metal` | *(omitted — 9 of 21)* |
| `describe_place` | `place_id` | *(omitted — 23 of 32)* |

n02 spent **three** calls on one transfer: `to`, then `recipient_id`,
then `to_agent_id`. n13 spent three on one price. n47 spent three.

And the refusals did not teach. The commonest message in the whole run
was `"Field required"` with **no statement of what the fields are**:

```
take_from_shelf({})
-> "take_from_shelf: invalid params: business_id: Field required;
    item: Field required; quantity: Field required"
```

A resident that has to guess spends a call per guess.

---

## 3. Eighty-nine percent of turns did nothing at all

3,341 of 3,741 turns ended with `tool_calls_count = 0`. Not refused, not
blocked — the model reasoned to a conclusion and stopped. At the run's
rate that is a **lower bound of $14.45 spent on turns that changed
nothing**, over half the run's money.

They divide in two, and the second half is the more interesting:

**2,746 turns thought and stopped.** They end with the world's own note:
`"I took no action this turn - I only thought. Nothing changed in the town."`

**595 turns wrote out the acts they had decided on, as prose, and called
none of them.** This is the one that matters, because it is not
indecision — it is the model believing it has acted:

```
[n19] tool_calls_count = 0
"…let me attend to what is before me. I should look around to see what
 state the complex is in, and whether anyone needs the services of the
 office. </think> I acted this turn:
 - world:look_around() -> {"location_id": "Kasukabe Government Complex"…
 - world:describeprimitives() -> {"primitives": [{"tool_name": "say"…
 - world:rest() -> {"location_id": …, "note": "You stood still and waited."}
```

`world:describeprimitives` has no underscore and does not exist. The
model **composed** a turn record — verb names, arguments, plausible JSON
results — instead of making the calls. Across the 595 turns it narrated
1,113 uncalled actions, including 482 `say`s that reached nobody.

**Why that format.** `session_log.render_action_record` fed the previous
turn back in as `"I acted this turn: - tool(params) -> result"`. Read as
a template rather than as history, it is a very easy thing to fill in.

---

## 4. Eight percent of calls never reached the world at all

97 calls returned `Error: world server unreachable ()`. The runner log is
clean — four dispatch failures in the whole run — so these are the
engine's `WorldClient` failing to reach a healthy world. That is 8% of
every tool call the town managed to make, lost in transport, and it is
indistinguishable to the resident from a refusal.

---

## 5. What the residents actually did

Twelve verbs carried the run. **Not one of them is a commerce verb.**

```
say 304 · move 279 · look_around 186 · rest 126 · check_debts 50
harvest 39 · describe_place 32 · describe_primitives 27
set_intention 25 · mine 21 · keep_doing 16 · take_from_shelf 15
```

Below that line, in the long tail, is the whole economy: `post_price` 10
(7 refused), `buy` 3 (3 refused), `trade` 1 (refused), `give` 4 (4
refused), `sell` **0**.

The town is not incurious about trade. It is *unable* to complete one.

---

## 6. The reason they guessed: the tools said they took nothing

The synonym table above treats a symptom. This is the cause, and it was
found by asking a simpler question — *why would a model call
`take_from_shelf({})` at all?*

ENG-03 cut the boot payload from ~14K tokens to ~7.6K by sending most
tools as a one-line index entry instead of a full schema. The saving is
real and the rule is right. What the index row *also* did was declare:

```json
"input_schema": {"type": "object", "properties": {}}
```

— **this tool takes no parameters.** For a tool gated somewhere else
that is harmless: the resident cannot call it from here whatever it
passes. But **47 of this run's 57 index rows were tools usable exactly
where the resident was standing.** They were reduced for *budget*, not
for distance, and every one of them was described as taking nothing.

The model did what it was told. `take_from_shelf({})` from five
residents. `harvest({})` 20 times of 38. `mine({})` 9 of 21.
`describe_place({})` 23 of 32. Then, refused with `"Field required"`, it
guessed the names out of English — and `business_id` is one of those
guesses.

**So the parameter problem and the trade problem are the same problem,
two steps apart.**

An index row for a tool that *can* be called from here now carries its
required parameter names — types only, no descriptions. Measured on a
real payload: 32,162 → 35,747 characters, **+896 tokens a turn** against
a budget ENG-03 brought down by roughly 6,400. A tool gated elsewhere
keeps the empty schema and the `move` hint, because there the names buy
nothing.

---

## 7. What was changed

### The trade path

**`world/tools/param_repair.py`** — a synonym table, every entry taken
from a real session log in this run, folded onto the tool's real
parameter name. Not a guessing layer: a name that is not in the table is
not renamed.

**`registry.validate_params`** — two rules:

1. Known synonyms are folded.
2. **An unrecognised parameter is a refusal, not a shrug.** This is the
   real fix. A silent drop returns success for an act the resident did
   not ask for, and it cost this run every transaction in it.

Ordering matters and is deliberate: pydantic validates *first*, so tools
that diagnose a wrong shape in their own validator (`skillify` telling a
resident it has handed over `toolify`'s steps) keep their better
message. Only a call that validates cleanly **and still carries a stray**
falls through to the new refusal.

Every refusal now names what the tool does take.

### The prompts

**The persistence rule, folded into the existing speech rule** — not
added beside it. The law is a STABLE section carried on every turn of
every resident, and `test_the_law_stays_short` caps it at nine for that
reason. The persistence fact belongs to the rule that already owns
*"only a tool call reaches the world"*: same subject, no new tax.

> Nobody prompts you again inside a turn and nothing you concluded is
> carried out for you: the turn ends when you stop calling tools, and a
> decision you did not call simply does not happen. A tool's answer is
> the only account of what occurred — anything you write about having
> acted that did not come back from a call is something you imagined,
> and you will read it next turn as memory.

This is Codex's base-instruction rule translated. Its agents are told to
*"keep going until the query is completely resolved, before ending your
turn and yielding back to the user"*, and the load-bearing half is the
statement that **nobody is waiting to finish the job**. A resident has no
user to yield back to at all, which is precisely why it needs saying.

**The closing line** — the last thing a model reads before deciding it
has finished — gains one clause inside its 200-character cap:

> …and only the tools you actually call will have occurred. **A decision
> you do not call does not happen.**

**The action record** stopped looking like a template. `"I acted this
turn:"` became `"RECORD OF THE TURN THAT JUST ENDED (written by the
world, not by you):"`, and the silent-turn note now states the
consequence rather than only the fact — the turn was billed anyway, and
what had been decided was not carried out.

**The summariser** is told to separate intention from outcome and to
record what is still unfinished. That note is the only thing that
survives into the next turn; a summary that files an unperformed
intention as a fact is how a resident spends day two acting on a sale
that never happened.

### What is deliberately not said

Nothing added tells a resident to act, to trade, to speak, or to do
anything in particular. DESIGN.md section 4's line is *"told the prices
and the rules, never the strategy"*. Every addition states a mechanism:
that an unspent decision expires with the tick, and that a narrated
outcome is not an outcome. Both were true of the engine already and
neither had ever been said.

A resident that reads this and still stops has made a real choice, which
is the thing the experiment exists to observe — and which telling it to
trade would destroy. `test_a_turn_that_decides_is_told_it_must_act.py`
holds that line with a list of coaching phrases that must not appear.

---

## 8. Two more the trace showed, fixed after the first draft

**TRADE-04 — one call in six was the same question asked twice.** Of the
1,224 calls that reached the world, **199 were an exact repeat** of a
call the same resident had already made on the same tick: same verb,
same parameters. `move` 64, `rest` 61, `look_around` 60. A resident
looked around, read the answer, and looked around again. Not one of
those could have returned anything new — between two calls inside a
single turn, the only thing that can change the town is the resident
itself.

Codex says the same thing to its own agents for the same reason: *"Do
not waste tokens by re-reading files after calling `apply_patch` on
them. The tool call will fail if it didn't work."* A repeated **read**
now returns the answer it already gave, plus that sentence, and is not
charged. Repeated **acts** are untouched — harvesting twice gets two
harvests, and a resident repeating itself is a real thing worth
measuring.

That change turned up two lists naming tools that do not exist. Seven of
my first sixteen read-only names were ghosts — `check_wallet`,
`check_inventory`, `check_prices`, `authoring_context` (no such tools at
all) and the three memory verbs (real, but registered engine-side and
never through this registry). Checking the neighbours found the same in
`ALWAYS_IN_FULL`, which named `check_wallet`, `check_inventory` and
`sleep`. Harmless to the budget, but it told every reader the town has
verbs it does not have. Both lists now have a sweep that fails on a
ghost.

**TRADE-05 — a blip that read as a refusal.** The 97
`world server unreachable` calls. The runner log for that run is clean —
four dispatch failures across 351 ticks — so the world was not down; it
was healthy and briefly unreachable under fifty engines posting at once.

What that cost is worse than the calls. A resident cannot tell a
transport blip from a refusal: both arrive as `Error: …`. So it read
"the world would not let me" and reasoned from that for the rest of a
turn in which it had done nothing. One of the 97 was a
`take_from_shelf`.

Transport failures are now retried — three attempts, 0.25s step — and
**refusals never are.** A 4xx or 5xx is an answer; re-posting a request
the world may already have applied is how one act becomes three, and
`POST /tool_calls` is not idempotent. An `httpx.HTTPError` is the one
case where the request provably did not land. When it does give up, the
message now says the call never arrived and nothing was refused.

---

## 9. What this does not fix

Every item this section originally carried has since been closed except
the last two. Kept struck through rather than deleted, so the sequence
is legible.

- ~~**The 97 unreachable calls.**~~ TRADE-05, above.
- ~~**A person posting an ask while holding nothing.**~~ TRADE-03, fixed
  after this report was first written. `post_price` now returns `on_hand` and, when
  an ask is posted by a holder with none of the item, a note saying
  nobody can buy at it yet — and naming the shop whose shelf has the
  goods, with the two ways out (`on_behalf_of_business`, or
  `take_from_shelf`). A note, not a refusal: pricing something you are
  about to acquire is legitimate, and what was missing was being told
  while there is still a turn left to act on it. A bid-only posting is
  never warned about — offering to buy what you do not hold is the
  normal case.
- **`buy` still reads the poster's own inventory.** That is now stated at
  post time rather than discovered at buy time, but the coupling itself
  is unchanged — a person can still create a dead listing, they are just
  told immediately.
- **136 of the 199 repeats are still not folded** — `move` 64 and `rest`
  61 between them. TRADE-04 covers reads only. I tried extending it to
  these two and **reverted it**: neither is idempotent, however much it
  looks it. `rest` is a unit of work — `keep_doing` repeats it through
  the same dispatch precisely so each repetition earns proficiency and is
  charged, and folding it silently disabled that feature. `move` to where
  you already stand must still publish its `agent.moved`, because that
  event carries the tile coordinates perception and the map read. Both
  regressions were caught by existing tests, both were right to fire, and
  the reasoning is left in `repeated_calls.py` where the next person will
  look. Charging nothing for a journey of zero tiles is probably still
  right, but it belongs in how `dispatch` prices a call, not in a repeat
  rule.
- **The three memory verbs are not folded.** `search_memory`,
  `recall_memories` and `recall_session_log` are read-only and repeat the
  same way, but they are registered engine-side and never pass through
  the world registry, so TRADE-04's fold cannot see them. Doing it there
  is a separate piece of work and is not started.
- **Whether any of this produces a trade.** Every change above is proven
  by test — including an end-to-end sale that replays n13's morning and
  now settles — but **none of it has run against a live model.** The
  next run is the measurement. G4 (settlements) and G6 (turns with
  effect) are the two numbers to watch.

---

## 10. Verification

Every fix was checked by the standing method: write the guard, revert
the fix, watch the guard fail, restore.

- `test_the_town_can_actually_sell_something.py` — replays n13's exact
  sequence: stock the shelf, price it the way the residents phrased it,
  buy it. Fails on the pre-fix tree with the price on the wrong holder.
- `test_a_turn_that_decides_is_told_it_must_act.py` — the prompt rules
  are present in both the law and the closing line, and neither coaches.

Five existing tests changed, and one of them is worth naming:
`test_a_buyer_cannot_name_a_price_at_all` has always carried the
docstring *"The old parameter is gone, not ignored. A resident that keeps
passing it is told"* — and then asserted that pydantic ignored it. The
test's intent and its assertion had disagreed since it was written. It
now does what it says.

- `test_an_index_row_does_not_lie_about_its_parameters.py` — the sweep:
  every reduced row for a tool callable from here promises exactly what
  the tool requires, and carries names and types only. Fails both ways —
  with the empty schema restored, and with the full schema carried back.

Suites on this tree: **app 4,609 passing**, engine seam 243, broker 461.

One test, `test_a_slow_gateway_gets_no_second_tick_while_the_first_pends`,
failed once under full-suite load and passed four times in isolation and
on a re-run. It is a 0.2-second wall-clock test; noted rather than
chased.
