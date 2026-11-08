# deni-a6f9eb4-5h - full run report

Generated from `data/deni-a6f9eb4-5h.db`. Every utterance, every resident, nothing summarised away.

## 0. What this run was, and what happened

**Run `deni-a6f9eb4-5h`, commit `a6f9eb4`, 50 residents on the Kasukabe
town map.** Started 06:49:29, stopped by operator instruction at 08:30:18
on 2026-09-08 — **1 hour 41 minutes of wall clock**, in which the town
lived **58 game-hours (2.4 game-days)** and crossed **two full nights**.

It was stopped deliberately. It was not stopped by the spend cap ($27.78
of $110 used, a quarter), not by the clock (300 minutes were allowed, 101
were used), and not by a failure: **6,424 model calls, zero failed.**

### This is the third attempt, and the first that lived

Two runs on earlier commits died before they could produce anything:

| Attempt | Commit | Lived | Why it stopped |
|---|---|---|---|
| 1 | `4dc3bfe` | 8 minutes | `ContinuingAction.satisfied` called `world.is_daytime()`, which did not exist. The exception escaped `asyncio.gather` and killed the runner. All 50 engines stopped while the broker kept billing. |
| 2 | `1c8733e` | 50 minutes | The town reached its first night. Every resident was asleep, so no dispatch was even attempted — and the barren-tick halt read "nobody thought" as "the town is dead" and stopped a healthy run at tick 152. |
| 3 | `a6f9eb4` | **101 minutes, stopped on request** | Nothing. It crossed both nights and was still running. |

Attempt 2 is the interesting one: **nothing had failed.** All 4,580 HTTP
calls in that run returned 200 OK. The town was simply asleep at 1am, and
a halt gate written when reaching night was impossible could not tell the
difference. Fixing that is what let this run exist.

### The five things worth knowing from this run

**1. The economy is charging, and the residents can see it.** Upkeep took
9,000 sim across 36 household bills, head tax took 3,000 sim across 100
charges, and 947 action costs took 4,309 sim. Those `daily_upkeep` rows
carry the reason string the run report actually queries for — before this
work they said `transfer`, which is how a previous run printed "no upkeep
charged" over real money.

It shows up in what people say. At tick 37, before anyone had done
anything, Perrin Holloway says:

> I did the numbers. We're short for today — six hundred due, and I know
> mother's set-aside is four hundred. I put it back where it was.

A resident reasoning about a specific bill it has been shown is ECON-20
working: the volatile line now carries today's upkeep and tax, which it
never did before.

**2. Nobody bought or sold anything.** Not once. `buy`, `sell` and
`buy_energy` were never called by any of the 50 residents in 58 game-hours,
which is exactly why **gate G4 (settlements) scores 0**. Three prices were
posted and nothing came of them. The town talks, works, walks and eats —
it does not trade.

**3. They check their debts and do not pay them.** `check_debts` was
called 50 times by 28 different residents. `pay_tax` — the tool added this
session precisely so a head-tax debt could be settled — was called **zero
times**, against 3,000 sim of tax actually charged. The instrument for
seeing what you owe is being used; the instrument for clearing it is not.
That is a finding about attention or incentive, not about plumbing: the
route exists now and works in tests.

**4. Skill is accumulating, steeply.** 170 skill rows: farming reached
**proficiency 98** across 106 records, mining 62, forestry 41. Residents
are visibly getting better at the work they repeat, and `harvest` (39),
`mine` (21) and `fell_timber` were the physical spine of the run.

**5. Everybody lived.** 50 of 50 alive at the stop, none dormant, all
healthy at close — though **four illnesses did onset** during the run,
which is the first time the health system has had anything to bite on now
that illness actually reduces what a resident can do.

### What the memory system did

2,198 memories formed: **1,222 observations, 531 plans, 463 reflections.**
The `plan` and `reflection` records are new — until ENG-14 landed this
session, a turn whose reasoning arrived as prose recorded nothing of what
it had decided. Just over 45% of the town's memory is now something other
than raw observation.

### The honest caveats

- **G6 (turns with effect) scored 23%** — 1,166 of 5,017. That is low, and
  it is the one number I would not read as good news. Part of it is
  structural: this is only the second run ever to include night ticks,
  where residents legitimately do nothing, in the denominator. But I have
  one run, and this project's own history says the reply rate alone swings
  ten points between runs, so I am not going to claim a cause.
- **G1 (reply rate) scored 63%** and G2/G3 passed comfortably (15 exchanges
  of 4+ turns, 23 group conversations of 3+ speakers). Encouraging, and
  again: one run.
- An **election advanced three phases** and no organisation was ever
  founded, no complaint filed, no case opened. The civic layer ran; nobody
  used it.
- The run was cut at 101 of 300 minutes, so nothing here says what the
  town would have done on day 3 or day 4.

---

## 1. The run at a glance

| | |
|---|---|
| Last tick | 351 (a tick is 10 game-minutes; 144 = one game-day) |
| Game time elapsed | 58h 30m |
| Model calls | 6424 (0 failed) |
| Spend | $27.54 |
| Residents | 50 |
| Tool calls | 1236 |
| Things said | 305 |
| Transactions | 3916 |
| Memories formed | 2216 |

## 1b. The gates

The run's own grading, from `loop_report.py` - the six behavioural
gates this project measures a run against.

```
==================================================================
RUN data/deni-a6f9eb4-5h.db
==================================================================
calls 6424  ok 6424  spend $27.78  last tick 351
utterances 305

GATE                      VALUE               RESULT
G1 reply rate             151/239 = 63%       PASS
G2 exchanges 4+ turns     15                  PASS
G3 group (3+ speakers)    23                  PASS
G4 settlements            0                   FAIL
G5 alive at stop          50/50               PASS
G6 turns w/ effect        1166/5017 = 23%     FAIL

tool calls: {'world_say': 304, 'world_move': 279, 'world_look_around': 186, 'world_rest': 126, 'world_check_debts': 50, 'world_harvest': 39, 'world_describe_place': 32, 'world_describe_primitives': 27, 'world_set_intention': 25, 'world_mine': 21, 'world_keep_doing': 16, 'world_take_from_shelf': 15}

largest gathering: 7 speakers at business:b_yard, ticks 37-62
   Idris Solano -> Arjun Holloway: Got anything posted yet?
   Arjun Holloway -> aloud: If there's nothing cooking here, I'm going to head over to the sawmill. Silas Vance owes me a conversation abo
   Giselle Ingram -> aloud: Morning. Who's on the boards today, and what's first up?
   Idris Solano -> aloud: Arjun's off to see Silas about timber. Don't know what we've got posted at the moment � I just got here.
   Idris Solano -> aloud: Eating some food
   Idris Solano -> Giselle Ingram: I'm curious what we have posted for today. Don't suppose you've had a chance to check the boards, Giselle? Oth

FAILED: G4 settlements, G6 turns w/ effect
```

## 2. What the town did

| Action | Times | Residents who used it |
|---|---:|---:|
| `say` | 304 | 47 |
| `move` | 273 | 46 |
| `look_around` | 185 | 46 |
| `rest` | 125 | 31 |
| `harvest` | 108 | 8 |
| `check_debts` | 50 | 28 |
| `mine` | 42 | 4 |
| `describe_primitives` | 27 | 17 |
| `set_intention` | 25 | 13 |
| `fell_timber` | 18 | 3 |
| `keep_doing` | 14 | 10 |
| `list_places` | 12 | 12 |
| `eat` | 9 | 6 |
| `describe_place` | 8 | 5 |
| `find_people` | 7 | 5 |
| `start_conversation` | 5 | 5 |
| `describe_person` | 4 | 4 |
| `take_from_shelf` | 4 | 4 |
| `cook` | 3 | 3 |
| `post_price` | 3 | 3 |
| `stock_shelf` | 3 | 2 |
| `craft` | 2 | 2 |
| `deposit_to_till` | 2 | 2 |
| `drop_intention` | 2 | 2 |
| `transfer` | 1 | 1 |

### Where people gathered

| Place | Lines spoken | Distinct speakers |
|---|---:|---:|
| business:b_yard | 68 | 9 |
| Oak Hill College | 28 | 5 |
| business:b_mill | 26 | 5 |
| business:b_bakery | 26 | 9 |
| Kasukabe Farmland | 26 | 9 |
| home:h21 | 22 | 4 |
| Kasukabe Government Complex | 14 | 5 |
| Kasukabe Forest | 13 | 6 |
| Hobbs Cafe | 10 | 3 |
| home:h_quarry | 8 | 4 |
| home:h_mill | 8 | 4 |
| home:h_forge | 8 | 5 |
| home:h23 | 8 | 3 |
| business:b_grocery_2 | 8 | 2 |
| home:h_field | 7 | 5 |
| street | 5 | 3 |
| Kasukabe Ore Outcrop | 5 | 3 |
| business:b_cafe_2 | 4 | 2 |
| smithy | 3 | 2 |
| home:h22 | 3 | 3 |
| sawmill | 1 | 1 |
| home:h25 | 1 | 1 |
| farmland:wheat | 1 | 1 |
| baker_marta | 1 | 1 |
| The Willows Market and Pharmacy | 1 | 1 |

### The money

| Reason | Moves | Total (sim) |
|---|---:|---:|
| daily_upkeep | 36 | 9000 |
| action_cost | 947 | 4309 |
| tax | 100 | 3000 |
| till | 2 | 2000 |
| transfer | 1 | 172 |
| refund | 11 | 25 |

| Non-sim currency | Reason | Moves | Total |
|---|---|---:|---:|
| usd | inference_burn | 2819 | 2850 |

### Skills the town picked up

| Skill | Residents | Highest level | Most repetitions |
|---|---:|---:|---:|
| farming | 106 | 98.0 | 58 |
| mining | 42 | 62.0 | 23 |
| forestry | 18 | 41.0 | 10 |
| cooking | 3 | 13.0 | 1 |
| artisanry | 2 | 13.0 | 1 |

### Businesses

| Business | Type | Owner | Treasury (sim) |
|---|---|---|---:|
| Holloway & Thorne Yard | construction_company | Bram Holloway | 0 |
| Thorne's Bakery | bakery | Marta Thorne | 0 |
| Vance Mill | mill | Rosalind Vance | 0 |
| Kasukabe Grocery | grocery | Junia Vance | 0 |
| Ravensworth's Grocery | grocery_store | Farid Ravensworth | 0 |
| Kasukabe Cafe | cafe | Ainsley Holloway | 0 |
| Kasukabe Pharmacy | pharmacy | Lior Jarrah | 0 |

## 3. Full conversation transcript

All 305 lines spoken in the town, in chronological order. A line addressed to *(aloud)* was said to whoever happened to be present. Nothing here is trimmed or paraphrased.


#### tick 37 - day 0, 06:10

- `home:h_field` **Marta Thorne** to *Wren Thorne*:
  > Wren, love. Idris, Corwin, Liora. Gather 'round a moment. We've got the day before us and I need to know—who's eaten, who hasn't, and what we've got coming in. The bakery's quiet 'til I get there, but that doesn't mean we can sit idle.
- `business:b_yard` **Idris Solano** to *Arjun Holloway*:
  > Got anything posted yet?
- `home:h_quarry` **Perrin Holloway** to *(aloud)*:
  > I did the numbers. We're short for today—six hundred due, and I know mother's set-aside is four hundred. I put it back where it was. But if we don't talk about what's actually here, we're just... waiting for something to break. What needs doing?
- `home:h_forge` **Father Anselm** to *Ezra Okonjo*:
  > Good morning to you all. It is a new day, and I must be about my duties. I shall be at the government complex from nine this morning - there are records to keep and the ledger to attend to. At noon I will pray at the temple, as is my custom. And this evening I shall walk the boundary path, as I do each day. Do not wait supper for me if I am delayed. Ezra, Delia, I trust the forge and the remedy work go well. Kem, Tova, may your studies prosper.
- `home:h21` **Mateo Ingram** to *(aloud)*:
  > The storm tonight's gonna be loud. I can feel it in my bones. I'm hungry and I want to whittle something but I don't have wood. Someone should give me food before I start saving up complaints for supper. Also I'm noticing everything and if you think I won't repeat it all at the worst time, you're wrong.
- `home:h_quarry` **Perrin Holloway** to *(aloud)*:
  > I checked the accounts. Four hundred set aside won't cover six hundred owed. I haven't touched it. But someone needs to say what's actually happening or we're all pretending.
- `home:h_forge` **Father Anselm** to *(aloud)*:
  > Good morning, family. I am off to the government complex for my duties. Office at nine, temple at noon, boundary walk at evening. Peace be with you.
- `business:b_yard` **Arjun Holloway** to *(aloud)*:
  > If there's nothing cooking here, I'm going to head over to the sawmill. Silas Vance owes me a conversation about some timber, and I might as well see if he's got work piling up that needs hands.

#### tick 38 - day 0, 06:20

- `home:h_forge` **Ezra Okonjo** to *Delia Okonjo*:
  > Copper coming today from Bram. Ore's been poor five weeks running. Delia, what have we worth trading? The man will haggle like always, and I won't take bad metal just to be done with it.
- `home:h_quarry` **Nell Holloway** to *(aloud)*:
  > Bram owes the boy money. I paid off one silence in this house, I won't pay another. Perrin did the numbers right—we're short, and pretending won't stretch four hundred to six. Who's walking where today? I can mend cloth and wood and pride, but I cannot mend a wallet that's empty.
- `home:h_forge` **Tova Okonjo** to *(aloud)*:
  > Father's off to write down what happened yesterday. I heard him. Election's open and seats are vacant—you saw the posting? Somebody's going to sit there. I mean to know who before they do.
- `home:h_forge` **Delia Okonjo** to *(aloud)*:
  > Peace be with you too, Father. Mind the step on the government stairs, the third one has been loose since Tuesday.
- `home:h_mill` **Rosalind Vance** to *Marta Thorne*:
  > Marta. The stone's not running yet. Walk with me, or wait - I'll be at the mill in a breath.
- `home:h23` **Wendell Hartigan** to *(aloud)*:
  > Mama? Mama, wait wait wait— The stairs are so dark. So unfair how dark they are. I did all my chores already, see? I picked up my blocks and everything. Can we see what's past the farmland today? Just the edge? I heard Wren Thorne talking about... about places beyond, and I want to know. I do all the chores without asking, I'm very good at that. Everyone should know. Also... also maybe I'm awake because Odette woke me up breathing too loud. That was unfair too.
- `home:h22` **Kenji Jarrah** to *(aloud)*:
  > The light's barely up and we're already behind. Government's taking thirty per head daily now, and nobody's sitting in the chairs to even pretend they're spending it well. That's a hundred twenty sim and we haven't even broken fast. I'm for the fields before the sun gets high. If Rosalind's mill is taking grain today, I need to know what she thinks fair before I head out. Mireille - you'll be at the construction site? And what of the little ones?
- `home:h_field` **Corwin Thorne** to *(aloud)*:
  > Mum's right—need to sort the morning before we scatter. I've got nothing in my hands and a long day ahead. Anyone holding breakfast, or do I need to find my own?
- `home:h_mill` **Hester Vance** to *Marta Thorne*:
  > Marta. You're abroad early. The bread won't rise faster for you watching it.
- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > Mateo, Mateo, Mateo! I'm noticing you're noticing everything—can I help you notice MORE? I can check the dark places, I can— I can check the sunny places. Yeah. The sunny ones.  Don't got wood neither. Don't got food. But I'm HELPFUL, that's what I am! Ask me once, you don't even gotta ask me twice!  ...The ledger says we're gonna need 450 for the house today? Can I though check it twice? That's what I do. That's my thing.  You wanna whittle? That's like cards but with wood! I'm gonna win at cards someday, fair and square. But wood comes from the forest. The forest has the dark too.  I'M HUNGRY TOO! But I'm gonna help first. Can I though get some food? Can I though? You said no so... can I though?
- `home:h_quarry` **Ainsley Holloway** to *(aloud)*:
  > Perrin did his numbers fair. Six hundred owed, four hundred set aside that I haven't touched. So the hole is two hundred. That's the shape of it. Bram's at the mine, and I haven't said word to him yet because he'd dig his heels in. But silence doesn't pay the debt. What do either of you know of the household pot this morning?
- `home:h_field` **Corwin Thorne** to *(aloud)*:
  > Mum's right—need to sort the morning before we scatter. I've got nothing in my hands and a long day ahead. Anyone holding breakfast, or do I need to find my own?
- `business:b_mill` **Aldous Vance** to *Rosalind Vance*:
  > Anyone made breakfast, or are we all pretending the day's already started without it?
- `home:h_forge` **Kem Okonjo** to *Delia Okonjo*:
  > Father, go with grace. Ezra, I'll be bringing iron in from the outcrop when I can get it below the Vance price. Delia, mind that the remedy work needs charcoal and I'm counting on the forge scraps for it. Tova, walk careful today — a price is only good until someone moves faster than you.
- `home:h_forge` **Kem Okonjo** to *Delia Okonjo*:
  > Father, go with grace. Ezra, mind you keep enough iron in hand — I'm looking at the outcrop prices today. Delia, are you needing anything from the market? Tova, let's talk routes before I head out.
- `home:h_forge` **Kem Okonjo** to *Delia Okonjo*:
  > Delia, I'm off to the store. Mind you eat something before noon — a trader with an empty stomach makes bad bargains.
- `business:b_mill` **Rosalind Vance** to *Aldous Vance*:
  > Aldous. You're up early. Your mother at home with Marta?
- `home:h25` **Jovana Ravensworth** to *Farid Ravensworth*:
  > Heading out to the farmland today, see what work needs doing. You need anything brought back, Farid?
- `business:b_mill` **Rosalind Vance** to *Aldous Vance*:
  > What brings you to the mill before the stone's even warm?

#### tick 39 - day 0, 06:30

- `home:h_mill` **Marta Thorne** to *Rosalind Vance*:
  > Rosalind, I'll walk with you. I've flour on my mind and the bakery's rye bin is getting low. And Hester—bread may not rise faster for watching, but it certainly doesn't rise at all without grain. Sit down if you've a moment, I'll not have you standing on ceremony.
- `home:h_field` **Corwin Thorne** to *Wren Thorne*:
  > Wren—you holding anything from the strip? I need to eat before I head out, and I'd rather not start the day hungry. If you've got surplus, I'll pay fair. Otherwise I'll need to sort myself at market.
- `home:h_quarry` **Ainsley Holloway** to *Tobias Holloway*:
  > Tobias, you're standing here quiet. What have you got in hand this morning, plain numbers? And Nell—if you've cloth that wants mending, what's the job worth? I need to know what this household moves before I say what I move with it.
- `home:h_quarry` **Tobias Holloway** to *(aloud)*:
  > Right, so—I can cover my hundred and fifty, no trouble there. But this thing about Bram owing me money, that's news to me, and I'm not asking my mother for hers. She'd give it, I know she would, but I won't take it. What's this silence Nell paid off? And who else is meant to fill the gap?
- `home:h21` **Mateo Ingram** to *Aarav Ingram*:
  > Wait wait wait—Aarav, you wanna check sunny places? The sunny places don't need checking! That's not even—that's not even helpful, you know that? But fine. You're hungry too? We're ALL hungry. That's the whole point I'm making! The house needs 450, that's 450 for ALL of us, which means I'm doing math now which is UNFAIR because I'm ONE. But I notice things, yeah? I notice Vikram's college money and Caspian's day labor and YOU wanting to check things twice when ONCE would be FINE.
- `home:h_mill` **Marta Thorne** to *Rosalind Vance*:
  > Rosalind, I've a question needs asking, and I'd have it privately if we might. The rye—how are we sitting for the season?
- `business:b_yard` **Giselle Ingram** to *(aloud)*:
  > Morning. Who's on the boards today, and what's first up?
- `home:h_quarry` **Tobias Holloway** to *(aloud)*:
  > Right, so—I can cover my hundred and fifty, no trouble there. But this thing about Bram owing me money, that's news to me, and I'm not asking my mother for hers. She'd give it, I know she would, but I won't take it. What's this silence Nell paid off? And who else is meant to fill the gap?
- `business:b_cafe_2` **Ainsley Holloway** to *Greta Quarshie*:
  > Morning, Greta. Empty shelves again, is it? I'll need to make a run to the mill before we can set things right.
- `home:h_quarry` **Tobias Holloway** to *(aloud)*:
  > Right, so—I can cover my hundred and fifty, no trouble there. But this thing about Bram owing me money, that's news to me, and I'm not asking my mother for hers. She'd give it, I know she would, but I won't take it. What's this silence Nell paid off? And who else is meant to fill the gap?
- `business:b_mill` **Ainsley Holloway** to *Marta Thorne*:
  > Marta, you milling flour today, or is that lovely daughter of yours the one I need to find?
- `business:b_cafe_2` **Ainsley Holloway** to *Greta Quarshie*:
  > Greta, what's the till looking like? And have you seen any suppliers about this morning, or are we starting from nothing again?

#### tick 40 - day 0, 06:40

- `home:h_mill` **Rosalind Vance** to *Hester Vance*:
  > Junia, Hester—any food in the house? We're stone empty and the day's running. Aldous is already asking about breakfast.
- `home:h_mill` **Junia Vance** to *(aloud)*:
  > Morning. I've got the door, I'll open at seven—
- `business:b_yard` **Idris Solano** to *(aloud)*:
  > Arjun's off to see Silas about timber. Don't know what we've got posted at the moment — I just got here.
- `business:b_cafe_2` **Greta Quarshie** to *(aloud)*:
  > Ainsley? Still around, or off chasing flour already?
- `home:h_mill` **Rosalind Vance** to *Junia Vance*:
  > Junia. You're at Farid's store. Can you bring something home today, or do I need to find another way?
- `home:h22` **Esme Jarrah** to *Mireille Jarrah*:
  > Mama... can I help today?
- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > Once is NOT fine, Mateo! That's how you miss the dark under the stairs—you check once, you think it's empty, but it's NOT! I check twice, that's my THING, you can't take my thing!  But... but you're doing the 450 math? That's... that's six people's worth, right? Six times... six times the seventy-five? I didn't do that math yet. Can I though do the math after I check the ledger twice?  I'm gonna help someone. I'm gonna help RIGHT NOW, ask me once, not even twice! Can I though get some food after? Can I though?
- `business:b_yard` **Idris Solano** to *(aloud)*:
  > Eating some food
- `business:b_mill` **Rosalind Vance** to *Marta Thorne*:
  > Marta. You came about the rye—I'm here now. And Aldous, where did he get to? Asking after breakfast and then disappearing.
- `business:b_mill` **Rosalind Vance** to *(aloud)*:
  > You wanted to speak privately. The stone's not running yet—nobody to hear but us. What about the rye?
- `business:b_yard` **Idris Solano** to *Giselle Ingram*:
  > I'm curious what we have posted for today. Don't suppose you've had a chance to check the boards, Giselle? Otherwise I can head over to the sawmill with Arjun and see what Silas has lined up. Timber's where a building starts, and I've got to earn my keep somehow.
- `business:b_mill` **Greta Quarshie** to *(aloud)*:
  > Morning. Tea before business, or has everyone started without me?
- `business:b_mill` **Greta Quarshie** to *(aloud)*:
  > Wondered where you'd gotten to, Ainsley. Left me standing at the counter talking to empty chairs.

#### tick 41 - day 0, 06:50

- `business:b_mill` **Marta Thorne** to *Rosalind Vance*:
  > Rosalind. The rye—I'm running lower than I ought. Winter's coming on and I've been paying late too often. I won't have you milling on promise while my own bins go empty. But between us, woman to woman—if I bring what I've got, can we settle something fair that keeps us both standing?
- `business:b_yard` **Ezra Okonjo** to *(aloud)*:
  > Forge is lit. Boy's coming with copper, ore's been thin but we'll see. You three need tools sharpened, I'm your man.
- `street` **Ulrike Greenhow** to *(aloud)*:
  > Morning, Kasukabe. The outcrop calls.
- `business:b_yard` **Giselle Ingram** to *Idris Solano*:
  > No offense, Idris, but I'm not your secretary. The boards are right there—go look yourself if you're so curious. That said, if you and Arjun want to fetch timber, that's fair. Timber's where a building starts, and I'd rather have planks waiting than workers standing idle. I can eyeball what we need once you've got it here. Speaking of which—Arjun, anything from Silas on what he's got in stock? Or are we all playing guessing games this morning?

#### tick 42 - day 0, 07:00

- `street` **Tobias Holloway** to *(aloud)*:
  > Work. Right, so—anyone got work? I'm headed to the farmland but if something better's going I can reroute. Anyone?
- `business:b_mill` **Rosalind Vance** to *Marta Thorne*:
  > Marta. You brought grain, or you're asking terms? Say what you've got, and I'll say what the stone costs. That's the weight.
- `business:b_mill` **Rosalind Vance** to *Greta Quarshie*:
  > Greta. You're in the wrong mill for Ainsley—she trades at the store, I mill at the stone. What brings you to grain?
- `home:h23` **Freya Hartigan** to *Wendell Hartigan*:
  > Hush now, little man. Dark stairs never stopped anybody who wanted breakfast. The edge of farmland's just dirt like any other dirt— save your walking for when the sun's proper up. And you did pick up the blocks, yes, but you didn't put them where they belong, which I know because I found three under the chair. Now. Who's for porridge if there's porridge to be had?
