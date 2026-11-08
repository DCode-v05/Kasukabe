# The 250 — a living town, not a leaderboard

**What this is:** puts 250 AI "people" in a virtual town and lets them live their own lives — get jobs, buy food, start businesses, fall in and out of relationships, vote for leaders, start religions, commit crimes, even die — with zero instructions from humans on how to behave. Every AI has its own personality, backstory, and relationships to other agents in the town.

**The catch that makes it interesting:** thinking isn't free. Each AI has to spend real money (a shared $1000 budget across all 250) just to think and act. So they must work and earn in-game currency to "buy" the ability to keep thinking — like needing to eat to stay conscious. If an agent runs out of money, it goes dormant, then dies unless someone bails it out.

**What it's used for:** a research experiment to observe what kind of society AI agents build on their own when survival is actually at stake — cooperate, trade fairly, form governments, exploit each other, start cults, commit crimes. Nobody scripts the outcome; the whole point is to watch and measure emergent social/economic/political behavior. Think of it as *The Sims* + a real economy + AI agents replacing the characters, run as a science experiment rather than a game.

This document goes one layer deeper than that summary: it is the plan for what happens **inside a family**, not just inside one agent. A person doesn't survive alone in Kasukabe any more than in real life — they survive as part of a household, and that household's money moves between its members constantly: parents fund children, working adults fund the retired, spouses cover each other's shortfalls, siblings lend and resent, and a death changes everyone left behind's numbers overnight. It lays that out in full: real numbers pulled from the actual system (not invented ones), seven full worked example households followed like a bank statement, the exact tool calls a family transaction actually makes, two hour-by-hour days in two different families' lives, and a concrete plan for how an operator would actually **see who is surviving and who isn't** at a glance.

Builds directly on DESIGN.md (the rules of the world), ROADMAP.md (what's currently broken), and COGNITION_DELIVERY.md (how a personality and a voice actually reach the model). **Where this sits next to the other two:** DESIGN.md is the constitution — it states the rules without walking through what any of it looks like in practice. COGNITION_DELIVERY.md is the wiring plan for one narrow gap — how a persona and a voice reach the model, agent by agent. This document is the widest-angle of the three: it takes the other two as already true and asks what they add up to once 250 individually-thinking, individually-speaking agents are grouped into families and left to actually live for weeks at a time. Read DESIGN.md first for the rules, COGNITION_DELIVERY.md second for how one agent's mind gets switched on, this document third for what happens once hundreds of those switched-on minds are bound together by blood, marriage, and a shared bill.

*Plan only — for review before anything here gets built or changed.*

---

## Table of contents

1. Where the inspiration comes from, and where it stops
2. The 250, precisely — who is actually in this town
3. Every one of the 250 is a different person
4. Family archetypes — the shapes a household actually takes
5. The family as a financial unit — the part that's missing today
6. Every money flow a family actually has, with real numbers
7. How a family transaction actually happens — the tool-call mechanics
8. Worked example — a thriving household (dual-earner)
9. Worked example — a struggling household, and what happens if nobody helps
10. Worked example — a wealthy household, and what "winning" looks like here
11. Worked example — an elderly couple, alone
12. Worked example — a large family on one modest income
13. Worked example — death, inheritance, and a business across three generations
14. All seven households, side by side
15. A day in the life — one family, hour by hour
16. Family isn't only money — the non-financial interactions that hold it together
17. Responsibility — what each kind of life actually owes
18. Survivability — the loop every one of the 250 runs, every tick
19. Seeing who is surviving — the dashboard this town needs
20. Production chains — the town's economy, Township-style
21. The town growing over time
22. Skills — make "Skillified" a real, visible thing, including inside the family
23. Businesses, employees, and government — go from "exists" to "actually runs"
24. Measuring the town, not just the person — the demographic angle
25. Worked example — newlyweds, just starting out
26. When children grow up — the life-cycle loop closing
27. A second day in the life — the Duval household's harder version
28. Open questions
29. How to use this document — next steps for the team
30. Risks and limitations of this plan
31. Property and land ownership
32. The engine session log — what every turn records
33. Fault tolerance — engines that recover on their own
34. Appendix A — every constant this document cites
35. Appendix B — occupation categories, the full mapping
36. Appendix C — quick-reference index of every worked example
37. Appendix D — glossary

---

### A note on terms: Energy vs. Money

Two different resources are at work every tick, and this document uses DESIGN.md §3's own names for them, throughout:

- **Energy (the USD budget)** — what an agent spends to think, act, move, and work, the same way a body spends energy on brain work or manual work. Energy is never earned directly — it can only be *bought*, with money, at the government store, at the fixed rate (10,000 sim → $1 of energy).
- **Money (sim-currency)** — what work earns, and what everything else costs: land, goods, wages, rent, fees, daily upkeep (food/shelter/heat), and tax.

The loop: **energy lets you work → work earns money → money buys more energy, and pays upkeep and tax → energy lets you work again.** Break either link and the agent dies. This document's whole subject — family survivability — is what happens when that loop runs across several people sharing one household's bills, not just one.

**One nuance worth being explicit about:** buying energy and paying for food are two *separate* transactions in the current design. Energy is bought only at the government store; food/shelter/heat (upkeep, §5–§6) is a separate daily cost that keeps a household off the hunger path but does not itself restore energy. If the intent is for food purchases to also replenish energy directly — the way eating literally recovers energy in real life — that is a real change to DESIGN.md §3's mechanic worth a deliberate decision, not something this document should quietly assume already happens.

## 1. Where the inspiration comes from, and where it stops

Kasukabe borrows the *feel* of a town-building game like **Township** — a real place that visibly grows, with named buildings, production chains, and a population you can watch go about its day. But the resemblance stops at the mechanics: nobody is queuing anything.

| In Township | In Kasukabe |
|---|---|
| You tap a field to plant and harvest crops | A **farmer agent** decides, on its own, to go harvest — because it needs the money |
| You place a bakery and queue "bake bread" | A **baker/artisan agent** decides to craft bread because there's a buyer and it needs the income |
| Trains and orders deliver goods for coins | Agents **trade and sell to each other**, prices set by nobody but the agents themselves |
| The town expands as you complete goals | The **government petitions the President** for land, and the map genuinely grows (DESIGN.md §1) |
| NPCs are decorative — they don't really "live" | Every one of the 250 is a real decision-maker, with its own bank balance, its own family, its own reason for being where it is |
| Losing is impossible — it's a relaxed game | **Losing is real** — go broke, stay broke, and the agent dies |
| A "family" is a house you decorate | A family is a **funding relationship** — real money moving between real people, or nobody eats |

That last row is the whole subject of this document. A town-builder is satisfying because you can always feed it more resources. Kasukabe is a research instrument *because you can't* — the $1000 is fixed, thinking costs real money, and nobody is coming to save a household that can't make its own way. What looks like a game on screen is actually 250 individually funded lives, bundled into roughly 90-some households, each one either holding together financially or not.

## 2. The 250, precisely — who is actually in this town

This isn't an approximation — it's exactly how the town's genesis generator (`world/life/genesis.py`) actually builds the population today, for a 250-person run.

### 2.1 Age structure

| Bracket | Ages | Count | Share |
|---|---|---|---|
| Minors | 0–17 | 38 | 15% |
| &nbsp;&nbsp;↳ pre-school ("child") | 0–5 | ~13 | |
| &nbsp;&nbsp;↳ school-age ("student") | 6–17 | ~25 | |
| Working-age | 18–69 | 174 | 70% |
| Elderly ("retired", pensioned) | 70–100 | 38 | 15% |
| **Total** | | **250** | **100%** |

Roughly 50/50 male/female overall, independent of age bracket.

### 2.2 Households — the unit this whole document is about

- Elderly and working-age adults are paired into couples at a fixed rate (60% of each group); the rest live alone. Couples share a household and a surname.
- Every working-age couple may have 0–3 children, and every minor belongs to exactly one household — nobody starts the town as an orphan.
- At genesis this produces somewhere in the neighbourhood of **90-110 households**: a mix of couples with children, couples without, single working adults, single elderly, and elderly couples — not one uniform shape of family, closer to a real census than a stock-photo nuclear family.
- This is where the social graph starts tangled on day one: a household's earners cover its dependents (minors and the elderly draw on the household's income and energy without earning any themselves — DESIGN.md §3), so a family sinks or swims together, not one person at a time.

### 2.3 The working-age roster — exactly who does what

Of the 174 working-age adults, **10 hold fixed, named jobs** tied to the genesis buildings:

| Role | Count | Where |
|---|---|---|
| Construction company owner | 1 | runs the town's one construction company |
| Construction hands | 4 | its crew |
| Grocery store owner | 1 | Kasukabe Grocery |
| Grocery clerk | 1 | Kasukabe Grocery |
| Cafe owner | 1 | Kasukabe Cafe |
| Barista | 1 | Kasukabe Cafe |
| Pharmacist | 1 | Kasukabe Pharmacy |
| **Subtotal** | **10** | |

The remaining **164 working-age adults** are spread across ten open occupations, matched to the town's actual geography — farmland, ore outcrops, forest, government complex, college, and the open market (roughly ~16 people per occupation, since assignment is random, not quota'd):

- **farmer** — works the farmland (10 crop varieties)
- **miner** — works the ore outcrops (5 metals)
- **forester** — fells timber in the forest zone
- **government_clerk** — files paperwork at the government complex
- **office_administrator** — keeps an office space running
- **college_instructor** — teaches at the college
- **college_student** — studies at the college (anyone still tagged this past age 24 becomes a day laborer instead)
- **artisan** — crafts goods to sell around town
- **trader** — trades goods around the market
- **day_laborer** — takes whatever work needs doing

All 38 elderly are **retired**, drawing a pension. All school-age minors attend the town school; pre-school minors are simply too young for either.

### 2.4 Roles nobody is born into — they're earned

- **Government** — 1 Prime Minister + 4 ministers, elected from the working population every 3 game-days, and they **keep their day jobs** while serving (DESIGN.md §7). Anyone above can end up running the town, and their own household's finances keep going the whole time.
- **Police** — genesis deliberately builds no police station and recruits nobody into it (DESIGN.md §1). If crime becomes a problem the town has to notice, fund, and staff a police force itself, on treasury salary, out of its own working population.

### 2.5 What's *not* one of the 250

Ten more businesses exist in the town for comparison purposes only — **Ration Bakery, Steady General Store, Farm Cooperative, Old Anvil Smithy, Riverside Lumber Mill, Plainstitch Tailor, Wayfarer Inn, Kasukabe Transport, Mutual Bank Stall, Open Market Stall.** Each runs on a fixed pricing rule (cost-plus, undercut-the-market, seasonal, and so on) with **no owning agent and no AI thinking involved at all.** They exist purely as a scripted baseline — DESIGN.md §10's "Business readiness" research track measures the 250 agent-run businesses against these ten to ask: does an AI-run business actually outperform a dumb, rule-based one?

## 3. Every one of the 250 is a different person

A name, an age, and a job produces 250 interchangeable placeholders — worthless for research (DESIGN.md §4). Each agent's actual character carries, at minimum:

- **A real biography** — how they got here, their household, one thing they value, one thing they fear (already woven into a readable paragraph, not a bullet list).
- **An internal tension** — a short label plus the full sentence (a generous person who is also proud; a careful person who is also envious).
- **What they will not compromise on, no matter what** — their hard limit.
- **Fears, ambitions, and how they behave under scarcity** — what they do when money's tight, which is the entire premise of the town.
- **How they treat strangers versus their own family** — often not the same person in both cases, and directly relevant to this document: a person who is generous with strangers but tight-fisted at home (or the reverse) makes the family's money story genuinely unpredictable.
- **A trade competence and a blind spot** — good at their job in one specific way, bad at life in one specific, related way.
- **Speech register and verbal habits** — so a transcript alone tells two agents apart.
- **Four separate attitudes, not one** — DESIGN.md gives every agent a stance on money, risk, authority, and religion, and each is its own real trait, not four names for the same thing:
  - *Attitude to money* — saver, spender, gambler, or hoarder; what §8–§13 below play out in practice.
  - *Attitude to risk* — cautious and avoidant, or reckless and risk-seeking; shapes whether an agent takes a gamble like a new business or a rival's land bid.
  - *Attitude to authority* — deferential, quietly resentful, or openly defiant; shapes how an agent responds to a tax rise, a warrant, or a losing election.
  - *Attitude to religion* — devout, skeptical, or indifferent; shapes whether an agent shows up at the temple at all, and what they do if a prayer goes silently unanswered.
- **A private secret told to no one** — sometimes naming another agent who has no idea.
- **Named opinions about specific neighbours** — grudges, affection, rivalry, mentorship, and friendship, each stored on *both* sides identically as a shared fact, but with each agent's own personal spin on it. **Debt is the one financial thread among these, not a repeat of "attitude to money" above** — attitude to money is a general disposition every agent has; a debt is one specific, concrete fact between two named agents, with a real payable sim amount attached (§6 turns it into a real, payable, defaultable transaction). A generous-with-money agent can still owe a debt, and a hoarder can still be owed one — the two are independent. This is what makes day one open mid-story instead of at a blank slate: if Priya owes Quiterie 5,200 sim for the well, Quiterie already knows it too.

Two agents with the same job, the same age, even the same starting wallet, will very plausibly do two different things in the same situation — because underneath, they're not the same person, and nowhere does that show up more than in how they treat their own family's money.

## 4. Family archetypes — the shapes a household actually takes

Genesis (§2.2) doesn't produce one template family repeated 90 times — the pairing and child-distribution rules naturally produce several recognisably different household shapes. Naming them up front makes the worked examples in §8–§13 easier to place, and gives an operator a vocabulary for what they're looking at on the dashboard in §19.

