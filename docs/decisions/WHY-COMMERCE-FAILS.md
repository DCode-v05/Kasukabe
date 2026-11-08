# Why commerce fails, and how long a turn should be

*Written 2026-09-18 from the 50-resident run's own turn records
(`docs/runs/teambox-50-100usd-2026-09-16-report.md`, 56,623 lines) and from the
code paths named below. Every count here is read out of that report; nothing is
estimated.*

---

## The finding: there is no market, only prices you can bump into

`buy` requires the **exact seller** to have posted an ask for the **exact item**
(`world/tools/primitives/commerce.py:195` — `world.posted(seller_id, params.item)`).

A resident can only see prices **where it is standing**:
`run_genesis.prices_for()` is `describe_prices(world, location_of(agent))`
(`world/run_genesis.py:8678`), and the perception's board is written by
`retrieval_depth.format_price_board`, which is silent when nothing is posted at
that spot.

In the 50-resident run:

| Measure | Count |
|---|--:|
| Times the perception's price board appeared (`"Priced where you stand"`) | **0** |
| `look_around` answers carrying `prices_here` | 167 |
| …of those, "Nothing is priced here." | **122 (73%)** |

So no resident was ever shown, in the ordinary course of a turn, that anything
was for sale anywhere. The only way to find a price was to spend a
`look_around`, and three times in four that returned nothing.

### What the residents do with that

They guess — they ask whoever they are standing next to:

| Verb | Attempts | Refused | The dominant reason |
|---|--:|--:|---|
| `buy` | 178 | **140 (79%)** | "…has not posted a price for potato" — **87** |
| `trade` | 168 | **108 (64%)** | "you hold 0 potato, not 1" — offering the thing they just failed to buy |
| `sell` | — | 40 | "…has not posted what they pay for timber" — **32** |

`buy`'s refusal text points at `trade`, and `trade` then refuses them for not
holding the goods they were trying to obtain. **248 paid model calls bought
nothing.** A third failure mode sits underneath: shopkeepers post prices for
stock they do not hold (`b_cafe needs 1 rye bread, has 0` ×16), which a resident
reported in-world before any instrument did:

> *"You've posted prices for potatoes and wheat, but when I try to buy them, the
> system says you have none. Do you actually have stock available, or are those
> just placeholder prices?"* — Vikram Ingram, tick 36

### The fix this implies

Make offers **visible beyond arm's reach**: a town-wide view of who is selling
what, at what price, and whether they hold it. Either in the perception or as a
verb a resident can call. Two supporting changes:

1. When `buy` fails for want of a post, name **where that item is priced**
   instead of listing what this particular seller happens to have priced.
2. Do not accept a posted ask for an item the seller holds none of, or mark it
   as out of stock on the board.

Target for the next run: `buy` refusals under 20%, from 79%.

---

## How long a turn is, and how much of it is failure

Reconstructed from the report's turn records: **1,957 turns, 9,488 tool calls.**

| Measure | Value |
|---|--:|
| Tool calls per turn | median 4 · mean 4.85 · p90 10 |
| Turns containing at least one refusal | 363 (19%) |
| Calls per turn — no refusal | median 3 · mean 4.42 |
| Calls per turn — with a refusal | median 6 · **mean 6.73 (+52%)** |
| Calls made after a turn's first refusal | 1,174 (**12% of all calls**) |

**It is not a retry loop.** Of 442 refusals that had a next action:

| What happened next | Count | Share |
|---|--:|--:|
| A different verb | 379 | 71% |
| The same verb, different parameters (another seller) | 53 | 10% |
| The same verb, identical parameters | **10** | **2%** |
| Nothing — the turn ended | 90 | — |

The most common single move after a failed purchase is `say` (149) — the
resident goes and *asks a person*, which is the rational response to a town with
no market list, and is the same finding as the section above from the other
direction.

So refusals stretch the long turns rather than create the volume: a turn where
nothing fails is already three actions.

### The ceiling is already a knob

`world/call_budget.py` bounds a resident's tick to between 2 and 12 model calls
and scales it with the wallet (5% of the balance ÷ $0.0075 a call). Both bounds
are environment-settable, and as of `c203735` both are recorded in the run
manifest — before that, a run at 5 and a run at 12 wrote down the same thing
about themselves.

Measured effect of lowering `KASUKABE_MAX_CALLS_PER_TICK`:

| Ceiling | Calls remaining | Saving | Turns cut short |
|--:|--:|--:|--:|
| 8 | 8,478 | −11% | 279 (14%) |
| 6 | 7,665 | −19% | 471 (24%) |
| **5** | 7,061 | **−26%** | 604 (31%) |
| 4 | 6,261 | −34% | 800 (41%) |
| 3 | 5,201 | −45% | 1,060 (54%) |

**Recommended: 5 or 6.** A resident doing ten things in ten game-minutes is not
a resident having a full morning; it is one that could not get the first thing
to work.

*Caveat on method: turns were split on the report's input blocks, and 4% of the
resulting turns show more than 12 calls, which the ceiling should forbid — so
some splits probably merge two turns. The proportions are unaffected; the
maximum is not to be quoted.*

---

## Together

Market visibility removes most of the 12% of calls that follow a refusal, and a
ceiling of 5 removes a further 26%. About a third of the bill, and the run stops
paying for residents trying to buy from people who were never selling.

*Related: `docs/runs/teambox-50-trace-analysis.md` (the run read line by line),
`docs/decisions/WHAT-A-BUDGET-BUYS.md` (what the saving is worth per run).*