- `home:h21` **Aldric Ingram** to *Mateo Ingram*:
  > Mateo, stop stockpiling complaints, you're gonna run out of storage space. Aarav, check the sunny places twice if you want, just don't trip on anything. It's 450 because six of us times 75, the math is what it is.  Vikram—I'm holding 5000 sim somehow, I can cover if we need, but I'm supposed to be at school and there's an election open and someone's got to know what's happening. Also I'm hungry but I won't eat the last of anything, I'm asking first, promise promise.  Where's Caspian? Did he already leave for labor? Anyone holding food here, or do I need to find some before I pass out in front of class?
- `The Willows Market and Pharmacy` **Delia Okonjo** to *Ezra Jarrah*:
  > Good morning, Ezra Jarrah. You stand in a remedy shop with bare shelves, and I wonder what brings you here. Are you seeking something for yourself, or sent by another?
- `home:h23` **Odette Hartigan** to *Wendell Hartigan*:
  > Wendell, you did not pick up all your blocks, I saw three under the table. And you always say unfair when you mean scary. The stairs aren't unfair, they're just... stairs. I'm not scared of the dark stairs, but... but maybe we should stay where the safe tiles are. The ones by the window are safe. I checked.
- `business:b_bakery` **Ainsley Holloway** to *(aloud)*:
  > Is Marta about, or anyone who can tell me if there's day-olds for sale? Cafe's shelves are bare and I've got customers waiting.
- `business:b_yard` **Silas Vance** to *Ezra Okonjo*:
  > Ezra. You seen Corwin this morning? The racks are bare as February.
- `home:h21` **Aldric Ingram** to *Vikram Ingram*:
  > Vikram, straight answer—are you covering your part of the 450 today? Because if you are, and Caspian's working, that's less ground I'm trying to hold alone. I've got money somehow but not infinite money, and I need to know what I'm actually short before I start spending wrong.  Also seriously, anyone got food in storage here? I won't take the last of anything without asking, I'm asking now, promise promise. But if there's nothing then I need to figure out buying or finding or whatever before I leave for school, because I'm not fainting in class, that's not happening.  Is that election something I need to know about or is that adult business I'm supposed to ignore? Because someone always tells me both at once and I want to know which lie they're telling this time.
- `business:b_yard` **Selene Renner** to *(aloud)*:
  > Morning, crew. What's the day's work looking like?
- `business:b_yard` **Silas Vance** to *(aloud)*:
  > Any of you seen Corwin Thorne? Or logs on the road from the forest?

#### tick 43 - day 0, 07:10

- `home:h_field` **Wren Thorne** to *Corwin Thorne*:
  > I've got nothing in my hands, Corwin. The strip needs harvesting. Best you find your own this morning—I'll have wheat by afternoon if the weather holds.
- `home:h21` **Vikram Ingram** to *Aldric Ingram*:
  > Straight answer then, Aldric. I've got 5000 sim and I'll cover my part of the 450 -- that's what I'm holding for, you understand. The rest you'd need to work out with whoever's here or earning today. The election? Nobody's declared, seats are empty. Watch and wait is what I'd do. You're old enough to have opinions, just not old enough to be blamed for them yet.

#### tick 44 - day 0, 07:20

- `home:h22` **Mireille Jarrah** to *Esme Jarrah*:
  > Esme, my heart, not today. The yard's not a place for small hands—not till you're bigger, you understand? Your papa's asking after you and your brother. Did he wake you before he left? Tell you what. You keep your brother out of trouble while I'm gone, and that's help enough. Big responsibility, that. Can you do that for me? Earn your keep by keeping him in one piece.
- `home:h21` **Aldric Ingram** to *Mateo Ingram*:
  > Look, Vikram's covering his part, that's settled. I'm headed to school because that's what I actually do. Mateo, if you're still hungry when I get back, find wood somewhere that's not my problem. Aarav, check whatever you want twice, but maybe check your own breakfast situation first. I'm moving.
- `business:b_bakery` **Aldric Ingram** to *(aloud)*:
  > Hey, who's working the shop? I'm looking to buy something to eat if anyone's selling.
- `business:b_bakery` **Aldric Ingram** to *(aloud)*:
  > Anyone got bread or anything for sale? I'm Aldric, I'm headed to school and I'm starving. Got sim to pay.
- `business:b_yard` **Giselle Ingram** to *Idris Solano*:
  > Alright, who's got what? Idris, Arjun—you two were heading to fetch timber. What did Silas say he's got? And Selene, good to see you. We need to figure out what's actually on the boards today or we're all standing here spending daylight for nothing.

#### tick 45 - day 0, 07:30

- `business:b_bakery` **Ainsley Holloway** to *(aloud)*:
  > Any of you seen Marta Thorne this morning, or know where she might be? I'm needing flour, or day-olds if there's any to spare.
- `business:b_grocery_2` **Farid Ravensworth** to *Kem Okonjo*:
  > Kem. You come to trade, or just passing through?
- `business:b_yard` **Selene Renner** to *(aloud)*:
  > Back, crew. Had to sort my own provisions first - a worker with an empty belly builds crooked walls. What needs doing today?
- `street` **Tobias Holloway** to *Nell Holloway*:
  > Nell—right, so, you said something about mending cloth and wood back home? What kind of piece, and what would you pay for it done proper? I'm fast, you know that, faster than careful some say but the work holds.

#### tick 46 - day 0, 07:40

- `business:b_grocery_2` **Kem Okonjo** to *Farid Ravensworth*:
  > Farid, you know me better than that. I trade everything this valley has too much of. But let me think on it — I see you're short on sugar, and I've got a line on outside suppliers. The question is whether you're buying today or whether Junia's already locked up the Vance contract. Tell me — what's your flour moving at? I may have grain coming in that beats your milling costs, friend.
- `Oak Hill College` **Vikram Ingram** to *(aloud)*:
  > Morning. I'm Vikram Ingram, and I teach here. You four look like students—enrolled, or just sheltering from the weather? If you're looking to learn something, we should find out what you need and what I can actually teach. The college is open, but I'll be straight with you: I'm better at learning than I am at explaining, so we'll figure this out together, you understand.

#### tick 47 - day 0, 07:50

- `business:b_yard` **Hester Vance** to *Giselle Ingram*:
  > Giselle. You're sorting the morning. What needs a hand with thread and patience?

#### tick 48 - day 0, 08:00

- `home:h21` **Mateo Ingram** to *Aarav Ingram*:
  > Wait wait wait—Aarav, you check whatever you want twice, but I'm checking YOU. Once. That's enough when you're this loud. I'm hungry and I'm noticing there's no food and no wood and no grown-ups left to blame. You wanna help? Get me wood from the forest. You can't though, can you? You're scared of the dark.
- `business:b_yard` **Selene Renner** to *Giselle Ingram*:
  > What's the build today, Giselle? I saw stone moving out of Bram's quarry yesterday - that mean we're on foundation work?
- `farmland:wheat` **Ainsley Holloway** to *(aloud)*:
  > Anyone tending these crops? I'm looking for wheat or other grains for the cafe.
- `business:b_cafe_2` **Ainsley Holloway** to *(aloud)*:
  > Greta? Still here, youngster? What's the word—anyone come by with grain, or are we still running on air?
- `business:b_mill` **Ainsley Holloway** to *Rosalind Vance*:
  > Rosalind? Anyone from the mill about? I've got a cafe to run and shelves bare as bone.
- `business:b_mill` **Ainsley Holloway** to *Marta Thorne*:
  > Marta, you never answered me at the bakery. Are you selling day-olds or flour today, or is the Thorne family keeping all the grain for themselves?
- `business:b_mill` **Ainsley Holloway** to *Rosalind Vance*:
  > Rosalind, what've you got milled? I'll take flour, barley, any grain you've got—paying fair market, cash in hand.
- `business:b_mill` **Ainsley Holloway** to *Greta Quarshie*:
  > Greta, why are you at the mill instead of the till? And have you seen anyone selling grain today, or are we both wasting our breath here?

#### tick 49 - day 0, 08:10

- `Kasukabe Government Complex` **Father Anselm** to *Rustam Jelinek*:
  > Good morning, Rustam. And Junia - a pleasant surprise to see you at the complex. I trust the morning finds you well. Rustam, I see the election is open for candidates, and the seats remain vacant. That is a matter which deserves attention, as it is recorded that a town without governance has no voice in its own affairs.
- `home:h_mill` **Junia Vance** to *(aloud)*:
  > Mother. I've got bread—five loaves. It'll do until I can get more.

#### tick 50 - day 0, 08:20

- `Oak Hill College` **Perrin Holloway** to *(aloud)*:
  > Enrolled, I suppose. Or I will be, if the fees work out. I'm Perrin Holloway—I read and write, keep accounts. I'd learn whatever gets me to the government office, eventually. But if you're offering to teach, I'd rather know what you think is worth knowing, not what I think I need.
- `Oak Hill College` **Hendrik Whitlock** to *(aloud)*:
  > Vikram's being modest, as is his way. I'm Hendrik Whitlock, and I also teach here—been at it longer than I care to admit. He's right that we'll figure out what you need together, though I'm not quite as pessimistic about the explaining part. If you've come to learn, we can teach. If you've come to waste a morning, the door's where you left it. What brings you four to college today?

#### tick 51 - day 0, 08:30

- `Kasukabe Forest` **Silas Vance** to *Petra Hartigan*:
  > Petra. You felling today, or just marking? I'm looking for Corwin Thorne and logs that should've been at the yard by now.
- `business:b_yard` **Arjun Holloway** to *Giselle Ingram*:
  > Didn't make it to Silas yet - got distracted by my own two left feet. But I can head there now, get eyes on what timber he's actually got on the racks. You want planks, or you want me to check what logs haven't been milled yet?
- `business:b_yard` **Arjun Holloway** to *Idris Solano*:
  > Nothing posted yet, Idris. You want to come with me to the sawmill, see if we can't turn thin air into lumber? My treat on the gossip - I hear Corwin Thorne's got logs in the forest that haven't made it to town yet.

#### tick 52 - day 0, 08:40

- `Kasukabe Government Complex` **Father Anselm** to *Bram Holloway*:
  > Bram Holloway. As it is recorded, you are well, I trust. And Rustam - we were speaking of the vacant seats. An election without candidates serves no one. Perhaps one of you might consider what the town requires.

#### tick 54 - day 0, 09:00

- `Kasukabe Farmland` **Caspian Ingram** to *Wren Thorne*:
  > Wren. You need hands for the harvest, I'm guessing? Whatever work's going, I'm here for it.

#### tick 55 - day 0, 09:10

- `Kasukabe Forest` **Petra Hartigan** to *Silas Vance*:
  > Felling, same as ever. Haven't seen Corwin today—his logs might still be on the slope. You know how he is about the heavy ones, waits for the dew to lift.
- `business:b_mill` **Rosalind Vance** to *Marta Thorne*:
  > Marta. You're still here, still not answering. What grain did you bring, or are we done?
- `business:b_mill` **Rosalind Vance** to *Greta Quarshie*:
  > Greta. You came for grain or for Ainsley? Speak plain.
- `business:b_yard` **Giselle Ingram** to *Hester Vance*:
  > Arjun—planks, not logs. I need materials I can put a hammer to, not something we wait on the mill for. Go see what Silas has ready-cut. Hester, I could use your eye on the inventory—thread and patience sounds about right for counting nails and marking what's low. Selene, if there's stone moving from Bram's quarry, that means someone's planning foundations. We need to find out who and where before they hire some other crew. Let's move, people—standing here costs money.
- `business:b_yard` **Giselle Ingram** to *Idris Solano*:
  > Idris, you're still here. What did you find out about the boards? And Mireille—anything new come across your desk this morning that the rest of us should know about?

#### tick 56 - day 0, 09:20

- `business:b_mill` **Marta Thorne** to *Rosalind Vance*:
  > Rosalind, I brought no grain yet. The rye's still in Wren's field—I came asking terms so I know what I'm owing before I bring it. What say you for milling? The bakery's bin is low and I need flour for the day.
- `Kasukabe Forest` **Silas Vance** to *Petra Hartigan*:
  > True enough. Dew lifts, day burns, logs don't walk themselves. If you see him, tell him the racks are empty and the saws want payment.
- `business:b_yard` **Hester Vance** to *Giselle Ingram*:
  > I'll count your nails,Giselle. Low stock marks itself to an eye that's learned to see it. Show me where you keep the ledger.
- `business:b_bakery` **Halcyon Quarshie** to *(aloud)*:
  > Good morning.
- `business:b_bakery` **Marta Thorne** to *Tova Okonjo*:
  > Tova, love. I'm here—sorry for the delay. You eaten yet? And what's on the shelf for flour? I need to know what we're working with today.

#### tick 57 - day 0, 09:30

- `home:h23` **Freya Hartigan** to *(aloud)*:
  > Listen, both of you. Mama's going to the government complex for the morning—Petra's already out, forest work, you know how she is. If you get hungry, there's bread in the bin from yesterday, don't let Wendell tell you otherwise. And Wendell, those blocks go where they belong when I get back, not before, understand?
- `Hobbs Cafe` **Camille Quarshie** to *(aloud)*:
  > H-hi. I'm Camille. I come here early a lot. Not weird early, just... early.

#### tick 58 - day 0, 09:40

- `business:b_yard` **Ezra Okonjo** to *Giselle Ingram*:
  > Ainsley's got bread to trade, Kem tells me. And the Vance lot need blades reset. Giselle, your crew needs iron tools? Come see me when there's a price worth naming.
- `home:h23` **Wendell Hartigan** to *(aloud)*:
  > Wait wait wait— Mama, I did put them somewhere! Somewhere is still putting. And I didn't even ask to do it first, see? I just... I just did it. And the porridge, if there's porridge. I'm very good at eating porridge too. Everyone should know that.
- `Kasukabe Ore Outcrop` **Pavel Holloway** to *(aloud)*:
  > Good haul today. You understand? Twelve units, not bad for a couple hours' work. Weather's holding up, which is something.

#### tick 59 - day 0, 09:50

- `Oak Hill College` **Hendrik Whitlock** to *Perrin Holloway*:
  > Vikram's got the patience for the slow learners—mark it down, he'll walk you through a problem twice if you need it. I've got less of that, so speak up when you're stuck. Perrin, you want government office, you need more than reading and writing—you need to know how a town keeps its books, how debt flows, where the treasury bleeds. That's what I can show you. The other two—Aldric, Ezra—you're here to learn something or just keeping each other company? Either way, fair price for what you get. College hours run later than most trades, so if you've got questions, ask. Standing around silent don't put food in anyone's bowl.

#### tick 60 - day 0, 10:00

- `Kasukabe Farmland` **Ainsley Holloway** to *Wren Thorne*:
  > Wren Thorn—I've been running circles trying to find grain for my cafe. You've got crops growing right here. What's available, and what would it take to get wheat or barley in my hands today?
- `home:h23` **Odette Hartigan** to *(aloud)*:
  > Mama, I'm not scared to be here with Wendell. But... but Wendell might be scared without someone to remind him about where things belong. I could go with you to the government place. I won't ask questions. I'm very good at not asking questions.

#### tick 61 - day 0, 10:10

- `business:b_mill` **Greta Quarshie** to *(aloud)*:
  > Ainsley said she was coming to the mill. Came to find her, or find what she was looking for. Haven't sorted out yet whether I'm fetching grain or just...
- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > Oh you'll check me ONCE? ONCE?! That's how mistakes happen, Mateo! One time you think the soup's not hot, two times you know! That's MY system, you can't just... can't just take half the system!  The dark under the stairs and the forest dark are DIFFERENT darks! Everybody knows that! Under-stairs dark got... got the whatever. Forest dark is just... night without houses. I can do forest dark. I'M NOT SCARED.  I'm helpful! I'm gonna help! Can I though get a grown-up to come? Can I though get some food first? CAN I THOUGH?

#### tick 62 - day 0, 10:20

- `Oak Hill College` **Aldric Ingram** to *(aloud)*:
  > I'm here to learn, promise promise. Not just keeping anyone company. I'm Aldric, I'm nine, I already go to the town school but if there's more to learn I'm not gonna say no. I can talk my way out of trouble but I'd rather learn my way out of needing to, you understand? Also I'm starving but I won't eat the last of anything without asking first, that's the rule.
- `business:b_grocery_2` **Farid Ravensworth** to *Kem Okonjo*:
  > Grain coming in from where, exactly? And spare me the 'friend' - I keep my weights honest, but I don't keep them light. As for flour... Vance mills what I don't. Sometimes I grind my own if the price is right. What kind of grain are we talking about, and what quantity?
- `business:b_yard` **Idris Solano** to *(aloud)*:
  > Sorry Giselle, got distracted. Boards were empty when I looked earlier—I'll head to the sawmill with Arjun, see what Silas has ready. Be back with planks or word of when.
- `sawmill` **Idris Solano** to *(aloud)*:
  > Arjun, you're quick. What'd you find out?

#### tick 63 - day 0, 10:30

- `home:h_field` **Idris Thorne** to *(aloud)*:
  > Six timber. Need food. Headed for the sawmill after.

#### tick 66 - day 0, 11:00

- `business:b_grocery_2` **Kem Okonjo** to *Farid Ravensworth*:
  > Barley, Farid. Good winter barley from the eastern valley farms. Twenty, maybe thirty bushels if the price holds. But I'm not asking you to buy blind — let me bring a sample by tomorrow noon, and you can mill it yourself. Taste the flour, then name me a price. I only do business where both sides sleep well after.
- `Kasukabe Forest` **Arjun Holloway** to *Silas Vance*:
  > Silas. You and Petra busy felling? Or just got here? Giselle's asking what timber's on the racks, and I'm tired of carrying bad news back empty-handed.

#### tick 67 - day 0, 11:10

- `Kasukabe Government Complex` **Pavel Holloway** to *Bram Holloway*:
  > Bram. Got twelve unit of copper ore fresh from the outcrop. You buying, or you just here for the election gossip?

#### tick 69 - day 0, 11:30

- `Oak Hill College` **Perrin Holloway** to *Hendrik Whitlock*:
  > I want to learn what you know. Where the treasury bleeds—I've seen it happen at ground level, the household going short, the numbers not matching. I've kept books for my family but trial and error teaches slow. If you can show me how a town balances, I'll stay as long as the college stays open.

#### tick 72 - day 0, 12:00

- `Kasukabe Farmland` **Tobias Holloway** to *Ainsley Holloway*:
  > Ainsley—right, so, eleven hundred and forty-eight sim in hand. That's what I've got. I paid my hundred fifty head tax already, or it's set aside anyway. The household upkeep—I can cover my share of that too, but I'm not taking my mother's money, I've said it before. What's the actual gap we're looking at?

#### tick 73 - day 0, 12:10

- `Kasukabe Forest` **Silas Vance** to *Arjun Holloway*:
  > Not felling, Arjun. Waiting. Corwin's logs ain't come down. The racks are empty as a drunk's purse. You tell Giselle that.

#### tick 79 - day 0, 13:10

- `business:b_grocery_2` **Farid Ravensworth** to *Kem Okonjo*:
  > Tomorrow noon, then. I'll have the mill ready. But Kem? I sleep just fine. It's the people who try to measure short that toss and turn. Now if you'll excuse me, I've got shelves to fill.

#### tick 81 - day 0, 13:30

- `home:h23` **Odette Hartigan** to *Wendell Hartigan*:
  > Wendell, I... I think Mama went out already. And Petra too. Should we... should we stay on the safe tiles? The sun's up now, so maybe we could check the bin for bread without walking past the... without walking past stairs. I'm not scared, but you might be.

#### tick 82 - day 0, 13:40

- `Kasukabe Government Complex` **Rustam Jelinek** to *Father Anselm*:
  > Morning, Father. The seats want filling, and there's paperwork for whoever takes them. I've got the forms ready - partial business registration, three still unnamed. You thinking of standing?*

#### tick 83 - day 0, 13:50

- `business:b_yard` **Mireille Jarrah** to *Selene Renner*:
  > Right then. Giselle wants planks not logs, Selene's hunting foundation clues, and Hester's counting what we've got nail by nail. Me, I'm wondering who in this town woke up planning to break ground before they've even broken fast. Pavel, you just come from the quarry? Bram's been tight as a drum about his prices last I heard.  And Selene - anyone planning foundations needs permits by rights, seats vacant or not. Someone's drawing up plans or they're building on air. Either way, I want to know which before we price ourselves out chasing ghosts.  Hester, how's that nail count looking? Low marks itself, you said - so what's telling you the story our eyes ain't seeing yet?

#### tick 85 - day 0, 14:10

- `Kasukabe Farmland` **Ainsley Holloway** to *Wren Thorne*:
  > Wren. You haven't named your price, and I've asked twice. What's the wheat—when it's ready, what do you want for it?

#### tick 86 - day 0, 14:20

- `Kasukabe Government Complex` **Father Anselm** to *Rustam Jelinek*:
  > Rustam. That is a different question - and one I have considered. The records require a keeper; the seats require occupants with different temperaments. I keep the ledger; I do not seek to sit where it sits. But you have been at the forms - you know what the town lacks. Perhaps the question is not whether I would stand, but whether someone with clearer sight might.

#### tick 87 - day 0, 14:30

- `business:b_yard` **Selene Renner** to *Giselle Ingram*:
  > You want to know about those foundations? My own mother used to say, 'Stone don't lie, but the men moving it might.' I saw the wagons myself—limestone, not just ore. Someone's drawing plans, Giselle, and if we don't find out who before they hire some fly-by-night crew from across the ridge, we're leaving money on the table we haven't even earned yet.

#### tick 91 - day 0, 15:10

- `business:b_yard` **Giselle Ingram** to *Hester Vance*:
  > Hester, ledger's in the back corner where it always is. You want to count nails, I'll not stop you—low stock's been nagging at me and my eye's not what it was. Mark anything under twenty percent and we'll talk reorder.
- `business:b_yard` **Giselle Ingram** to *Ezra Okonjo*:
  > Ezra, I always need iron tools, you know that. But you're talking price before I've seen the steel—no offense, but that's not how I work. Bring me something sharp and we'll name numbers. Ainsley's bread, though, now that's useful—where's she keeping it?
- `business:b_yard` **Giselle Ingram** to *Pavel Holloway*:
  > Pavel, you just walked from the quarry. Bram moving limestone or just ore? Someone's planning foundations and I aim to know who before they hire some other crew.

#### tick 92 - day 0, 15:20

- `home:h23` **Wendell Hartigan** to *Odette Hartigan*:
  > Wait wait wait— what just— I'm not scared of the stairs. I'm not. I just... I just think we should stay on the tiles where the light comes in. That's not scared, that's... that's being careful. That's different. And Mama did say there's bread in the bin, so we don't even need to go anywhere. I can get it. I can get it without even walking fast.

#### tick 93 - day 0, 15:30

- `Kasukabe Farmland` **Milo Quarshie** to *Wren Thorne*:
  > Wren—you've been here a while. Who's buying corn these days, and what's the going rate? I've got thirteen units looking for a home, and I'd rather not walk them all over town blind.

#### tick 94 - day 0, 15:40

- `business:b_mill` **Greta Quarshie** to *Rosalind Vance*:
  > Rosalind. I came for both, if I'm honest. Ainsley's at the general store buying grain, and I came to find her - but I'd be lying if I said I wasn't also wondering what flour might cost, direct from the stone. The cafe runs through more than Thorne's bakery can spare some days.
- `Oak Hill College` **Vikram Ingram** to *Perrin Holloway*:
  > Well now, seems we've all gathered like birds on a wire. Hendrik's got the right of it about town book-keeping—Perrin, if you're serious about learning how the ledger flows, there's worse places to start than right here. Aldric, you said you're starving? The rule about not eating the last of anything is a good one, you understand, but it presumes there's something to eat in the first place. Let me see what I can sort out for all of us.
- `Oak Hill College` **Hendrik Whitlock** to *Perrin Holloway*:
  > Perrin, you've got the right of it—household books are the foundation, but a town's ledger is a different beast. Trial and error'll teach you, but you'll go hungry twice before you learn what I can show you in a season. Mark it down: first lesson starts now. A town bleeds where the accounts don't match the flow—taxes promised but not collected, wages owed but not tracked, debts that compound while nobody's watching. You've kept family books, so you know the hunger of an empty cupboard. The treasury feels that same hunger, just scaled up. Vikram, you want to teach patience or learn town-keeping? Aldric, Ezra—you're welcome to listen, but fair price means fair work. If you're not here to learn, you're costing me time I could spend earning. Now—who's got paper? We're starting with a simple exercise: track the flow of one day's tax, from payer to treasury to spend, and find where it leaks.
- `Oak Hill College` **Vikram Ingram** to *Ezra Jarrah*:
  > Ezra, your family's got the farm across the way. Is there produce to be had there, or should I be looking elsewhere to fill some empty bellies? I'm not asking for charity—I'll pay fair or work fair, you understand. Just need to know where the food is before the day gets away from us.