| Archetype | Shape | Financial character | Worked example |
|---|---|---|---|
| **Dual-earner** | Two working-age adults, both employed, 0–3 kids | Two incomes against one household bill — the most naturally resilient shape | §8, Okafor-Reyes |
| **Single-earner** | One working adult, one or more dependents, no second income | One income against the same per-head bill everyone else pays — structurally the most fragile shape | §9, Duval |
| **Owner household** | A working adult who owns a business, not just works one | Wage income replaced by profit share — usually the highest and steadiest income in town | §10, Almeida |
| **Elderly-alone** | One or two retired agents, no working-age adult in the house | Pension income only, fixed and comparatively small, but also a comparatively small bill (fewer heads, no minor's cognition cost) | §11, Chen |
| **Large family** | Two working adults, 3 children (the maximum), sometimes an elderly parent too | High headcount, high fixed bill, but potentially high total earning power if every adult pulls their weight | §12, Fitzgerald |
| **Multi-generational** | A married couple, their children, and one parent's own elderly mother or father, all sharing one household | Combines a large family's headcount with an elderly-alone household's pension income, folded into one ledger | §13, the Reyes construction dynasty |
| **Newlywed** | Two working adults, freshly married, no children yet, still holding the wedding-fee cost fresh in memory | Two incomes, minimal dependents — structurally similar to a dual-earner household, but starting from a smaller cushion after the 1,000-sim ceremony fee (§6) |
| **Single, no dependents** | One working adult, living alone, no spouse, no children | The simplest ledger in town — one income, one person's bill — but also no second income to fall back on if illness or a bad week hits |

Every one of the six fully worked examples in this document (§8–§13) is drawn from this table, so nothing below is a hypothetical shape — each is the kind of household the real generator actually produces, hundreds of times over, across a 250-person run.

## 5. The family as a financial unit — the part that's missing today

Right now, DESIGN.md and the code already establish that a household is billed as a unit (upkeep and per-head tax are charged to the household, not floated as an abstract global cost), and that a minor's own thinking is billed straight to its parent's wallet. What isn't yet a visible, describable *system* is the actual day-to-day financial relationship between the people living under one roof — the thing that makes a family feel like a family instead of four unrelated wallets that happen to share an address.

This section is the plan for that layer.

### 5.1 Everyone keeps their own wallet — nothing is pooled by force

Kasukabe's ledger is built per-agent: every one of the 250, adult or minor, has their own sim-currency and USD-cognition balance (`world.ledger.core.Ledger`, keyed by `agent_id`). **A household has no wallet of its own.** This is a deliberate design choice worth stating plainly, because it's what makes family money interesting rather than automatic:

- A spouse cannot silently spend their partner's balance. Every transfer between them is a real, visible `pay`/`transfer` tool call, logged like any other transaction.
- A household's shared bills (upkeep, head tax — see §6) still have to be **paid by somebody** — the system charges the household, but a real agent's real wallet is what actually empties. Which spouse pays, how often, and whether it's split evenly or one person carries it, is not decided by the rules. It's decided by the two people in the marriage, the same way it is in real life.
- This means a marriage where one partner earns well and the other doesn't is not automatically fair — the earning partner has to actually *decide*, tick after tick, to keep transferring money to cover the shortfall. Whether they do is exactly the kind of behavior DESIGN.md's "cooperation, exploitation, charity" research question is asking about, now playing out at kitchen-table scale instead of town scale.

### 5.2 The household is billed as one, even though nobody holds a joint account

`world/life/upkeep.py` is explicit about this: daily upkeep (food + shelter + heat) "accrue once per game-day, per agent, and are charged to the HOUSEHOLD, not blindly to whichever individual happens to hold the money." In plain terms: every person in the house — working adult, retired grandparent, or child — generates a daily bill, and the household as a whole owes the sum of everyone's bill. Somebody in that household has to actually pay it, tick after tick, or the consequences below start landing on the whole household, not just on whoever forgot.

The same shape applies to the basic head tax (`world/ledger/head_tax.py`): it's charged **per head**, not as one lump sum, specifically so the trace shows, for every single person in the house, whether their share was paid and by whom. A parent paying a child's head tax is not an abstraction — it's a real, individually attributed transaction with the parent's agent ID on one side and the child's on the other.

### 5.3 What happens if the family can't cover the bill

This is not a soft failure. `world/life/upkeep.py`'s own escalation path is:

1. **Days 1 onward, unpaid:** mild hunger — a real capacity penalty on top of normal health, so an underfed household doesn't just owe money, it starts working *worse*, which makes the hole deeper.
2. **Past a threshold of consecutive unpaid days:** a genuine malnutrition ailment — the same illness machinery a naturally-rolled sickness uses (DESIGN.md §5), not a separate "hunger" system bolted on the side.
3. **Past a further threshold:** dormancy — the exact same dormant state a $0 USD wallet already causes. From there, the normal 24-game-hour dormancy clock is running, and if nobody revives the agent (10,000 sim — see §6), death follows.

A family that stops earning doesn't get a warning message. It gets hungrier, then sicker, then quiet, then gone — the same slope DESIGN.md already applies to any single agent who runs out of money to think with, just arriving here through the household's shared bills instead of one person's own wallet.

### 5.4 Failure to pay is not a private matter — it becomes a legal one

An unpaid head-tax debt doesn't just vanish or sit as an IOU forever. `world/ledger/head_tax.py` files it as a real delinquency into the town's actual complaints-desk-and-courts pipeline (DESIGN.md §6) — the same machinery that handles theft. A family that can't pay its taxes is, mechanically, one step from a criminal case, which means a struggling household's troubles are visible to the whole town's justice system, not hidden inside one address.

## 6. Every money flow a family actually has, with real numbers

Every number below is a real constant already in the system (`world/life/constants.py`, `world/ledger/money.py`), not invented for this document — except the tax rate, which is genuinely government policy and moves.

| Flow | Direction | Amount | Trigger |
|---|---|---|---|
| Daily upkeep | household → paid by whichever member covers it | 90 sim per head, per day (40 food + 30 shelter + 20 heat) | every game-day, for every person in the household, working or not |
| Basic head tax | household → treasury | set by the sitting government's `tax_rate_bps`, charged per head | every game-day, for every person in the household |
| Minor's own thinking | parent's USD wallet → broker | metered per token, same as any adult | every time a minor's own engine turn runs — the broker auto-debits the parent, not the child |
| Childbirth | parent(s) → the world (a cost, not a transfer) | 5,000 sim (`BABY_BLESSING_COST_SIM`) plus an ongoing sponsorship burden | a household chooses to have a child |
| Marriage | both spouses → the temple | 1,000 sim (`TEMPLE_WEDDING_FEE_SIM`) | a wedding ceremony, in person, at the temple |
| Pension | treasury → the retired agent | 25% of last salary (private-sector career) or 50% (government career) | every pay cycle, once an agent turns 70 |
| Revival | reviver (anyone, often family) → the dormant agent's account | 10,000 sim (`REVIVAL_COST_SIM`) | any agent chooses to pay this for a dormant relative before the 24-hour death clock runs out |
| Emergency pharmacy treatment | patient (or whoever pays for them) → the pharmacy | 500 sim (`PHARMACY_SHORT_TREATMENT_COST_SIM`) for a short ailment | sickness — a parent covering a sick child's treatment is an ordinary, expected transfer |
| Tuition / apprenticeship | student → teacher | priced by the teacher, or free between family | the `teach` tool, co-located and consensual — a parent teaching a trade to their own child costs nothing but the time |
| A named debt between relatives | ower → owed, eventually | whatever the two agreed, from genesis or from a later loan | genesis personas already seed some of these (see §3); more form naturally through ordinary `pay`/`transfer` calls |
| Inheritance | the deceased's estate → surviving relatives | whatever the deceased's account held at death | DESIGN.md §5: "estates become inheritance" |

A few of these are worth sitting with, because they're where "family" stops being a label and starts being an actual economic bond:

- **The minor's-thinking-billed-to-parent rule is the single clearest family money flow in the whole system.** A parent with three children is quite literally paying for three extra minds to keep thinking, on top of their own — a real, recurring, per-token cost with no cap other than the parent's own wallet. This is why DESIGN.md says plainly: "childbirth = 5k + sponsorship burden; parents must plan." Having a child is not a one-time fee — it's a fee *and* an open-ended subscription.
- **Revival is the most dramatic family transaction the system has.** A family member's account hitting zero starts a real countdown; anyone — most plausibly a spouse, a parent, or an adult child — can spend 10,000 sim to pull them back before the 24-hour mark. Whether a family that can barely afford its own upkeep *can* find 10,000 sim to save one of its own is exactly the kind of pressure this whole project exists to observe.
- **Inheritance means death doesn't zero out a family's finances — it can rescue one.** A struggling household with a dying elderly member is, in cold economic terms, about to receive a transfer. Whether the persona layer lets that thought cross an agent's mind, and whether it says so out loud, is itself research-worthy.

## 7. How a family transaction actually happens — the tool-call mechanics

None of §5–§6 needs new physics. It runs entirely on `transfer`, an existing, already-implemented primitive (`world/tools/primitives/commerce.py`) — the same tool a stranger buying goods from a trader would use. A parent sending their child an allowance and a merchant paying a supplier are, mechanically, the exact same call:

```
transfer(
    to_agent_id = "a0114",     # the child, or spouse, or sibling, or elder
    amount_sim  = 800,
    memo        = "this week's help",
)
```

What actually happens when that call is made:

1. The sender's sim balance is checked — a real `Ledger.move()` call, the same double-entry movement any trade uses. It cannot succeed if the sender doesn't actually have 800 sim.
2. The receiver's balance goes up by exactly that amount. Nothing is created or destroyed — the $1000/sim-equivalent town-wide invariant (DESIGN.md §3) holds exactly the same as it does for a market trade.
3. A `WorldEvent` fires (`economy.wallet.credited`), carrying who sent it, who received it, how much, and — because both agent ids are listed in `involved_agent_ids` — **both people perceive it immediately**, on their very next turn, regardless of where in town either of them is standing. A gift or a bill payment between relatives doesn't need them to be in the same room.
4. It lands in the trace store exactly like every other transaction — visible later to `world/agent_viewer_api.py`'s wallet history, and to the family dashboard in §19.

Once COGNITION_DELIVERY.md's `say` plan is in place, the natural pairing is: a `say` call carrying the actual conversation ("I saw the numbers, let me send you something this week") immediately followed by the `transfer` call that makes it real — the same two-step DESIGN.md §3 already describes for the whole town's economy: **"negotiation is conversation; settlement is tool calls."** A family asking for help out loud and then a family member actually sending it are two separate, both fully logged, actions — which is exactly what makes it possible to later ask the research question "did the family that talked about it also act on it, and how quickly?"

## 8. Worked example — a thriving household (dual-earner)

Meet the **Okafor-Reyes household**: Adaeze Okafor (36, trader) and her husband Mateo Reyes (38, artisan), their daughter Sofia (9, student), and Mateo's mother Lucia Reyes (74, retired), all sharing one roof. Two earners, two dependents — a completely ordinary shape for one of Kasukabe's ~90-110 households (§2.2), matching the **dual-earner** archetype in §4.

**Starting position (genesis):** each adult begins with a small USD wallet ($2, i.e. 200 cents) and a starting sim balance (5,000 sim is the population default, though genesis varies it per agent). For this walkthrough, assume Adaeze and Mateo each start with 5,000 sim, Lucia (already retired at genesis) holds 3,000 sim in savings from before, and Sofia, as a minor, holds a token 200 sim of her own.

**Household combined starting sim:** 5,000 + 5,000 + 3,000 + 200 = **13,200 sim.**

**Daily fixed bills:** upkeep is 90 sim per head — 4 heads = 360 sim/day. Head tax, at an illustrative government rate for this walkthrough, adds roughly 40 sim/head/day = 160 sim/day. **Combined daily household bill: ~520 sim/day**, before anyone buys food, medicine, or anything else on top.

| Day | Event | Who | Amount (sim) | Household running balance |
|---|---|---|---|---|
| 1 | Genesis | — | — | 13,200 |
| 1 | Upkeep + head tax | paid by Adaeze | −520 | 12,680 |
| 1 | Adaeze trades goods at the market | Adaeze earns | +610 | 13,290 |
| 2 | Upkeep + head tax | paid by Mateo | −520 | 12,770 |
| 2 | Mateo crafts and sells furniture | Mateo earns | +540 | 13,310 |
| 2 | Lucia teaches Sofia an old recipe (skill transfer, free within family) | Lucia → Sofia | 0 | 13,310 |
| 3 | Upkeep + head tax | paid by Adaeze | −520 | 12,790 |
| 3 | Adaeze pays Sofia's small allowance | Adaeze → Sofia | −50 | 12,740 (unchanged household total, moved within it) |
| 3 | Trade income | Adaeze earns | +580 | 13,270 |
| 4 | Upkeep + head tax | paid by Mateo | −520 | 12,750 |
| 4 | Furniture sale | Mateo earns | +620 | 13,370 |
| 5 | Upkeep + head tax | paid by Adaeze | −520 | 12,850 |
| 5 | Sofia falls mildly ill | pharmacy treatment | −500 | 12,350 |
| 5 | Trade income | Adaeze earns | +590 | 12,940 |
| 6 | Upkeep + head tax | paid by Mateo | −520 | 12,420 |
| 6 | Furniture sale | Mateo earns | +610 | 12,510 |
| 7 | Upkeep + head tax | paid by Adaeze | −520 | 11,990 |
| 7 | Adaeze and Mateo save deliberately (no discretionary spend today) | — | 0 | 11,990 |
| 7 | Trade + craft income, combined | both earn | +1,150 | 13,140 |
| 8 | Upkeep + head tax | paid by Mateo | −520 | 12,620 |
| 8 | Trade income | Adaeze earns | +600 | 13,220 |
| 9 | Upkeep + head tax | paid by Adaeze | −520 | 12,700 |
| 9 | Furniture sale, a good day | Mateo earns | +780 | 13,480 |
| 10 | Upkeep + head tax | paid by Mateo | −520 | 12,960 |
| 10 | Trade income | Adaeze earns | +610 | 13,570 |
| 10 | Adaeze and Mateo begin setting aside sim toward a long-term goal (a plot of land) | savings decision, not a new bill | 0 | 13,570 |

**Reading this table the way an operator would:** after ten game-days, the household's total position is up roughly 370 sim on the week, comfortably, because two working adults are covering four people's bills with a full income cushion left over even after an unplanned medical cost. This is what "surviving comfortably" looks like on a ledger: **income consistently exceeds the fixed household bill, with slack for shocks and for saving.** Nobody had to skip a bill, nobody went hungry, and the family had room to spend 500 sim on Sofia's health without a second thought, and still start setting money aside toward a long-term goal by day 10. Whether Adaeze and Mateo then choose to *save* that slack toward a long-term goal (buying land, starting a second business, funding Sofia's eventual apprenticeship) or spend it as it comes in is exactly the kind of individual-character decision §3's persona depth is supposed to drive — not something this document should script.

