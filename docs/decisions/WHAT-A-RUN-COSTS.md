# What a run costs, by days and by residents

*Written 2026-09-18, from two measured runs and one measured fix. Every figure here
is either **measured** (named with its run) or **projected** (named as such, with the
measurement it is derived from). Nothing in this file is a guess presented as a
number.*

---

## The unit the whole table is built on

A run's bill is **residents × ticks × cost per agent-tick**. One game-day is
**144 ticks**. So the only figure that has to be measured is the third one.

| Source | Residents | Ticks | Spend | Cost per agent-tick | Notes |
|---|--:|--:|--:|--:|---|
| **Run 29** (2026-09-15) | 20 | 40 | $20.06 | **$0.02507** | all 20 still thinking in the final 24 ticks — nobody went dormant |
| **teambox 50** (2026-09-16) | 50 | 194 | $100.40 | $0.01035 | **37 of 50 were dormant by the end**, and a dormant resident costs nothing |

Run 29 is the honest rate for planning, because it is the rate of a town whose
residents are all awake. The 50-agent run looks half as expensive per resident
only because three quarters of the town had stopped thinking.

**The fix applied on 2026-09-17** (`engine/src/sim/resident_voice.py`: an older
perception reaches the model as its first line, not in full) measured **−24% input
tokens** on 30 real late calls replayed through Kimi on Flex — 20,514 → 15,558
tokens, with residents calling a tool 30/30 instead of 26/30. Early calls save less
than late ones, so the projection below takes **−20% overall**:

> **$0.02507 measured → $0.02006 per agent-tick projected → $2.89 per resident per game-day.**
> The next run measures this; until then it is a projection.

---

## What a run costs

Flex prices (Kimi K2.5, ap-south-1, $0.36/M input, $1.80/M output), every resident
awake for the whole run, no dormancy.

| Game-days | Ticks | 20 residents (before the fix) | **20 residents** | 50 residents (before the fix) | **50 residents** |
|--:|--:|--:|--:|--:|--:|
| 2 | 288 | $144 | **$116** | $361 | **$289** |
| 3 | 432 | $217 | **$173** | $542 | **$433** |
| 4 | 576 | $289 | **$231** | $722 | **$578** |
| 7 | 1,008 | $505 | **$404** | $1,264 | **$1,011** |

**On Standard tier, double every figure.** Flex is half price; the broker asks for
Flex by default and warns if Bedrock serves something else.

### How long each run takes

Wall-clock, from measured tick rates: run 29 held **56.3 s per tick** at 20
residents with no rate-limit waiting at all (73 calls/min median, 113 peak). The
50-resident figure is an estimate anchored on the teambox run's observed peak of
239 calls/min.

| Game-days | 20 residents | 50 residents | Report generation |
|--:|--:|--:|--:|
| 2 | ≈ 4.5 h | ≈ 4.8 h | + 3–5 min |
| 3 | ≈ 6.8 h | ≈ 7.2 h | + 3–5 min |
| 4 | ≈ 9.0 h | ≈ 9.6 h | + 5–10 min |
| 7 | ≈ 15.8 h | ≈ 16.8 h | + 10 min |

More residents barely costs wall-clock, because turns run concurrently — it costs
money. A 7-day run needs checkpoint/resume; it will not survive one sitting.

---

## The constraint that actually decides these runs

**A resident starts with $2.00 of thinking money.** At $2.89 per resident per
game-day, that is **0.69 of a game-day — about 100 ticks**.

This is not theory. The 50-agent run's first dormancies appear at **tick 95**, and
by the end **37 of 50 residents** were dormant. $100.40 spent over 50 residents is
$2.01 each: the run did not stop because the cap was hit, it stopped because every
wallet was empty.

| Run shape | Cost per resident | What each wallet must hold |
|---|--:|---|
| 2 days | $5.78 | ~2.9× the genesis wallet |
| 3 days | $8.66 | ~4.3× |
| 4 days | $11.55 | ~5.8× |
| 7 days | $20.22 | ~10.1× |

And the way out that the world already has — `buy_energy` at **5,000 sim per $1** —
does not open at current prices:

- one game-day of thinking costs a resident **14,440 sim**;
- wheat sold for **12–25 sim** in the 50-agent run, timber for 45–70;
- so a resident must sell roughly **720 units of wheat per day** to pay for its own
  thinking. The whole town produced 299 harvests in 194 ticks.

The 50-agent run bought energy **6 times, for $2.21 in total**, which is the
measurement of that gap rather than a failure of nerve.

### The ceiling above all of this

The project holds a **$1,000 invariant**: 250 wallets × $2 plus a $500 treasury.

- 20 residents × 7 days = **$404** — inside it.
- 50 residents × 4 days = **$578** — inside it.
- 50 residents × 7 days = **$1,011** — **larger than every dollar the world has.**

So a 7-day, 50-resident run is not a budget decision; it needs the invariant
re-rated, or thinking made cheaper still, before it can be run at all.

---

## What to do before the next long run

1. **Fund the residents for the run's length**, or they go dormant on day one.
   Either raise the genesis wallet to cover the planned days ($5.78 for 2 days,
   $20.22 for 7), or lower the cost of thinking further, or make energy buyable at
   prices the town's own goods can reach.
2. **Decide the exchange rate against measured prices.** 5,000 sim per $1 was set
   when nobody was selling anything; wheat at 12–25 sim makes it unreachable.
3. **Keep the prompt shrinking.** After the 2026-09-17 fix, a median call is ~15,558
   tokens, of which roughly 11,600 is tool definitions plus system prompt — that,
   not history, is now the largest single item.

---

*Measured from `data/kasukabe-run29-*.db` (2,717 calls) and
`docs/runs/teambox-50-100usd-2026-09-16-report.md` (9,998 calls). Method: cost per
agent-tick × residents × days × 144. Related: `NO-PROMPT-CACHE-COST.md` for where
the tokens go, `LAW-1-MANIFEST.md` for what may be changed.*