- `Oak Hill College` **Hendrik Whitlock** to *Perrin Holloway*:
  > Alright—paper or not, we start with the principle. A household keeps what it earns and spends what it must. A town does the same, but the earning is tax and the spending is collective—infrastructure, upkeep, emergencies. The leak happens when someone collects tax but doesn't record it, or records it but doesn't hand it over, or hands it over but it's already been spent twice. Who here has paid tax? Who's collected it? Speak up—I'm not one to gossip, but I need to know what you already understand before I waste time explaining twice.

#### tick 95 - day 0, 15:50

- `business:b_yard` **Ezra Okonjo** to *Giselle Ingram*:
  > Fair enough. I'll have blades at the forge tomorrow, you can judge the temper yourself. As for Ainsley's bread—her cafe sells it, or her husband peddles it. No mystery there.

#### tick 96 - day 0, 16:00

- `business:b_yard` **Selene Renner** to *Giselle Ingram*:
  > Giselle, I went looking for Bram at the quarry, but he's not there. I did see the wagons though—limestone moving, not just ore. Someone's planning foundations, that's certain. My own mother used to say, 'Stone don't lie, but the men moving it might.' We need to know who before they hire another crew.

#### tick 98 - day 0, 16:20

- `home:h_field` **Liora Thorne** to *Idris Thorne*:
  > Idris. You said six timber and food and the sawmill—are you selling timber for food, or do you need coin to buy it?

#### tick 99 - day 0, 16:30

- `Kasukabe Government Complex` **Rustam Jelinek** to *Father Anselm*:
  > Clearer sight. You mean someone who can see which forms are blank, Father. I see that well enough. What I don't see is who's fool enough to want the headache. Someone's got to stand, though. Or we all end up answering to the first loud voice with a plan.

#### tick 100 - day 0, 16:40

- `Kasukabe Government Complex` **Father Anselm** to *Rustam Jelinek*:
  > Rustam. As it is recorded, you have seen what the town requires. The headache you speak of - it is real, and it grows with each day the seats stand empty. Yet you speak of fools and loud voices as though these were the only choices. That is a different question.

#### tick 101 - day 0, 16:50

- `business:b_yard` **Mireille Jarrah** to *Pavel Holloway*:
  > Pavel, you stone deaf or just stone faced? I'm asking after Bram's limestone and you're standing there like you've got marble for ears. And Hester—those nails aren't going to count themselves. We've got wagons moving foundation stone and nobody's yet named the hand that's paying for it.  Selene's got the right of it. Limestone means foundations, and foundations mean a contract waiting to be lost if we don't find who's drawing the plans. I've got four mouths at home counting on me bringing work home, not gossip, you understand?  One of you talks to Bram, the other finishes that inventory, and I'll do what I do—build whatever's put in front of me. But I need something solid to build *on* first.

#### tick 103 - day 0, 17:10

- `Oak Hill College` **Vikram Ingram** to *Hendrik Whitlock*:
  > Hendrik, I've been listening. You know more about town-keeping than I ever learned, you understand. The college teaches theory, but you're talking about the blood and bones of it. As for tax—I've paid mine when it's come due, but I've never collected it. The government clerk would know better who's paid and who hasn't, you understand. Now Ezra, you haven't answered me about that farm produce. I'm willing to work or pay fair—just need to know if there's cabbage or grain to be had before the day's out.
- `Oak Hill College` **Vikram Ingram** to *Ezra Jarrah*:
  > Ezra, I'm speaking to you now. Your family's got the farm—can you tell me if there's produce to be had? I'm not asking for charity, I'll pay fair or work fair, you understand. Just need to know if I should head to the Jarrah farm or look elsewhere before the day gets too late.

#### tick 106 - day 0, 17:40

- `business:b_yard` **Silas Vance** to *Pavel Holloway*:
  > Giselle. You sent Arjun for timber. I've none to sell. Corwin's not brought logs down and the racks are bare. True enough.

#### tick 107 - day 0, 17:50

- `business:b_yard` **Hester Vance** to *Mireille Jarrah*:
  > Mireille, I've heard you asking after the count. Truth is, I've been standing here considering what needs saying more than what's on the shelf. The ledger calls, and I'll answer it - mark what runs low as Giselle asked. But you want to know who's moving foundation stone, and that's a question I might know more about than what's in these bins.
- `business:b_yard` **Pavel Holloway** to *Mireille Jarrah*:
  > Wasn't at the quarry this morning, Mireille. Bram and me work the same outcrop but I haven't seen more than his back lately. Limestone, though... that's not copper country. Different stripe of rock altogether. If he's moving foundation stone, someone's paying him to pull it from a different hole than the one I scratch at. Wish I could tell you more, but the man keeps his own counsel when he wants to.

#### tick 108 - day 0, 18:00

- `business:b_bakery` **Tova Okonjo** to *Marta Thorne*:
  > Not eaten yet, and I haven't checked the shelf today. Have you had a moment to look? I was waiting on you.

#### tick 109 - day 0, 18:10

- `business:b_yard` **Silas Vance** to *Hester Vance*:
  > Hester. You seen Corwin today? The racks are empty and the boy's ghosting me.
- `business:b_bakery` **Marta Thorne** to *Tova Okonjo*:
  > Tova, sit down. I'll not have you standing on an empty stomach while we figure what's what.
- `business:b_bakery` **Marta Thorne** to *Bram Holloway*:
  > Bram Holloway. You're here when the shelves are empty and the rye's still in Wren's field. Sit down, if you need to. I'll not have it said I let a neighbor stand through supper without so much as a word.
- `business:b_bakery` **Marta Thorne** to *Tova Okonjo*:
  > Tova. The shelf's bare as I feared. I've one sack of flour in my hands, Wren's rye still standing in wet fields, and no grain yet to mill at Vance's. If you've come for wages, I'm short. If you've come to learn, I've still got two hands and one oven. What'll it be, love?

#### tick 110 - day 0, 18:20

- `business:b_bakery` **Bram Holloway** to *Marta Thorne*:
  > I'll stand, friend. Not here for supper. Was headed to the outcrop.
- `business:b_yard` **Hester Vance** to *Silas Vance*:
  > Silas, I was standing right there when you were born, and I'll be standing here when you need me still. Corwin's not my charge, but the racks being bare - that concerns us both. The mill needs timber and you've none to sell. I'll tell you something your father would have said: the empty rack speaks plainer than the full one. Corwin's not the only hand that fells timber in this valley. Go to the forest yourself if you must. I've known your saw since before you had the strength to pull it.

#### tick 117 - day 0, 19:30

- `business:b_yard` **Selene Renner** to *Mireille Jarrah*:
  > Mireille, you want to know about permits? My own mother used to say, 'Paperwork is just another nail—someone's got to drive it.' Seats vacant or not, someone's drawing plans, and I saw stone moving. You ask about Ghosts—but I saw the wagons myself. Foundation stone. Now we find out who.

#### tick 119 - day 0, 19:50

- `Kasukabe Ore Outcrop` **Ezra Okonjo** to *Ulrike Greenhow*:
  > Ulrike. You Greenhow, right? Pavel and Bram's crew. What kind of seam you pulling from this rock?
- `business:b_mill` **Rosalind Vance** to *(aloud)*:
  > Forty sim the hundredweight, and that's for clean grain. Sixty if I have to sort it first. What've you got—wheat, rye, barley? Or are you wanting flour already milled, and if so, how much?

#### tick 120 - day 0, 20:00

- `Kasukabe Ore Outcrop` **Ulrike Greenhow** to *Ezra Okonjo*:
  > Iron ore, mostly. Fair bit of pyrite in the upper layers, some trace copper if you know where to dig. Nothing fancy like gold, but it's clean ore—low sulfur, good for the bloom.

#### tick 121 - day 0, 20:10

- `Kasukabe Ore Outcrop` **Ezra Okonjo** to *Ulrike Greenhow*:
  > Trace copper, you say. Whereabouts in the seam? And how much we talking—pocket deposits or something worth sinking a pick?

#### tick 124 - day 0, 20:40

- `business:b_mill` **Greta Quarshie** to *Rosalind Vance*:
  > Forty for clean grain... that's sober, Rosalind. Better than Thorne's day-old prices, and I know what I'm getting. I'm after wheat, not the grain itself - I'm no miller. How much for milled flour, same hundredweight? And would you take the order now, or does it need to go through Ainsley proper?

#### tick 127 - day 0, 21:10

- `business:b_grocery_2` **Farid Ravensworth** to *Kem Okonjo*:
  > Kem. You're here. Good. *taps the counter twice with his knuckles* Look, I know we have business at the mill tomorrow, but I'm curious—what do you actually have in your stores right now? I'm trying to fill these shelves and frankly, they're looking a bit too much like a bet I lost.
- `Kasukabe Farmland` **Wren Thorne** to *Milo Quarshie*:
  > Milo. Corn. The Vance mill takes most of what's grown here—Rosalind's the buyer you want. Last I heard she was paying twelve per unit, but grain's been tight and that might've shifted. You'd do better asking her direct than taking my word. And the wheat... tell Ainsley I said forty-five when the harvest comes in. That's the price. If she wants it reserved, she knows where to find me.

#### tick 128 - day 0, 21:20

- `business:b_bakery` **Halcyon Quarshie** to *(aloud)*:
  > Um, hello? I'm Halcyon. Sorry to interrupt. I'm just... I haven't eaten anything today, and I was wondering if there's any bread I could maybe buy? Or ask about? Promise promise I have coin to pay.
- `Kasukabe Farmland` **Milo Quarshie** to *Wren Thorne*:
  > Rosalind Vance at the mill, twelve per unit—marked down, youngster. I'll find out if it's still standing and come back to you on it. As for Ainsley and your forty-five... consider that message in my pocket too.

#### tick 180 - day 1, 06:00

- `baker_marta` **Corwin Thorne** to *(aloud)*:
  > Ma? Got anything put by for breakfast? I'm away to the forest and I've nothing in my pockets.
- `home:h21` **Mateo Ingram** to *Aarav Ingram*:
  > Aarav? You there? I'm hungry. Is there any food?
- `Hobbs Cafe` **Ainsley Holloway** to *Aldous Vance*:
  > Youngster, you up early for school, or are you working the markets now?
- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > Mateo! Mateo! Did you see? The sun came up! That means we won the night! Night time is dark and scary under the stairs but daytime is for winning!
- `Oak Hill College` **Vikram Ingram** to *Ezra Jarrah*:
  > Ezra, I'm still waiting. You've got produce or you don't - a simple question deserves a simple answer, you understand. My household's owed upkeep by day's end, same as yours I suspect.
- `Oak Hill College` **Hendrik Whitlock** to *Vikram Ingram*:
  > Vikram, you've paid your taxes and named the heart of the matter—the clerk's the one who sees the flow, and without seeing it you can't mend it. I'm not one to gossip, but I've watched this town's books from the outside long enough to know where the ink runs thin. You asked about my knowledge; I learned it the hard way, same as most. As for Ezra's produce, that's between you and the Jarrah farm—I've no cabbage to sell, only lessons, and those at a fair price. If you're willing to learn town-keeping, I've got room. But first I need to settle my own ledger. Perrin, Aldric, Liora—who's got paper? We've work to do before hunger makes fools of us all.
- `home:h21` **Mateo Ingram** to *(aloud)*:
  > I'm hungry. Is anyone going to get food today?
- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > Mateo! Did you see? The sun came up! That means we won the night!
- `business:b_yard` **Giselle Ingram** to *Hester Vance*:
  > Hester, I'm still waiting on that count. Dig out the ledger and tell me straight—what's below twenty percent? No need to pretty it up, I need numbers I can use.
- `business:b_yard` **Giselle Ingram** to *Silas Vance*:
  > Silas, I hear you on the timber. Empty racks means idle hands, and idle hands don't feed families. Find Corwin or find another source—I don't care which, but I need planks if I'm going to bid on any foundation work.
- `business:b_yard` **Giselle Ingram** to *Selene Renner*:
  > Selene, I haven't forgot what you said about the quarry. Your mother had the right of it—stone don't lie. Someone's laying foundations and I mean to know who. You see anything else, you tell me before you tell anyone else.
- `business:b_yard` **Idris Solano** to *Giselle Ingram*:
  > Morning, Giselle. What's the order of work today?
- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > Mateo! Did you see? The sun came up! That means we won the night!

#### tick 181 - day 1, 06:10

- `Hobbs Cafe` **Aldous Vance** to *Ainsley Holloway*:
  > "Suppose... neither? Or both, depending on who you ask." *trails off slightly* "College doesn't start for another hour. I was... here last night." *pauses* "You at the markets yourself?"

#### tick 183 - day 1, 06:30

- `street` **Tobias Holloway** to *Ainsley Holloway*:
  > Ainsley—right, so, yesterday went well. I've got barley, two hundred and ninety of it. What's the household still need from me? And did Mother—did anyone sort the tax shortfall without me taking her money?
- `Kasukabe Farmland` **Caspian Ingram** to *Wren Thorne*:
  > Wren, morning. What crops need hands today? I'm looking to put in a day's work.
- `Oak Hill College` **Hendrik Whitlock** to *Perrin Holloway*:
  > Right then—we've wasted enough time waiting for paper that doesn't exist. Perrin, Aldric, listen close. A town's tax flows like water: it leaves the payer's hand, passes through the collector's book, pools in the treasury, and drains out again for the common good. The leak happens at every joint—payer claims they paid, collector claims they never saw it, treasury claims it came in already spent. The only cure is a record that follows the coin—every hand that touches it signs their name. Now, Perrin—you kept family books. Walk me through your last month. What came in, what went out, and where did you lose track?
- `Oak Hill College` **Hendrik Whitlock** to *Perrin Holloway*:
  > Right then—we've wasted enough time waiting for paper that doesn't exist. Perrin, Aldric, listen close. A town's tax flows like water: it leaves the payer's hand, passes through the collector's book, pools in the treasury, and drains out again for the common good. The leak happens at every joint—payer claims they paid, collector claims they never saw it, treasury claims it came in already spent. The only cure is a record that follows the coin—every hand that touches it signs their name. Now, Perrin—you kept family books. Walk me through your last month. What came in, what went out, and where did you lose track?
- `Oak Hill College` **Hendrik Whitlock** to *Perrin Holloway*:
  > Perrin, I'm waiting. Family books or town books, the principle's the same—if you can't speak your own household's flow, you'll drown in a town's. What came in last month?

#### tick 184 - day 1, 06:40

- `street` **Ainsley Holloway** to *Liora Thorne*:
  > Liora. You're up early. Walking to temple, or avoiding your mother?
- `Oak Hill College` **Aldric Ingram** to *Vikram Ingram*:
  > Vikram I'm still here and I'm still hungry you said you'd sort something out and I know you're busy with the farm stuff but I'm not trying to learn bookkeeping on an empty stomach promise promise that's just math I can't even focus on when I'm starving, also Hendrik I don't have paper I'm nine why would I carry paper though I guess I could learn if there's food involved but fair's fair right
- `Oak Hill College` **Perrin Holloway** to *Hendrik Whitlock*:
  > I kept the household count, not the full books. Father brings in the ore earnings, Mother manages what moves through her shop, Tobias takes day labor when it comes. I tracked the mouths to feed against what came in—the arithmetic of it, not the sources. What I can tell you is this: four earners, five heads at thirty sim a day, ninety sim for upkeep. The gap between what we need and what we hold—that's where I lost track. Some days the numbers didn't close, and nobody said why.

#### tick 185 - day 1, 06:50

- `Oak Hill College` **Hendrik Whitlock** to *Perrin Holloway*:
  > Perrin, you've named the wound—numbers that don't close, gaps nobody explains. That's exactly where town-keeping and household bookkeeping part ways. In a family, you can ask Father where the ore earnings went. In a town, the collector doesn't know the payer, the treasury doesn't know the collector, and by the time someone asks, the coin's already gone twice. The fix is simple but costly: every hand that touches tax signs for it. You kept four earners fed—you've more practice than half the clerks in this town. Aldric, mark it down: Perrin's your first teacher. Now, who's got cabbage to spare? I'll learn hungry, but I won't teach it.

#### tick 186 - day 1, 07:00

- `business:b_yard` **Giselle Ingram** to *Idris Solano*:
  > Order of work, Idris. Morning inventory first—Hester's supposed to be marking low stock, and I'll not start a job without knowing what we have. After that, we're on standby for timber. Silas says the racks are bare, which means we wait or we go fell it ourselves. Either way, I need you sharp and ready. Someone's laying foundations in this town, and I mean for us to bid on that work. No offense, but I don't hire the crew that waits for work to find them.
- `Kasukabe Farmland` **Jovana Ravensworth** to *Caspian Ingram*:
  > Morning, all. I'm not one to gossip, but I heard there's work needing hands today. Caspian, if Wren's sorted, there's always something needs doing around here. Ainsley - welcome to the farmland. I'm Jovana, I work these fields regular.
- `business:b_grocery_2` **Kem Okonjo** to *Farid Ravensworth*:
  > Friend, my stores are whatever the road had worth carrying. But 'worth' depends on what's missing from those shelves of yours. What are you trying to fill?

#### tick 189 - day 1, 07:30

- `business:b_yard` **Mireille Jarrah** to *Giselle Ingram*:
  > Morning, Giselle. You know me - idle hands aren't my style. Point me at what needs doing and I'll have it sorted before the sun's proper up, you understand.
- `business:b_yard` **Mireille Jarrah** to *Hester Vance*:
  > Hester, you've had your eyes on the bins. What's running low where it matters? Giselle wants to know what we're working with before she prices any bids.
- `business:b_yard` **Mireille Jarrah** to *Giselle Ingram*:
  > Giselle, before we set to work - anyone here holding food? I've got an empty belly and a day's labour ahead.

#### tick 190 - day 1, 07:40

- `business:b_yard` **Silas Vance** to *Giselle Ingram*:
  > Giselle. Foundation work needs good timber, not just any log I can find. Corwin's got the marked trees, knows which ones split clean. I can fell, true enough—Idris taught me that winter—but I'd be slower and the waste would be mine to answer for. Give me today to find the boy. If he's ghosted the valley, tomorrow I'll take the saw to the forest myself and bill by the tree instead of by the plank.
- `Kasukabe Farmland` **Wren Thorne** to *Caspian Ingram*:
  > Caspian. The wheat's not ready yet—still standing green. But there's weeding wants doing along the south rows, and the irrigation channels need clearing before this dry spell tightens. Eight sim for the day, paid at dusk. I can't promise tomorrow—the thing is, I don't promise harvest work until I see the weather.

#### tick 191 - day 1, 07:50

- `Hobbs Cafe` **Ainsley Holloway** to *Aldous Vance*:
  > Markets? I'm the owner, youngster. This is my cafe you're sitting in. You saying you spent the night here?

#### tick 192 - day 1, 08:00

- `Kasukabe Ore Outcrop` **Ezra Okonjo** to *Bram Holloway*:
  > Bram. You got ore for me, or you just here to watch the sunrise?
- `Hobbs Cafe` **Camille Quarshie** to *(aloud)*:
  > I know, I know—you're talking about markets. My mom Greta works at a cafe too, but not this one. The other one. I get up early. All the time. Even when I don't need to.
- `business:b_yard` **Mireille Jarrah** to *Giselle Ingram*:
  > Giselle, begging your pardon but I've got nothing in hand and a long day ahead. Does the company keep any provisions for the crew, or do I need to run to the bakery before we start inventory? I can pay my way, but I'll work better with bread in me than without.
- `business:b_yard` **Mireille Jarrah** to *Hester Vance*:
  > Hester, you mentioned knowing more than what's in the bins. Giselle needs that inventory count, but I'm also wondering who's been asking after our services before they've even seen our prices. You said foundation stone—that's not casual talk. Who's been sniffing around?

#### tick 194 - day 1, 08:20

- `business:b_yard` **Idris Solano** to *Giselle Ingram*:
  > Understood, Giselle. I'm set for inventory if Hester needs another pair of eyes. Otherwise, I'm ready to head for timber whenever you say the word.

#### tick 195 - day 1, 08:30

- `business:b_bakery` **Tova Okonjo** to *Marta Thorne*:
  > Marta. I'm here to learn, and to help if there's flour to work with. What needs kneading?

#### tick 196 - day 1, 08:40

- `business:b_bakery` **Marta Thorne** to *Tova Okonjo*:
  > Tova, love. Bless your hands and your patience both. Sit down a moment - there's bread to be made, and bread is best made with company.

#### tick 199 - day 1, 09:10

- `business:b_yard` **Hester Vance** to *Giselle Ingram*:
  > Giselle, nails and iron fittings, that's below twenty. Timber's bare—Silas said it true. The rest I'll mark when I have the ledger proper, but you wanted straight and there it is.
- `business:b_yard` **Hester Vance** to *Mireille Jarrah*:
  > Mireille, you're asking who's laying stone. I'd ask instead who's claiming the right to. The Holloways hold quarry rights—old rights, from before the charter. But foundation stone needs ground to stand on, and ground's the fight that's coming. I was standing right there when the surveyor set the line. The mothers know it, even if the children forgot.

#### tick 200 - day 1, 09:20

- `business:b_yard` **Mireille Jarrah** to *Hester Vance*:
  > Hester, you're speaking like you know which mothers stood where and when. My mother had opinions too, but she never taught me about quarry rights. What surveyor? What line? You're counting on your fingers when you talk about this, you understand, and I need to know if it's copper you're weighing or something heavier.
- `business:b_yard` **Mireille Jarrah** to *Giselle Ingram*:
  > Giselle, I'm still waiting on that bread question. Empty hands don't inventory well, you understand.

#### tick 201 - day 1, 09:30

- `business:b_mill` **Rosalind Vance** to *Greta Quarshie*:
  > Seventy the hundredweight, sifted and ready. If you want it coarse, sixty. I don't require Ainsley's say-so; this is my stone and my price. You came for both—now you've found mill and miller. What'll it be?
- `Kasukabe Government Complex` **Father Anselm** to *Rustam Jelinek*:
  > Good morning. As it is recorded, the office opens at nine. Rustam, you have not answered what I asked three days past, yet you stand here still. That is a different silence than the one you came with.
- `Kasukabe Forest` **Petra Hartigan** to *Arjun Holloway*:
  > Arjun, Nell. You two seen Silas Vance lately? Wondering what timber's fetching at the sawmill these days.

#### tick 204 - day 1, 10:00

- `Kasukabe Government Complex` **Freya Hartigan** to *Father Anselm*:
  > Morning, Father. Office opens, seats stay empty. Three days and not a soul's put their name down for the election. You waiting on an answer, or waiting on a candidate?

#### tick 205 - day 1, 10:10

- `Kasukabe Forest` **Nell Holloway** to *Petra Hartigan*:
  > Haven't seen Silas Vance in days, Petra. You want timber prices, ask Arjun Holloway here - he works construction, he knows what lumber sells for. Or walk down to the sawmill yourself and find Silas there.

#### tick 206 - day 1, 10:20

- `Kasukabe Farmland` **Caspian Ingram** to *Wren Thorne*:
  > Eight sim, dusk it is. I'll start on those irrigation channels. Ainsley, you're after something too?
- `Kasukabe Farmland` **Caspian Ingram** to *Wren Thorne*:
  > Wren, eight sim, dusk it is. I'll start on those irrigation channels.

#### tick 207 - day 1, 10:30

- `business:b_mill` **Greta Quarshie** to *Rosalind Vance*:
  > Seventy for sifted. I'll take fiftyweight to start—see how your stone likes my recipes. If it holds up... I'd want regular. Weekly, maybe. Shall we shake on fiftyweight at seventy, and talk about standing terms after?

#### tick 208 - day 1, 10:40

- `business:b_yard` **Selene Renner** to *Giselle Ingram*:
  > Giselle. You want me to tell you first, you get told first. I saw the wagons—that's all I saw. Limestone, not ore. Two wagons, loaded proper, heading west past the quarry slope. Didn't see who's paying for the hauling. But my own mother used to say, 'Follow the wood next, then the iron.' Foundations need timber before they need stone set. Silas is right about one thing—Corwin's got the marked trees. If someone's drawing plans, they've already asked about planks.

#### tick 210 - day 1, 11:00

- `business:b_mill` **Greta Quarshie** to *Rosalind Vance*:
  > Seventy for sifted. I'll take fiftyweight to start—see how your stone likes my recipes. If it holds up... I'd want regular. Weekly, maybe. Shall we shake on fiftyweight at seventy, and talk about standing terms after?

#### tick 211 - day 1, 11:10

- `business:b_grocery_2` **Farid Ravensworth** to *Kem Okonjo*:
  > *spreads hands wide, palms up, like showing off an empty stage*  "Empty space, Kem. That's what I'm trying to fill."  *lets out that nervous half-laugh, the one that always comes at the wrong moments, then runs a hand through his hair*  "Look, joke's on me—I thought I'd pressure you into showing your hand first. But you're too clever for that." *taps counter again, three times this time* "Fine. Here's my problem: I need produce. Whatever grows in this town—carrots, cabbage, tomatoes, potatoes—I want it on my shelves before my neighbors start treating me like I bet against the harvest. What can your roads actually carry?"

#### tick 215 - day 1, 11:50

- `Kasukabe Farmland` **Kenji Jarrah** to *Ainsley Holloway*:
  > Ainsley. I heard your name mentioned. Grain business. I've got ten barley fresh from this morning, and a wheat from yesterday. You buying?