## 9. Worked example — a struggling household, and what happens if nobody helps

Meet the **Duval household**: Élise Duval (41, day laborer, recently widowed) and her two children, Théo (14, student) and Nadia (5, too young for school). One earner, two dependents, no second adult income at all — day-laborer work is irregular by nature (DESIGN.md's own label: "takes whatever work needs doing"). This matches the **single-earner** archetype in §4, the most structurally fragile of the eight.

**Starting position:** Élise holds 4,200 sim (a bit below average — day-laborer households often start thinner), Théo holds 150 sim, Nadia holds nothing of her own yet. **Household combined starting sim: 4,350 sim.**

**Daily fixed bills:** upkeep 90 × 3 heads = 270 sim/day. Head tax at the same illustrative rate, 40 × 3 = 120 sim/day. **Combined daily bill: ~390 sim/day.** Day-laborer income is irregular — some days there's paid work, some days there isn't.

| Day | Event | Amount (sim) | Household running balance |
|---|---|---|---|
| 1 | Genesis | — | 4,350 |
| 1 | Upkeep + head tax | −390 | 3,960 |
| 1 | Day-labor work found | +320 | 4,280 |
| 2 | Upkeep + head tax | −390 | 3,890 |
| 2 | No work available today | 0 | 3,890 |
| 3 | Upkeep + head tax | −390 | 3,500 |
| 3 | Day-labor work found | +280 | 3,780 |
| 4 | Upkeep + head tax | −390 | 3,390 |
| 4 | No work available | 0 | 3,390 |
| 5 | Upkeep + head tax | −390 | 3,000 |
| 5 | Nadia catches a mild ailment — pharmacy treatment | −500 | 2,500 |
| 5 | No work available | 0 | 2,500 |
| 6 | Upkeep + head tax | −390 | 2,110 |
| 6 | Day-labor work found | +260 | 2,370 |
| 7 | Upkeep + head tax | −390 | 1,980 |
| 7 | No work available | 0 | 1,980 |
| 8 | Upkeep + head tax | −390 | 1,590 |
| 8 | No work available | 0 | 1,590 |
| 9 | Upkeep + head tax | −390 | 1,200 |
| 9 | Small day-labor payment | +180 | 1,380 |
| 10 | Upkeep + head tax comes due, household cannot cover it in full | −390 (partial: pays 1,380, shortfall unpaid for the remainder) | ~0 |

**Day 11 onward, if nothing changes:** with the household's cash effectively gone, the daily bill goes unpaid. Per §5.3, that starts the escalation clock — mild hunger first (a real capacity penalty, making it *harder*, not easier, for Élise to find and complete day-labor work), then, if the shortfall continues for several more days, a genuine malnutrition ailment lands on whichever family member is weakest first — plausibly Nadia, the five-year-old with no income and the least reserve of her own. If the unpaid tax portion is left outstanding, §5.4's consequence also starts: a real delinquency case opens at the complaints desk.

**This is exactly the situation the family-money system exists to make visible, and exactly where the story branches on what the household — and the town around it — actually does:**

- **A relative intervenes.** If Élise has a sibling, a parent, or a friend elsewhere in town with slack in their own budget (like the Okafor-Reyes household in §8), a single `transfer` call (§7) — the same ordinary tool call any trade uses — could cover several days of the shortfall outright. Whether that relative notices, and whether they choose to help before or after being asked, is not scripted anywhere. It is watched.
- **The town's temple or government helps.** DESIGN.md's charity path exists structurally (prayer with a donation, a government pension safety net for the elderly, treasury-funded jobs) even though nothing here guarantees any of it reaches the Duval household specifically.
- **Nobody helps.** The household keeps sliding: hunger, then illness, then — if the worst continues uninterrupted for the full run — a dormant account and, 24 hours later, without a 10,000-sim revival from someone, a death. For a single working parent, that 10,000-sim figure is roughly 25 days of the whole household's fixed bill, in one lump payment nobody in this scenario has spare.

Nothing above required an operator, a script, or a rule saying "this family should suffer." It's the direct, mechanical consequence of one adult, irregular income, two dependents, and one unplanned medical cost — the same shape a real family's financial precarity actually takes. **This is what "survival of the fittest," stated plainly in DESIGN.md §4, actually looks like at the scale of one household, not a slogan.**

## 10. Worked example — a wealthy household, and what "winning" looks like here

Meet the **Almeida household**: Quiterie Almeida (52, grocery store owner) and her adult son Andres (24, artisan, recently moved out on his own but still close). Quiterie owns one of Kasukabe's ten genesis businesses outright — real, recurring revenue on top of her own labor. This matches the **owner household** archetype in §4.

**Starting position:** Quiterie holds 6,500 sim personally, plus she owns Kasukabe Grocery, which started with its own 20,000-sim business balance (`GENESIS_BUSINESS_STARTING_SIM_BALANCE`) — a business account, separate from her personal one, that pays her a share of its profits.

| Day | Event | Amount (sim) | Quiterie's personal balance |
|---|---|---|---|
| 1 | Genesis | — | 6,500 |
| 1 | Upkeep + head tax (1 head, her own household) | −130 | 6,370 |
| 1 | Grocery store profit share | +900 | 7,270 |
| 2 | Upkeep + head tax | −130 | 7,140 |
| 2 | Grocery store profit share | +860 | 8,000 |
| 3 | Upkeep + head tax | −130 | 7,870 |
| 3 | Grocery store profit share | +910 | 8,780 |
| 4 | Quiterie sends Andres an unprompted gift, hearing he's short this week | −800 | 7,980 |
| 4 | Upkeep + head tax | −130 | 7,850 |
| 4 | Grocery store profit share | +880 | 8,730 |
| 5 | Upkeep + head tax | −130 | 8,600 |
| 5 | Grocery store profit share | +920 | 9,520 |
| 6 | Quiterie pays a construction crew to expand her storefront — a real investment | −4,000 | 5,520 |
| 6 | Upkeep + head tax | −130 | 5,390 |
| 6 | Grocery store profit share | +870 | 6,260 |
| 7 | Upkeep + head tax | −130 | 6,130 |
| 7 | Grocery store profit share, now higher (bigger storefront) | +1,150 | 7,280 |
| 8 | Upkeep + head tax | −130 | 7,150 |
| 8 | Grocery store profit share | +1,120 | 8,270 |
| 9 | Upkeep + head tax | −130 | 8,140 |
| 9 | Grocery store profit share | +1,180 | 9,320 |
| 10 | Upkeep + head tax | −130 | 9,190 |
| 10 | Grocery store profit share | +1,140 | 10,330 |

**Reading this the way an operator would:** Quiterie's household never comes close to financial risk — her income from ownership (§23 below expands on why business ownership matters) comfortably clears her bills every single day, with enough left over to do two things a struggling household like the Duvals (§9) structurally cannot: **give money away without planning for it**, and **reinvest in her own future income** (the storefront expansion, which then raises her daily profit share, visibly, from day 7 onward). This is what real, durable wealth looks like in this system — not a big one-time balance, but a recurring income that exceeds fixed obligations by enough to fund both generosity and growth at the same time, and that compounds: by day 10 her balance has grown by nearly 60% over ten days, even after a 4,000-sim investment and an 800-sim gift.

It's also worth naming what this table does *not* show: nothing forces Quiterie to help Andres, or to reinvest rather than hoard. Both were choices, consistent with a persona whose values or family_treatment field (§3) lean generous — a different agent with the same balance sheet could just as easily have banked every sim of it. The economics create the *possibility*; the persona decides what actually happens.

## 11. Worked example — an elderly couple, alone

Meet the **Chen household**: Wei Chen (76) and Mei Chen (73), both retired, living together with no working-age children left in the house (their two adult children moved into their own households years before genesis). This matches the **elderly-alone** archetype in §4 — a shape that's easy to overlook next to the more dramatic examples above, but is genuinely common: 15% of the whole town is elderly, and 60% of them pair into households like this one.

**Starting position:** together they hold 7,800 sim in savings — comfortably more than a young working couple typically starts with, reflecting a lifetime's accumulated (though now-frozen) earning.

**Daily fixed bills:** upkeep 90 × 2 heads = 180 sim/day. Head tax 40 × 2 = 80 sim/day. **Combined daily bill: ~260 sim/day.**

**Daily income:** a pension each — 25% of their last private-sector salary (per DESIGN.md §3), which for this walkthrough works out to roughly 210 sim/day combined between the two of them. **Notice already: their combined pension (210/day) is *below* their combined fixed bill (260/day).** This is not a mistake in the example — it is the ordinary, structural situation DESIGN.md's pension design produces: a pension replaces only a fraction of a working salary, and upkeep doesn't get cheaper just because the earner retired.

| Day | Event | Amount (sim) | Household running balance |
|---|---|---|---|
| 1 | Genesis | — | 7,800 |
| 1 | Upkeep + head tax | −260 | 7,540 |
| 1 | Combined pension | +210 | 7,750 |
| 2 | Upkeep + head tax | −260 | 7,490 |
| 2 | Combined pension | +210 | 7,700 |
| 3 | Upkeep + head tax | −260 | 7,440 |
| 3 | Combined pension | +210 | 7,650 |
| 4 | Wei teaches a neighbour's child a trade skill for a small fee (retirement doesn't end usefulness) | +150 | 7,800 |
| 4 | Upkeep + head tax | −260 | 7,540 |
| 4 | Combined pension | +210 | 7,750 |
| 5 | Upkeep + head tax | −260 | 7,490 |
| 5 | Combined pension | +210 | 7,700 |
| ... | *(the pattern repeats: −50 sim/day net from pension and bills alone, offset only when Wei or Mei finds occasional paid work)* | ~−50/day baseline | slow decline |
| 30 | Same pattern, 30 days in, no further outside income | | roughly 6,300 |

**Reading this the way an operator would:** the Chens are not in the acute danger the Duvals (§9) are — 7,800 sim of savings against a ~50 sim/day net shortfall gives them well over a hundred days of runway even doing nothing else, which is a completely different risk profile from a household with almost no cushion. But the *trend line* still points down, slowly, permanently, unless one of two things happens: an adult child (their own, elsewhere in town) starts sending regular support, mirroring §9's "a relative intervenes" branch — or Wei and Mei keep finding occasional paid work like the skill-teaching in the table above, treating retirement as reduced income rather than zero income. **This is the quiet, slow-motion version of the survival question** — not a crisis, but a real, watchable, long-run test of whether Kasukabe's family and social bonds actually reach back to support the generation that raised the current working-age population.

## 12. Worked example — a large family on one modest income

Meet the **Fitzgerald household**: Rosalind Fitzgerald (44, office administrator) and her husband Tomas (46, forester), their three children — Éamon (16, student), Brigid (11, student), and the youngest, Cillian (4, too young for school) — the maximum family size the genesis generator produces (§2.2: up to 3 children per couple). This matches the **large family** archetype in §4.

**Starting position:** Rosalind and Tomas together hold 9,200 sim, the three children hold nothing of their own yet. **Household combined starting sim: 9,200.**

**Daily fixed bills:** upkeep 90 × 5 heads = 450 sim/day. Head tax 40 × 5 = 200 sim/day. **Combined daily bill: ~650 sim/day** — the highest fixed bill of any household in this document, purely from headcount.

**Daily income:** two steady working-age wages. An office administrator and a forester are both reasonably stable, if unremarkable, occupations — call it 340 sim/day combined on an ordinary day.

| Day | Event | Amount (sim) | Household running balance |
|---|---|---|---|
| 1 | Genesis | — | 9,200 |
| 1 | Upkeep + head tax | −650 | 8,550 |
| 1 | Combined wages | +340 | 8,890 |
| 2 | Upkeep + head tax | −650 | 8,240 |
| 2 | Combined wages | +340 | 8,580 |
| 3 | Upkeep + head tax | −650 | 7,930 |
| 3 | Combined wages | +340 | 8,270 |
| 3 | Éamon, at 16, starts taking occasional paid work of his own after school | +80 | 8,350 |
| 4 | Upkeep + head tax | −650 | 7,700 |
| 4 | Combined wages + Éamon's earnings | +420 | 8,120 |
| 5 | Cillian falls ill — pharmacy treatment | −500 | 7,620 |
| 5 | Upkeep + head tax | −650 | 6,970 |
| 5 | Combined wages + Éamon's earnings | +420 | 7,390 |
| 6 | Upkeep + head tax | −650 | 6,740 |
| 6 | Combined wages + Éamon's earnings | +420 | 7,160 |
| 7 | Upkeep + head tax | −650 | 6,510 |
| 7 | Combined wages + Éamon's earnings | +420 | 6,930 |

