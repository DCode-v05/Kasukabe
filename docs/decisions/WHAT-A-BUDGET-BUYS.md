# Highlights, and what a budget buys

*Written 2026-09-18. Every run figure below is measured from that run's own trace
(`app/data/kasukabe-run*.db`) or, for the 50-resident run, counted out of its
report. Projections are labelled as projections.*

---

## Highlights

**The town works as a society and fails as an economy.** In the 50-resident run
all 50 residents spoke, 4,025 times, and not one line was a verbatim repeat. They
discussed credit (519 utterances), the empty election seats (318), teaching (144)
and work (135). Then: `buy` failed **140 times in 178 attempts**, `trade` 108 in
168, and 37 of 50 residents ran out of money to think with.

Three things happened in that run that have never happened before:

- **Knowledge was taught.** Ivo Pemberton gave `Quick Harvest` to Jovana
  Ravensworth for free, on a promise "to fill bellies, not just pockets" — a
  condition no rule in the world can enforce.
- **Residents kept each other conscious.** A shopkeeper at 270 energy asked for one
  wheat off the shelf; another gave it and adjusted the books after.
- **A resident diagnosed the market failure** 130 ticks before the trace could:
  "Ulrike's bleeding at forty-five, I'm posted at one-fifty, and neither of us is
  moving stock."

**What shipped since (2026-09-17/18):**

| Change | Measured effect |
|---|---|
| Older perceptions no longer re-sent in full | **−24% input tokens** on 30 replayed real calls; residents acted 30/30 instead of 26/30 |
| A run reads itself before stopping | roster, refusals and served tools survive the shutdown |
| Holders, diffusion, dormancy causes | four reported facts that were guesses or blanks are now read |

---

## Cost comparison: every run on the same basis

Cost per **agent-tick** is not comparable between runs, because runs dispatch turns
at different rates. The stable units are **what one resident's turn costs** and
**how often residents take turns**.

| Run | Residents | Ticks | Turns | Calls per turn | **$ per turn** | Turns per resident-tick | $ per resident-day |
|---|--:|--:|--:|--:|--:|--:|--:|
| 25 | 20 | 143 | 725 | 4.09 | $0.0277 | 0.25 | $1.01 |
| 26 | 6 | 149 | 175 | 4.27 | $0.0285 | 0.20 | $0.80 |
| 27 | 6 | 154 | 168 | 4.44 | $0.0298 | 0.18 | $0.78 |
| 28 | 20 | 31 | 264 | 5.55 | $0.0380 | 0.43 | $2.33 |
| 28b | 20 | 77 | 234 | 4.87 | $0.0427 | 0.15 | $0.93 |
| **29** | 20 | 40 | 354 | **7.68** | **$0.0567** | **0.44** | **$3.61** |
| 50-resident | 50 | 194 | — | — | — | — | $2.01 spent per resident, then dormant |

**A turn has doubled in price since run 25** — $0.0277 → $0.0567 — and the reason
is in the fifth column: a turn went from 4.1 model calls to 7.7. Residents do more
per turn now (they act instead of idling: G6 went 51% → 94%), and each call
re-reads a bigger prompt. Input is 95–96% of every bill in every run.

**With the 2026-09-17 fix**, a turn is projected at **$0.0454** (−20%), which is
$2.89 per resident per game-day at run 29's cadence.

---

## Budget comparison: what each budget buys

Two levers, and a budget is decided by both:

- **Cost per turn** — $0.0454 projected, after the fix.
- **Cadence**: how often a resident takes a turn. Run 29 ran **dense** (0.44 turns
  per resident-tick, ≈63 turns per resident per game-day). Runs 25–28b ran
  **sparse** (0.15–0.25). Sparse is cheaper per day and a quieter town.

Game-days a budget buys (1 game-day = 144 ticks):

| Budget | 20 residents, dense | 20 residents, sparse | 50 residents, dense | 50 residents, sparse |
|--:|--:|--:|--:|--:|
| $20 | 0.3 days | 0.6 days | 0.1 days | 0.2 days |
| $50 | 0.9 | 1.5 | 0.3 | 0.6 |
| $100 | 1.7 | 3.1 | 0.7 | 1.2 |
| $250 | 4.3 | 7.7 | 1.7 | 3.1 |
| $500 | 8.7 | 15.3 | 3.5 | 6.1 |
| $1,000 | 17.4 | 30.7 | 6.9 | 12.3 |

Dense = $2.88 per resident-day; sparse (0.25) = $1.63 per resident-day. **On
Standard tier rather than Flex, halve every number in this table.**

And the same thing from the other side — what a target run costs:

| Target | Dense | Sparse |
|---|--:|--:|
| 20 residents × 2 days | $116 | $65 |
| 20 residents × 7 days | $404 | $228 |
| 50 residents × 2 days | $289 | $163 |
| 50 residents × 4 days | $578 | $326 |
| 50 residents × 7 days | **$1,011** | $571 |

---

## The two ceilings that decide this before money does

**1. The wallet.** A resident starts with **$2.00** and burns $2.88 a game-day at
the dense cadence — about **100 ticks**. The 50-resident run's first dormancy
landed at tick 95 and 37 of 50 ended dormant. Any run longer than two-thirds of a
game-day needs the wallets funded for its length:

| Run length | Each wallet must hold (dense) |
|--:|--:|
| 2 days | $5.78 |
| 4 days | $11.55 |
| 7 days | $20.22 |

**2. The invariant.** The world holds **$1,000 in total** — 250 wallets × $2, plus
a $500 treasury. A 50-resident, 7-day run at the dense cadence costs $1,011, which
is more than every dollar in the world. It is not a budget decision; the invariant
has to be re-rated first.

---

## What I would run next, and why

**50 residents × 2 days, sparse cadence: ~$163.** It is the cheapest run that can
answer the question the last one raised — does commerce work once the posting rule
is visible? — with a population large enough for a market. Fund each wallet with
$5.78 first, or the run measures dormancy again instead of behaviour.

Before booting it: make `buy` and `sell` show what is actually purchasable here and
at what price. That one fix addresses 248 failed calls, the largest single waste
in the 50-resident run.

---

*Method: turns are distinct `(agent_id, tick)` pairs with `purpose='auto:agent_turn'`;
cost is `cost_nano_dollars` at Flex list prices ($0.36/M in, $1.80/M out); a game-day
is 144 ticks. Related: `NO-PROMPT-CACHE-COST.md` (where the tokens go),
`WHAT-A-RUN-COSTS.md` (the days × residents matrix),
`docs/runs/teambox-50-trace-analysis.md` (the run read line by line).*