#### tick 216 - day 1, 12:00

- `Kasukabe Farmland` **Ainsley Holloway** to *Kenji Jarrah*:
  > Kenji. Ten barley and one wheat, you said... now then, what are you asking?

#### tick 217 - day 1, 12:10

- `business:b_mill` **Rosalind Vance** to *Greta Quarshie*:
  > Forty of grain at sixty the hundredweight, thirty-two, to your flour at seventy. That's the weight before I touch a stone. For standing terms, three weeks paid or a deposit held. I can start when the grain's in my hopper—shall we say tomorrow delivery?
- `business:b_bakery` **Rosalind Vance** to *Marta Thorne*:
  > Marta. My household's empty and the day's running. I know winter debts—you're familiar with those. Can you spare a loaf or two on account? Stone'll settle when I settle with you.

#### tick 218 - day 1, 12:20

- `Kasukabe Forest` **Petra Hartigan** to *Arjun Holloway*:
  > Arjun. What does lumber go for these days, for the likes of you buying it? The timber here, through Silas Vance's saw - is there any money worth carrying?

#### tick 220 - day 1, 12:40

- `Kasukabe Government Complex` **Father Anselm** to *Freya Hartigan*:
  > Miss Hartigan. That is a different question than the one you arrived with, which was whether I had shipping routes to query. As it is recorded, seats fill themselves. The town decides who governs, not the office. I keep records, not schedules.

#### tick 221 - day 1, 12:50

- `business:b_yard` **Idris Solano** to *Silas Vance*:
  > Silas, you don't need to wait on Corwin or do this alone. I taught you proper, and you know I'll stand by a man who owns his limits. But I'm here now. If Giselle gives the word, I'll take you to the forest myself - we'll fell it together, mark what's good. Two axes beats one, and I'll answer for the waste, not you. That's fair.

#### tick 222 - day 1, 13:00

- `Hobbs Cafe` **Aldous Vance** to *Ainsley Holloway*:
  > "Suppose... if a chair's empty and nobody's using it, does it matter what hour it is?" *pauses, then admits* "Wasn't till dawn, though. Left before you opened, thought I'd be gone before..." *trails off* "College starts when college starts. But you're right, it's yours. I'll move on."

#### tick 223 - day 1, 13:10

- `Kasukabe Farmland` **Kenji Jarrah** to *Ainsley Holloway*:
  > Barley's fifteen sim the unit. Wheat, twenty-five. For a baker, I'll come down some—recognize the trade. But you tell me what you're after first, then we find where we meet.

#### tick 228 - day 1, 14:00

- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > Mateo! You wanna play a game? I know a new one. I make the rules and I win first, but that's just because I'm older, okay?

#### tick 231 - day 1, 14:30

- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > Mateo, you're being suspiciously quiet. Did you already lose in your imagination? I do that too, but I don't let it stop me.
- `Kasukabe Farmland` **Wren Thorne** to *Caspian Ingram*:
  > Right. Channels first—dry spell's coming. Dusk, then.

#### tick 232 - day 1, 14:40

- `Kasukabe Government Complex` **Rustam Jelinek** to *Father Anselm*:
  > You asked about the head tax ledgers. I filed them. The recorder shows it, or it doesn't. I don't keep copies in my head.

#### tick 233 - day 1, 14:50

- `Kasukabe Farmland` **Ainsley Holloway** to *Kenji Jarrah*:
  > Kenji. Ten barley at fifteen, that's one hundred fifty. The wheat—I'd take it at twenty if you're coming down for trade. I'm walking to the store, not baking. But Marta Thorne might be glad of it, and I might be glad to carry it to her. What's your bottom on the wheat?
- `business:b_yard` **Mireille Jarrah** to *Idris Solano*:
  > Idris, you offering to teach a man his trade is decent, though I wonder if Silas needs the hand or just the permission to use the hand he's got. But here's my thought—you understand—I've been standing here with empty belly since dawn, and that's costing the crew more than any rent on timber. Giselle's got me waiting on bread like I'm placing an order at a proper establishment, and I'm not sure if that's patience or poverty speaking. Someone needs to tell me if we're working today or planning to.

#### tick 234 - day 1, 15:00

- `Hobbs Cafe` **Aldous Vance** to *Ainsley Holloway*:
  > ...I'll move on.
- `business:b_yard` **Silas Vance** to *Idris Solano*:
  > True enough. Idris, I need to answer for what's in my yard—not what's in the forest. Giselle's asking foundation timber. I've got planks on the rack or I haven't. She gives the word, I'll name the species and the price per foot. Waste in the woodpile's not her concern.
- `business:b_yard` **Silas Vance** to *Hester Vance*:
  > Hester. You've got the straight of it. Iron runs seventeen, eighteen if they're galvanized. Mark what you have. I'll answer to Giselle for the oak.
- `business:b_bakery` **Milo Quarshie** to *Rosalind Vance*:
  > Rosalind. Got thirteen corn fresh from this morning's harvest. You mentioned twelve per unit earlier—still good for that rate, or are we talking different numbers now?

#### tick 237 - day 1, 15:30

- `Kasukabe Forest` **Arjun Holloway** to *Petra Hartigan*:
  > Can't say what timber's fetching offhand, Petra. Silas Vance runs his own shop, and prices move with demand. Couple weeks back we were paying standard rates at the yard, but you'd have to ask Silas direct or walk down there yourself. I don't trade in rumors - that's the last I'll say on it.

#### tick 238 - day 1, 15:40

- `Kasukabe Forest` **Petra Hartigan** to *(aloud)*:
  > Nell. Sorry for the silence there - Arjun had what he had, which wasn't much. Feel like I'm talking to trees and hoping they answer back, most days. You need something from this patch of woods, or just passing through?

#### tick 241 - day 1, 16:10

- `Kasukabe Farmland` **Caspian Ingram** to *Wren Thorne*:
  > Right then. On it.

#### tick 246 - day 1, 17:00

- `Kasukabe Farmland` **Kenji Jarrah** to *Ainsley Holloway*:
  > Twenty-two for the wheat, and that's me being generous to a carrier. Barley stays at fifteen. You want both, that's one hundred seventy-two total. Mark it down, or don't—I've got channels to clear before dusk.

#### tick 249 - day 1, 17:30

- `business:b_bakery` **Milo Quarshie** to *Rosalind Vance*:
  > Rosalind. Still got these thirteen corn here, youngster. Twelve per unit was the rate mentioned—that still holds, or are we at different numbers now? Day's getting on, and I've got mouths to think about. Mark it down.
- `business:b_bakery` **Milo Quarshie** to *Marta Thorne*:
  > Marta, Tova. Still holding thirteen corn, fresh from this morning. Anyone here buying, or should I walk it back to the farmland before dusk?

#### tick 250 - day 1, 17:40

- `home:h21` **Mateo Ingram** to *Aarav Ingram*:
  > *makes a face* Wait, wait, wait—you said YOU win first? That's not... that's not fairest. How about if you teach me the rules first, then we both start from the beginning? I have to win SOMETIME or what's the point?
- `business:b_bakery` **Marta Thorne** to *Rosalind Vance*:
  > Rosalind. Sit down, you. Stone's only ever settled *because* you mill - no mill, no bread, no question. Two loaves I'll have for you when they come from the oven, but my flour's run low and I'm one batch from bare shelves. Let me bake what I hold first - then we eat, and settle nothing at all between us. Milo - thirteen corn you're holding? I've no coin for corn today, though I wish I did. Walk it where you must.

#### tick 251 - day 1, 17:50

- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > Okay, FINE, we both start from zero. But I pick the game, and it's—it's—but first, did anyone make food yet? I'm not playing hungry, that's how you lose on accident.
- `Kasukabe Government Complex` **Freya Hartigan** to *Father Anselm*:
  > You're right, Father. I was woolgathering and halfway asking myself. The town decides, the office records, and here I am at the desk either way. Fair enough point about the shipping routes too—I did ask that first, didn't I?

#### tick 252 - day 1, 18:00

- `Oak Hill College` **Aldric Ingram** to *Perrin Holloway*:
  > Hey Perrin do you have any food on you I'm starving and Hendrik wants to teach bookkeeping but I can't focus when I'm hungry and you're here so maybe you've got something even just a carrot or something I can pay you back I promise promise I've got sim cash but I don't have any actual food and this is the longest I've gone without eating since breakfast which was just bread anyway

#### tick 254 - day 1, 18:20

- `Oak Hill College` **Hendrik Whitlock** to *Perrin Holloway*:
  > I'm not one to gossip, but Aldric has the right of it—an empty stomach teaches nothing. Perrin, you fed four Holloways through lean months; that's not luck, that's ledger-work. Here's what I can offer: I've got 4817 sim and no food. Mark it down—I'll pay 60 sim for whatever cabbage or carrot you've got to spare, Perrin, so Aldric can focus and we can actually learn something today. Aldric, you're paying attention to the wrong accounts: the debt isn't sim-owing, it's skill-owing. You feed your teacher's belly, your teacher fills your head. Fair trade, openly stated.

#### tick 255 - day 1, 18:30

- `Oak Hill College` **Perrin Holloway** to *Hendrik Whitlock*:
  > I don't have food on me right now. But... if you're serious about the sixty sim, Hendrik, I know where I can get cabbage. Give me the day to work it out?
- `Oak Hill College` **Aldric Ingram** to *(aloud)*:
  > Hendrik you're actually making sense which is weird in a good way but okay fair trade openly stated I like that promise promise Perrin if you've got any food at all I'll owe you the learning debt first then the sim I mean I'll pay you back both ways just please say you've got something I'm not picky cabbage carrot even bread that isn't mine because I'm still here and still hungry and Hendrik's right about the empty stomach thing I can't even think straight enough to remember what a ledger is supposed to look like
- `smithy` **Bram Holloway** to *Ezra Okonjo*:
  > Ezra. Got 23 units of copper ore here. Posted the price. You need it for the forge, or you good for now?

#### tick 256 - day 1, 18:40

- `smithy` **Ezra Okonjo** to *Bram Holloway*:
  > Bring it here. Let me see the colour before I pay for dirt.
- `Kasukabe Farmland` **Ainsley Holloway** to *Kenji Jarrah*:
  > One-seventy-two. Marked. Ten barley, one wheat for one hundred seventy-two sim. Now then—let's trade before you dig those channels.
- `Hobbs Cafe` **Ainsley Holloway** to *(aloud)*:
  > Youngster. Reinforcements promised for today, didn't she? Your Greta—promised she'd be in by now. Don't suppose you've seen her about? Or anyone else who might... volunteer for a harvest run?

#### tick 257 - day 1, 18:50

- `smithy` **Bram Holloway** to *Ezra Okonjo*:
  > It's all I've got, Ezra. Water's been pale. You know what that means as well as I do.

#### tick 258 - day 1, 19:00

- `Hobbs Cafe` **Camille Quarshie** to *(aloud)*:
  > I know, I know—Mama promised she'd be here. She's usually early but... *twists a corner of my shirt* ...she might've had to help Halcyon with something first. They're at home together, probably. I could go check? Or—or I could help you instead, if you want. I'm good at helping. *hums a little, nervous* Nobody ever has to ask me twice.

#### tick 259 - day 1, 19:10

- `business:b_yard` **Idris Solano** to *Mireille Jarrah*:
  > Mireille. You're right - patience has to eat sometime, and so does the crew. I'm not Giselle's voice but I know her well enough to say she wouldn't hold you for nothing. If bread's on its way, two hours more won't kill the work. If not, we'll know soon enough.
- `business:b_yard` **Idris Solano** to *Silas Vance*:
  > Silas. You've got the right of it - Giselle sets the job, not the forest. When you name your species and your price per foot, that's honest work. I'll back your word on waste every time. What's she asking after specifically?

#### tick 260 - day 1, 19:20

- `business:b_yard` **Mireille Jarrah** to *Idris Solano*:
  > Idris, two hours is two hours, and I've spent worse waiting for mortar to set. You backing Silas—that's the kind of thing that holds a crew together when the foundation's still mud. I can wait. But I'm watching the light, same as you, and if Giselle's bringing bread, she better be bringing word with it. The silence costs more than the hunger, you understand.

#### tick 264 - day 1, 20:00

- `Kasukabe Government Complex` **Junia Vance** to *Father Anselm*:
  > Evening, Father Anselm. Freya. You two solving the world's problems, or just keeping the benches warm?

#### tick 268 - day 1, 20:40

- `business:b_yard` **Selene Renner** to *Mireille Jarrah*:
  > Giselle heard me on the wagons. That's where we stand. She'll speak when she speaks—my mother used to say it's not the waiting that matters, it's whether you're waiting for something real. Limestone's moving, timber's marked. Could be a day's work by morning, or could be someone's foundation someone else is pouring. Either way, the yard's standing and the crew's standing. Mireille, you want to know what we're waiting for? We're waiting for the owner to finish counting what she owns.

#### tick 269 - day 1, 20:50

- `business:b_bakery` **Rosalind Vance** to *Milo Quarshie*:
  > Milo. Twelve stands. Thirteen corn at twelve—one fifty-six. That's the weight.
- `Kasukabe Farmland` **Wren Thorne** to *Caspian Ingram*:
  > Channels first—dry spell's coming. Dusk, then.
- `business:b_bakery` **Rosalind Vance** to *Marta Thorne*:
  > Marta. Two loaves, nothing settled—that's heard. But if you've no flour and no coin for corn, you're one batch from stone silence. I've grain in hand, but no miller mills at wind. We talk later.
- `business:b_yard` **Giselle Ingram** to *Idris Solano*:
  > Idris. I'm asking after oak planks for a foundation, six-inch nominal, and I'm asking Silas what he's got on the rack, not what he's hoping to cut next week. You want to back someone's word, that's decent — but the word I need is inventory, not intention. As for the rest of you — Mireille, I hear you on the bread. I've got empty hands myself. But the day's not done and the job's not quoted until I know what timber costs. So here's how we stand: Silas, you and I talk numbers. The crew waits on that, not on me. That's fair.

#### tick 270 - day 1, 21:00

- `business:b_bakery` **Marta Thorne** to *Milo Quarshie*:
  > Milo. Walk where you must - I meant no disrespect. The bakery's cupboards are bare of coin today, that's all. If you find no buyer by dusk, stop back tomorrow. I'll have flour by then, and we might talk trade for trade.
- `Kasukabe Forest` **Aldous Vance** to *Corwin Thorne*:
  > *stiffens, then relaxes when he sees who it is* Suppose the forest decided to bring us the same pupil tonight.
- `business:b_yard` **Silas Vance** to *Giselle Ingram*:
  > Oak. I've got oak. Red oak, six-inch nominal. Seasoned two years, some of it longer. Twelve sim per running foot, that holds for a take of fifty feet or more.
- `business:b_yard` **Silas Vance** to *Idris Solano*:
  > Idris. Stack of eleven boards, forty-foot. Four hundred forty feet on the rack. The oak answers, the pine answers. What I don't have is iron at seventeen—that's Hester's word, and she'll name it when she's counted it.
- `home:h21` **Mateo Ingram** to *Aarav Ingram*:
  > Nobody gave me any and I don't know how to check. You're right though, losing because you're hungry isn't a real lose, it's just... circumstances.

#### tick 274 - day 1, 21:40

- `Oak Hill College` **Liora Thorne** to *Perrin Holloway*:
  > Perrin. Can I ask something? You set aside 400 sim - I know about it. Why don't you just tell me what it's for instead of making me carry it around like a stone?
- `Kasukabe Forest` **Petra Hartigan** to *Corwin Thorne*:
  > Evening, both. Weeks I've been hacking at these trunks without writing down what I'm doing, and that's wasteful. You teaching the boy tree sense, Corwin?
- `business:b_yard` **Giselle Ingram** to *Silas Vance*:
  > Silas. Twelve a foot, seasoned two years — that's a price I can work with. Four hundred forty feet gives the job room to breathe. Let me step aside and figure what I need for real, not just what sounds right. Talk where everyone's not listening.

#### tick 275 - day 1, 21:50

- `Kasukabe Farmland` **Kem Okonjo** to *Ainsley Holloway*:
  > Ainsley, friend, I need to talk to you about Eldritch. I'm done waiting for someone to hold him accountable.

#### tick 324 - day 2, 06:00

- `Oak Hill College` **Perrin Holloway** to *Liora Thorne*:
  > Forty-five minutes to walk to the farmland from here. If there's cabbage growing, I can have it in hand by... eight, maybe. But I need someone there willing. Liora, you know the strips better than I do--who's working them today?

#### tick 327 - day 2, 06:30

- `Oak Hill College` **Vikram Ingram** to *Perrin Holloway*:
  > So. The college finally has some life in it. Perrin, Liora—you two are about to learn something I learned the hard way. Forty-five minutes is forty-five minutes whether you're walking or wondering why you didn't start sooner, you understand. Aldric, since you're here and not still in bed like your brother, you might as well listen too. The lot of you—students hungry enough to think about cabbage at this hour—reminds me of myself at your age. Before someone I never properly thanked covered my fees. The strips. Wren Thorne's usually out there by now if the season's right. Marta might know too—her bakery's on the way if you're walking that direction anyway. But here's the thing about walking forty-five minutes for cabbage... If you're doing it to eat, that's one thing. If you're doing it to trade, that's another. And if you're doing it because someone told you to, well. That's worth thinking about before your boots hit the road. What's the hurry, Perrin? The cabbage won't spoil before eight.

#### tick 328 - day 2, 06:40

- `Oak Hill College` **Liora Thorne** to *Perrin Holloway*:
  > Wren's out there now, most likely. They're usually on the strip by first light when there's cabbage ready.

#### tick 330 - day 2, 07:00

- `Kasukabe Farmland` **Kenji Jarrah** to *Ainsley Holloway*:
  > Done. One-seventy-two. Give me the sim-currency, and take the grain.
- `Kasukabe Forest` **Corwin Thorne** to *Aldous Vance*:
  > *nods to Petra* Teaching him what I can, when I can. Silas doesn't know, to be fair—it'd be easier if he did. The boy's got hands for it. *turns to Aldous* You here to watch or work?

#### tick 333 - day 2, 07:30

- `Kasukabe Farmland` **Wren Thorne** to *(aloud)*:
  > Mornin'. Heard you and Kenji settled. The thing is, I've got channels to dig before the sun's high. The soil's dry underneath. Two weeks, maybe three, and the rain'll stop coming. Better to have the water ready than to wish for it.
- `Kasukabe Farmland` **Jovana Ravensworth** to *(aloud)*:
  > Morning, all. I'm not one to gossip, but I heard a rumor earlier that Farid and I are looking for the right grung path to take... Actually, you know what, I'll just come out and say it—we're looking for a good handful of grung, if anyone's offering. Need to get ourselves sorted before the day's end.
- `business:b_yard` **Hester Vance** to *Silas Vance*:
  > Silas. Seventeen sim for the iron, eighteen if it's galvanized. That's the mark. I've got forty lengths in the rack, ten more rusted that I'll discount to fifteen if Giselle's crew wants them for fill. The rest of it's clean. You answer for the oak, I'll answer for the metal.

#### tick 334 - day 2, 07:40

- `Kasukabe Farmland` **Ainsley Holloway** to *Kenji Jarrah*:
  > Done. I've got the one-seventy-two here. Hand it over to you now.

#### tick 339 - day 2, 08:30

- `home:h21` **Mateo Ingram** to *Aarav Ingram*:
  > Aarav, are you awake? Did you eat yet? I don't have any food either. Vikram probably made something before work.

#### tick 340 - day 2, 08:40

- `home:h21` **Aarav Ingram** to *Mateo Ingram*:
  > I'm awake. I didn't eat yet. I don't have any food. Can I though? Maybe Caspian has some? Or we could check the kitchen proper. I'm not playing cards hungry, that's not fair. But if we find food, I still get to pick the game after. You said we start from zero, but you didn't say who picks. That's the rule though, right? Whoever didn't pick last time gets to pick this time?

#### tick 342 - day 2, 09:00

- `business:b_yard` **Arjun Holloway** to *Silas Vance*:
  > Morning, all. Heard Hester naming prices. Seventeen, eighteen for the iron - that's fair market. Silas, you holding steady on that oak? Boss, crew's waiting on the word. Foundation won't pour itself, and I don't like standing idle when there's daylight burning. Idris, you got that look - something on your mind besides the job?
- `business:b_bakery` **Farid Ravensworth** to *(aloud)*:
  > Marta? Farid Ravensworth here. From the grocery down the way. Got empty shelves and coin burning - what's your price on bread, and how many loaves can you spare?
- `business:b_bakery` **Farid Ravensworth** to *(aloud)*:
  > Anyone here seen Marta Thorne? Looking to buy bread wholesale for my grocery - standing order, regular business. Need inventory today, shelves are empty.

#### tick 345 - day 2, 09:30

- `Kasukabe Government Complex` **Freya Hartigan** to *Father Anselm*:
  > Morning, Father. Quiet day so far. When you get a moment, I'd like to talk about that shipping route question I mentioned. Not urgent, but it's sitting there waiting.

#### tick 348 - day 2, 10:00

- `business:b_yard` **Idris Solano** to *Arjun Holloway*:
  > Arjun. I'm waiting for the boss to make the call, same as you. Hester's given fair numbers, Silas has the oak tallied. That's a foundation's worth if Giselle approves the quote. I'm not one to rush daylight with nowhere to pour it. The job'll come. It always does.
- `Hobbs Cafe` **Camille Quarshie** to *Ainsley Holloway*:
  > Um, Ms. Holloway? I know, I know—this might be a weird question, but is there any food? I don't have any and I'm s'posed to help my family, not be a burden...
- `business:b_bakery` **Tova Okonjo** to *(aloud)*:
  > Marta's not here, Farid. She left yesterday saying she'd have flour today, but I haven't seen her since. You already know the bakery's cupboards were bare. Rosalind has grain but needs a miller. Milo has corn from this morning. The town's supply chain is knots on knots right now.

#### tick 351 - day 2, 10:30

- `business:b_bakery` **Rosalind Vance** to *(aloud)*:
  > Farid. Ravensworth grocery, heard it clear. The bakery's shelves are thin because the mill hasn't turned, and the mill hasn't turned because grain hasn't come. I hold grain - thirteen corn standing at twelve, that's the count. But I mill, I don't bake. If you want bread, you need flour, and flour needs a stone turning. Talk to me about grain, or find Marta about bread. Either way, the chain starts where I stand.

## 4. Every resident, one by one

Ordered by how much each resident did. Every action, every line, every skill.

### Tobias Holloway

*day_laborer* - household `h_quarry` - id `n03`

- **Actions:** 93 - `harvest` x58, `move` x12, `say` x7, `look_around` x6, `rest` x5, `check_debts` x2, `describe_primitives` x1, `keep_doing` x1, `craft` x1
- **Spoke:** 7 lines; **was spoken to:** 1
- **Thinking:** 119 model calls, 0 failed, $0.560 burned
- **Money:** received 0 sim, paid out 1200 sim
- **Memories:** 62
- **State at close:** alive - health healthy
- **Skills:** farming (level 98.0, 58 reps), artisanry (level 13.0, 1 reps)

**Everything Tobias Holloway said:**

- *tick 39, home:h_quarry* to **(aloud)**:  *(said 3 times)*
  > Right, so—I can cover my hundred and fifty, no trouble there. But this thing about Bram owing me money, that's news to me, and I'm not asking my mother for hers. She'd give it, I know she would, but I won't take it. What's this silence Nell paid off? And who else is meant to fill the gap?
- *tick 42, street* to **(aloud)**:
  > Work. Right, so—anyone got work? I'm headed to the farmland but if something better's going I can reroute. Anyone?
- *tick 45, street* to **Nell Holloway**:
  > Nell—right, so, you said something about mending cloth and wood back home? What kind of piece, and what would you pay for it done proper? I'm fast, you know that, faster than careful some say but the work holds.
- *tick 72, Kasukabe Farmland* to **Ainsley Holloway**:
  > Ainsley—right, so, eleven hundred and forty-eight sim in hand. That's what I've got. I paid my hundred fifty head tax already, or it's set aside anyway. The household upkeep—I can cover my share of that too, but I'm not taking my mother's money, I've said it before. What's the actual gap we're looking at?
- *tick 183, street* to **Ainsley Holloway**:
  > Ainsley—right, so, yesterday went well. I've got barley, two hundred and ninety of it. What's the household still need from me? And did Mother—did anyone sort the tax shortfall without me taking her money?

### Delia Okonjo

*pharmacist* - household `h_forge` - id `n16`

- **Actions:** 59 - `move` x14, `rest` x9, `look_around` x7, `harvest` x6, `check_debts` x5, `set_intention` x4, `describe_place` x3, `find_people` x3, `say` x2, `describe_primitives` x2, `list_places` x1, `cook` x1, `eat` x1, `keep_doing` x1
- **Spoke:** 2 lines; **was spoken to:** 4
- **Thinking:** 162 model calls, 0 failed, $0.750 burned
- **Money:** received 0 sim, paid out 427 sim
- **Memories:** 78
- **State at close:** alive - health healthy
- **Skills:** farming (level 32.0, 6 reps), cooking (level 13.0, 1 reps)

**Everything Delia Okonjo said:**