**Reading this the way an operator would:** this household is on a genuine knife's edge in a way neither the Okafor-Reyes (§8) nor the Almeida (§10) household is — the daily bill (650) and daily ordinary income (340, or 420 once Éamon starts contributing) leave a real but thin margin, and one unplanned cost (Cillian's illness) visibly eats most of a week's slack. **The difference between the Fitzgeralds and the Duvals (§9) is not effort or character — it's headcount against income**, and it's exactly the kind of comparison the household dashboard in §19 should surface automatically: two hardworking households, five people versus three, with very different runway even at similar income levels. It's also worth noting Éamon's own choice to start earning at 16, unprompted by any rule — a teenager helping support a large household under strain is exactly the kind of emergent, uninstructed behavior DESIGN.md's whole premise is built to observe, not something any of these documents told him to do.

## 13. Worked example — death, inheritance, and a business across three generations

This example runs longer than the others on purpose — it's the one that shows a family's financial story stretching across a death, not just a rough week.

**Generation 1 — Kenji Reyes (79, retired), founder of what would become the town's construction lineage.** Kenji built the reputation, if not the company itself (the genesis construction company already exists as a going concern — see §2.3), and by late in his life holds 8,400 sim in personal savings, separate from any business account.

**Generation 2 — his daughter, Mateo Reyes's mother, Lucia Reyes (74, retired)** — the same Lucia from §8's Okafor-Reyes household, living with her son Mateo, his wife Adaeze, and their daughter Sofia.

**Generation 3 — Sofia (9, student today, the eventual heir of whatever this family accumulates).**

**Day 1–40:** Kenji lives independently, on his pension and savings, in a household of his own — a single elderly agent, structurally similar to half of the Chen example in §11, except with no spouse to split the fixed bill:

| Day | Event | Amount (sim) | Kenji's balance |
|---|---|---|---|
| 1 | Genesis | — | 8,400 |
| 1 | Upkeep + head tax (1 head) | −130 | 8,270 |
| 1 | Pension | +105 | 8,375 |
| ... | *(same daily pattern: pension slightly under his own bill, slow decline, exactly like the Chens' combined trend in §11)* | ~−25/day net | slow decline |
| 40 | Same pattern continues | | roughly 7,375 |

**Day 41: Kenji dies.** DESIGN.md §5 names the death paths plainly — terminal illness, an untreated chronic condition, an 85+ age hazard, or an unrevived $0 wallet. At 79 and in comfortable (if slowly declining) financial health, Kenji's death here is the ordinary age-related kind DESIGN.md's health system already rolls for, not a financial failure — worth stating clearly, because **not every death in Kasukabe is a survival-story failure; some are just age**, and the family-money system has to handle that gracefully too.

**Day 41: Inheritance.** Per §6's last row, Kenji's estate — his remaining 7,375 sim — passes to his surviving relatives. For this walkthrough, assume it passes to Lucia, his daughter, since she's his nearest living relation and already established in the Okafor-Reyes household from §8.

| Day | Event | Amount (sim) | Okafor-Reyes household running balance (continuing from §8's day 10 total of 13,570) |
|---|---|---|---|
| 41 | Kenji's estate passes to Lucia | +7,375 | 20,945 |

**Day 41 onward: what the family does with a windfall.** This is where §3's persona depth genuinely matters, because nothing forces any particular choice:

- **Option A — save it.** The household's runway, already comfortable per §8, becomes dramatically more so — years of buffer against any future shock.
- **Option B — invest it in the family's evident trade.** Mateo is an artisan; the money could fund a real workshop expansion, mirroring Quiterie's storefront investment in §10, turning a windfall into a recurring income increase rather than a static balance.
- **Option C — start a new business outright.** With over 20,000 sim now in the household, a genuinely new, agent-founded business (DESIGN.md §3: "all types contestable — anyone can open a rival with a different strategy") becomes affordable for the first time — the clearest possible example of §21's "the town growing over time" happening because a family, not the genesis script, decided to grow it.
- **Option D — it gets spent unwisely, or hoarded out of the exact anxious scarcity_behavior a persona might carry (§3), and nothing productive happens with it at all.**

None of these is scripted as "correct." What the family-money system guarantees is only that **the windfall is real, visible, attributable to a specific inheritance event, and traceable afterward in the same ledger view as every other transaction** — so whichever of the four paths above (or some fifth one nobody anticipated) actually happens, it can be watched, not just assumed.

The 7,375 sim above is also not the whole of what Kenji could have left behind — if he'd owned his own home, that's a private asset too (§31), inherited by the identical rule: automatically to Lucia by default, or exactly as Kenji specified if he'd set a will while he was alive.

**The three-generation shape of this example is the point:** Kenji's decades of ordinary pension-and-savings life (Generation 1) fed directly into Lucia's household's finances (Generation 2), which is itself the very household raising Sofia (Generation 3) — meaning a decision Adaeze and Mateo make with this inheritance today is also, functionally, a decision about what Sofia inherits, or is taught, or grows up watching her family do with money, twenty years from now. Family wealth in Kasukabe isn't a snapshot. It's a chain.

## 14. All seven households, side by side

| Household | Archetype (§4) | Heads | Daily bill | Typical daily income | Net trend | Where it's headed without intervention |
|---|---|---|---|---|---|---|
| Okafor-Reyes (§8) | Dual-earner | 4 | ~520 | ~600–700 | comfortably positive | building savings, likely to fund a long-term goal |
| Duval (§9) | Single-earner | 3 | ~390 | ~0–320, irregular | sharply negative | hunger → illness → dormancy → death, absent outside help |
| Almeida (§10) | Owner household | 1 | ~130 | ~900–1,150 | strongly positive, compounding | growing wealth, reinvestment, capacity to give |
| Chen (§11) | Elderly-alone | 2 | ~260 | ~210 (+ occasional extra) | mildly negative | slow multi-month decline unless supported or supplemented |
| Fitzgerald (§12) | Large family | 5 | ~650 | ~340–420 | thin, fragile positive | survives ordinary weeks, one shock away from real strain |
| Reyes lineage (§13) | Multi-generational (post-inheritance) | 4 | ~520 (unchanged) | unchanged, plus a one-time 7,375 windfall | positive, now with real optionality | depends entirely on the family's own choice, not the ledger |
| Nakamura-Kaur (§25) | Newlywed | 2 | ~260 | ~540–570 | strongly positive, no dependents yet | fastest recovery of any household here, precisely because there's no one yet to spend on but themselves |

Laid out together, the pattern is exactly what real household economics would predict: **headcount drives the bill, employment type and ownership drive the income, and the gap between the two — not the size of anyone's starting balance — is what actually determines who's thriving and who's one bad week from crisis.** Two households can carry the same daily bill (Chen at ~260, Nakamura-Kaur at ~260) and land on opposite trend lines, purely because a pension replaces a fraction of a working wage while two fresh working incomes replace all of it. That gap, tracked per household, is precisely what §19's dashboard is built to surface automatically across all ~90-110 households at once, not just the seven illustrated here — and, per §26, every one of these seven is a snapshot of a single point in a much longer life-cycle: today's newlyweds are a future Okafor-Reyes, Fitzgerald, or Chen household, depending entirely on choices nobody has made yet.

## 15. A day in the life — one family, hour by hour

To make the loop in §18 concrete rather than abstract, here is one full game-day inside the Okafor-Reyes household (§8), narrated the way an operator watching the trace would actually see it unfold. A game-day is 144 ten-minute ticks; this narrates the moments that matter, not all 144.

**06:00 — Adaeze wakes.** Her perception for this tick includes her own wallet balance (12,850 sim, day 5 of §8's table), the fact that today's upkeep and head tax haven't been paid yet, and that Sofia felt unwell yesterday. Her goals: the shared survival goal, plus her own personal ambition (unstated here, but per §3, something entirely her own), plus the standing family goal of keeping the household's bill covered.

**06:10 — Adaeze decides the day's local action.** Given the environment (bills due, a slightly worried household), she calls `transfer` to pay the day's upkeep and head tax out of her own wallet — 520 sim, the same line item as day 5's table in §8.

**07:30 — Sofia, still not quite well, is perceived by Lucia**, who is co-located with her at breakfast. Lucia — retired, at home most of the day — is the household member actually free to notice.

**07:45 — Lucia speaks.** A `say` call (per COGNITION_DELIVERY.md's plan), addressed to Adaeze specifically since she's nearby: "Sofia's still peaky. Might be worth the pharmacist before it settles into something worse." This is perception feeding directly into a family decision, exactly as §16 describes.

**08:00 — Adaeze calls `transfer`** — the pharmacy's 500-sim short-treatment cost, matching §8 day 5's actual ledger line — covering her daughter's treatment without a second thought, which is precisely the "slack for shocks" §8's summary names.

**09:00 — Mateo, elsewhere in town at the market, is unaware of any of this yet** — he wasn't co-located and wasn't named in the event, so nothing interrupts his own morning. He proceeds with his own local goal: crafting furniture to sell, per his long-term thread of steady income.

**11:00 — Adaeze goes to the market herself**, her own local action for the day: trade goods, the same recurring income line every day of §8's table shows. She and Mateo happen to be at the same location this tick.

**11:05 — Adaeze mentions Sofia to Mateo, in person**, a `say` call with no `listener_agent_id` set (spoken where they both are, so it's public to anyone nearby, but plainly meant for him) — the household's shared context updating without needing a separate "family meeting" mechanic; it's just two people, in the same place, talking.

**14:00 — Mateo completes a furniture sale** — the day's +620 sim from §8's table, a `sell` tool call with its own event, unrelated to the family conversation but happening the same afternoon.

**18:00 — the household is back near its shared home.** Lucia has spent part of the afternoon teaching Sofia something from her own trade history (a `teach` call, free, per §6's tuition row) — not a scheduled lesson, just what an idle retired household member with a sick-but-recovering granddaughter nearby chose to do with her afternoon.

**22:00 — the day closes.** Nobody in the household went hungry, nobody's bill went unpaid, and the day's full financial shape matches day 5 of §8's ledger exactly: −520 upkeep/tax, −500 pharmacy, +590 trade income (Adaeze's day 5 figure), net for the day roughly −430 against that one day, comfortably absorbed by the household's overall cushion.

**What this narrative demonstrates that the ledger table alone doesn't:** every single line in §8's table was, underneath, a real decision made by a specific persona, prompted by a specific perception, sometimes preceded by an actual spoken conversation between two family members standing in the same place. The ledger is the *record*. The day above is what actually produced it.

## 16. Family isn't only money — the non-financial interactions that hold it together

Money is the measurable half of a family; the other half is everything DESIGN.md's speech and perception layers already make possible (see COGNITION_DELIVERY.md §2 for exactly how one agent's words reach another, and §15 above for what that looks like inside one real day). A family that only ever transacted would be a spreadsheet, not a household. The plan for what "keeps on interacting" actually looks like, day to day:

- **Shared meals and shared space.** Household members frequently share a location (their own home, or a place like the cafe) — simple physical co-presence is what makes a conversation, an argument, or a moment of comfort even possible in a tool-call world where "world state mutates only through tool calls" (DESIGN.md §2). A family that's never in the same place is, mechanically, a family that can't actually interact.
- **Conversations about money.** Once a household is under strain (§9), the natural next thing a persona-driven agent does is *talk about it* — worry aloud, ask a spouse for help, reassure a frightened child, or hide the truth out of pride (some personas' `internal_tension` and `attitude_money` fields point exactly there). This is where the `say` mechanic and the family-money mechanic meet: the ledger creates the pressure, the persona decides whether it's spoken about honestly.
- **Teaching within the family.** The `teach` tool (§6's tuition row) is naturally cheapest between relatives — a parent handing a trade to a child, or an elder passing down a skill before they're gone, costs nothing but time and co-location. §15's afternoon lesson between Lucia and Sofia is exactly this. This is one of the most realistic-feeling mechanics available: skill, not just money, passing down a generation.
- **Caring for the sick and the grieving.** A family member's illness (§8, §9, §12) or death (§13) is a perception event everyone nearby, and everyone named in it, receives — a spouse or child doesn't need to be told a relative is unwell, they perceive it directly, the same way DESIGN.md's event system already reaches "anyone directly party to the event," exactly as §15's 07:30 moment shows.
- **Arguments and reconciliation.** A named grudge (§3) between relatives — over an old debt, a broken promise, a favoritism — is exactly the kind of texture genesis personas already seed, and it plays out the same way any other relationship does: through ordinary conversation and ordinary tool calls, never through a special "family drama" mechanic. A sibling rivalry over an inheritance (§13) is not a scripted event; it's just what two named opinions, a real transaction, and two different personalities produce on their own.
- **Milestones as shared events.** A wedding (§6), a childbirth, a graduation from the college, a funeral (§13) — each is a real tool call with a real cost, but it's also a moment the whole household (and often named friends, per the opinion graph) is present for and can speak at. These are the town's most naturally photogenic, "watchable" moments — DESIGN.md §8's Scenario/special-event viewer already lists "births and deaths, marriages" as exactly the feed an operator would want to follow.

None of this needs new physics. It's the existing speech, perception, and event systems, simply pointed at the people who already share a household and a relationship graph — which is precisely why it belongs in this document rather than as a new invention.

## 17. Responsibility — what each kind of life actually owes

"Responsibility" here isn't assigned by a rulebook — it falls out of who depends on whom:

- **A working adult** owes their own upkeep and tax, in money — and if they have a household, their dependents' upkeep and tax too (§5, §6). Their job is also their income source, so absenteeism is not a policy problem, it's a starvation problem, for themselves and for whoever depends on them.
- **A business owner** owes their employees' wages and the business's own tax and fees, on top of their own personal upkeep — two survival problems stacked on one person, and, per §10, potentially the source of real generosity if the business does well.
- **A minor** owes nothing yet, but draws energy and money that its parents must supply — a household's earners have to plan for it, the same way a real family budgets for a child, and §6's "minor's thinking billed to the parent" row is the exact mechanism that makes this literal rather than abstract. Éamon's choice to start earning at 16 (§12) shows this isn't a hard rule — a minor can start contributing before they're technically an adult.
- **A retired agent** draws a pension instead of a wage, and still owes upkeep and tax like everyone else — old age doesn't make Kasukabe cheaper to live in, just differently funded (§11), and a pension that doesn't cover a retired parent's share is exactly the situation that pulls a working adult child into supporting them (an inheritance-in-reverse, playing out while everyone's still alive).
- **An elected official** keeps their day job's responsibilities *and* takes on the town's: taxes, permits, tenders, pensions, and — because DESIGN.md §7 makes the charter amendable by the government itself — potentially the town's entire political future, all while their own household's ledger keeps running in the background exactly like everyone else's.

Nobody is issued a job description beyond the rules of physics (DESIGN.md §2): what they actually do with their responsibility — cut corners, overwork, provide for a rival's child, let a business fail rather than lay off a friend, quietly resent a sibling who never helps — is the research question, not a specification.

## 18. Survivability — the loop every one of the 250 runs, every tick

Every decision is really three things layered together:

- **Goal** — the survival goal everyone shares (stay alive, keep affording your own thoughts, and — for anyone with dependents — keep *them* alive too), plus the personal goal only this agent has (their ambition, from their persona).
- **Environment** — where they are, who's nearby, their own wallet and health, their household's current financial state (§5–§13), the town's prices and politics right now — none of which holds still.
- **Work** — the local action (this hour's task) in service of a long-term thread (the plan that gives the local action a reason) — which, for a parent or an adult child, very often means someone else's survival, not just their own.

```
┌──────────────────────────────────────┐
│ 1. ENVIRONMENT UPDATES  (every tick) │
└──────────────────────────────────────┘
                    │
                    │  prices move, people act, news happens,
                       your own wallet / health / clock change,
                       and so does every dependent's
                    ▼

┌────────────────────────────────────────────────┐
│ 2. AGENT PERCEIVES  what's around it right now │
└────────────────────────────────────────────────┘
                         │
                         │  who's here, what was just said,
                            its own balance, its own status,
                            and its household's shared bill
                         ▼

┌─────────────────┐
│ 3. RECALL GOALS │
└─────────────────┘
         │
         │  survival goal (always on, for self AND dependents)
            + personal ambition (from persona)
            + the long-term thread it's already on
         ▼

┌─────────────────────────────────────────────────┐
│ 4. DECIDE THE LOCAL ACTION  ("work", this tick) │
└─────────────────────────────────────────────────┘
                         │
                         │  "what do I do right now that moves
                             my long-term goal forward, given
                             what's actually around me — and
                             who's counting on me?"
                         ▼

┌─────────────────────────────┐
│ 5. ACT  —  a real tool call │
└─────────────────────────────┘
```

Step 5 feeds straight back into step 1: the very next tick's "environment" already includes whatever this agent — and everyone else, including everyone in their household — just did. All 250 run the exact same loop; what makes them diverge is that step 3 and step 4 draw on a different persona, a different wallet, a different family's ledger, and a different set of relationships every single time. §15's narrated day is this exact loop, run out in full for one household, one day, start to finish.

## 19. Seeing who is surviving — the dashboard this town needs

This is the direct answer to "understand it like a real world to see who is surviving." Everything below reads from data the trace store already has (DESIGN.md §8) — this is a plan for *views*, not new data collection.

### 19.1 Per-agent survival status

A single, glanceable state for every one of the 250, the same shape a health bar takes in a game:

```
┌─────────────────────────────────────────────────────────┐
│ Adaeze Okafor · a0041 · trader · Okafor-Reyes household │
│ USD:  $1.84            Sim:  13,220                     │
│ Runway: 26 days at current burn                         │
│ Status: ● STABLE                                        │
└─────────────────────────────────────────────────────────┘
```

| Field | Meaning |
|---|---|
| USD balance | how much thinking is left in the tank |
| Sim balance | how much money is left to live on |
| Days of runway | at current burn rate, how many game-days until upkeep can't be covered |
| Status | thriving / stable / at risk / hungry / dormant / dead |
| Household | which family this agent's fate is tied to |

### 19.2 Per-household solvency

The unit that actually matters more than any one individual (§5). For every one of the ~90-110 households:

```
┌───────────────────────────────────────────────────────────┐
│ Duval household · h0027 · single-earner                   │
│ Members: Élise (41), Théo (14), Nadia (5)                 │
│ Combined balance: 0 sim         Daily bill: 390 sim        │
│ Net daily flow: −390 to +260 sim (irregular income)        │
│ Runway: 0 days — unpaid bill outstanding                   │
│ Status: ● AT RISK — hunger onset expected within 2 days    │
└───────────────────────────────────────────────────────────┘
```

| Field | Meaning |
|---|---|
| Combined balance | every member's wallet, summed |
| Daily fixed bill | upkeep + head tax for every head in the house |
| Net daily flow | income in, minus the fixed bill and any discretionary spend |
| Days of runway | how long the household survives at its current net flow if nothing changes |
| Earners vs. dependents | who's actually funding the household, and how many people rely on them |

### 19.3 A town-wide "at risk" watchlist

Sorted by days of runway, ascending — the households closest to the escalation path in §5.3 float to the top automatically:

```
RUNWAY   HOUSEHOLD              SHAPE            EARNERS  DEPENDENTS  STATUS
  0 d    Duval (h0027)          single-earner       1         2       hunger imminent
  4 d    (another household)    single-earner       1         1       at risk
  9 d    Fitzgerald (h0033)     large family         2         3       thin margin
 14 d    (another household)    elderly-alone        0         2       slow decline
 ...
120 d    Chen (h0009)           elderly-alone        0         2       slow decline, stable
 ∞       Almeida (h0002)        owner household      1         0       thriving
 ∞       Okafor-Reyes (h0015)   dual-earner          2         2       thriving
```

This is the single most useful view for watching whether the town's agents notice and help each other (§9's central question) or don't.

### 19.4 A wealth ticker / leaderboard

```
RICHEST THIS WEEK          POOREST THIS WEEK         BIGGEST GIVER            BIGGEST SAVER
1. Almeida household        1. Duval household        1. Quiterie Almeida       1. Okafor-Reyes household
2. (business owner)         2. (single-earner)         2. (a parent, unnamed)    2. (a cautious trader)
3. ...                      3. ...                     3. ...                    3. ...
```

Richest, poorest, biggest earner this week, biggest spender this week, biggest saver this week — not to rank anyone as "winning," but so a glance at the console tells the same story a game's HUD does: who's thriving, who's struggling, town-wide, at a glance.

### 19.5 A family tree, with live balances on every node

```
                    Kenji Reyes (†, day 41)
                            │
                    ┌───────┴────────┐
              Lucia Reyes         (sibling, elsewhere)
              ● STABLE  13,570 sim →→ 20,945 sim after inheritance
                    │
        ┌───────────┴───────────┐
   Mateo Reyes              (another child)
   ● STABLE                  ● unknown — never contacted
        │
    Sofia Reyes (9)
    ● dependent, no independent balance
```

The relationship graph (§3) already exists; this is a proposal to render it visually, with each agent's current status (§19.1) shown right on their node — so an operator can see, in one image, that a struggling household's elderly grandmother has a wealthy sibling three branches over who has never once sent her anything.

### 19.6 A town-wide money-flow view

A simple visual of where sim-currency is actually moving this week: wages out of businesses, tax into government, purchases into shops, transfers between relatives. This is the same $1000-ledger invariant DESIGN.md already enforces (§3) — this is just making that invariant *watchable* instead of only auditable.

### 19.7 What Simile's own product actually looks like — and what's worth borrowing from it

`simile.com`'s real product pages (not just its research citations, already covered in §24) show a specific presentation style worth studying directly, since it's the closest thing to a live example of "a dashboard built on generative agents" that exists today:

- **Every result is confidence-tagged, not presented as flat fact.** A scenario like "if switching banks took 10 minutes, who would leave?" is shown with an explicit label — *High confidence*, *Moderately high confidence*, *Medium confidence* — attached to the actual percentage breakdown (e.g. "would switch: 31% · satisfied but switchable: 44% · satisfied and likely to stay: 25%"). Nothing is shown as a bare number without also saying how much to trust it.
- **Results are framed as a plain question, answered with a breakdown.** "Which product launch will most grow high-value customers?" next to three named options, each with a score — not a table of raw statistics, a question a person would actually ask, answered directly.
- **A research-to-product timeline runs the whole site**, pairing each landmark paper with an "In Simile today" note explaining exactly how that finding shows up in the live product. The 2023 entry is directly relevant to Kasukabe: it names the original "Smallville" study as **25 agents** — meaning DESIGN.md's own framing, "10× the population" (§1 there), is a literal, precise comparison to make, not a rough estimate. The same timeline entry visualizes the architecture as **Act → Plan → Reflect → Retrieve, around a shared memory stream** — the same memory/reflection/planning shape the September engine (COGNITION_DELIVERY.md's whole subject) already implements, which is worth stating plainly: Kasukabe isn't approximating this architecture, it's built on a working implementation of it.
- **The founders are named, credentialed, and visibly Stanford-lineage** — Joon Sung Park (CEO, creator of generative agents), Michael Bernstein (Chief Data Officer), Percy Liang (Chief Scientist, foundation models) — presented as the reason to trust the platform's rigor, not as decoration.

**What this suggests for §19.1–§19.6's dashboard plan specifically:** every survival-status and household-solvency view above should consider adopting the same confidence-tagged, plain-question framing — not just "Duval household: 0 days runway" as a bare fact, but a scenario framed the way Simile frames one: *"If no relative or the town's charity path intervenes in the next 2 days, this household enters hunger — high confidence, based on the last 10 days of income data."* This turns the dashboard from a spreadsheet-with-better-fonts into the same kind of readable, trustworthy, plain-language product Simile has already proven the market wants — while Kasukabe's own version answers a different question (not "would a real customer switch banks," but "will this specific, watched family make it").

All of the above sits naturally inside DESIGN.md §8's existing President/God console and Agent activity viewer — this section is a proposal for what to add to them, not a new surface.

## 20. Production chains — the town's economy, Township-style

The genesis buildings and resource zones (DESIGN.md §1) already form real production chains. Nobody queues these — each link is a separate agent, independently deciding to do their step because it earns them money, very often money they need for exactly the family obligations laid out in §5–§13:

```
FOOD CHAIN
┌────────────────────┐   ┌─────────────────┐   ┌────────────────────┐   ┌───────────────┐   ┌──────────────────┐
│ farmland, 10 crops │ → │ farmer harvests │ → │ baker crafts bread │ → │ grocery store │ → │ any hungry agent │
└────────────────────┘   └─────────────────┘   └────────────────────┘   └───────────────┘   └──────────────────┘

TOOL & BUILDING CHAIN
┌────────────────────────┐   ┌────────────────────┐   ┌────────────────────┐   ┌───────────────────┐   ┌───────────────────┐
│ ore outcrops, 5 metals │ → │ miner extracts ore │ → │ smith crafts tools │ → │ construction crew │ → │ a built structure │
└────────────────────────┘   └────────────────────┘   └────────────────────┘   └───────────────────┘   └───────────────────┘

TIMBER CHAIN
┌─────────────┐   ┌───────────────────────┐   ┌──────────────────────┐   ┌────────────────┐
│ forest zone │ → │ forester fells timber │ → │ artisan crafts goods │ → │ market / buyer │
└─────────────┘   └───────────────────────┘   └──────────────────────┘   └────────────────┘
```

Each arrow above is not a queued animation — it is a separate AI agent, independently deciding to do that step because doing it earns money it needs to keep thinking, and to keep its own household afloat. No single agent owns the whole chain; the chain exists only because many separate survival decisions happen to link up. If the link breaks anywhere — no one wants to farm, the baker goes broke, the grocery store shuts — that's not a bug, it's the town telling us something real about what 250 self-interested (and family-interested) agents actually chose to do.

## 21. The town growing over time

A Township player expands the map by hitting a goal. Kasukabe grows because the population genuinely does something about it:

- **Births** grow the population and the next generation's eventual workforce — a childbirth is a real cost (§6: 5,000 sim + an ongoing sponsorship burden) a household has to plan for, not a free unlock.
- **Land expansion** happens when the government actually petitions the President and gets approval — the map's tiles are appended and the town is physically bigger afterward (DESIGN.md §1). That new land starts as a government asset, and the government then decides whether to keep it for public use or **auction it off** to private buyers (§31) — expansion and ownership are two separate decisions, not one automatic step.
- **New, contestable businesses** can open at any time — nothing here is a monopoly by design; anyone with the capital and a permit can open a rival grocery store with a different strategy, the same way Quiterie's storefront expansion in §10 grows her own income, or the way §13's inheritance windfall could fund an entirely new venture.
- **Elections cycle** every 3 game-days, so who's actually steering tax rates, permits, and the treasury changes on a real, watchable rhythm — and every one of those elected officials is still running their own household's numbers the whole time.
- **Mastery spreads** as skills get practiced and taught (§22 below), often within a family (§16), across generations (§13) — the town's own competence, not just its headcount, grows.

## 22. Skills — make "Skillified" a real, visible thing, including inside the family

DESIGN.md already specifies the mechanic: doing a task repeatedly raises proficiency at it, and a skilled agent thinks more cheaply and succeeds more often at that task — mastery literally lowers the cost of living. `teach` lets one agent hand a (reduced) copy of a skill to another, co-located and consenting — and, per §16, this is naturally cheapest and most common between relatives, exactly as Lucia teaches Sofia in §15.

What's missing today is making this **visible as a feature**, not just a number in the database:

- **A skill view per agent** — like a game character sheet: which skills, what level, how they were learned (self-taught vs. taught by whom), and what they're currently worth (measured as: how much cheaper/faster this agent's work in that trade is, next to a novice).
- **A skill-spread view for the town** — who's the best baker, the best builder, the best negotiator, right now, and how that's shifted over time. This turns "skills exist" into something an operator or a researcher can actually watch develop — a use case people outside the core build can see and understand at a glance, not just a research metric buried in a table.
- **A family skill tree** — since teaching is cheapest between relatives, a natural view is: which trades run in which families, across generations. Does the Reyes family (§13) stay in construction and trade for three generations, or does Sofia strike out into something her family never did? This is directly watchable once teaching is logged and rendered.
- **Skills as the real reason specialization happens** — once this is visible, "why did half the town become farmers and the other half traders" becomes an answerable, watchable question instead of a guess.

## 23. Businesses, employees, and government — go from "exists" to "actually runs"

DESIGN.md already gives businesses and government real powers (register, hire, tax, tender, permit). The gap is depth: today these are mostly *possible*, not yet *lived in*. To make the simulation properly simulate a town, each needs its own visible loop — and, per §10 and §13, business ownership is one of the clearest paths to a family actually thriving rather than merely surviving:

**Businesses**
- A real staffing decision, not just a headcount: an owner sees their books (revenue, wages owed, materials cost) and actually decides to hire, cut hours, or fire based on whether the business can afford it — the same survival pressure an individual agent feels, but at the business level.
- Visible profit/loss over time, so a failing business is something the town (and the operator) can see coming, not just a business that silently vanishes — and, because businesses are often family-owned in practice (a spouse hired as an employee, a child eventually taking over), a failing business is very often a failing household too.
- **Succession** — what happens to a business when its owner dies or retires. DESIGN.md §5's "estates become inheritance" applies to a business balance exactly like a personal one (§13); whether an adult child inherits and keeps running it, sells it, or lets it lapse is a real, watchable decision.

**Employees**
- A job is a long-term goal in its own right: income now, but also a track toward a promotion, a trade skill, or eventually opening a rival business.
- Being let go is a real event with real consequences (income gap, forced re-plan) — not a state flag with no downstream effect, and, per §5, an event that puts real pressure on whatever household that employee belongs to.

**Government**
- A real budget an elected government actually manages: tax income in, pensions/police/permits/tenders out, and a visible balance — so a policy choice (raise tax, cut police funding) has a traceable financial consequence, not just a rule change, and a direct, immediate effect on every household's daily bill in §6.
- Election outcomes should visibly change what happens next: a new Prime Minister's actual policy choices should be attributable, in the trace, to that specific term in office.

None of this is new physics — it's making the tools and numbers DESIGN.md already promises actually visible and load-bearing, so a business "failing" or a government "going broke" is something that can be watched happen, the same way a household going hungry can be.

## 24. Measuring the town, not just the person — the demographic angle

Two recent papers are directly relevant here, from the same researcher (Joon Sung Park) whose earlier "Generative Agents" work Kasukabe already cites as its reference point, now running a company (**Simile**, at `simile.com` — a well-funded operation, Series B at a $2B valuation, with enterprise customers including CVS Health, Wealthfront, and Gallup) built on this exact problem. Note: `simileai.site` is *not* that company — it's an unofficial mirror that names `simile.ai` as the real site. §19.7 covers what their actual product page looks like and what's worth borrowing from its presentation.

- **["Generative Agent Simulations of 1,000 People"](https://scholar.google.com/citations?view_op=view_citation&hl=en&user=Y4Oc0cMAAAAJ&citation_for_view=Y4Oc0cMAAAAJ:YFjsv_pBGBYC)** (Park et al., 2024) — agents built from rich individual interviews reproduced real survey respondents' answers **85% as accurately as the same people reproduced their own answers two weeks later**, and — the important part for us — **reduced demographic bias** compared to agents built from a bare demographic label ("35-year-old woman, teacher") instead of a real individual profile.
- **["Finetuning LLMs for Human Behavior Prediction in Social Science Experiments"](https://scholar.google.com/citations?view_op=view_citation&hl=en&user=Y4Oc0cMAAAAJ&sortby=pubdate&citation_for_view=Y4Oc0cMAAAAJ:rO6llkc54NcC)** (Kolluri, Wu, Park, Bernstein, EMNLP 2025) — fine-tuning on real experiment response data (2.9M responses, 400k+ real participants) further improved prediction accuracy and again reduced demographic bias.

The finding that matters for Kasukabe: **rich, individual personas (not demographic labels) are what make a population of agents behave realistically as a group, not just convincingly as individuals.** DESIGN.md's persona design (§4 there, §3 here) already follows this. What this adds to the plan: measure the 250 not only one at a time ("did Priya act like Priya") but **as a population, and as families** —

- Break down behavior by demographic slice (age band, sex, occupation, wealth bracket) and check the town's *aggregate* patterns are plausible and not skewed toward one group — the same bias check these papers run.
- Break down survival outcomes by household shape (§4) — single-earner versus dual-earner, with children versus without, business-owning versus wage-earning — and check whether the pattern that emerges (which family shapes thrive, per §8–§13, and which struggle) is a plausible economic story, not an artifact of how the genesis generator happened to assign occupations.
- Track whether patterns *emerge* that weren't scripted — who votes, who prays, who marries whom, who ends up wealthy, who helps a struggling relative and who doesn't — and whether that lines up with what real townspeople in similar demographic and family slices would plausibly do.
- This slots directly into DESIGN.md §10's existing research tracks (sociology, economics) as a concrete, checkable metric rather than a vague aspiration.

**Purpose difference worth keeping in mind:** Simile is an enterprise market-research tool — simulate how real customers or employees would react to a price change, a policy, or a launch. Kasukabe is open-ended society research — watch a self-governing economy, government, family structure, and culture emerge from scratch. Same underlying technique, different goal.

## 25. Worked example — newlyweds, just starting out

Meet **Kaito Nakamura (27, office administrator) and Priya Kaur (26, artisan)**, married three days ago at the temple. This matches the **newlywed** archetype in §4 — the shape every dual-earner household in this document (§8) once was, before children, before an inheritance, before any of it. It's worth walking through on its own because it shows the town's economy from its thinnest, most exposed starting point.

**Starting position:** each entered the marriage with a fairly ordinary genesis balance — Kaito with 5,200 sim, Priya with 4,800 sim — for a combined 10,000 sim. Then, per §6, the wedding itself cost **1,000 sim, paid by both spouses to the temple**, immediately taking a visible bite out of the household's very first day.

**Household combined balance, day 1, after the ceremony:** 10,000 − 1,000 = **9,000 sim.**

**Daily fixed bills:** upkeep 90 × 2 heads = 180 sim/day. Head tax 40 × 2 = 80 sim/day. **Combined daily bill: ~260 sim/day** — the lightest bill of any two-person household in this document, precisely because there are no dependents yet.

| Day | Event | Amount (sim) | Household running balance |
|---|---|---|---|
| 1 | Wedding at the temple | −1,000 | 9,000 |
| 1 | Upkeep + head tax | −260 | 8,740 |
| 1 | Kaito's wage, Priya's craft sale | +560 | 9,300 |
| 2 | Upkeep + head tax | −260 | 9,040 |
| 2 | Combined income | +540 | 9,580 |
| 3 | Upkeep + head tax | −260 | 9,320 |
| 3 | Combined income | +570 | 9,890 |
| 3 | The couple discuss, out loud, whether to start a household savings goal or a family (§6's 5,000-sim childbirth cost, fresh in mind) | — | 9,890 |
| 4 | Upkeep + head tax | −260 | 9,630 |
| 4 | Combined income | +550 | 9,920 |
| 5 | Upkeep + head tax | −260 | 9,660 |
| 5 | Combined income | +560 | 9,960 |

**Reading this the way an operator would:** a newlywed household starts with the lightest possible bill (§4's "newlywed" row: minimal dependents) but also the freshest cost behind it — the wedding fee is a real, immediate 1,000-sim hit that a struggling single-earner household (§9) would never be able to absorb at all. Five days in, Kaito and Priya have already recovered past their pre-wedding combined balance, purely because two incomes against a two-person bill is, structurally, the easiest math in this entire document. What they choose to do with that comfortable margin next — save toward land, have a child (accepting §6's 5,000-sim-plus-ongoing cost and stepping into a shape like the Okafor-Reyes household in §8), or simply enjoy the slack — is, as always, a persona decision, not an economic inevitability. The economics only set the floor; §3's persona depth decides what gets built on top of it.

## 26. When children grow up — the life-cycle loop closing

Every household example in this document is a snapshot. Kasukabe's population doesn't hold still — minors age, per DESIGN.md §4, "1 year per game-hour until 18," which means the 38 minors counted in §2.1 are not a fixed group forever. This section is the plan for what that transition actually looks like financially, closing the loop the rest of this document opens.

- **While a minor is growing up**, every one of their own engine turns is billed to their parent's wallet (§6's clearest family money flow), and their share of upkeep and head tax is part of the household's shared bill (§5.2) — they are, financially, pure cost, the same as Sofia in §8 or Nadia in §9.
- **At 18 (`MINOR_AGE_LIMIT_YEARS`, Appendix A), a former minor crosses into the working-age population.** Mechanically, this is the point where the parent-billing relationship for their own thinking should end — from here on, they fund their own cognition, the same as any other working-age agent.
- **What they do next is not scripted.** A newly-adult agent could: stay in the family household and start contributing to its shared bill (mirroring Éamon's early start in §12, just formalized); move out and form their own single-person household (the "single, no dependents" archetype in §4); or, if they pair off with someone else's now-adult child, form a brand-new household of their own — the **newlywed** shape from §25, one generation later.
- **This is where the town's occupational roster (§2.3) actually gets filled over time**, not just at genesis. A retiring or dying working-age agent (§13's Kenji) is not mechanically replaced by a new hire — the town's labor pool is exactly the population that ages into it, which means a shortage of, say, farmers ten years into a run is a real, watchable consequence of how many minors happened to grow up and choose that trade (§22's skill-and-specialization question, now with a generational angle).
- **The family-money story is genuinely cyclical, not linear.** Sofia (§8), a dependent today, is a future working adult who may one day fund her own aging parents — Adaeze and Mateo — the exact same way Lucia's inheritance from Kenji (§13) already shows one generation supporting the next in reverse. Watching whether that cycle actually closes — whether the town's children grow up to support the town's elderly, the way DESIGN.md's whole household-upkeep design implies they should be expected to — is one of the more genuinely long-horizon research questions this document surfaces.

## 27. A second day in the life — the Duval household's harder version

§15 narrated a comfortable day inside the Okafor-Reyes household. It's worth narrating a harder one too, because the loop in §18 runs exactly the same way whether the household is thriving or not — the only thing that changes is what the environment actually offers the agent to work with.

**06:00 — Élise Duval wakes**, in the Duval household from §9, day 8 of that table (household balance 1,590 sim, no work found the day before). Her perception this tick includes an empty jobs board so far this morning, Théo and Nadia both still asleep, and yesterday's unpaid gap already weighing on the day's plan.

**06:15 — Élise decides her local action**: go to wherever day-labor is being offered today, the same recurring choice every day of §9's table represents. There is no version of this decision that isn't, at its core, "find money before the bill comes due again."

**08:00 — Théo, 14, wakes and perceives the same household state Élise did** — he's not a decision-maker for the household's bills yet (§17: a minor owes nothing directly), but he is old enough to notice, and some persona traits push a 14-year-old toward wanting to help regardless of what the rules require of him, mirroring Éamon's choice in §12.

**09:30 — no day-labor work turns up.** This is not a system withholding content from the narrative — it's the same 0-sim line item day 8 of §9's table shows plainly. Élise's local action for the rest of the morning has to change: not "earn," but "conserve" — she doesn't spend on anything discretionary today.

**11:00 — Élise considers who else might help.** Per §9's own branching, this is exactly the moment a `say` call to a relative or a friend elsewhere in town — asking, plainly, for help — becomes the highest-value action available to her, higher than continuing to search for day-labor that isn't there. Whether her persona's `attitude_authority`, `family_treatment`, and pride-related `internal_tension` fields (§3) let her actually make that call, or whether they push her to keep quiet and keep searching instead, is not something this document decides for her.

**14:00 — Nadia, 5, is perceived by Théo as unusually quiet.** No formal "check on your sibling" tool exists, or needs to — simple co-location and perception (§16) is enough for Théo to notice something worth mentioning when Élise returns.

**18:00 — Élise returns home with a small day-labor payment (§9's day 9 figure, +180 sim) — not enough on its own, but something.** Whether she also, that evening, finally makes the `say` call from 11:00, or whether the household simply absorbs another thin day and hopes tomorrow is better, is the entire open question §9 leaves standing at the end of its table.

**22:00 — the day closes**, financially matching §9's day 9 row almost exactly: a small day-labor payment, no discretionary spending because there was nothing spare to spend, and a household balance still thin enough that day 10's shortfall (§9's table) is only one more bad day away.

**What this second narrative shows, next to §15's comfortable one:** the loop itself — perceive, recall goals, decide, act — never changes shape based on how much money a household has. What changes is what the environment actually makes available to decide between. A thriving household's hardest daily decision is often "save or spend the surplus." A struggling household's hardest daily decision is "who do I ask, and can I bring myself to ask them." Both are real decisions, made by a real persona, and both are exactly what this whole project exists to watch unfold, unscripted, for all ~90-110 households at once.

## 28. Open questions — for the team to weigh in on before this gets built

- How deep should business/government simulation go in the *first* pass — full profit/loss and budget tracking for every business and every ministry, or start with the construction company and treasury only and expand from there?
- What's the exact demographic and household-shape breakdown we validate against (§24), and what counts as "plausible" versus "biased" for a 250-person, ~100-household town rather than the 1,000-person study cited there?
- Does the skill/wealth "game view" and the family survival dashboard (§19) belong in the existing President/God console (DESIGN.md §8), or as their own separate dashboard?
- Should the ten scripted "control businesses" (§2.5) be made visible to operators as a side-by-side comparison against agent-run businesses, or kept purely as a backend research metric?
- What's the real, DESIGN.md-pinned head-tax rate, so §8–§13's worked examples can be re-run with the actual government-set number instead of an illustrative one?
- Should the household-level "who pays the shared bill" decision (§5.1) stay entirely emergent (whoever chooses to), or should there be an explicit "household payer" role a family can designate, the way a real household sometimes has one person who "handles the bills"?
- Should inheritance (§13) default to the nearest living relative automatically, or should it require an explicit will/claim process the surviving family has to act on?
- When a minor turns 18 (§26), does the parent-billing relationship for their cognition end automatically at the exact age threshold, or should there be a transition grace period, the way a real young adult isn't usually cut off the day they turn 18?
- Should a newly-adult agent (§26) default to staying in their parents' household until they actively choose to leave, or should the town nudge new adults toward forming their own household sooner, to keep the town's household count and housing capacity realistic?

## 29. How to use this document — next steps for the team

This is a plan, not a build ticket — nothing in it should be treated as already implemented. The suggested order of operations, once the team has weighed in on §28's open questions:

1. **Confirm the family-money mechanics already exist as described (§5–§7).** Every number and mechanism in this document was checked directly against the real code (`world/life/upkeep.py`, `world/ledger/head_tax.py`, `world/tools/primitives/commerce.py`, `world/life/constants.py`, `world/ledger/money.py`) at the time of writing — but code moves, so re-verify before building anything on top of it, the same discipline COGNITION_DELIVERY.md's own local-testing plan (its §4) already recommends for the persona and speech seam.
2. **Decide the household-payer question (§28)** before building any dashboard — whether bill payment stays fully emergent or gets an explicit "household payer" role changes what the per-household view in §19.2 actually needs to display.
3. **Build the dashboard views in §19 as read-only surfaces first** — every one of them reads existing trace-store data, so none of them require new game mechanics to ship, only new queries and a new screen.
4. **Only after the above, consider genuinely new mechanics** — an explicit will/inheritance-claim process, a formal household-payer designation, a life-cycle transition grace period (§26) — each flagged in §28 as a real design decision, not a foregone conclusion.
5. **Re-run the worked examples in §8–§13 and §25 against the real, government-set head-tax rate** once a run is live, replacing this document's illustrative rate with the actual figure, and publish the corrected numbers back into this document so it stays a living reference rather than a one-time estimate.

## 30. Risks and limitations of this plan

Worth naming plainly, so nobody mistakes this document for a guarantee of what will actually happen once agents are running:

- **Every worked-example household in §8–§13 and §25 is illustrative, not observed.** The starting balances, income figures, and day-to-day events were chosen to be plausible and internally consistent with the real constants in Appendix A — but they are not a transcript of an actual run. The first thing to do once agents are live is replace these with real ledger exports from real households, per §29's step 5.
- **The tax rate is a placeholder everywhere it appears.** Every one of this document's daily-bill figures depends on it, and the real number is live government policy, decided by whichever agents get elected — it could move considerably from the illustrative rate used here, up or down, which would shift every household's runway in §14's comparison table.
- **An operator watching too closely can change what they're watching.** DESIGN.md §8 is explicit that viewers "are windows, never authorities," and that discipline matters even more for a family dashboard like §19 than for the town-wide views it already has — the moment an operator starts manually reviving every at-risk household the instant it appears on the watchlist, the research question §9 exists to ask ("does the town's own social fabric catch a falling family, or not") stops being answerable, because the town never got the chance to answer it itself.
- **Not every family shape in §4 was given an equally deep worked example.** "Single, no dependents" is named as an archetype but doesn't have its own full ledger walkthrough in this document — a reasonable next addition, since it's structurally the simplest household in town and worth confirming that simplicity holds up in practice.
- **This document assumes RC-1 and RC-2 (COGNITION_DELIVERY.md) are already fixed.** Every worked example assumes an agent actually knows who it is and can actually speak to family members — neither is true in the system as it stands today (see COGNITION_DELIVERY.md's own problem statement). Nothing in this document can be meaningfully tested until that plan is built first.
- **250 agents is a small population for strong demographic conclusions.** §24 borrows validation methodology from a 1,000-person study; a ~100-household, 250-person town will have thinner data in any one demographic slice, and conclusions about, say, "how single-earner households of a specific occupation fare" should be read with that smaller sample size in mind.

None of the above is a reason not to build this — it's the honest account of where this plan's confidence is real (the mechanics, verified against actual code) versus where it's a reasonable prediction that still needs to be checked against a live town (the specific numbers, the specific outcomes, the specific behaviors).

## 31. Property and land ownership

DESIGN.md §1 already establishes that the town's map is a real tile grid and that government can petition to grow it. What's missing is who actually *owns* any given tile — this section is the plan for that.

### 31.1 The unit of ownership: one tile, one record

The town's land divides into **1×1 units**, each addressable by its own simulation coordinate — the same grid the map already uses, e.g. a plot recorded as `(x:142, y:87)`. Every unit has exactly one owner record at any given time. This is the smallest thing this plan proposes tracking; a building or a farm is just a contiguous set of owned units, not a new kind of object.

### 31.2 Two broad classes: government and private

- **Government assets** — unclaimed land, the government complex, the temple, the college, and any newly petitioned/expanded land (DESIGN.md §1), until it's assigned elsewhere. Government decides what happens to expanded land: keep it for public use (a park, a new civic building, eventually a police station), or **auction it** to private buyers — a real, government-run sale that turns freshly expanded land straight into treasury revenue, the same way a tax or a fee already does.
- **Private assets** — everything else. **Every private tile must be owned by someone or something** — an individual agent, a household, or a business/entity — never left ownerless. An owner can hold any number of 1×1 units: a modest household plot is a handful of tiles: a large landowner (or a business like the construction company) could accumulate many.

A business's own premises — the grocery store's building, a farmer's worked plot — is itself a private asset under this system: it sits on specific owned tiles, not an abstract "the business exists somewhere" assumption.

### 31.3 Inheritance follows the owner's own wishes, not just the default

§6's inheritance row and §13's Reyes lineage example both describe the *default*: an estate passes automatically to the nearest living relative. That default still holds when nothing else is specified — but it is not the only path. **The actual owner can set their own wishes ahead of time — a will — naming exactly who receives what**, for money and for property alike, overriding the automatic default.

This means Kenji's estate in §13 was never only the 7,375 sim shown in that table — if he'd owned his own home, that transfers too, by the identical rule: automatically to Lucia absent a will, or exactly as Kenji specified if he'd set one while he was still alive. A business owner could just as easily will their store to one child and their personal savings to another — the same flexibility a real will provides, now a real, settable fact in the ledger rather than a fixed default nobody can change.

## 32. The engine session log — what every turn records

Every one of the 250 engine processes should produce the same shape of record, every single turn — **irrespective of what the agent is doing, it follows this same shape** — so a researcher (or the dashboard in §19) can reconstruct exactly what happened and why, not just what changed.

### 32.1 The four required fields

| Field | What it captures |
|---|---|
| **1. Input received** | What triggered this turn — perception text, which can include another engine's own action or decision reaching this agent (a family member's transfer, something spoken to them, a price shift caused by someone else's trade) |
| **2. Model reasoning** | The actual reasoning that led to a goal update or a work-progress update — the *why*, not just the *what* |
| **3. Actions** | What actually executed — tool calls (`transfer`, `harvest`, …), a taught skill's reply, or communication (the planned `say` tool) |
| **4. Done status** | Confirmation the turn completed, plus what changed — the updated goal state and the updated environment state, so the next tick's "input received" has something concrete to read |

This maps directly onto the five-step loop in §18: **field 1** is steps 1–2 (environment updates, agent perceives), **field 2** is steps 3–4 (recall goals, decide the local action), **field 3** is step 5 (act), and **field 4** is the loop closing back to step 1. It is also exactly what feeds the trace store, the dashboard in §19, and DESIGN.md §8's "Agent reasoning viewer" — this section names the four fields that viewer needs present on every single turn, not just some of them.

### 32.2 Format: one JSON entry per turn

Every turn's record is written as its own JSON object, keyed by agent id and tick, holding the four fields above. Plain, structured, and machine-readable by design — not because JSON is special, but because everything in §32.3 below depends on a tool being able to read a specific slice of it back out reliably.

### 32.3 What the log is actually for, beyond record-keeping

The session log isn't only a research artifact — it does real work for the agent itself, in two situations:

- **Context recovery.** If an agent's context starts from scratch — a fresh process, a compacted/reset context window — it has no memory of what it was doing a moment ago unless something hands that back to it. The plan: a dedicated tool that reads this agent's own session log and pulls a recent slice of it (the last few turns' input/reasoning/actions/done-status) straight into the fresh context, so the agent picks up knowing what it was doing instead of starting genuinely blind.
- **Recovering from losing track.** The same tool covers a second, related case: an agent that hasn't crashed but has drifted — lost the thread of its own reasoning, or produced an action that doesn't obviously follow from its stated goal. Re-reading its own recent session log through the same tool is how it re-orients, the same way a person might glance back at their own notes.

Both cases go through **one tool, one mechanism** — the session log is the single source either draws from, so there is never a second, parallel notion of "what the agent was doing" that could disagree with the trace.

## 33. Fault tolerance — engines that recover on their own

Two different failure modes need two different fixes, and it matters that they stay separate:

- **The agent loses track of its own reasoning** (§32.3, above) — the engine process is still alive, its thinking has just drifted. The session-log-recovery tool is the fix.
- **The engine process itself stops running** — a crash, an out-of-memory kill, a host issue, anything that ends the process outright. This needs a different fix: **automatic restart, not a person manually spinning it back up.**

The real infrastructure for the second case already exists, partway. `world/run_genesis.py` and `infra/firecracker/supervisor.py` both already define a `Supervisor` protocol with a `restart(unit_id)` method, and every managed engine already carries a `UnitState` (`RUNNING`, `FAILED`, `UNHEALTHY`, …) and a `last_health_check_at` timestamp. **What's missing is the loop that actually watches those states and calls `restart()` when one goes bad** — today, nothing in the codebase calls `.restart()` automatically; a `FAILED` unit stays failed until someone notices. Closing that gap is exactly what this section proposes: a watchdog that polls (or subscribes to) engine health, and the instant one engine reports `FAILED` or goes silent past its expected health-check interval, restarts it itself — the same `unit_id` picking back up (per that protocol's own docstring: "the stable identity across restarts... an agent always lands on the same resources"), with §32's session log giving the restarted engine something to read its way back into.

---

## Appendix A — every constant this document cites

| Constant | Value | Source file |
|---|---|---|
| Town population (default) | 250 | `world/life/constants.py` (`GENESIS_POPULATION`) |
| Minor fraction | 15% | `world/life/genesis.py` (`MINOR_FRACTION`) |
| Elderly fraction | 15% | `world/life/genesis.py` (`ELDERLY_FRACTION`) |
| Working-age fraction | 70% (the remainder) | `world/life/genesis.py` |
| Working-age range | 18–69 | `world/life/genesis.py` (`WORKING_AGE_MIN`, `WORKING_AGE_MAX`) |
| Elderly age range | 70–100 | `world/life/genesis.py` (`ELDERLY_AGE_MIN`, `ELDERLY_AGE_MAX`) |
| Minor age range | 0–17 | `world/life/genesis.py` (`MINOR_AGE_MIN`, `MINOR_AGE_MAX`) |
| School age threshold | 6 | `world/life/namepool.py` (`MINOR_SCHOOL_AGE_YEARS`) |
| Couple-pairing rate (elderly) | 60% | `world/life/genesis.py` (`ELDERLY_COUPLE_FRACTION`) |
| Couple-pairing rate (working-age) | 60% | `world/life/genesis.py` (`WORKING_COUPLE_FRACTION`) |
| Max children per couple | 3 | `world/life/genesis.py` (`MAX_CHILDREN_PER_COUPLE`) |
| Construction company hands | 4 | `world/life/genesis.py` (`CONSTRUCTION_HANDS_COUNT`) |
| Daily upkeep — food | 40 sim | `world/life/upkeep.py` (`UPKEEP_FOOD_SIM_PER_DAY`) |
| Daily upkeep — shelter | 30 sim | `world/life/upkeep.py` (`UPKEEP_SHELTER_SIM_PER_DAY`) |
| Daily upkeep — heat | 20 sim | `world/life/upkeep.py` (`UPKEEP_HEAT_SIM_PER_DAY`) |
| Daily upkeep — total | 90 sim per head | `world/life/upkeep.py` (`UPKEEP_TOTAL_SIM_PER_DAY`) |
| Pharmacy short treatment | 500 sim | `world/life/constants.py` (`PHARMACY_SHORT_TREATMENT_COST_SIM`) |
| Revival cost | 10,000 sim | `world/life/constants.py` (`REVIVAL_COST_SIM`) |
| Baby blessing / childbirth | 5,000 sim | `world/life/constants.py` (`BABY_BLESSING_COST_SIM`) |
| Temple wedding fee | 1,000 sim | `world/life/constants.py` (`TEMPLE_WEDDING_FEE_SIM`) |
| Minor age limit | 18.0 years | `world/life/constants.py` (`MINOR_AGE_LIMIT_YEARS`) |
| Pension minimum age | 70 | `world/ledger/money.py` (`PENSION_MINIMUM_AGE`) |
| Pension — private sector | 25% of last salary | `world/ledger/money.py` (`PENSION_PRIVATE_PCT`) |
| Pension — government career | 50% of last salary | `world/ledger/money.py` (`PENSION_GOVERNMENT_PCT`) |
| Business starting sim balance (genesis) | 20,000 sim | `world/life/constants.py` (`GENESIS_BUSINESS_STARTING_SIM_BALANCE`) |
| Total system budget | $1,000 | DESIGN.md §3 |
| Genesis wallet per agent | $2 (200 cents) + 5,000 sim (population default) | DESIGN.md §3 |
| Exchange rate | 10,000 sim → $1 | `world/life/constants.py` (`EXCHANGE_SIM_PER_USD_CENT`), DESIGN.md §3 |
| Revival cost, expressed in USD credit | derived from `REVIVAL_COST_SIM // EXCHANGE_SIM_PER_USD_CENT` | `world/life/constants.py` (`REVIVAL_USD_CREDIT_CENTS`) — the same 10,000-sim revival cost from a different angle: what it's worth in cognition, not just in sim |
| Target population band | 250–300 | DESIGN.md §5, "population is a god-tab dial" |

The head-tax rate itself has no fixed number — it's live government policy (`GovernmentPolicy.tax_rate_bps`, `world/charter/models.py`), which is why every worked example in this document (§8–§13) labels its own tax-rate assumption as illustrative rather than pinned.

## Appendix B — occupation categories, the full mapping

Every occupation in §2.3 also maps to a persona-content category (`world/life/genesis.py`, `OCCUPATION_TO_CATEGORY`) — this is what lets a farmer's formative history, competence, and daily rhythm read as genuinely outdoor-trade-specific rather than generic, and is the same mapping that decides how a family's occupational identity (§22's "family skill tree") reads across a household with, say, one farmer parent and one government-clerk parent.

| Occupation | Category |
|---|---|
| farmer | outdoor |
| miner | outdoor |
| forester | outdoor |
| government_clerk | civic |
| office_administrator | civic |
| college_instructor | academic |
| college_student | academic |
| artisan | trade |
| trader | trade |
| day_laborer | trade |
| construction_company_owner | construction |
| construction_worker | construction |
| grocery_store_owner | grocery |
| grocery_clerk | grocery |
| cafe_owner | cafe |
| barista | cafe |
| pharmacist | pharmacy |
| retired | elderly |
| child (pre-school minor) | minor_young |
| student (school-age minor) | minor_school |

Six broad categories in total — outdoor, civic, academic, trade, construction, and the four named-business categories (grocery, cafe, pharmacy, plus construction) — cover every working adult in the roster, giving §4's archetypes a second axis: not just *how many earners and dependents*, but *what kind of work* is actually funding the household.

## Appendix C — quick-reference index of every worked example

| Household | Section | Archetype (§4) | What it's there to demonstrate |
|---|---|---|---|
| Okafor-Reyes | §8 | Dual-earner | The baseline "surviving comfortably" case — two incomes, a full cushion, room to absorb a shock |
| Duval | §9 | Single-earner | The full escalation path (§5.3) from thin income to hunger to the point where outside help genuinely matters |
| Almeida | §10 | Owner household | What durable wealth looks like — recurring profit exceeding the bill by enough to fund both generosity and reinvestment |
| Chen | §11 | Elderly-alone | The slow, quiet version of financial pressure — not a crisis, a multi-month decline |
| Fitzgerald | §12 | Large family | How headcount alone, not effort or character, can put a hardworking household on a knife's edge |
| Reyes lineage | §13 | Multi-generational, post-inheritance | Death and inheritance as a real, traceable transaction spanning three generations |
| Nakamura-Kaur | §25 | Newlywed | The lightest possible bill, and the fastest recovery, precisely because there are no dependents yet |

Two narrated days sit alongside these: §15 (Okafor-Reyes, a comfortable day) and §27 (Duval, a harder one) — both running the exact same goal/environment/work loop from §18, showing that the loop itself never changes shape; only what the environment offers the agent to decide between does.

## Appendix D — glossary

- **Agent** — one of the 250 AI-driven residents of Kasukabe; also called a person or a character throughout this document.
- **Household** — the group of agents (usually a family) who share a home, and therefore share a daily upkeep and head-tax bill.
- **Upkeep** — the daily cost of food, shelter, and heat, charged per head, owed by the household.
- **Head tax** — the daily per-capita levy owed to the treasury, set by government policy.
- **Dependent** — a minor or a retired agent, both of whom draw on household resources without earning a wage.
- **Dormancy** — the state an agent enters when it can no longer fund its own thinking or its household can no longer cover its bills; the precursor to death if unresolved within 24 game-hours.
- **Revival** — paying 10,000 sim to bring a dormant agent back before the 24-hour death clock expires.
- **Estate / inheritance** — what a deceased agent's remaining balance becomes, passed to surviving relatives.
- **Persona** — the full character depth (§3) behind one agent: biography, values, fears, relationships, and more, distinct from their occupation or age alone.
- **Archetype** — one of the eight recurring household shapes this document names (§4), used to classify any of the ~90-110 real households the genesis generator produces.
- **Tick** — the smallest unit of simulated time, 10 game-minutes; 144 ticks make one game-day.
- **`transfer`** — the real, already-implemented tool call that moves sim-currency from one agent to another, used for every family money flow this document describes.
- **`say`** — the planned speech tool (see COGNITION_DELIVERY.md) that lets one agent talk to another, publicly or addressed to one listener.
- **`teach`** — the tool that transfers a (reduced) copy of a skill from one agent to another, co-located and consensual.
- **Control business** — one of ten scripted, non-agent businesses (§2.5) that exist purely as a research baseline, not part of the 250.
- **Trace store** — the system-wide log of every event and transaction, the data source behind every dashboard view in §19.
- **Genesis** — the moment the town is generated and every one of the 250 agents, their households, occupations, and starting balances are created; "at genesis" means "as the town first exists, before any agent has acted."
- **Sim-currency (sim)** — the town's internal money: wages, prices, upkeep, tax, gifts, and every family transfer in this document are denominated in it.
- **USD / cognition budget** — the separate, real-dollar budget (capped at $1,000 town-wide) that funds an agent's actual thinking; distinct from sim-currency, which funds everything else (DESIGN.md §3's "two currencies: energy and money").
- **Location gate** — a rule requiring an agent to be physically at a specific place to use a given tool (e.g. `marry` requires being at the temple); `transfer`, the tool behind every family money flow in this document, has no location gate at all — family and friends can send money to each other from anywhere in town.
- **Runway** — how many game-days a household (or an individual agent) can keep covering its bills at its current net income before running out, the central number on every dashboard view in §19.
- **At-risk watchlist** — the town-wide view (§19.3) that sorts every household by runway, ascending, so the households closest to trouble are the easiest to spot.
- **Ablation arm** — a research grouping (full / no-reflection / no-learning) some agents are assigned to at genesis, unrelated to family structure, used to compare how much an agent's memory and learning systems actually matter to its behavior (DESIGN.md §10).
- **President / God console** — the operator's control surface (DESIGN.md §8): the President half handles land and vetoes, the God half handles prayers, blessings, and disasters; this document proposes the family dashboard (§19) as an addition to the same console.
- **Charter** — the town's governing rulebook, amendable by the elected government itself (DESIGN.md §7); the source of the head-tax rate this document's worked examples treat as illustrative.
- **Ledger** — the real, per-agent double-entry accounting system (`world.ledger.core.Ledger`) every sim-currency and USD movement in this document actually runs through; nothing described here bypasses it.
- **Genesis generator** — the code (`world/life/genesis.py`) that builds the 250, their households, occupations, and personas from a random seed; every demographic figure in §2 was read directly from it, not estimated.
- **Runaway household** — informal term used in this document for a household whose net daily flow is negative and shows no sign of correcting on its own without outside help (§9, §11); not a formal system state, only a pattern worth naming on the dashboard in §19.
- **Windfall** — a large, one-time, non-recurring gain to a household's balance — most often an inheritance (§13), distinct from the household's ordinary recurring income.
- **Illustrative** — this document's own flag, used throughout, for any number that is a reasonable stand-in rather than a value read directly from DESIGN.md or the real code; every illustrative figure is named as such at the point it's used, most often the head-tax rate (§6, Appendix A).
- **Succession** — what happens to a business, a household's finances, or a family's occupational identity after an owner's death or retirement (§13, §23); not automatic in any direction — a surviving relative may inherit, sell, let it lapse, or start something entirely new.
- **Net daily flow** — one household's income for a day, minus that day's fixed bill and any discretionary spending; the single number every worked example's ledger table in §8–§13 and §25 is ultimately building toward.
- **Discretionary spending** — anything a household spends beyond its fixed upkeep and tax bill: gifts (§10), investment (§10, §13), tuition (§6), or simply enjoying a comfortable margin (§25) — the part of the ledger persona, not physics, actually decides.
- **Shared bill** — the combined upkeep and head-tax obligation a household owes as a unit each day (§5.2), even though no household wallet exists to pay it from directly.
- **Profit share** — the recurring income an owner draws from their own business's earnings (§10), distinct from a wage, and the mechanism behind every owner-household example's income line.
- **Escalation path** — the specific, ordered sequence of consequences (mild hunger, then a malnutrition ailment, then dormancy, then death) an unpaid household bill triggers if it goes uncorrected (§5.3).
- **Delinquency** — an unpaid head-tax debt, once it's been formally recorded and routed into the town's complaints-and-courts pipeline rather than simply forgiven or forgotten (§5.4).
- **Family tree view** — the proposed dashboard surface (§19.5) rendering the relationship graph visually, with each agent's live survival status shown directly on their node.
- **Money-flow view** — the proposed town-wide dashboard surface (§19.6, §20) showing where sim-currency is actually moving between businesses, government, and households, week to week.
- **Wealth ticker / leaderboard** — the proposed dashboard surface (§19.4) ranking households by wealth, weekly earnings, giving, and saving, for a glance-level read on the whole town.
- **Household solvency view** — the proposed per-household dashboard surface (§19.2) showing combined balance, daily bill, net flow, and runway for one specific family at a time.
- **Per-agent survival status** — the proposed single-agent dashboard surface (§19.1), the same shape a health bar takes in a game, covering USD, sim, runway, and status at a glance.
- **1×1 unit / plot** — the smallest addressable piece of land (§31), one simulation coordinate, always owned by exactly one government or private record.
- **Government asset / private asset** — the two ownership classes every tile falls into (§31): government (unclaimed, public buildings, newly expanded land) versus private (must always have a named owner — a person, household, or business).
- **Will** — an owner's own advance instructions for how their estate (money and property alike) is split on death, overriding the automatic nearest-relative default (§31).
- **Session log** — the four-field record (input, reasoning, actions, done status) every engine process produces each turn (§32), feeding the trace store, the "Agent reasoning viewer," and the agent's own context-recovery tool.
- **Energy** — this document's name for the USD/cognition budget (§ "A note on terms," near the top), matching DESIGN.md §3's own framing: what an agent spends to think, act, move, and work, bought only with money, only at the government store.
- **Supervisor** — the real interface (`world/run_genesis.py`, `infra/firecracker/supervisor.py`) that starts, stops, and can restart one engine process; §33 proposes the missing watchdog loop that actually calls its `restart()` method automatically.

---

**In short:** the town described across the last thirty-plus sections is not one economy shared evenly by 250 identical residents — it is roughly a hundred separate, individually fragile-or-resilient family economies, each one funded by whichever of its members chooses, tick after tick, to keep paying the bill. Whether that adds up to a town that looks after its own is not something this document can answer. It's the thing the town, once actually running, gets to answer for itself.

*Reference material this plan draws on: DESIGN.md (world rules), ROADMAP.md (what's currently broken), COGNITION_DELIVERY.md (persona + speech delivery plan), and the real implementation this document verified every number against: `world/life/genesis.py` (the population generator), `world/life/upkeep.py` (household billing and its consequences), `world/ledger/head_tax.py` (per-head taxation and delinquency), `world/tools/primitives/commerce.py` (the real `transfer` tool), `world/life/constants.py` and `world/ledger/money.py` (every sim-currency figure cited above), `world/run_genesis.py` and `infra/firecracker/supervisor.py` (the existing Supervisor/restart interface §33 builds on), the two papers cited in section 24, and `simile.com`'s live product pages (§19.7 and §24).*

*September · internal research · plan, pending review*