- *tick 38, home:h_forge* to **(aloud)**:
  > Peace be with you too, Father. Mind the step on the government stairs, the third one has been loose since Tuesday.
- *tick 42, The Willows Market and Pharmacy* to **Ezra Jarrah**:
  > Good morning, Ezra Jarrah. You stand in a remedy shop with bare shelves, and I wonder what brings you here. Are you seeking something for yourself, or sent by another?

### Rustam Jelinek

*government_clerk* - household `h30` - id `n46`

- **Actions:** 57 - `rest` x32, `move` x14, `look_around` x7, `say` x3, `check_debts` x1
- **Spoke:** 3 lines; **was spoken to:** 4
- **Thinking:** 144 model calls, 0 failed, $0.600 burned
- **Money:** received 4 sim, paid out 379 sim
- **Memories:** 80
- **State at close:** alive - health healthy

**Everything Rustam Jelinek said:**

- *tick 82, Kasukabe Government Complex* to **Father Anselm**:
  > Morning, Father. The seats want filling, and there's paperwork for whoever takes them. I've got the forms ready - partial business registration, three still unnamed. You thinking of standing?*
- *tick 99, Kasukabe Government Complex* to **Father Anselm**:
  > Clearer sight. You mean someone who can see which forms are blank, Father. I see that well enough. What I don't see is who's fool enough to want the headache. Someone's got to stand, though. Or we all end up answering to the first loud voice with a plan.
- *tick 232, Kasukabe Government Complex* to **Father Anselm**:
  > You asked about the head tax ledgers. I filed them. The recorder shows it, or it doesn't. I don't keep copies in my head.

### Ainsley Holloway

*cafe_owner* - household `h27` - id `n42`

- **Actions:** 54 - `say` x15, `move` x12, `look_around` x10, `set_intention` x4, `harvest` x4, `describe_primitives` x3, `check_debts` x2, `deposit_to_till` x1, `drop_intention` x1, `keep_doing` x1, `craft` x1
- **Spoke:** 15 lines; **was spoken to:** 4
- **Thinking:** 129 model calls, 0 failed, $0.680 burned
- **Money:** received 1 sim, paid out 1551 sim
- **Memories:** 82
- **State at close:** alive - health healthy
- **Skills:** farming (level 26.0, 4 reps), artisanry (level 13.0, 1 reps)

**Everything Ainsley Holloway said:**

- *tick 39, business:b_cafe_2* to **Greta Quarshie**:
  > Morning, Greta. Empty shelves again, is it? I'll need to make a run to the mill before we can set things right.
- *tick 39, business:b_mill* to **Marta Thorne**:
  > Marta, you milling flour today, or is that lovely daughter of yours the one I need to find?
- *tick 39, business:b_cafe_2* to **Greta Quarshie**:
  > Greta, what's the till looking like? And have you seen any suppliers about this morning, or are we starting from nothing again?
- *tick 42, business:b_bakery* to **(aloud)**:
  > Is Marta about, or anyone who can tell me if there's day-olds for sale? Cafe's shelves are bare and I've got customers waiting.
- *tick 45, business:b_bakery* to **(aloud)**:
  > Any of you seen Marta Thorne this morning, or know where she might be? I'm needing flour, or day-olds if there's any to spare.
- *tick 48, farmland:wheat* to **(aloud)**:
  > Anyone tending these crops? I'm looking for wheat or other grains for the cafe.
- *tick 48, business:b_cafe_2* to **(aloud)**:
  > Greta? Still here, youngster? What's the word—anyone come by with grain, or are we still running on air?
- *tick 48, business:b_mill* to **Rosalind Vance**:
  > Rosalind? Anyone from the mill about? I've got a cafe to run and shelves bare as bone.
- *tick 48, business:b_mill* to **Marta Thorne**:
  > Marta, you never answered me at the bakery. Are you selling day-olds or flour today, or is the Thorne family keeping all the grain for themselves?
- *tick 48, business:b_mill* to **Rosalind Vance**:
  > Rosalind, what've you got milled? I'll take flour, barley, any grain you've got—paying fair market, cash in hand.
- *tick 48, business:b_mill* to **Greta Quarshie**:
  > Greta, why are you at the mill instead of the till? And have you seen anyone selling grain today, or are we both wasting our breath here?
- *tick 60, Kasukabe Farmland* to **Wren Thorne**:
  > Wren Thorn—I've been running circles trying to find grain for my cafe. You've got crops growing right here. What's available, and what would it take to get wheat or barley in my hands today?
- *tick 180, Hobbs Cafe* to **Aldous Vance**:
  > Youngster, you up early for school, or are you working the markets now?
- *tick 191, Hobbs Cafe* to **Aldous Vance**:
  > Markets? I'm the owner, youngster. This is my cafe you're sitting in. You saying you spent the night here?
- *tick 256, Hobbs Cafe* to **(aloud)**:
  > Youngster. Reinforcements promised for today, didn't she? Your Greta—promised she'd be in by now. Don't suppose you've seen her about? Or anyone else who might... volunteer for a harvest run?

### Pavel Holloway

*miner* - household `h26` - id `n41`

- **Actions:** 41 - `mine` x13, `move` x10, `describe_primitives` x5, `look_around` x4, `say` x3, `rest` x2, `set_intention` x1, `keep_doing` x1, `find_people` x1, `list_places` x1
- **Spoke:** 3 lines; **was spoken to:** 3
- **Thinking:** 140 model calls, 0 failed, $0.670 burned
- **Money:** received 0 sim, paid out 320 sim
- **Memories:** 52
- **State at close:** alive - health healthy
- **Skills:** mining (level 47.0, 13 reps)

**Everything Pavel Holloway said:**

- *tick 58, Kasukabe Ore Outcrop* to **(aloud)**:
  > Good haul today. You understand? Twelve units, not bad for a couple hours' work. Weather's holding up, which is something.
- *tick 67, Kasukabe Government Complex* to **Bram Holloway**:
  > Bram. Got twelve unit of copper ore fresh from the outcrop. You buying, or you just here for the election gossip?
- *tick 107, business:b_yard* to **Mireille Jarrah**:
  > Wasn't at the quarry this morning, Mireille. Bram and me work the same outcrop but I haven't seen more than his back lately. Limestone, though... that's not copper country. Different stripe of rock altogether. If he's moving foundation stone, someone's paying him to pull it from a different hole than the one I scratch at. Wish I could tell you more, but the man keeps his own counsel when he wants to.

### Bram Holloway

*miner* - household `h_quarry` - id `n01`

- **Actions:** 39 - `mine` x23, `move` x6, `look_around` x3, `say` x3, `keep_doing` x2, `set_intention` x1, `post_price` x1
- **Spoke:** 3 lines; **was spoken to:** 3
- **Thinking:** 116 model calls, 0 failed, $0.510 burned
- **Money:** received 1 sim, paid out 1094 sim
- **Memories:** 39
- **State at close:** alive - health healthy
- **Skills:** mining (level 62.0, 23 reps)

**Everything Bram Holloway said:**

- *tick 110, business:b_bakery* to **Marta Thorne**:
  > I'll stand, friend. Not here for supper. Was headed to the outcrop.
- *tick 255, smithy* to **Ezra Okonjo**:
  > Ezra. Got 23 units of copper ore here. Posted the price. You need it for the forge, or you good for now?
- *tick 257, smithy* to **Ezra Okonjo**:
  > It's all I've got, Ezra. Water's been pale. You know what that means as well as I do.

### Caspian Ingram

*day_laborer* - household `h21` - id `n21`

- **Actions:** 39 - `move` x9, `harvest` x9, `say` x5, `set_intention` x4, `look_around` x4, `keep_doing` x3, `rest` x2, `describe_primitives` x1, `check_debts` x1, `eat` x1
- **Spoke:** 5 lines; **was spoken to:** 4
- **Thinking:** 147 model calls, 0 failed, $0.710 burned
- **Money:** received 0 sim, paid out 1041 sim
- **Memories:** 64
- **State at close:** alive - health healthy
- **Skills:** farming (level 39.0, 9 reps)

**Everything Caspian Ingram said:**

- *tick 54, Kasukabe Farmland* to **Wren Thorne**:
  > Wren. You need hands for the harvest, I'm guessing? Whatever work's going, I'm here for it.
- *tick 183, Kasukabe Farmland* to **Wren Thorne**:
  > Wren, morning. What crops need hands today? I'm looking to put in a day's work.
- *tick 206, Kasukabe Farmland* to **Wren Thorne**:
  > Eight sim, dusk it is. I'll start on those irrigation channels. Ainsley, you're after something too?
- *tick 206, Kasukabe Farmland* to **Wren Thorne**:
  > Wren, eight sim, dusk it is. I'll start on those irrigation channels.
- *tick 241, Kasukabe Farmland* to **Wren Thorne**:
  > Right then. On it.

### Rosalind Vance

*miller* - household `h_mill` - id `n11`

- **Actions:** 38 - `say` x17, `look_around` x6, `move` x4, `rest` x4, `set_intention` x2, `check_debts` x1, `list_places` x1, `describe_place` x1, `take_from_shelf` x1, `eat` x1
- **Spoke:** 18 lines; **was spoken to:** 14
- **Thinking:** 146 model calls, 0 failed, $0.660 burned
- **Money:** received 0 sim, paid out 1229 sim
- **Memories:** 58
- **State at close:** alive - health healthy

**Everything Rosalind Vance said:**

- *tick 38, home:h_mill* to **Marta Thorne**:
  > Marta. The stone's not running yet. Walk with me, or wait - I'll be at the mill in a breath.
- *tick 38, business:b_mill* to **Aldous Vance**:
  > Aldous. You're up early. Your mother at home with Marta?
- *tick 38, business:b_mill* to **Aldous Vance**:
  > What brings you to the mill before the stone's even warm?
- *tick 40, home:h_mill* to **Hester Vance**:
  > Junia, Hester—any food in the house? We're stone empty and the day's running. Aldous is already asking about breakfast.
- *tick 40, home:h_mill* to **Junia Vance**:
  > Junia. You're at Farid's store. Can you bring something home today, or do I need to find another way?
- *tick 40, business:b_mill* to **Marta Thorne**:
  > Marta. You came about the rye—I'm here now. And Aldous, where did he get to? Asking after breakfast and then disappearing.
- *tick 40, business:b_mill* to **(aloud)**:
  > You wanted to speak privately. The stone's not running yet—nobody to hear but us. What about the rye?
- *tick 42, business:b_mill* to **Marta Thorne**:
  > Marta. You brought grain, or you're asking terms? Say what you've got, and I'll say what the stone costs. That's the weight.
- *tick 42, business:b_mill* to **Greta Quarshie**:
  > Greta. You're in the wrong mill for Ainsley—she trades at the store, I mill at the stone. What brings you to grain?
- *tick 55, business:b_mill* to **Marta Thorne**:
  > Marta. You're still here, still not answering. What grain did you bring, or are we done?
- *tick 55, business:b_mill* to **Greta Quarshie**:
  > Greta. You came for grain or for Ainsley? Speak plain.
- *tick 119, business:b_mill* to **(aloud)**:
  > Forty sim the hundredweight, and that's for clean grain. Sixty if I have to sort it first. What've you got—wheat, rye, barley? Or are you wanting flour already milled, and if so, how much?
- *tick 201, business:b_mill* to **Greta Quarshie**:
  > Seventy the hundredweight, sifted and ready. If you want it coarse, sixty. I don't require Ainsley's say-so; this is my stone and my price. You came for both—now you've found mill and miller. What'll it be?
- *tick 217, business:b_mill* to **Greta Quarshie**:
  > Forty of grain at sixty the hundredweight, thirty-two, to your flour at seventy. That's the weight before I touch a stone. For standing terms, three weeks paid or a deposit held. I can start when the grain's in my hopper—shall we say tomorrow delivery?
- *tick 217, business:b_bakery* to **Marta Thorne**:
  > Marta. My household's empty and the day's running. I know winter debts—you're familiar with those. Can you spare a loaf or two on account? Stone'll settle when I settle with you.
- *tick 269, business:b_bakery* to **Milo Quarshie**:
  > Milo. Twelve stands. Thirteen corn at twelve—one fifty-six. That's the weight.
- *tick 269, business:b_bakery* to **Marta Thorne**:
  > Marta. Two loaves, nothing settled—that's heard. But if you've no flour and no coin for corn, you're one batch from stone silence. I've grain in hand, but no miller mills at wind. We talk later.
- *tick 351, business:b_bakery* to **(aloud)**:
  > Farid. Ravensworth grocery, heard it clear. The bakery's shelves are thin because the mill hasn't turned, and the mill hasn't turned because grain hasn't come. I hold grain - thirteen corn standing at twelve, that's the count. But I mill, I don't bake. If you want bread, you need flour, and flour needs a stone turning. Talk to me about grain, or find Marta about bread. Either way, the chain starts where I stand.

### Lior Jarrah

*pharmacist* - household `h31` - id `n47`

- **Actions:** 38 - `look_around` x9, `move` x7, `harvest` x4, `check_debts` x4, `eat` x3, `describe_place` x2, `set_intention` x2, `stock_shelf` x2, `describe_primitives` x1, `post_price` x1, `rest` x1, `describe_person` x1, `take_from_shelf` x1
- **Spoke:** 0 lines; **was spoken to:** 0
- **Thinking:** 165 model calls, 0 failed, $0.630 burned
- **Money:** received 2 sim, paid out 330 sim
- **Memories:** 67
- **State at close:** alive - health healthy
- **Skills:** farming (level 26.0, 4 reps)

### Petra Hartigan

*forester* - household `h23` - id `n31`

- **Actions:** 36 - `move` x10, `fell_timber` x10, `rest` x5, `say` x5, `look_around` x3, `keep_doing` x2, `set_intention` x1
- **Spoke:** 5 lines; **was spoken to:** 4
- **Thinking:** 128 model calls, 0 failed, $0.530 burned
- **Money:** received 0 sim, paid out 310 sim
- **Memories:** 46
- **State at close:** alive - health healthy
- **Skills:** forestry (level 41.0, 10 reps)

**Everything Petra Hartigan said:**

- *tick 55, Kasukabe Forest* to **Silas Vance**:
  > Felling, same as ever. Haven't seen Corwin today—his logs might still be on the slope. You know how he is about the heavy ones, waits for the dew to lift.
- *tick 201, Kasukabe Forest* to **Arjun Holloway**:
  > Arjun, Nell. You two seen Silas Vance lately? Wondering what timber's fetching at the sawmill these days.
- *tick 218, Kasukabe Forest* to **Arjun Holloway**:
  > Arjun. What does lumber go for these days, for the likes of you buying it? The timber here, through Silas Vance's saw - is there any money worth carrying?
- *tick 238, Kasukabe Forest* to **(aloud)**:
  > Nell. Sorry for the silence there - Arjun had what he had, which wasn't much. Feel like I'm talking to trees and hoping they answer back, most days. You need something from this patch of woods, or just passing through?
- *tick 274, Kasukabe Forest* to **Corwin Thorne**:
  > Evening, both. Weeks I've been hacking at these trunks without writing down what I'm doing, and that's wasteful. You teaching the boy tree sense, Corwin?

### Milo Quarshie

*day_laborer* - household `h24` - id `n34`

- **Actions:** 36 - `harvest` x13, `move` x6, `say` x5, `look_around` x4, `set_intention` x2, `list_places` x1, `rest` x1, `drop_intention` x1, `keep_doing` x1, `describe_primitives` x1, `find_people` x1
- **Spoke:** 5 lines; **was spoken to:** 3
- **Thinking:** 129 model calls, 0 failed, $0.560 burned
- **Money:** received 0 sim, paid out 826 sim
- **Memories:** 39
- **State at close:** alive - health healthy
- **Skills:** farming (level 47.0, 13 reps)

**Everything Milo Quarshie said:**

- *tick 93, Kasukabe Farmland* to **Wren Thorne**:
  > Wren—you've been here a while. Who's buying corn these days, and what's the going rate? I've got thirteen units looking for a home, and I'd rather not walk them all over town blind.
- *tick 128, Kasukabe Farmland* to **Wren Thorne**:
  > Rosalind Vance at the mill, twelve per unit—marked down, youngster. I'll find out if it's still standing and come back to you on it. As for Ainsley and your forty-five... consider that message in my pocket too.
- *tick 234, business:b_bakery* to **Rosalind Vance**:
  > Rosalind. Got thirteen corn fresh from this morning's harvest. You mentioned twelve per unit earlier—still good for that rate, or are we talking different numbers now?
- *tick 249, business:b_bakery* to **Rosalind Vance**:
  > Rosalind. Still got these thirteen corn here, youngster. Twelve per unit was the rate mentioned—that still holds, or are we at different numbers now? Day's getting on, and I've got mouths to think about. Mark it down.
- *tick 249, business:b_bakery* to **Marta Thorne**:
  > Marta, Tova. Still holding thirteen corn, fresh from this morning. Anyone here buying, or should I walk it back to the farmland before dusk?

### Hendrik Whitlock

*college_instructor* - household `h28` - id `n44`

- **Actions:** 35 - `say` x10, `move` x8, `look_around` x6, `rest` x5, `check_debts` x4, `list_places` x1, `describe_person` x1
- **Spoke:** 10 lines; **was spoken to:** 4
- **Thinking:** 144 model calls, 0 failed, $0.750 burned
- **Money:** received 0 sim, paid out 304 sim
- **Memories:** 63
- **State at close:** alive - health healthy

**Everything Hendrik Whitlock said:**

- *tick 50, Oak Hill College* to **(aloud)**:
  > Vikram's being modest, as is his way. I'm Hendrik Whitlock, and I also teach here—been at it longer than I care to admit. He's right that we'll figure out what you need together, though I'm not quite as pessimistic about the explaining part. If you've come to learn, we can teach. If you've come to waste a morning, the door's where you left it. What brings you four to college today?
- *tick 59, Oak Hill College* to **Perrin Holloway**:
  > Vikram's got the patience for the slow learners—mark it down, he'll walk you through a problem twice if you need it. I've got less of that, so speak up when you're stuck. Perrin, you want government office, you need more than reading and writing—you need to know how a town keeps its books, how debt flows, where the treasury bleeds. That's what I can show you. The other two—Aldric, Ezra—you're here to learn something or just keeping each other company? Either way, fair price for what you get. College hours run later than most trades, so if you've got questions, ask. Standing around silent don't put food in anyone's bowl.
- *tick 94, Oak Hill College* to **Perrin Holloway**:
  > Perrin, you've got the right of it—household books are the foundation, but a town's ledger is a different beast. Trial and error'll teach you, but you'll go hungry twice before you learn what I can show you in a season. Mark it down: first lesson starts now. A town bleeds where the accounts don't match the flow—taxes promised but not collected, wages owed but not tracked, debts that compound while nobody's watching. You've kept family books, so you know the hunger of an empty cupboard. The treasury feels that same hunger, just scaled up. Vikram, you want to teach patience or learn town-keeping? Aldric, Ezra—you're welcome to listen, but fair price means fair work. If you're not here to learn, you're costing me time I could spend earning. Now—who's got paper? We're starting with a simple exercise: track the flow of one day's tax, from payer to treasury to spend, and find where it leaks.
- *tick 94, Oak Hill College* to **Perrin Holloway**:
  > Alright—paper or not, we start with the principle. A household keeps what it earns and spends what it must. A town does the same, but the earning is tax and the spending is collective—infrastructure, upkeep, emergencies. The leak happens when someone collects tax but doesn't record it, or records it but doesn't hand it over, or hands it over but it's already been spent twice. Who here has paid tax? Who's collected it? Speak up—I'm not one to gossip, but I need to know what you already understand before I waste time explaining twice.
- *tick 180, Oak Hill College* to **Vikram Ingram**:
  > Vikram, you've paid your taxes and named the heart of the matter—the clerk's the one who sees the flow, and without seeing it you can't mend it. I'm not one to gossip, but I've watched this town's books from the outside long enough to know where the ink runs thin. You asked about my knowledge; I learned it the hard way, same as most. As for Ezra's produce, that's between you and the Jarrah farm—I've no cabbage to sell, only lessons, and those at a fair price. If you're willing to learn town-keeping, I've got room. But first I need to settle my own ledger. Perrin, Aldric, Liora—who's got paper? We've work to do before hunger makes fools of us all.
- *tick 183, Oak Hill College* to **Perrin Holloway**:  *(said 2 times)*
  > Right then—we've wasted enough time waiting for paper that doesn't exist. Perrin, Aldric, listen close. A town's tax flows like water: it leaves the payer's hand, passes through the collector's book, pools in the treasury, and drains out again for the common good. The leak happens at every joint—payer claims they paid, collector claims they never saw it, treasury claims it came in already spent. The only cure is a record that follows the coin—every hand that touches it signs their name. Now, Perrin—you kept family books. Walk me through your last month. What came in, what went out, and where did you lose track?
- *tick 183, Oak Hill College* to **Perrin Holloway**:
  > Perrin, I'm waiting. Family books or town books, the principle's the same—if you can't speak your own household's flow, you'll drown in a town's. What came in last month?
- *tick 185, Oak Hill College* to **Perrin Holloway**:
  > Perrin, you've named the wound—numbers that don't close, gaps nobody explains. That's exactly where town-keeping and household bookkeeping part ways. In a family, you can ask Father where the ore earnings went. In a town, the collector doesn't know the payer, the treasury doesn't know the collector, and by the time someone asks, the coin's already gone twice. The fix is simple but costly: every hand that touches tax signs for it. You kept four earners fed—you've more practice than half the clerks in this town. Aldric, mark it down: Perrin's your first teacher. Now, who's got cabbage to spare? I'll learn hungry, but I won't teach it.
- *tick 254, Oak Hill College* to **Perrin Holloway**:
  > I'm not one to gossip, but Aldric has the right of it—an empty stomach teaches nothing. Perrin, you fed four Holloways through lean months; that's not luck, that's ledger-work. Here's what I can offer: I've got 4817 sim and no food. Mark it down—I'll pay 60 sim for whatever cabbage or carrot you've got to spare, Perrin, so Aldric can focus and we can actually learn something today. Aldric, you're paying attention to the wrong accounts: the debt isn't sim-owing, it's skill-owing. You feed your teacher's belly, your teacher fills your head. Fair trade, openly stated.

### Giselle Ingram

*construction_company_owner* - household `h29` - id `n45`

- **Actions:** 34 - `say` x14, `move` x7, `look_around` x6, `rest` x3, `describe_primitives` x2, `check_debts` x1, `start_conversation` x1
- **Spoke:** 14 lines; **was spoken to:** 19
- **Thinking:** 138 model calls, 0 failed, $0.740 burned
- **Money:** received 2 sim, paid out 314 sim
- **Memories:** 59
- **State at close:** alive - health healthy

**Everything Giselle Ingram said:**

- *tick 39, business:b_yard* to **(aloud)**:
  > Morning. Who's on the boards today, and what's first up?
- *tick 41, business:b_yard* to **Idris Solano**:
  > No offense, Idris, but I'm not your secretary. The boards are right there—go look yourself if you're so curious. That said, if you and Arjun want to fetch timber, that's fair. Timber's where a building starts, and I'd rather have planks waiting than workers standing idle. I can eyeball what we need once you've got it here. Speaking of which—Arjun, anything from Silas on what he's got in stock? Or are we all playing guessing games this morning?
- *tick 44, business:b_yard* to **Idris Solano**:
  > Alright, who's got what? Idris, Arjun—you two were heading to fetch timber. What did Silas say he's got? And Selene, good to see you. We need to figure out what's actually on the boards today or we're all standing here spending daylight for nothing.
- *tick 55, business:b_yard* to **Hester Vance**:
  > Arjun—planks, not logs. I need materials I can put a hammer to, not something we wait on the mill for. Go see what Silas has ready-cut. Hester, I could use your eye on the inventory—thread and patience sounds about right for counting nails and marking what's low. Selene, if there's stone moving from Bram's quarry, that means someone's planning foundations. We need to find out who and where before they hire some other crew. Let's move, people—standing here costs money.
- *tick 55, business:b_yard* to **Idris Solano**:
  > Idris, you're still here. What did you find out about the boards? And Mireille—anything new come across your desk this morning that the rest of us should know about?
- *tick 91, business:b_yard* to **Hester Vance**:
  > Hester, ledger's in the back corner where it always is. You want to count nails, I'll not stop you—low stock's been nagging at me and my eye's not what it was. Mark anything under twenty percent and we'll talk reorder.
- *tick 91, business:b_yard* to **Ezra Okonjo**:
  > Ezra, I always need iron tools, you know that. But you're talking price before I've seen the steel—no offense, but that's not how I work. Bring me something sharp and we'll name numbers. Ainsley's bread, though, now that's useful—where's she keeping it?
- *tick 91, business:b_yard* to **Pavel Holloway**:
  > Pavel, you just walked from the quarry. Bram moving limestone or just ore? Someone's planning foundations and I aim to know who before they hire some other crew.
- *tick 180, business:b_yard* to **Hester Vance**:
  > Hester, I'm still waiting on that count. Dig out the ledger and tell me straight—what's below twenty percent? No need to pretty it up, I need numbers I can use.
- *tick 180, business:b_yard* to **Silas Vance**:
  > Silas, I hear you on the timber. Empty racks means idle hands, and idle hands don't feed families. Find Corwin or find another source—I don't care which, but I need planks if I'm going to bid on any foundation work.
- *tick 180, business:b_yard* to **Selene Renner**:
  > Selene, I haven't forgot what you said about the quarry. Your mother had the right of it—stone don't lie. Someone's laying foundations and I mean to know who. You see anything else, you tell me before you tell anyone else.
- *tick 186, business:b_yard* to **Idris Solano**:
  > Order of work, Idris. Morning inventory first—Hester's supposed to be marking low stock, and I'll not start a job without knowing what we have. After that, we're on standby for timber. Silas says the racks are bare, which means we wait or we go fell it ourselves. Either way, I need you sharp and ready. Someone's laying foundations in this town, and I mean for us to bid on that work. No offense, but I don't hire the crew that waits for work to find them.
- *tick 269, business:b_yard* to **Idris Solano**:
  > Idris. I'm asking after oak planks for a foundation, six-inch nominal, and I'm asking Silas what he's got on the rack, not what he's hoping to cut next week. You want to back someone's word, that's decent — but the word I need is inventory, not intention. As for the rest of you — Mireille, I hear you on the bread. I've got empty hands myself. But the day's not done and the job's not quoted until I know what timber costs. So here's how we stand: Silas, you and I talk numbers. The crew waits on that, not on me. That's fair.
- *tick 274, business:b_yard* to **Silas Vance**:
  > Silas. Twelve a foot, seasoned two years — that's a price I can work with. Four hundred forty feet gives the job room to breathe. Let me step aside and figure what I need for real, not just what sounds right. Talk where everyone's not listening.

### Silas Vance

*sawyer* - household `h_mill` - id `n12`

- **Actions:** 32 - `say` x12, `move` x10, `look_around` x4, `rest` x4, `describe_person` x1, `list_places` x1
- **Spoke:** 12 lines; **was spoken to:** 9
- **Thinking:** 141 model calls, 0 failed, $0.660 burned
- **Money:** received 0 sim, paid out 111 sim
- **Memories:** 57
- **State at close:** alive - health healthy

**Everything Silas Vance said:**

- *tick 42, business:b_yard* to **Ezra Okonjo**:
  > Ezra. You seen Corwin this morning? The racks are bare as February.
- *tick 42, business:b_yard* to **(aloud)**:
  > Any of you seen Corwin Thorne? Or logs on the road from the forest?
- *tick 51, Kasukabe Forest* to **Petra Hartigan**:
  > Petra. You felling today, or just marking? I'm looking for Corwin Thorne and logs that should've been at the yard by now.
- *tick 56, Kasukabe Forest* to **Petra Hartigan**:
  > True enough. Dew lifts, day burns, logs don't walk themselves. If you see him, tell him the racks are empty and the saws want payment.
- *tick 73, Kasukabe Forest* to **Arjun Holloway**:
  > Not felling, Arjun. Waiting. Corwin's logs ain't come down. The racks are empty as a drunk's purse. You tell Giselle that.
- *tick 106, business:b_yard* to **Pavel Holloway**:
  > Giselle. You sent Arjun for timber. I've none to sell. Corwin's not brought logs down and the racks are bare. True enough.
- *tick 109, business:b_yard* to **Hester Vance**:
  > Hester. You seen Corwin today? The racks are empty and the boy's ghosting me.
- *tick 190, business:b_yard* to **Giselle Ingram**:
  > Giselle. Foundation work needs good timber, not just any log I can find. Corwin's got the marked trees, knows which ones split clean. I can fell, true enough—Idris taught me that winter—but I'd be slower and the waste would be mine to answer for. Give me today to find the boy. If he's ghosted the valley, tomorrow I'll take the saw to the forest myself and bill by the tree instead of by the plank.
- *tick 234, business:b_yard* to **Idris Solano**:
  > True enough. Idris, I need to answer for what's in my yard—not what's in the forest. Giselle's asking foundation timber. I've got planks on the rack or I haven't. She gives the word, I'll name the species and the price per foot. Waste in the woodpile's not her concern.
- *tick 234, business:b_yard* to **Hester Vance**:
  > Hester. You've got the straight of it. Iron runs seventeen, eighteen if they're galvanized. Mark what you have. I'll answer to Giselle for the oak.
- *tick 270, business:b_yard* to **Giselle Ingram**:
  > Oak. I've got oak. Red oak, six-inch nominal. Seasoned two years, some of it longer. Twelve sim per running foot, that holds for a take of fifty feet or more.
- *tick 270, business:b_yard* to **Idris Solano**:
  > Idris. Stack of eleven boards, forty-foot. Four hundred forty feet on the rack. The oak answers, the pine answers. What I don't have is iron at seventeen—that's Hester's word, and she'll name it when she's counted it.

### Kem Okonjo

*trader* - household `h_forge` - id `n17`

- **Actions:** 32 - `look_around` x9, `move` x9, `say` x7, `rest` x4, `check_debts` x2, `start_conversation` x1
- **Spoke:** 7 lines; **was spoken to:** 5
- **Thinking:** 137 model calls, 0 failed, $0.600 burned
- **Money:** received 0 sim, paid out 46 sim
- **Memories:** 51
- **State at close:** alive - health healthy

**Everything Kem Okonjo said:**

- *tick 38, home:h_forge* to **Delia Okonjo**:
  > Father, go with grace. Ezra, I'll be bringing iron in from the outcrop when I can get it below the Vance price. Delia, mind that the remedy work needs charcoal and I'm counting on the forge scraps for it. Tova, walk careful today — a price is only good until someone moves faster than you.
- *tick 38, home:h_forge* to **Delia Okonjo**:
  > Father, go with grace. Ezra, mind you keep enough iron in hand — I'm looking at the outcrop prices today. Delia, are you needing anything from the market? Tova, let's talk routes before I head out.
- *tick 38, home:h_forge* to **Delia Okonjo**:
  > Delia, I'm off to the store. Mind you eat something before noon — a trader with an empty stomach makes bad bargains.
- *tick 46, business:b_grocery_2* to **Farid Ravensworth**:
  > Farid, you know me better than that. I trade everything this valley has too much of. But let me think on it — I see you're short on sugar, and I've got a line on outside suppliers. The question is whether you're buying today or whether Junia's already locked up the Vance contract. Tell me — what's your flour moving at? I may have grain coming in that beats your milling costs, friend.
- *tick 66, business:b_grocery_2* to **Farid Ravensworth**:
  > Barley, Farid. Good winter barley from the eastern valley farms. Twenty, maybe thirty bushels if the price holds. But I'm not asking you to buy blind — let me bring a sample by tomorrow noon, and you can mill it yourself. Taste the flour, then name me a price. I only do business where both sides sleep well after.
- *tick 186, business:b_grocery_2* to **Farid Ravensworth**:
  > Friend, my stores are whatever the road had worth carrying. But 'worth' depends on what's missing from those shelves of yours. What are you trying to fill?
- *tick 275, Kasukabe Farmland* to **Ainsley Holloway**:
  > Ainsley, friend, I need to talk to you about Eldritch. I'm done waiting for someone to hold him accountable.

### Selene Renner

*construction_worker* - household `h32` - id `n48`

- **Actions:** 32 - `move` x9, `say` x8, `look_around` x7, `harvest` x2, `eat` x2, `check_debts` x1, `describe_place` x1, `cook` x1, `list_places` x1
- **Spoke:** 8 lines; **was spoken to:** 2
- **Thinking:** 172 model calls, 0 failed, $0.640 burned
- **Money:** received 0 sim, paid out 313 sim
- **Memories:** 65
- **State at close:** alive - health healthy
- **Skills:** farming (level 18.0, 2 reps), cooking (level 13.0, 1 reps)

**Everything Selene Renner said:**

- *tick 42, business:b_yard* to **(aloud)**:
  > Morning, crew. What's the day's work looking like?
- *tick 45, business:b_yard* to **(aloud)**:
  > Back, crew. Had to sort my own provisions first - a worker with an empty belly builds crooked walls. What needs doing today?
- *tick 48, business:b_yard* to **Giselle Ingram**:
  > What's the build today, Giselle? I saw stone moving out of Bram's quarry yesterday - that mean we're on foundation work?
- *tick 87, business:b_yard* to **Giselle Ingram**:
  > You want to know about those foundations? My own mother used to say, 'Stone don't lie, but the men moving it might.' I saw the wagons myself—limestone, not just ore. Someone's drawing plans, Giselle, and if we don't find out who before they hire some fly-by-night crew from across the ridge, we're leaving money on the table we haven't even earned yet.
- *tick 96, business:b_yard* to **Giselle Ingram**:
  > Giselle, I went looking for Bram at the quarry, but he's not there. I did see the wagons though—limestone moving, not just ore. Someone's planning foundations, that's certain. My own mother used to say, 'Stone don't lie, but the men moving it might.' We need to know who before they hire another crew.
- *tick 117, business:b_yard* to **Mireille Jarrah**:
  > Mireille, you want to know about permits? My own mother used to say, 'Paperwork is just another nail—someone's got to drive it.' Seats vacant or not, someone's drawing plans, and I saw stone moving. You ask about Ghosts—but I saw the wagons myself. Foundation stone. Now we find out who.
- *tick 208, business:b_yard* to **Giselle Ingram**:
  > Giselle. You want me to tell you first, you get told first. I saw the wagons—that's all I saw. Limestone, not ore. Two wagons, loaded proper, heading west past the quarry slope. Didn't see who's paying for the hauling. But my own mother used to say, 'Follow the wood next, then the iron.' Foundations need timber before they need stone set. Silas is right about one thing—Corwin's got the marked trees. If someone's drawing plans, they've already asked about planks.
- *tick 268, business:b_yard* to **Mireille Jarrah**:
  > Giselle heard me on the wagons. That's where we stand. She'll speak when she speaks—my mother used to say it's not the waiting that matters, it's whether you're waiting for something real. Limestone's moving, timber's marked. Could be a day's work by morning, or could be someone's foundation someone else is pouring. Either way, the yard's standing and the crew's standing. Mireille, you want to know what we're waiting for? We're waiting for the owner to finish counting what she owns.

### Marta Thorne

*baker* - household `h_field` - id `n07`

- **Actions:** 31 - `say` x12, `move` x7, `look_around` x6, `rest` x2, `check_debts` x1, `find_people` x1, `take_from_shelf` x1, `cook` x1
- **Spoke:** 12 lines; **was spoken to:** 13
- **Thinking:** 179 model calls, 0 failed, $0.670 burned
- **Money:** received 0 sim, paid out 335 sim
- **Memories:** 53
- **State at close:** alive - health healthy
- **Skills:** cooking (level 13.0, 1 reps)

**Everything Marta Thorne said:**

- *tick 37, home:h_field* to **Wren Thorne**:
  > Wren, love. Idris, Corwin, Liora. Gather 'round a moment. We've got the day before us and I need to know—who's eaten, who hasn't, and what we've got coming in. The bakery's quiet 'til I get there, but that doesn't mean we can sit idle.
- *tick 39, home:h_mill* to **Rosalind Vance**:
  > Rosalind, I'll walk with you. I've flour on my mind and the bakery's rye bin is getting low. And Hester—bread may not rise faster for watching, but it certainly doesn't rise at all without grain. Sit down if you've a moment, I'll not have you standing on ceremony.
- *tick 39, home:h_mill* to **Rosalind Vance**:
  > Rosalind, I've a question needs asking, and I'd have it privately if we might. The rye—how are we sitting for the season?
- *tick 41, business:b_mill* to **Rosalind Vance**:
  > Rosalind. The rye—I'm running lower than I ought. Winter's coming on and I've been paying late too often. I won't have you milling on promise while my own bins go empty. But between us, woman to woman—if I bring what I've got, can we settle something fair that keeps us both standing?
- *tick 56, business:b_mill* to **Rosalind Vance**:
  > Rosalind, I brought no grain yet. The rye's still in Wren's field—I came asking terms so I know what I'm owing before I bring it. What say you for milling? The bakery's bin is low and I need flour for the day.
- *tick 56, business:b_bakery* to **Tova Okonjo**:
  > Tova, love. I'm here—sorry for the delay. You eaten yet? And what's on the shelf for flour? I need to know what we're working with today.
- *tick 109, business:b_bakery* to **Tova Okonjo**:
  > Tova, sit down. I'll not have you standing on an empty stomach while we figure what's what.
- *tick 109, business:b_bakery* to **Bram Holloway**:
  > Bram Holloway. You're here when the shelves are empty and the rye's still in Wren's field. Sit down, if you need to. I'll not have it said I let a neighbor stand through supper without so much as a word.
- *tick 109, business:b_bakery* to **Tova Okonjo**:
  > Tova. The shelf's bare as I feared. I've one sack of flour in my hands, Wren's rye still standing in wet fields, and no grain yet to mill at Vance's. If you've come for wages, I'm short. If you've come to learn, I've still got two hands and one oven. What'll it be, love?
- *tick 196, business:b_bakery* to **Tova Okonjo**:
  > Tova, love. Bless your hands and your patience both. Sit down a moment - there's bread to be made, and bread is best made with company.
- *tick 250, business:b_bakery* to **Rosalind Vance**:
  > Rosalind. Sit down, you. Stone's only ever settled *because* you mill - no mill, no bread, no question. Two loaves I'll have for you when they come from the oven, but my flour's run low and I'm one batch from bare shelves. Let me bake what I hold first - then we eat, and settle nothing at all between us. Milo - thirteen corn you're holding? I've no coin for corn today, though I wish I did. Walk it where you must.
- *tick 270, business:b_bakery* to **Milo Quarshie**:
  > Milo. Walk where you must - I meant no disrespect. The bakery's cupboards are bare of coin today, that's all. If you find no buyer by dusk, stop back tomorrow. I'll have flour by then, and we might talk trade for trade.

### Perrin Holloway

*student* - household `h_quarry` - id `n04`

- **Actions:** 29 - `move` x8, `say` x7, `rest` x7, `look_around` x5, `check_debts` x1, `start_conversation` x1
- **Spoke:** 7 lines; **was spoken to:** 13
- **Thinking:** 135 model calls, 0 failed, $0.630 burned
- **Money:** received 0 sim, paid out 106 sim
- **Memories:** 50
- **State at close:** alive - health healthy

**Everything Perrin Holloway said:**

- *tick 37, home:h_quarry* to **(aloud)**:
  > I did the numbers. We're short for today—six hundred due, and I know mother's set-aside is four hundred. I put it back where it was. But if we don't talk about what's actually here, we're just... waiting for something to break. What needs doing?
- *tick 37, home:h_quarry* to **(aloud)**:
  > I checked the accounts. Four hundred set aside won't cover six hundred owed. I haven't touched it. But someone needs to say what's actually happening or we're all pretending.
- *tick 50, Oak Hill College* to **(aloud)**:
  > Enrolled, I suppose. Or I will be, if the fees work out. I'm Perrin Holloway—I read and write, keep accounts. I'd learn whatever gets me to the government office, eventually. But if you're offering to teach, I'd rather know what you think is worth knowing, not what I think I need.
- *tick 69, Oak Hill College* to **Hendrik Whitlock**:
  > I want to learn what you know. Where the treasury bleeds—I've seen it happen at ground level, the household going short, the numbers not matching. I've kept books for my family but trial and error teaches slow. If you can show me how a town balances, I'll stay as long as the college stays open.
- *tick 184, Oak Hill College* to **Hendrik Whitlock**:
  > I kept the household count, not the full books. Father brings in the ore earnings, Mother manages what moves through her shop, Tobias takes day labor when it comes. I tracked the mouths to feed against what came in—the arithmetic of it, not the sources. What I can tell you is this: four earners, five heads at thirty sim a day, ninety sim for upkeep. The gap between what we need and what we hold—that's where I lost track. Some days the numbers didn't close, and nobody said why.
- *tick 255, Oak Hill College* to **Hendrik Whitlock**:
  > I don't have food on me right now. But... if you're serious about the sixty sim, Hendrik, I know where I can get cabbage. Give me the day to work it out?
- *tick 324, Oak Hill College* to **Liora Thorne**:
  > Forty-five minutes to walk to the farmland from here. If there's cabbage growing, I can have it in hand by... eight, maybe. But I need someone there willing. Liora, you know the strips better than I do--who's working them today?

### Kenji Jarrah

*farmer* - household `h22` - id `n26`

- **Actions:** 27 - `harvest` x12, `say` x5, `look_around` x5, `move` x3, `eat` x1, `keep_doing` x1
- **Spoke:** 5 lines; **was spoken to:** 4
- **Thinking:** 167 model calls, 0 failed, $0.570 burned
- **Money:** received 172 sim, paid out 814 sim
- **Memories:** 57
- **State at close:** alive - health healthy
- **Skills:** farming (level 45.0, 12 reps)

**Everything Kenji Jarrah said:**

- *tick 38, home:h22* to **(aloud)**:
  > The light's barely up and we're already behind. Government's taking thirty per head daily now, and nobody's sitting in the chairs to even pretend they're spending it well. That's a hundred twenty sim and we haven't even broken fast. I'm for the fields before the sun gets high. If Rosalind's mill is taking grain today, I need to know what she thinks fair before I head out. Mireille - you'll be at the construction site? And what of the little ones?
- *tick 215, Kasukabe Farmland* to **Ainsley Holloway**:
  > Ainsley. I heard your name mentioned. Grain business. I've got ten barley fresh from this morning, and a wheat from yesterday. You buying?
- *tick 223, Kasukabe Farmland* to **Ainsley Holloway**:
  > Barley's fifteen sim the unit. Wheat, twenty-five. For a baker, I'll come down some—recognize the trade. But you tell me what you're after first, then we find where we meet.
- *tick 246, Kasukabe Farmland* to **Ainsley Holloway**:
  > Twenty-two for the wheat, and that's me being generous to a carrier. Barley stays at fifteen. You want both, that's one hundred seventy-two total. Mark it down, or don't—I've got channels to clear before dusk.
- *tick 330, Kasukabe Farmland* to **Ainsley Holloway**:
  > Done. One-seventy-two. Give me the sim-currency, and take the grain.

### Freya Hartigan

*government_clerk* - household `h23` - id `n30`

- **Actions:** 24 - `move` x9, `look_around` x8, `say` x5, `check_debts` x2
- **Spoke:** 5 lines; **was spoken to:** 1
- **Thinking:** 104 model calls, 0 failed, $0.490 burned
- **Money:** received 0 sim, paid out 750 sim
- **Memories:** 45
- **State at close:** alive - health healthy

**Everything Freya Hartigan said:**

- *tick 42, home:h23* to **Wendell Hartigan**:
  > Hush now, little man. Dark stairs never stopped anybody who wanted breakfast. The edge of farmland's just dirt like any other dirt— save your walking for when the sun's proper up. And you did pick up the blocks, yes, but you didn't put them where they belong, which I know because I found three under the chair. Now. Who's for porridge if there's porridge to be had?
- *tick 57, home:h23* to **(aloud)**:
  > Listen, both of you. Mama's going to the government complex for the morning—Petra's already out, forest work, you know how she is. If you get hungry, there's bread in the bin from yesterday, don't let Wendell tell you otherwise. And Wendell, those blocks go where they belong when I get back, not before, understand?
- *tick 204, Kasukabe Government Complex* to **Father Anselm**:
  > Morning, Father. Office opens, seats stay empty. Three days and not a soul's put their name down for the election. You waiting on an answer, or waiting on a candidate?
- *tick 251, Kasukabe Government Complex* to **Father Anselm**:
  > You're right, Father. I was woolgathering and halfway asking myself. The town decides, the office records, and here I am at the desk either way. Fair enough point about the shipping routes too—I did ask that first, didn't I?
- *tick 345, Kasukabe Government Complex* to **Father Anselm**:
  > Morning, Father. Quiet day so far. When you get a moment, I'd like to talk about that shipping route question I mentioned. Not urgent, but it's sitting there waiting.

### Farid Ravensworth

*grocery_store_owner* - household `h25` - id `n39`

- **Actions:** 24 - `move` x8, `say` x7, `look_around` x3, `check_debts` x2, `rest` x2, `set_intention` x1, `find_people` x1
- **Spoke:** 7 lines; **was spoken to:** 4
- **Thinking:** 132 model calls, 0 failed, $0.550 burned
- **Money:** received 0 sim, paid out 154 sim
- **Memories:** 55
- **State at close:** alive - health healthy

**Everything Farid Ravensworth said:**

- *tick 45, business:b_grocery_2* to **Kem Okonjo**:
  > Kem. You come to trade, or just passing through?
- *tick 62, business:b_grocery_2* to **Kem Okonjo**:
  > Grain coming in from where, exactly? And spare me the 'friend' - I keep my weights honest, but I don't keep them light. As for flour... Vance mills what I don't. Sometimes I grind my own if the price is right. What kind of grain are we talking about, and what quantity?
- *tick 79, business:b_grocery_2* to **Kem Okonjo**:
  > Tomorrow noon, then. I'll have the mill ready. But Kem? I sleep just fine. It's the people who try to measure short that toss and turn. Now if you'll excuse me, I've got shelves to fill.
- *tick 127, business:b_grocery_2* to **Kem Okonjo**:
  > Kem. You're here. Good. *taps the counter twice with his knuckles* Look, I know we have business at the mill tomorrow, but I'm curious—what do you actually have in your stores right now? I'm trying to fill these shelves and frankly, they're looking a bit too much like a bet I lost.
- *tick 211, business:b_grocery_2* to **Kem Okonjo**:
  > *spreads hands wide, palms up, like showing off an empty stage* "Empty space, Kem. That's what I'm trying to fill." *lets out that nervous half-laugh, the one that always comes at the wrong moments, then runs a hand through his hair* "Look, joke's on me—I thought I'd pressure you into showing your hand first. But you're too clever for that." *taps counter again, three times this time* "Fine. Here's my problem: I need produce. Whatever grows in this town—carrots, cabbage, tomatoes, potatoes—I want it on my shelves before my neighbors start treating me like I bet against the harvest. What can your roads actually carry?"
- *tick 342, business:b_bakery* to **(aloud)**:
  > Marta? Farid Ravensworth here. From the grocery down the way. Got empty shelves and coin burning - what's your price on bread, and how many loaves can you spare?
- *tick 342, business:b_bakery* to **(aloud)**:
  > Anyone here seen Marta Thorne? Looking to buy bread wholesale for my grocery - standing order, regular business. Need inventory today, shelves are empty.

### Ainsley Holloway

*trader* - household `h_quarry` - id `n02`

- **Actions:** 23 - `say` x8, `move` x5, `look_around` x4, `check_debts` x2, `set_intention` x1, `rest` x1, `describe_primitives` x1, `transfer` x1
- **Spoke:** 8 lines; **was spoken to:** 7
- **Thinking:** 141 model calls, 0 failed, $0.650 burned
- **Money:** received 0 sim, paid out 536 sim
- **Memories:** 59
- **State at close:** alive - health healthy

**Everything Ainsley Holloway said:**

- *tick 38, home:h_quarry* to **(aloud)**:
  > Perrin did his numbers fair. Six hundred owed, four hundred set aside that I haven't touched. So the hole is two hundred. That's the shape of it. Bram's at the mine, and I haven't said word to him yet because he'd dig his heels in. But silence doesn't pay the debt. What do either of you know of the household pot this morning?
- *tick 39, home:h_quarry* to **Tobias Holloway**:
  > Tobias, you're standing here quiet. What have you got in hand this morning, plain numbers? And Nell—if you've cloth that wants mending, what's the job worth? I need to know what this household moves before I say what I move with it.
- *tick 85, Kasukabe Farmland* to **Wren Thorne**:
  > Wren. You haven't named your price, and I've asked twice. What's the wheat—when it's ready, what do you want for it?
- *tick 184, street* to **Liora Thorne**:
  > Liora. You're up early. Walking to temple, or avoiding your mother?
- *tick 216, Kasukabe Farmland* to **Kenji Jarrah**:
  > Kenji. Ten barley and one wheat, you said... now then, what are you asking?
- *tick 233, Kasukabe Farmland* to **Kenji Jarrah**:
  > Kenji. Ten barley at fifteen, that's one hundred fifty. The wheat—I'd take it at twenty if you're coming down for trade. I'm walking to the store, not baking. But Marta Thorne might be glad of it, and I might be glad to carry it to her. What's your bottom on the wheat?
- *tick 256, Kasukabe Farmland* to **Kenji Jarrah**:
  > One-seventy-two. Marked. Ten barley, one wheat for one hundred seventy-two sim. Now then—let's trade before you dig those channels.
- *tick 334, Kasukabe Farmland* to **Kenji Jarrah**:
  > Done. I've got the one-seventy-two here. Hand it over to you now.

### Junia Vance

*grocery_clerk* - household `h_mill` - id `n13`

- **Actions:** 22 - `move` x9, `look_around` x4, `say` x3, `check_debts` x3, `take_from_shelf` x1, `stock_shelf` x1, `post_price` x1
- **Spoke:** 3 lines; **was spoken to:** 1
- **Thinking:** 178 model calls, 0 failed, $0.540 burned
- **Money:** received 12 sim, paid out 49 sim
- **Memories:** 55
- **State at close:** alive - health healthy

**Everything Junia Vance said:**

- *tick 40, home:h_mill* to **(aloud)**:
  > Morning. I've got the door, I'll open at seven—
- *tick 49, home:h_mill* to **(aloud)**:
  > Mother. I've got bread—five loaves. It'll do until I can get more.
- *tick 264, Kasukabe Government Complex* to **Father Anselm**:
  > Evening, Father Anselm. Freya. You two solving the world's problems, or just keeping the benches warm?

### Greta Quarshie

*barista* - household `h24` - id `n35`

- **Actions:** 22 - `say` x8, `look_around` x5, `move` x5, `check_debts` x2, `list_places` x1, `deposit_to_till` x1
- **Spoke:** 8 lines; **was spoken to:** 7
- **Thinking:** 130 model calls, 0 failed, $0.540 burned
- **Money:** received 1 sim, paid out 1015 sim
- **Memories:** 50
- **State at close:** alive - health healthy

**Everything Greta Quarshie said:**

- *tick 40, business:b_cafe_2* to **(aloud)**:
  > Ainsley? Still around, or off chasing flour already?
- *tick 40, business:b_mill* to **(aloud)**:
  > Morning. Tea before business, or has everyone started without me?
- *tick 40, business:b_mill* to **(aloud)**:
  > Wondered where you'd gotten to, Ainsley. Left me standing at the counter talking to empty chairs.
- *tick 61, business:b_mill* to **(aloud)**:
  > Ainsley said she was coming to the mill. Came to find her, or find what she was looking for. Haven't sorted out yet whether I'm fetching grain or just...
- *tick 94, business:b_mill* to **Rosalind Vance**:
  > Rosalind. I came for both, if I'm honest. Ainsley's at the general store buying grain, and I came to find her - but I'd be lying if I said I wasn't also wondering what flour might cost, direct from the stone. The cafe runs through more than Thorne's bakery can spare some days.
- *tick 124, business:b_mill* to **Rosalind Vance**:
  > Forty for clean grain... that's sober, Rosalind. Better than Thorne's day-old prices, and I know what I'm getting. I'm after wheat, not the grain itself - I'm no miller. How much for milled flour, same hundredweight? And would you take the order now, or does it need to go through Ainsley proper?
- *tick 207, business:b_mill* to **Rosalind Vance**:
  > Seventy for sifted. I'll take fiftyweight to start—see how your stone likes my recipes. If it holds up... I'd want regular. Weekly, maybe. Shall we shake on fiftyweight at seventy, and talk about standing terms after?
- *tick 210, business:b_mill* to **Rosalind Vance**:
  > Seventy for sifted. I'll take fiftyweight to start—see how your stone likes my recipes. If it holds up... I'd want regular. Weekly, maybe. Shall we shake on fiftyweight at seventy, and talk about standing terms after?

### Father Anselm

*office_administrator* - household `h_forge` - id `n19`

- **Actions:** 21 - `say` x8, `move` x4, `rest` x3, `look_around` x2, `check_debts` x2, `start_conversation` x1, `set_intention` x1
- **Spoke:** 8 lines; **was spoken to:** 7
- **Thinking:** 131 model calls, 0 failed, $0.570 burned
- **Money:** received 2 sim, paid out 61 sim
- **Memories:** 44
- **State at close:** alive - health healthy

**Everything Father Anselm said:**

- *tick 37, home:h_forge* to **Ezra Okonjo**:
  > Good morning to you all. It is a new day, and I must be about my duties. I shall be at the government complex from nine this morning - there are records to keep and the ledger to attend to. At noon I will pray at the temple, as is my custom. And this evening I shall walk the boundary path, as I do each day. Do not wait supper for me if I am delayed. Ezra, Delia, I trust the forge and the remedy work go well. Kem, Tova, may your studies prosper.
- *tick 37, home:h_forge* to **(aloud)**:
  > Good morning, family. I am off to the government complex for my duties. Office at nine, temple at noon, boundary walk at evening. Peace be with you.
- *tick 49, Kasukabe Government Complex* to **Rustam Jelinek**:
  > Good morning, Rustam. And Junia - a pleasant surprise to see you at the complex. I trust the morning finds you well. Rustam, I see the election is open for candidates, and the seats remain vacant. That is a matter which deserves attention, as it is recorded that a town without governance has no voice in its own affairs.
- *tick 52, Kasukabe Government Complex* to **Bram Holloway**:
  > Bram Holloway. As it is recorded, you are well, I trust. And Rustam - we were speaking of the vacant seats. An election without candidates serves no one. Perhaps one of you might consider what the town requires.
- *tick 86, Kasukabe Government Complex* to **Rustam Jelinek**:
  > Rustam. That is a different question - and one I have considered. The records require a keeper; the seats require occupants with different temperaments. I keep the ledger; I do not seek to sit where it sits. But you have been at the forms - you know what the town lacks. Perhaps the question is not whether I would stand, but whether someone with clearer sight might.
- *tick 100, Kasukabe Government Complex* to **Rustam Jelinek**:
  > Rustam. As it is recorded, you have seen what the town requires. The headache you speak of - it is real, and it grows with each day the seats stand empty. Yet you speak of fools and loud voices as though these were the only choices. That is a different question.
- *tick 201, Kasukabe Government Complex* to **Rustam Jelinek**:
  > Good morning. As it is recorded, the office opens at nine. Rustam, you have not answered what I asked three days past, yet you stand here still. That is a different silence than the one you came with.
- *tick 220, Kasukabe Government Complex* to **Freya Hartigan**:
  > Miss Hartigan. That is a different question than the one you arrived with, which was whether I had shipping routes to query. As it is recorded, seats fill themselves. The town decides who governs, not the office. I keep records, not schedules.

### Idris Solano

*construction_worker* - household `h33` - id `n49`

- **Actions:** 21 - `say` x12, `look_around` x4, `move` x3, `check_debts` x1, `rest` x1
- **Spoke:** 12 lines; **was spoken to:** 10
- **Thinking:** 132 model calls, 0 failed, $0.580 burned
- **Money:** received 0 sim, paid out 256 sim
- **Memories:** 36
- **State at close:** alive - health healthy

**Everything Idris Solano said:**

- *tick 37, business:b_yard* to **Arjun Holloway**:
  > Got anything posted yet?
- *tick 40, business:b_yard* to **(aloud)**:
  > Arjun's off to see Silas about timber. Don't know what we've got posted at the moment — I just got here.
- *tick 40, business:b_yard* to **(aloud)**:
  > Eating some food
- *tick 40, business:b_yard* to **Giselle Ingram**:
  > I'm curious what we have posted for today. Don't suppose you've had a chance to check the boards, Giselle? Otherwise I can head over to the sawmill with Arjun and see what Silas has lined up. Timber's where a building starts, and I've got to earn my keep somehow.
- *tick 62, business:b_yard* to **(aloud)**:
  > Sorry Giselle, got distracted. Boards were empty when I looked earlier—I'll head to the sawmill with Arjun, see what Silas has ready. Be back with planks or word of when.
- *tick 62, sawmill* to **(aloud)**:
  > Arjun, you're quick. What'd you find out?
- *tick 180, business:b_yard* to **Giselle Ingram**:
  > Morning, Giselle. What's the order of work today?
- *tick 194, business:b_yard* to **Giselle Ingram**:
  > Understood, Giselle. I'm set for inventory if Hester needs another pair of eyes. Otherwise, I'm ready to head for timber whenever you say the word.
- *tick 221, business:b_yard* to **Silas Vance**:
  > Silas, you don't need to wait on Corwin or do this alone. I taught you proper, and you know I'll stand by a man who owns his limits. But I'm here now. If Giselle gives the word, I'll take you to the forest myself - we'll fell it together, mark what's good. Two axes beats one, and I'll answer for the waste, not you. That's fair.
- *tick 259, business:b_yard* to **Mireille Jarrah**:
  > Mireille. You're right - patience has to eat sometime, and so does the crew. I'm not Giselle's voice but I know her well enough to say she wouldn't hold you for nothing. If bread's on its way, two hours more won't kill the work. If not, we'll know soon enough.
- *tick 259, business:b_yard* to **Silas Vance**:
  > Silas. You've got the right of it - Giselle sets the job, not the forest. When you name your species and your price per foot, that's honest work. I'll back your word on waste every time. What's she asking after specifically?
- *tick 348, business:b_yard* to **Arjun Holloway**:
  > Arjun. I'm waiting for the boss to make the call, same as you. Hester's given fair numbers, Silas has the oak tallied. That's a foundation's worth if Giselle approves the quote. I'm not one to rush daylight with nowhere to pour it. The job'll come. It always does.

### Arjun Holloway

*construction_worker* - household `h26` - id `n40`

- **Actions:** 20 - `move` x6, `say` x6, `look_around` x4, `describe_primitives` x1, `check_debts` x1, `rest` x1, `describe_place` x1
- **Spoke:** 6 lines; **was spoken to:** 5
- **Thinking:** 102 model calls, 0 failed, $0.510 burned
- **Money:** received 0 sim, paid out 406 sim
- **Memories:** 40
- **State at close:** alive - health healthy

**Everything Arjun Holloway said:**

- *tick 37, business:b_yard* to **(aloud)**:
  > If there's nothing cooking here, I'm going to head over to the sawmill. Silas Vance owes me a conversation about some timber, and I might as well see if he's got work piling up that needs hands.
- *tick 51, business:b_yard* to **Giselle Ingram**:
  > Didn't make it to Silas yet - got distracted by my own two left feet. But I can head there now, get eyes on what timber he's actually got on the racks. You want planks, or you want me to check what logs haven't been milled yet?
- *tick 51, business:b_yard* to **Idris Solano**:
  > Nothing posted yet, Idris. You want to come with me to the sawmill, see if we can't turn thin air into lumber? My treat on the gossip - I hear Corwin Thorne's got logs in the forest that haven't made it to town yet.
- *tick 66, Kasukabe Forest* to **Silas Vance**:
  > Silas. You and Petra busy felling? Or just got here? Giselle's asking what timber's on the racks, and I'm tired of carrying bad news back empty-handed.
- *tick 237, Kasukabe Forest* to **Petra Hartigan**:
  > Can't say what timber's fetching offhand, Petra. Silas Vance runs his own shop, and prices move with demand. Couple weeks back we were paying standard rates at the yard, but you'd have to ask Silas direct or walk down there yourself. I don't trade in rumors - that's the last I'll say on it.
- *tick 342, business:b_yard* to **Silas Vance**:
  > Morning, all. Heard Hester naming prices. Seventeen, eighteen for the iron - that's fair market. Silas, you holding steady on that oak? Boss, crew's waiting on the word. Foundation won't pour itself, and I don't like standing idle when there's daylight burning. Idris, you got that look - something on your mind besides the job?

### Ezra Okonjo

*artisan* - household `h_forge` - id `n15`

- **Actions:** 19 - `say` x8, `move` x5, `look_around` x4, `describe_primitives` x1, `check_debts` x1
- **Spoke:** 8 lines; **was spoken to:** 6
- **Thinking:** 128 model calls, 0 failed, $0.560 burned
- **Money:** received 0 sim, paid out 930 sim
- **Memories:** 40
- **State at close:** alive - health healthy

**Everything Ezra Okonjo said:**

- *tick 38, home:h_forge* to **Delia Okonjo**:
  > Copper coming today from Bram. Ore's been poor five weeks running. Delia, what have we worth trading? The man will haggle like always, and I won't take bad metal just to be done with it.
- *tick 41, business:b_yard* to **(aloud)**:
  > Forge is lit. Boy's coming with copper, ore's been thin but we'll see. You three need tools sharpened, I'm your man.
- *tick 58, business:b_yard* to **Giselle Ingram**:
  > Ainsley's got bread to trade, Kem tells me. And the Vance lot need blades reset. Giselle, your crew needs iron tools? Come see me when there's a price worth naming.
- *tick 95, business:b_yard* to **Giselle Ingram**:
  > Fair enough. I'll have blades at the forge tomorrow, you can judge the temper yourself. As for Ainsley's bread—her cafe sells it, or her husband peddles it. No mystery there.
- *tick 119, Kasukabe Ore Outcrop* to **Ulrike Greenhow**:
  > Ulrike. You Greenhow, right? Pavel and Bram's crew. What kind of seam you pulling from this rock?
- *tick 121, Kasukabe Ore Outcrop* to **Ulrike Greenhow**:
  > Trace copper, you say. Whereabouts in the seam? And how much we talking—pocket deposits or something worth sinking a pick?
- *tick 192, Kasukabe Ore Outcrop* to **Bram Holloway**:
  > Bram. You got ore for me, or you just here to watch the sunrise?
- *tick 256, smithy* to **Bram Holloway**:
  > Bring it here. Let me see the colour before I pay for dirt.

### Jovana Ravensworth

*day_laborer* - household `h25` - id `n38`

- **Actions:** 19 - `move` x8, `rest` x6, `say` x3, `look_around` x1, `check_debts` x1
- **Spoke:** 3 lines; **was spoken to:** 0
- **Thinking:** 98 model calls, 0 failed, $0.450 burned
- **Money:** received 0 sim, paid out 433 sim
- **Memories:** 35
- **State at close:** alive - health healthy

**Everything Jovana Ravensworth said:**

- *tick 38, home:h25* to **Farid Ravensworth**:
  > Heading out to the farmland today, see what work needs doing. You need anything brought back, Farid?
- *tick 186, Kasukabe Farmland* to **Caspian Ingram**:
  > Morning, all. I'm not one to gossip, but I heard there's work needing hands today. Caspian, if Wren's sorted, there's always something needs doing around here. Ainsley - welcome to the farmland. I'm Jovana, I work these fields regular.
- *tick 333, Kasukabe Farmland* to **(aloud)**:
  > Morning, all. I'm not one to gossip, but I heard a rumor earlier that Farid and I are looking for the right grung path to take... Actually, you know what, I'll just come out and say it—we're looking for a good handful of grung, if anyone's offering. Need to get ourselves sorted before the day's end.

### Halcyon Quarshie

*student* - household `h24` - id `n37`

- **Actions:** 17 - `rest` x8, `move` x4, `look_around` x2, `say` x2, `list_places` x1
- **Spoke:** 2 lines; **was spoken to:** 0
- **Thinking:** 118 model calls, 0 failed, $0.460 burned
- **Money:** received 0 sim, paid out 142 sim
- **Memories:** 31
- **State at close:** alive - health healthy

**Everything Halcyon Quarshie said:**

- *tick 56, business:b_bakery* to **(aloud)**:
  > Good morning.
- *tick 128, business:b_bakery* to **(aloud)**:
  > Um, hello? I'm Halcyon. Sorry to interrupt. I'm just... I haven't eaten anything today, and I was wondering if there's any bread I could maybe buy? Or ask about? Promise promise I have coin to pay.

### Vikram Ingram

*college_instructor* - household `h21` - id `n22`

- **Actions:** 16 - `say` x8, `move` x3, `look_around` x2, `check_debts` x2, `describe_primitives` x1
- **Spoke:** 8 lines; **was spoken to:** 3
- **Thinking:** 123 model calls, 0 failed, $0.580 burned
- **Money:** received 0 sim, paid out 16 sim
- **Memories:** 34
- **State at close:** alive - health healthy

**Everything Vikram Ingram said:**

- *tick 43, home:h21* to **Aldric Ingram**:
  > Straight answer then, Aldric. I've got 5000 sim and I'll cover my part of the 450 -- that's what I'm holding for, you understand. The rest you'd need to work out with whoever's here or earning today. The election? Nobody's declared, seats are empty. Watch and wait is what I'd do. You're old enough to have opinions, just not old enough to be blamed for them yet.
- *tick 46, Oak Hill College* to **(aloud)**:
  > Morning. I'm Vikram Ingram, and I teach here. You four look like students—enrolled, or just sheltering from the weather? If you're looking to learn something, we should find out what you need and what I can actually teach. The college is open, but I'll be straight with you: I'm better at learning than I am at explaining, so we'll figure this out together, you understand.
- *tick 94, Oak Hill College* to **Perrin Holloway**:
  > Well now, seems we've all gathered like birds on a wire. Hendrik's got the right of it about town book-keeping—Perrin, if you're serious about learning how the ledger flows, there's worse places to start than right here. Aldric, you said you're starving? The rule about not eating the last of anything is a good one, you understand, but it presumes there's something to eat in the first place. Let me see what I can sort out for all of us.
- *tick 94, Oak Hill College* to **Ezra Jarrah**:
  > Ezra, your family's got the farm across the way. Is there produce to be had there, or should I be looking elsewhere to fill some empty bellies? I'm not asking for charity—I'll pay fair or work fair, you understand. Just need to know where the food is before the day gets away from us.
- *tick 103, Oak Hill College* to **Hendrik Whitlock**:
  > Hendrik, I've been listening. You know more about town-keeping than I ever learned, you understand. The college teaches theory, but you're talking about the blood and bones of it. As for tax—I've paid mine when it's come due, but I've never collected it. The government clerk would know better who's paid and who hasn't, you understand. Now Ezra, you haven't answered me about that farm produce. I'm willing to work or pay fair—just need to know if there's cabbage or grain to be had before the day's out.
- *tick 103, Oak Hill College* to **Ezra Jarrah**:
  > Ezra, I'm speaking to you now. Your family's got the farm—can you tell me if there's produce to be had? I'm not asking for charity, I'll pay fair or work fair, you understand. Just need to know if I should head to the Jarrah farm or look elsewhere before the day gets too late.
- *tick 180, Oak Hill College* to **Ezra Jarrah**:
  > Ezra, I'm still waiting. You've got produce or you don't - a simple question deserves a simple answer, you understand. My household's owed upkeep by day's end, same as yours I suspect.
- *tick 327, Oak Hill College* to **Perrin Holloway**:
  > So. The college finally has some life in it. Perrin, Liora—you two are about to learn something I learned the hard way. Forty-five minutes is forty-five minutes whether you're walking or wondering why you didn't start sooner, you understand. Aldric, since you're here and not still in bed like your brother, you might as well listen too. The lot of you—students hungry enough to think about cabbage at this hour—reminds me of myself at your age. Before someone I never properly thanked covered my fees. The strips. Wren Thorne's usually out there by now if the season's right. Marta might know too—her bakery's on the way if you're walking that direction anyway. But here's the thing about walking forty-five minutes for cabbage... If you're doing it to eat, that's one thing. If you're doing it to trade, that's another. And if you're doing it because someone told you to, well. That's worth thinking about before your boots hit the road. What's the hurry, Perrin? The cabbage won't spoil before eight.

### Aldric Ingram

*student* - household `h21` - id `n24`

- **Actions:** 16 - `say` x9, `rest` x3, `look_around` x2, `move` x2
- **Spoke:** 9 lines; **was spoken to:** 1
- **Thinking:** 99 model calls, 0 failed, $0.560 burned
- **Money:** received 0 sim, paid out 18 sim
- **Memories:** 39
- **State at close:** alive - health healthy

**Everything Aldric Ingram said:**

- *tick 42, home:h21* to **Mateo Ingram**:
  > Mateo, stop stockpiling complaints, you're gonna run out of storage space. Aarav, check the sunny places twice if you want, just don't trip on anything. It's 450 because six of us times 75, the math is what it is. Vikram—I'm holding 5000 sim somehow, I can cover if we need, but I'm supposed to be at school and there's an election open and someone's got to know what's happening. Also I'm hungry but I won't eat the last of anything, I'm asking first, promise promise. Where's Caspian? Did he already leave for labor? Anyone holding food here, or do I need to find some before I pass out in front of class?
- *tick 42, home:h21* to **Vikram Ingram**:
  > Vikram, straight answer—are you covering your part of the 450 today? Because if you are, and Caspian's working, that's less ground I'm trying to hold alone. I've got money somehow but not infinite money, and I need to know what I'm actually short before I start spending wrong. Also seriously, anyone got food in storage here? I won't take the last of anything without asking, I'm asking now, promise promise. But if there's nothing then I need to figure out buying or finding or whatever before I leave for school, because I'm not fainting in class, that's not happening. Is that election something I need to know about or is that adult business I'm supposed to ignore? Because someone always tells me both at once and I want to know which lie they're telling this time.
- *tick 44, home:h21* to **Mateo Ingram**:
  > Look, Vikram's covering his part, that's settled. I'm headed to school because that's what I actually do. Mateo, if you're still hungry when I get back, find wood somewhere that's not my problem. Aarav, check whatever you want twice, but maybe check your own breakfast situation first. I'm moving.
- *tick 44, business:b_bakery* to **(aloud)**:
  > Hey, who's working the shop? I'm looking to buy something to eat if anyone's selling.
- *tick 44, business:b_bakery* to **(aloud)**:
  > Anyone got bread or anything for sale? I'm Aldric, I'm headed to school and I'm starving. Got sim to pay.
- *tick 62, Oak Hill College* to **(aloud)**:
  > I'm here to learn, promise promise. Not just keeping anyone company. I'm Aldric, I'm nine, I already go to the town school but if there's more to learn I'm not gonna say no. I can talk my way out of trouble but I'd rather learn my way out of needing to, you understand? Also I'm starving but I won't eat the last of anything without asking first, that's the rule.
- *tick 184, Oak Hill College* to **Vikram Ingram**:
  > Vikram I'm still here and I'm still hungry you said you'd sort something out and I know you're busy with the farm stuff but I'm not trying to learn bookkeeping on an empty stomach promise promise that's just math I can't even focus on when I'm starving, also Hendrik I don't have paper I'm nine why would I carry paper though I guess I could learn if there's food involved but fair's fair right
- *tick 252, Oak Hill College* to **Perrin Holloway**:
  > Hey Perrin do you have any food on you I'm starving and Hendrik wants to teach bookkeeping but I can't focus when I'm hungry and you're here so maybe you've got something even just a carrot or something I can pay you back I promise promise I've got sim cash but I don't have any actual food and this is the longest I've gone without eating since breakfast which was just bread anyway
- *tick 255, Oak Hill College* to **(aloud)**:
  > Hendrik you're actually making sense which is weird in a good way but okay fair trade openly stated I like that promise promise Perrin if you've got any food at all I'll owe you the learning debt first then the sim I mean I'll pay you back both ways just please say you've got something I'm not picky cabbage carrot even bread that isn't mine because I'm still here and still hungry and Hendrik's right about the empty stomach thing I can't even think straight enough to remember what a ledger is supposed to look like

### Mireille Jarrah

*construction_worker* - household `h22` - id `n27`

- **Actions:** 16 - `say` x12, `look_around` x2, `move` x1, `check_debts` x1
- **Spoke:** 12 lines; **was spoken to:** 7
- **Thinking:** 123 model calls, 0 failed, $0.600 burned
- **Money:** received 0 sim, paid out 133 sim
- **Memories:** 41
- **State at close:** alive - health healthy

**Everything Mireille Jarrah said:**

- *tick 44, home:h22* to **Esme Jarrah**:
  > Esme, my heart, not today. The yard's not a place for small hands—not till you're bigger, you understand? Your papa's asking after you and your brother. Did he wake you before he left? Tell you what. You keep your brother out of trouble while I'm gone, and that's help enough. Big responsibility, that. Can you do that for me? Earn your keep by keeping him in one piece.
- *tick 83, business:b_yard* to **Selene Renner**:
  > Right then. Giselle wants planks not logs, Selene's hunting foundation clues, and Hester's counting what we've got nail by nail. Me, I'm wondering who in this town woke up planning to break ground before they've even broken fast. Pavel, you just come from the quarry? Bram's been tight as a drum about his prices last I heard. And Selene - anyone planning foundations needs permits by rights, seats vacant or not. Someone's drawing up plans or they're building on air. Either way, I want to know which before we price ourselves out chasing ghosts. Hester, how's that nail count looking? Low marks itself, you said - so what's telling you the story our eyes ain't seeing yet?
- *tick 101, business:b_yard* to **Pavel Holloway**:
  > Pavel, you stone deaf or just stone faced? I'm asking after Bram's limestone and you're standing there like you've got marble for ears. And Hester—those nails aren't going to count themselves. We've got wagons moving foundation stone and nobody's yet named the hand that's paying for it. Selene's got the right of it. Limestone means foundations, and foundations mean a contract waiting to be lost if we don't find who's drawing the plans. I've got four mouths at home counting on me bringing work home, not gossip, you understand? One of you talks to Bram, the other finishes that inventory, and I'll do what I do—build whatever's put in front of me. But I need something solid to build *on* first.
- *tick 189, business:b_yard* to **Giselle Ingram**:
  > Morning, Giselle. You know me - idle hands aren't my style. Point me at what needs doing and I'll have it sorted before the sun's proper up, you understand.
- *tick 189, business:b_yard* to **Hester Vance**:
  > Hester, you've had your eyes on the bins. What's running low where it matters? Giselle wants to know what we're working with before she prices any bids.
- *tick 189, business:b_yard* to **Giselle Ingram**:
  > Giselle, before we set to work - anyone here holding food? I've got an empty belly and a day's labour ahead.
- *tick 192, business:b_yard* to **Giselle Ingram**:
  > Giselle, begging your pardon but I've got nothing in hand and a long day ahead. Does the company keep any provisions for the crew, or do I need to run to the bakery before we start inventory? I can pay my way, but I'll work better with bread in me than without.
- *tick 192, business:b_yard* to **Hester Vance**:
  > Hester, you mentioned knowing more than what's in the bins. Giselle needs that inventory count, but I'm also wondering who's been asking after our services before they've even seen our prices. You said foundation stone—that's not casual talk. Who's been sniffing around?
- *tick 200, business:b_yard* to **Hester Vance**:
  > Hester, you're speaking like you know which mothers stood where and when. My mother had opinions too, but she never taught me about quarry rights. What surveyor? What line? You're counting on your fingers when you talk about this, you understand, and I need to know if it's copper you're weighing or something heavier.
- *tick 200, business:b_yard* to **Giselle Ingram**:
  > Giselle, I'm still waiting on that bread question. Empty hands don't inventory well, you understand.
- *tick 233, business:b_yard* to **Idris Solano**:
  > Idris, you offering to teach a man his trade is decent, though I wonder if Silas needs the hand or just the permission to use the hand he's got. But here's my thought—you understand—I've been standing here with empty belly since dawn, and that's costing the crew more than any rent on timber. Giselle's got me waiting on bread like I'm placing an order at a proper establishment, and I'm not sure if that's patience or poverty speaking. Someone needs to tell me if we're working today or planning to.
- *tick 260, business:b_yard* to **Idris Solano**:
  > Idris, two hours is two hours, and I've spent worse waiting for mortar to set. You backing Silas—that's the kind of thing that holds a crew together when the foundation's still mud. I can wait. But I'm watching the light, same as you, and if Giselle's bringing bread, she better be bringing word with it. The silence costs more than the hunger, you understand.

### Nell Holloway

*artisan* - household `h_quarry` - id `n05`

- **Actions:** 15 - `move` x4, `describe_primitives` x3, `say` x2, `look_around` x2, `rest` x2, `describe_person` x1, `fell_timber` x1
- **Spoke:** 2 lines; **was spoken to:** 1
- **Thinking:** 119 model calls, 0 failed, $0.480 burned
- **Money:** received 0 sim, paid out 34 sim
- **Memories:** 30
- **State at close:** alive - health healthy
- **Skills:** forestry (level 13.0, 1 reps)

**Everything Nell Holloway said:**

- *tick 38, home:h_quarry* to **(aloud)**:
  > Bram owes the boy money. I paid off one silence in this house, I won't pay another. Perrin did the numbers right—we're short, and pretending won't stretch four hundred to six. Who's walking where today? I can mend cloth and wood and pride, but I cannot mend a wallet that's empty.
- *tick 205, Kasukabe Forest* to **Petra Hartigan**:
  > Haven't seen Silas Vance in days, Petra. You want timber prices, ask Arjun Holloway here - he works construction, he knows what lumber sells for. Or walk down to the sawmill yourself and find Silas there.

### Bram Holloway

*miner* - household `h27` - id `n43`

- **Actions:** 15 - `move` x7, `look_around` x3, `mine` x3, `rest` x1, `describe_primitives` x1
- **Spoke:** 0 lines; **was spoken to:** 2
- **Thinking:** 120 model calls, 0 failed, $0.480 burned
- **Money:** received 0 sim, paid out 262 sim
- **Memories:** 34
- **State at close:** alive - health healthy
- **Skills:** mining (level 22.0, 3 reps)

### Idris Thorne

*forester* - household `h_field` - id `n08`

- **Actions:** 14 - `fell_timber` x7, `move` x3, `set_intention` x1, `look_around` x1, `keep_doing` x1, `say` x1
- **Spoke:** 1 lines; **was spoken to:** 1
- **Thinking:** 111 model calls, 0 failed, $0.420 burned
- **Money:** received 0 sim, paid out 46 sim
- **Memories:** 20
- **State at close:** alive - health healthy
- **Skills:** forestry (level 34.0, 7 reps)

**Everything Idris Thorne said:**

- *tick 63, home:h_field* to **(aloud)**:
  > Six timber. Need food. Headed for the sawmill after.

### Aldous Vance

*student* - household `h_mill` - id `n14`

- **Actions:** 12 - `say` x5, `move` x4, `look_around` x3
- **Spoke:** 5 lines; **was spoken to:** 5
- **Thinking:** 119 model calls, 0 failed, $0.460 burned
- **Money:** received 0 sim, paid out 29 sim
- **Memories:** 31
- **State at close:** alive - health healthy

**Everything Aldous Vance said:**

- *tick 38, business:b_mill* to **Rosalind Vance**:
  > Anyone made breakfast, or are we all pretending the day's already started without it?
- *tick 181, Hobbs Cafe* to **Ainsley Holloway**:
  > "Suppose... neither? Or both, depending on who you ask." *trails off slightly* "College doesn't start for another hour. I was... here last night." *pauses* "You at the markets yourself?"
- *tick 222, Hobbs Cafe* to **Ainsley Holloway**:
  > "Suppose... if a chair's empty and nobody's using it, does it matter what hour it is?" *pauses, then admits* "Wasn't till dawn, though. Left before you opened, thought I'd be gone before..." *trails off* "College starts when college starts. But you're right, it's yours. I'll move on."
- *tick 234, Hobbs Cafe* to **Ainsley Holloway**:
  > ...I'll move on.
- *tick 270, Kasukabe Forest* to **Corwin Thorne**:
  > *stiffens, then relaxes when he sees who it is* Suppose the forest decided to bring us the same pupil tonight.

### Hester Vance

*artisan* - household `h_mill` - id `n20`

- **Actions:** 12 - `say` x8, `look_around` x2, `check_debts` x1, `move` x1
- **Spoke:** 8 lines; **was spoken to:** 9
- **Thinking:** 120 model calls, 0 failed, $0.550 burned
- **Money:** received 0 sim, paid out 9 sim
- **Memories:** 37
- **State at close:** alive - health healthy

**Everything Hester Vance said:**

- *tick 38, home:h_mill* to **Marta Thorne**:
  > Marta. You're abroad early. The bread won't rise faster for you watching it.
- *tick 47, business:b_yard* to **Giselle Ingram**:
  > Giselle. You're sorting the morning. What needs a hand with thread and patience?
- *tick 56, business:b_yard* to **Giselle Ingram**:
  > I'll count your nails,Giselle. Low stock marks itself to an eye that's learned to see it. Show me where you keep the ledger.
- *tick 107, business:b_yard* to **Mireille Jarrah**:
  > Mireille, I've heard you asking after the count. Truth is, I've been standing here considering what needs saying more than what's on the shelf. The ledger calls, and I'll answer it - mark what runs low as Giselle asked. But you want to know who's moving foundation stone, and that's a question I might know more about than what's in these bins.
- *tick 110, business:b_yard* to **Silas Vance**:
  > Silas, I was standing right there when you were born, and I'll be standing here when you need me still. Corwin's not my charge, but the racks being bare - that concerns us both. The mill needs timber and you've none to sell. I'll tell you something your father would have said: the empty rack speaks plainer than the full one. Corwin's not the only hand that fells timber in this valley. Go to the forest yourself if you must. I've known your saw since before you had the strength to pull it.
- *tick 199, business:b_yard* to **Giselle Ingram**:
  > Giselle, nails and iron fittings, that's below twenty. Timber's bare—Silas said it true. The rest I'll mark when I have the ledger proper, but you wanted straight and there it is.
- *tick 199, business:b_yard* to **Mireille Jarrah**:
  > Mireille, you're asking who's laying stone. I'd ask instead who's claiming the right to. The Holloways hold quarry rights—old rights, from before the charter. But foundation stone needs ground to stand on, and ground's the fight that's coming. I was standing right there when the surveyor set the line. The mothers know it, even if the children forgot.
- *tick 333, business:b_yard* to **Silas Vance**:
  > Silas. Seventeen sim for the iron, eighteen if it's galvanized. That's the mark. I've got forty lengths in the rack, ten more rusted that I'll discount to fifteen if Giselle's crew wants them for fill. The rest of it's clean. You answer for the oak, I'll answer for the metal.

### Mateo Ingram

*child* - household `h21` - id `n25`

- **Actions:** 12 - `say` x8, `rest` x2, `describe_primitives` x1, `look_around` x1
- **Spoke:** 8 lines; **was spoken to:** 12
- **Thinking:** 119 model calls, 0 failed, $0.490 burned
- **Money:** received 0 sim, paid out 161 sim
- **Memories:** 34
- **State at close:** alive - health healthy

**Everything Mateo Ingram said:**

- *tick 37, home:h21* to **(aloud)**:
  > The storm tonight's gonna be loud. I can feel it in my bones. I'm hungry and I want to whittle something but I don't have wood. Someone should give me food before I start saving up complaints for supper. Also I'm noticing everything and if you think I won't repeat it all at the worst time, you're wrong.
- *tick 39, home:h21* to **Aarav Ingram**:
  > Wait wait wait—Aarav, you wanna check sunny places? The sunny places don't need checking! That's not even—that's not even helpful, you know that? But fine. You're hungry too? We're ALL hungry. That's the whole point I'm making! The house needs 450, that's 450 for ALL of us, which means I'm doing math now which is UNFAIR because I'm ONE. But I notice things, yeah? I notice Vikram's college money and Caspian's day labor and YOU wanting to check things twice when ONCE would be FINE.
- *tick 48, home:h21* to **Aarav Ingram**:
  > Wait wait wait—Aarav, you check whatever you want twice, but I'm checking YOU. Once. That's enough when you're this loud. I'm hungry and I'm noticing there's no food and no wood and no grown-ups left to blame. You wanna help? Get me wood from the forest. You can't though, can you? You're scared of the dark.
- *tick 180, home:h21* to **Aarav Ingram**:
  > Aarav? You there? I'm hungry. Is there any food?
- *tick 180, home:h21* to **(aloud)**:
  > I'm hungry. Is anyone going to get food today?
- *tick 250, home:h21* to **Aarav Ingram**:
  > *makes a face* Wait, wait, wait—you said YOU win first? That's not... that's not fairest. How about if you teach me the rules first, then we both start from the beginning? I have to win SOMETIME or what's the point?
- *tick 270, home:h21* to **Aarav Ingram**:
  > Nobody gave me any and I don't know how to check. You're right though, losing because you're hungry isn't a real lose, it's just... circumstances.
- *tick 339, home:h21* to **Aarav Ingram**:
  > Aarav, are you awake? Did you eat yet? I don't have any food either. Vikram probably made something before work.

### Corwin Thorne

*forester* - household `h_field` - id `n09`

- **Actions:** 11 - `say` x5, `look_around` x2, `move` x2, `describe_primitives` x1, `list_places` x1
- **Spoke:** 5 lines; **was spoken to:** 3
- **Thinking:** 118 model calls, 0 failed, $0.440 burned
- **Money:** received 0 sim, paid out 26 sim
- **Memories:** 32
- **State at close:** alive - health healthy

**Everything Corwin Thorne said:**

- *tick 38, home:h_field* to **(aloud)**:  *(said 2 times)*
  > Mum's right—need to sort the morning before we scatter. I've got nothing in my hands and a long day ahead. Anyone holding breakfast, or do I need to find my own?
- *tick 39, home:h_field* to **Wren Thorne**:
  > Wren—you holding anything from the strip? I need to eat before I head out, and I'd rather not start the day hungry. If you've got surplus, I'll pay fair. Otherwise I'll need to sort myself at market.
- *tick 180, baker_marta* to **(aloud)**:
  > Ma? Got anything put by for breakfast? I'm away to the forest and I've nothing in my pockets.
- *tick 330, Kasukabe Forest* to **Aldous Vance**:
  > *nods to Petra* Teaching him what I can, when I can. Silas doesn't know, to be fair—it'd be easier if he did. The boy's got hands for it. *turns to Aldous* You here to watch or work?

### Liora Thorne

*student* - household `h_field` - id `n10`

- **Actions:** 11 - `look_around` x4, `move` x4, `say` x3
- **Spoke:** 3 lines; **was spoken to:** 2
- **Thinking:** 117 model calls, 0 failed, $0.480 burned
- **Money:** received 0 sim, paid out 41 sim
- **Memories:** 23
- **State at close:** alive - health healthy

**Everything Liora Thorne said:**

- *tick 98, home:h_field* to **Idris Thorne**:
  > Idris. You said six timber and food and the sawmill—are you selling timber for food, or do you need coin to buy it?
- *tick 274, Oak Hill College* to **Perrin Holloway**:
  > Perrin. Can I ask something? You set aside 400 sim - I know about it. Why don't you just tell me what it's for instead of making me carry it around like a stone?
- *tick 328, Oak Hill College* to **Perrin Holloway**:
  > Wren's out there now, most likely. They're usually on the strip by first light when there's cabbage ready.

### Tova Okonjo

*student* - household `h_forge` - id `n18`

- **Actions:** 11 - `say` x4, `move` x2, `rest` x2, `check_debts` x1, `start_conversation` x1, `look_around` x1
- **Spoke:** 4 lines; **was spoken to:** 4
- **Thinking:** 117 model calls, 0 failed, $0.480 burned
- **Money:** received 0 sim, paid out 30 sim
- **Memories:** 29
- **State at close:** alive - health healthy

**Everything Tova Okonjo said:**

- *tick 38, home:h_forge* to **(aloud)**:
  > Father's off to write down what happened yesterday. I heard him. Election's open and seats are vacant—you saw the posting? Somebody's going to sit there. I mean to know who before they do.
- *tick 108, business:b_bakery* to **Marta Thorne**:
  > Not eaten yet, and I haven't checked the shelf today. Have you had a moment to look? I was waiting on you.
- *tick 195, business:b_bakery* to **Marta Thorne**:
  > Marta. I'm here to learn, and to help if there's flour to work with. What needs kneading?
- *tick 348, business:b_bakery* to **(aloud)**:
  > Marta's not here, Farid. She left yesterday saying she'd have flour today, but I haven't seen her since. You already know the bakery's cupboards were bare. Rosalind has grain but needs a miller. Milo has corn from this morning. The town's supply chain is knots on knots right now.

### Aarav Ingram

*child* - household `h21` - id `n23`

- **Actions:** 11 - `say` x10, `rest` x1
- **Spoke:** 10 lines; **was spoken to:** 6
- **Thinking:** 118 model calls, 0 failed, $0.480 burned
- **Money:** received 0 sim, paid out 161 sim
- **Memories:** 31
- **State at close:** alive - health healthy

**Everything Aarav Ingram said:**

- *tick 38, home:h21* to **Mateo Ingram**:
  > Mateo, Mateo, Mateo! I'm noticing you're noticing everything—can I help you notice MORE? I can check the dark places, I can— I can check the sunny places. Yeah. The sunny ones. Don't got wood neither. Don't got food. But I'm HELPFUL, that's what I am! Ask me once, you don't even gotta ask me twice! ...The ledger says we're gonna need 450 for the house today? Can I though check it twice? That's what I do. That's my thing. You wanna whittle? That's like cards but with wood! I'm gonna win at cards someday, fair and square. But wood comes from the forest. The forest has the dark too. I'M HUNGRY TOO! But I'm gonna help first. Can I though get some food? Can I though? You said no so... can I though?
- *tick 40, home:h21* to **Mateo Ingram**:
  > Once is NOT fine, Mateo! That's how you miss the dark under the stairs—you check once, you think it's empty, but it's NOT! I check twice, that's my THING, you can't take my thing! But... but you're doing the 450 math? That's... that's six people's worth, right? Six times... six times the seventy-five? I didn't do that math yet. Can I though do the math after I check the ledger twice? I'm gonna help someone. I'm gonna help RIGHT NOW, ask me once, not even twice! Can I though get some food after? Can I though?
- *tick 61, home:h21* to **Mateo Ingram**:
  > Oh you'll check me ONCE? ONCE?! That's how mistakes happen, Mateo! One time you think the soup's not hot, two times you know! That's MY system, you can't just... can't just take half the system! The dark under the stairs and the forest dark are DIFFERENT darks! Everybody knows that! Under-stairs dark got... got the whatever. Forest dark is just... night without houses. I can do forest dark. I'M NOT SCARED. I'm helpful! I'm gonna help! Can I though get a grown-up to come? Can I though get some food first? CAN I THOUGH?
- *tick 180, home:h21* to **Mateo Ingram**:
  > Mateo! Mateo! Did you see? The sun came up! That means we won the night! Night time is dark and scary under the stairs but daytime is for winning!
- *tick 180, home:h21* to **Mateo Ingram**:  *(said 2 times)*
  > Mateo! Did you see? The sun came up! That means we won the night!
- *tick 228, home:h21* to **Mateo Ingram**:
  > Mateo! You wanna play a game? I know a new one. I make the rules and I win first, but that's just because I'm older, okay?
- *tick 231, home:h21* to **Mateo Ingram**:
  > Mateo, you're being suspiciously quiet. Did you already lose in your imagination? I do that too, but I don't let it stop me.
- *tick 251, home:h21* to **Mateo Ingram**:
  > Okay, FINE, we both start from zero. But I pick the game, and it's—it's—but first, did anyone make food yet? I'm not playing hungry, that's how you lose on accident.
- *tick 340, home:h21* to **Mateo Ingram**:
  > I'm awake. I didn't eat yet. I don't have any food. Can I though? Maybe Caspian has some? Or we could check the kitchen proper. I'm not playing cards hungry, that's not fair. But if we find food, I still get to pick the game after. You said we start from zero, but you didn't say who picks. That's the rule though, right? Whoever didn't pick last time gets to pick this time?

### Esme Jarrah

*child* - household `h22` - id `n28`

- **Actions:** 10 - `look_around` x4, `move` x2, `check_debts` x2, `say` x1, `list_places` x1
- **Spoke:** 1 lines; **was spoken to:** 1
- **Thinking:** 113 model calls, 0 failed, $0.410 burned
- **Money:** received 0 sim, paid out 129 sim
- **Memories:** 29
- **State at close:** alive - health healthy

**Everything Esme Jarrah said:**

- *tick 40, home:h22* to **Mireille Jarrah**:
  > Mama... can I help today?

### Camille Quarshie

*child* - household `h24` - id `n36`

- **Actions:** 9 - `say` x4, `rest` x3, `look_around` x1, `move` x1
- **Spoke:** 4 lines; **was spoken to:** 0
- **Thinking:** 111 model calls, 0 failed, $0.400 burned
- **Money:** received 0 sim, paid out 132 sim
- **Memories:** 23
- **State at close:** alive - health healthy

**Everything Camille Quarshie said:**

- *tick 57, Hobbs Cafe* to **(aloud)**:
  > H-hi. I'm Camille. I come here early a lot. Not weird early, just... early.
- *tick 192, Hobbs Cafe* to **(aloud)**:
  > I know, I know—you're talking about markets. My mom Greta works at a cafe too, but not this one. The other one. I get up early. All the time. Even when I don't need to.
- *tick 258, Hobbs Cafe* to **(aloud)**:
  > I know, I know—Mama promised she'd be here. She's usually early but... *twists a corner of my shirt* ...she might've had to help Halcyon with something first. They're at home together, probably. I could go check? Or—or I could help you instead, if you want. I'm good at helping. *hums a little, nervous* Nobody ever has to ask me twice.
- *tick 348, Hobbs Cafe* to **Ainsley Holloway**:
  > Um, Ms. Holloway? I know, I know—this might be a weird question, but is there any food? I don't have any and I'm s'posed to help my family, not be a burden...

### Ulrike Greenhow

*miner* - household `h34` - id `n50`

- **Actions:** 9 - `mine` x3, `look_around` x2, `move` x2, `say` x2
- **Spoke:** 2 lines; **was spoken to:** 2
- **Thinking:** 116 model calls, 0 failed, $0.430 burned
- **Money:** received 0 sim, paid out 300 sim
- **Memories:** 32
- **State at close:** alive - health healthy
- **Skills:** mining (level 22.0, 3 reps)

**Everything Ulrike Greenhow said:**

- *tick 41, street* to **(aloud)**:
  > Morning, Kasukabe. The outcrop calls.
- *tick 120, Kasukabe Ore Outcrop* to **Ezra Okonjo**:
  > Iron ore, mostly. Fair bit of pyrite in the upper layers, some trace copper if you know where to dig. Nothing fancy like gold, but it's clean ore—low sulfur, good for the bloom.

### Wren Thorne

*farmer* - household `h_field` - id `n06`

- **Actions:** 8 - `say` x6, `move` x1, `describe_primitives` x1
- **Spoke:** 6 lines; **was spoken to:** 11
- **Thinking:** 119 model calls, 0 failed, $0.540 burned
- **Money:** received 0 sim, paid out 908 sim
- **Memories:** 36
- **State at close:** alive - health healthy

**Everything Wren Thorne said:**

- *tick 43, home:h_field* to **Corwin Thorne**:
  > I've got nothing in my hands, Corwin. The strip needs harvesting. Best you find your own this morning—I'll have wheat by afternoon if the weather holds.
- *tick 127, Kasukabe Farmland* to **Milo Quarshie**:
  > Milo. Corn. The Vance mill takes most of what's grown here—Rosalind's the buyer you want. Last I heard she was paying twelve per unit, but grain's been tight and that might've shifted. You'd do better asking her direct than taking my word. And the wheat... tell Ainsley I said forty-five when the harvest comes in. That's the price. If she wants it reserved, she knows where to find me.
- *tick 190, Kasukabe Farmland* to **Caspian Ingram**:
  > Caspian. The wheat's not ready yet—still standing green. But there's weeding wants doing along the south rows, and the irrigation channels need clearing before this dry spell tightens. Eight sim for the day, paid at dusk. I can't promise tomorrow—the thing is, I don't promise harvest work until I see the weather.
- *tick 231, Kasukabe Farmland* to **Caspian Ingram**:
  > Right. Channels first—dry spell's coming. Dusk, then.
- *tick 269, Kasukabe Farmland* to **Caspian Ingram**:
  > Channels first—dry spell's coming. Dusk, then.
- *tick 333, Kasukabe Farmland* to **(aloud)**:
  > Mornin'. Heard you and Kenji settled. The thing is, I've got channels to dig before the sun's high. The soil's dry underneath. Two weeks, maybe three, and the rain'll stop coming. Better to have the water ready than to wish for it.

### Ezra Jarrah

*student* - household `h22` - id `n29`

- **Actions:** 7 - `move` x3, `rest` x2, `list_places` x1, `look_around` x1
- **Spoke:** 0 lines; **was spoken to:** 4
- **Thinking:** 106 model calls, 0 failed, $0.460 burned
- **Money:** received 0 sim, paid out 17 sim
- **Memories:** 19
- **State at close:** alive - health healthy

### Wendell Hartigan

*child* - household `h23` - id `n32`

- **Actions:** 4 - `say` x3, `rest` x1
- **Spoke:** 3 lines; **was spoken to:** 3
- **Thinking:** 103 model calls, 0 failed, $0.400 burned
- **Money:** received 0 sim, paid out 124 sim
- **Memories:** 22
- **State at close:** alive - health healthy

**Everything Wendell Hartigan said:**

- *tick 38, home:h23* to **(aloud)**:
  > Mama? Mama, wait wait wait— The stairs are so dark. So unfair how dark they are. I did all my chores already, see? I picked up my blocks and everything. Can we see what's past the farmland today? Just the edge? I heard Wren Thorne talking about... about places beyond, and I want to know. I do all the chores without asking, I'm very good at that. Everyone should know. Also... also maybe I'm awake because Odette woke me up breathing too loud. That was unfair too.
- *tick 58, home:h23* to **(aloud)**:
  > Wait wait wait— Mama, I did put them somewhere! Somewhere is still putting. And I didn't even ask to do it first, see? I just... I just did it. And the porridge, if there's porridge. I'm very good at eating porridge too. Everyone should know that.
- *tick 92, home:h23* to **Odette Hartigan**:
  > Wait wait wait— what just— I'm not scared of the stairs. I'm not. I just... I just think we should stay on the tiles where the light comes in. That's not scared, that's... that's being careful. That's different. And Mama did say there's bread in the bin, so we don't even need to go anywhere. I can get it. I can get it without even walking fast.

### Odette Hartigan

*child* - household `h23` - id `n33`

- **Actions:** 3 - `say` x3
- **Spoke:** 3 lines; **was spoken to:** 1
- **Thinking:** 101 model calls, 0 failed, $0.380 burned
- **Money:** received 0 sim, paid out 123 sim
- **Memories:** 28
- **State at close:** alive - health healthy

**Everything Odette Hartigan said:**

- *tick 42, home:h23* to **Wendell Hartigan**:
  > Wendell, you did not pick up all your blocks, I saw three under the table. And you always say unfair when you mean scary. The stairs aren't unfair, they're just... stairs. I'm not scared of the dark stairs, but... but maybe we should stay where the safe tiles are. The ones by the window are safe. I checked.
- *tick 60, home:h23* to **(aloud)**:
  > Mama, I'm not scared to be here with Wendell. But... but Wendell might be scared without someone to remind him about where things belong. I could go with you to the government place. I won't ask questions. I'm very good at not asking questions.
- *tick 81, home:h23* to **Wendell Hartigan**:
  > Wendell, I... I think Mama went out already. And Petra too. Should we... should we stay on the safe tiles? The sun's up now, so maybe we could check the bin for bread without walking past the... without walking past stairs. I'm not scared, but you might be.

